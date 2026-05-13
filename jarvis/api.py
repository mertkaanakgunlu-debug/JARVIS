"""JARVIS FastAPI REST server (Faz 9) + HUD WebSocket (Faz 11).

Start with:
    python -m jarvis --api            # default port 8000
    python -m jarvis --api --port 9090

Endpoints:
    GET  /health           — liveness check (no auth required)
    WS   /ws               — WebSocket event stream for the HUD (no auth, local-only)
    POST /chat             — single-turn chat, returns full response
    POST /chat/stream      — streaming chat via Server-Sent Events
    GET  /status           — session + model + cost info
    POST /reset            — clear conversation history

Auth:
    All endpoints except /health and /ws require header:
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
from jarvis.ws import event_bus, start_metrics_task

# ── App lifespan (starts background metrics push) ─────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_metrics_task()
    yield

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(title="JARVIS API", version="0.1.0", docs_url="/docs", lifespan=lifespan)

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


# ── Request / Response models ─────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    language: str = ""  # optional language hint ("tr", "en", …)


class ChatResponse(BaseModel):
    response: str
    model: str


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
    return {"status": "ok", "version": "0.1.0"}


# ── WebSocket — HUD event stream ──────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    """Real-time event stream for the JARVIS HUD (Electron app).
    No auth — intended for localhost only.
    """
    await event_bus.connect(websocket)
    # Send initial state snapshot
    agent = _agent
    if agent:
        await event_bus.broadcast({"type": "state", "value": "idle"})
        await event_bus.broadcast({
            "type": "vault",
            "entries": [],
            "count": agent.memory.count_docs(),
        })
    try:
        while True:
            # Keep connection alive; we don't process incoming messages yet
            await websocket.receive_text()
    except WebSocketDisconnect:
        event_bus.disconnect(websocket)
    except Exception:
        event_bus.disconnect(websocket)


@app.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request):
    _check_auth(request)
    agent = get_agent()
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

    Client reads:
        data: <token>\\n\\n
        data: [DONE]\\n\\n
    """
    _check_auth(request)
    agent = get_agent()

    async def _sse_generator() -> AsyncGenerator[str, None]:
        try:
            async for token in agent.chat_stream(
                body.message,
                detected_language=body.language or "en",
            ):
                # Escape newlines inside a single SSE data field
                safe = token.replace("\n", "\\n")
                yield f"data: {safe}\n\n"
        except Exception as e:
            yield f"data: [ERROR] {e}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
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
