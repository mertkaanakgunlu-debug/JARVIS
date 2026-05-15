"""JARVIS FastAPI REST server (Faz 9) + HUD WebSocket (Faz 11) + Mobile API (Faz 19A-0).

Start with:
    python -m jarvis --api            # default port 8000
    python -m jarvis --api --port 9090

Endpoints:
    GET  /health           — liveness check (no auth required)
    GET  /system/ping      — auth-free PC awake check for mobile WoL
    WS   /ws               — WebSocket event stream for the HUD (?token= optional auth)
    POST /chat             — single-turn chat, returns full response
    POST /chat/stream      — streaming chat via Server-Sent Events
    GET  /status           — session + model + cost info
    POST /reset            — clear conversation history
    --- Mobile routers (auth required) ---
    /todos, /finance, /calendar, /vault, /push, /tasks, /system/wake

Auth:
    All endpoints except /health and /system/ping require header:
        X-API-Key: <JARVIS_API_KEY from .env>
    If JARVIS_API_KEY is empty, auth is disabled (local-only use).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from jarvis.config import Settings
from jarvis.agent import JarvisAgent
from jarvis.ws import event_bus, start_metrics_task, start_live_data_task, live_data_snapshot

# Mobile routers
from jarvis.api_routers import todos as todos_router
from jarvis.api_routers import finance as finance_router
from jarvis.api_routers import calendar as calendar_router
from jarvis.api_routers import vault as vault_router
from jarvis.api_routers import push as push_router
from jarvis.api_routers import tasks as tasks_router
from jarvis.api_routers import system as system_router

# ── App lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_metrics_task()
    if _agent is not None:
        start_live_data_task(_agent, _settings)
    yield

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(title="JARVIS API", version="0.2.0", docs_url="/docs", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Single shared agent (personal assistant — one user)
_agent: JarvisAgent | None = None
_settings: Settings | None = None


def init_agent(settings: Settings) -> None:
    global _agent, _settings
    _settings = settings
    _agent = JarvisAgent(settings)
    _wire_routers(settings, _agent)


def _wire_routers(settings: Settings, agent: JarvisAgent) -> None:
    """Inject store/service references into all mobile routers."""
    from jarvis.push_store import PushStore
    from jarvis.fcm_sender import FcmSender
    from jarvis.task_executor import TaskExecutor
    from jarvis.finance_store import FinanceStore
    from pathlib import Path

    db_path = Path("data/sessions.db")
    push_store = PushStore(db_path)
    fcm = FcmSender(push_store, settings.firebase_credentials_path) if settings.push_enabled else None
    executor = TaskExecutor(agent, fcm_sender=fcm)
    finance_store = FinanceStore(db_path)

    todos_router.init_todos(agent.todo_store)
    finance_router.init_finance(finance_store)
    calendar_router.init_calendar(settings)
    vault_router.init_vault(agent.memory)
    push_router.init_push(push_store, fcm)
    tasks_router.init_tasks(executor)

    # Attach executor to agent for /chat async-heuristic
    agent._task_executor = executor


def get_agent() -> JarvisAgent:
    if _agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    return _agent


# ── Auth ──────────────────────────────────────────────────────────────────────

def _check_auth(request: Request) -> None:
    if not _settings:
        return
    api_key = _settings.jarvis_api_key
    if not api_key:
        return  # auth disabled
    provided = request.headers.get("X-API-Key", "")
    if provided != api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header")


# ── Mount mobile routers (all behind auth dependency) ─────────────────────────

app.include_router(system_router.router)   # /system/ping is auth-free by design
app.include_router(todos_router.router,    dependencies=[Depends(_check_auth)])
app.include_router(finance_router.router,  dependencies=[Depends(_check_auth)])
app.include_router(calendar_router.router, dependencies=[Depends(_check_auth)])
app.include_router(vault_router.router,    dependencies=[Depends(_check_auth)])
app.include_router(push_router.router,     dependencies=[Depends(_check_auth)])
app.include_router(tasks_router.router,    dependencies=[Depends(_check_auth)])


# ── Request / Response models ─────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    language: str = ""
    force_async: bool = False


class ChatResponse(BaseModel):
    response: str
    model: str


class AsyncChatResponse(BaseModel):
    async_: bool = True
    task_id: str
    status: str


class StatusResponse(BaseModel):
    session_id: str
    memory_turns: int
    vault_chunks: int
    model: str
    session_cost_usd: float
    vertex_active: bool


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.2.0"}


# ── WebSocket — HUD event stream ──────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, token: str | None = None):
    """Real-time event stream for JARVIS HUD (Electron) and mobile app.

    Authentication: ?token=<JARVIS_API_KEY> query param.
    If API key is set and token is wrong, connection is closed with code 4401.
    """
    if _settings and _settings.jarvis_api_key:
        if token != _settings.jarvis_api_key:
            await websocket.close(code=4401)
            return

    await event_bus.connect(websocket)
    agent = _agent
    if agent:
        await event_bus.broadcast({"type": "state", "value": "idle"})
        await live_data_snapshot(agent, _settings)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        event_bus.disconnect(websocket)
    except Exception:
        event_bus.disconnect(websocket)


@app.post("/chat")
async def chat(body: ChatRequest, request: Request):
    _check_auth(request)
    agent = get_agent()

    # Async heuristic: offload long tasks to TaskExecutor
    executor = getattr(agent, "_task_executor", None)
    if executor and executor.should_async(body.message, force=body.force_async):
        task = executor.submit(body.message)
        return {"async": True, "task_id": task.task_id, "status": task.status}

    try:
        response, model_label = await agent.chat(
            body.message,
            detected_language=body.language or "en",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return ChatResponse(response=response, model=model_label)


@app.post("/chat/stream")
async def chat_stream(body: ChatRequest, request: Request):
    """Stream response tokens via Server-Sent Events.

    If the query triggers the async heuristic, returns a single JSON SSE frame
    with {"async": true, "task_id": "..."} instead of streaming.

    Client reads:
        data: <token>\\n\\n
        data: [DONE]\\n\\n
    """
    _check_auth(request)
    agent = get_agent()

    # Async heuristic check
    executor = getattr(agent, "_task_executor", None)
    if executor and executor.should_async(body.message, force=body.force_async):
        task = executor.submit(body.message)
        import json

        async def _async_sse():
            payload = json.dumps({"async": True, "task_id": task.task_id, "status": task.status})
            yield f"data: {payload}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            _async_sse(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def _sse_generator() -> AsyncGenerator[str, None]:
        try:
            async for token in agent.chat_stream(
                body.message,
                detected_language=body.language or "en",
            ):
                safe = token.replace("\n", "\\n")
                yield f"data: {safe}\n\n"
        except Exception as e:
            yield f"data: [ERROR] {e}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/status", response_model=StatusResponse)
async def status(request: Request):
    _check_auth(request)
    agent = get_agent()
    return StatusResponse(
        session_id=agent.session_id,
        memory_turns=agent.memory.count(),
        vault_chunks=agent.memory.count_docs(),
        model=agent.current_model_label,
        session_cost_usd=agent.usage.session_cost,
        vertex_active=agent.settings.use_vertex,
    )


@app.post("/reset")
async def reset(request: Request):
    _check_auth(request)
    agent = get_agent()
    agent.reset()
    return {"ok": True, "message": "Conversation history cleared"}


# ── Runner ────────────────────────────────────────────────────────────────────

def run_server(settings: Settings, port: int = 8000) -> None:
    """Start the Uvicorn server (blocking)."""
    try:
        import uvicorn
    except ImportError:
        raise RuntimeError(
            "uvicorn not installed. Run: pip install fastapi uvicorn[standard]"
        )

    init_agent(settings)

    key_status = f"auth enabled (key: {settings.jarvis_api_key[:4]}…)" if settings.jarvis_api_key else "auth DISABLED"
    print(f"\n  JARVIS API  —  http://0.0.0.0:{port}")
    print(f"  Docs        —  http://127.0.0.1:{port}/docs")
    print(f"  Auth        —  {key_status}")
    print(f"  Model       —  {settings.vertex_model_fast if settings.use_vertex else settings.effective_cloud_model}\n")

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
