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
    If JARVIS_API_KEY is empty, auth is disabled (local-only use) and the
    server refuses to bind to anything but loopback -- see
    resolve_api_bind_host(). Set JARVIS_API_HOST/JARVIS_API_CORS_ORIGINS in
    .env to reach this from another device (phone/Tailscale).
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import uuid as _uuid

from fastapi import FastAPI, File, Form, HTTPException, Depends, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from jarvis.config import Settings
from jarvis.agent import JarvisAgent, ConfirmationRequired
from jarvis.ws import event_bus, start_metrics_task, start_live_data_task, live_data_snapshot
from jarvis.voice_api import start_voice_task, trigger_ptt

logger = logging.getLogger(__name__)

# Mobile routers
from jarvis.api_routers import todos as todos_router
from jarvis.api_routers import finance as finance_router
from jarvis.api_routers import calendar as calendar_router
from jarvis.api_routers import vault as vault_router
from jarvis.api_routers import push as push_router
from jarvis.api_routers import tasks as tasks_router
from jarvis.api_routers import system as system_router

# ── App lifespan ──────────────────────────────────────────────────────────────

_voice_enabled: bool = False    # set by run_server() before uvicorn starts — True for --voice or --wakeword
_voice_wakeword: bool = False   # set by run_server() before uvicorn starts
_monitor_enabled: bool = False  # set by run_server() before uvicorn starts — True for --monitor
_monitor_instance = None        # JarvisMonitor | None — set in lifespan() when _monitor_enabled


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _monitor_instance
    start_metrics_task()
    if _agent is not None:
        # Faz 5: connect MCP servers (Playwright, etc.) now, on uvicorn's own
        # real long-lived loop, before any request (including one that could
        # spawn a TaskExecutor background job) is served -- see
        # JarvisAgent.connect_mcp_tools()'s docstring for why that ordering
        # matters (a background job's own short-lived asyncio.run() loop must
        # never be the one that opens the MCP stdio session).
        await _agent.connect_mcp_tools()
        _agent.run_startup_backfill()
        start_live_data_task(_agent, _settings)
        if _voice_enabled or _voice_wakeword:
            start_voice_task(_agent, _settings, wakeword=_voice_wakeword)
        if _monitor_enabled:
            # Faz 7 ("monitor-in-api", subsumed from the old refactor backlog):
            # previously --monitor was silently ignored in --api mode (only the
            # CLI branch in cli.py ever constructed a JarvisMonitor) -- the
            # always-on API server is where proactive monitoring matters most,
            # not just an interactive CLI session. agent=_agent gives it a real
            # path into agent.chat() (via proactive_turn()) subject to
            # settings.monitor_proactive_enabled, same as the CLI wiring.
            from jarvis.monitor import JarvisMonitor
            _monitor_instance = JarvisMonitor(
                _settings, scheduler=_agent.scheduler, todo_store=_agent.todo_store, agent=_agent,
            )
            _monitor_instance.start()
    yield
    # ── Shutdown: archive current session so next startup begins clean ──────────
    if _monitor_instance is not None:
        _monitor_instance.stop()
        _monitor_instance = None
    if _agent is not None:
        try:
            await _agent.close_mcp_tools()  # Faz 5: don't leave a launched browser process behind
        except Exception as _e:
            print(f"[lifespan] MCP shutdown failed: {_e}")
        try:
            # reset_async: state mutation on a worker thread, summarization
            # scheduled back on this loop — the run_in_executor(reset) shape
            # ran create_task on a loopless worker thread and always skipped
            # the summary (silently, thanks to this except).
            await _agent.reset_async()
        except Exception as _e:
            print(f"[lifespan] auto-reset on shutdown failed: {_e}")

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(title="JARVIS API", version="0.2.0", docs_url="/docs", lifespan=lifespan)

# GPT-5.6 review remediation, Faz 1: CORS used to be an unconditional "*",
# which combined with an empty JARVIS_API_KEY (see resolve_api_bind_host
# below) meant any website open in any browser on the LAN could script
# requests against this API and read the response. Explicit allowlist
# instead -- see resolve_cors_origins()'s docstring for what's always
# permitted vs. settings-driven. Registered once at import time (before
# uvicorn ever serves a request in the real run_server() path), reading a
# fresh Settings() so JARVIS_API_CORS_ORIGINS in .env takes effect without
# needing the module-global _settings (not populated until init_agent()).
_CORS_LOCALHOST_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"


def resolve_cors_origins(settings: Settings) -> list[str]:
    """Explicit CORS allowlist for CORSMiddleware -- never '*'. Always
    includes the shipped Electron desktop client's file:// origin (Chromium
    serializes a file:// page's fetch() Origin header as literally "file://"
    or, for some sandboxed contexts, the opaque-origin string "null") plus
    whatever the user adds via JARVIS_API_CORS_ORIGINS (e.g. a future LAN web
    client). localhost/127.0.0.1 on any port -- covering the Vite dev
    server's variable port -- is handled separately via allow_origin_regex,
    not this list."""
    return ["file://", "null", *settings.api_cors_origins]


app.add_middleware(
    CORSMiddleware,
    allow_origins=resolve_cors_origins(Settings()),
    allow_origin_regex=_CORS_LOCALHOST_REGEX,
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
    from jarvis import paths

    db_path = paths.data_dir() / "sessions.db"
    push_store = PushStore(db_path)
    fcm = (
        FcmSender(push_store, paths.resolve(settings.firebase_credentials_path))
        if settings.push_enabled else None
    )
    executor = TaskExecutor(agent, fcm_sender=fcm)
    finance_store = FinanceStore(db_path)

    todos_router.init_todos(agent.todo_store)
    finance_router.init_finance(finance_store)
    calendar_router.init_calendar(settings)
    vault_router.init_vault(agent.memory)
    push_router.init_push(push_store, fcm)
    tasks_router.init_tasks(executor)
    system_router.init_system(settings)

    # Attach executor to agent for /chat async-heuristic
    agent._task_executor = executor


def get_agent() -> JarvisAgent:
    if _agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    return _agent


# ── Auth ──────────────────────────────────────────────────────────────────────

def _check_auth(request: Request) -> None:
    from jarvis.api_auth import check_auth
    check_auth(request, _settings.jarvis_api_key if _settings else "")


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


class ConfirmRequest(BaseModel):
    decision: str  # "approve" | "deny" | "deny:<optional guidance>"


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
    # Stabilization sprint — runtime truth fields. None before the first
    # completed foreground turn; `model`/`session_cost_usd` stay for older
    # clients (Electron HUD / Flutter).
    requested_role: str | None = None
    actual_provider: str | None = None
    actual_model: str | None = None
    fallback_used: bool | None = None
    cloud_policy: str = "auto"
    # Features currently running in degraded (no-LLM) mode because
    # CLOUD_POLICY=off disabled their direct-Gemini call — see
    # jarvis/providers.degraded_features().
    degraded: list[str] = []


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.2.0"}


# ── WebSocket — HUD event stream ──────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, token: str | None = None):
    """Real-time event stream for JARVIS HUD (Electron) and mobile app.

    Authentication: X-API-Key header (preferred — never appears in a URL, so
    it can't leak into proxy/access logs or connection history the way a query
    param does) or ?token=<JARVIS_API_KEY> query param (kept for Electron,
    whose renderer uses the browser WebSocket API and cannot set custom
    headers on the upgrade request; mobile uses the header — see
    mobile/lib/core/ws_client.dart / BUG-mob-tls). If API key is set and
    neither matches, connection is closed with code 4401. Note this closes the
    URL-logging exposure, not wire-level cleartext -- this server has no TLS
    termination, so confidentiality on an untrusted network still depends on
    tunneling through Tailscale rather than exposing this port directly.

    Faz 3: this same connection can also carry a remote-audio session — binary
    PCM mic/TTS frames multiplexed alongside the existing JSON event stream via
    audio_session_start/stop control messages. See docs/VOICE_PROTOCOL.md for
    the full wire format.
    """
    if _settings and _settings.jarvis_api_key:
        provided = websocket.headers.get("X-API-Key") or token
        if provided != _settings.jarvis_api_key:
            await websocket.close(code=4401)
            return

    await event_bus.connect(websocket)
    agent = _agent
    if agent:
        await event_bus.broadcast({"type": "state", "value": "idle"})
        await live_data_snapshot(agent, _settings)

    audio_owner_id = f"remote-ws-{id(websocket)}"
    audio_io = None
    audio_task: "asyncio.Task | None" = None
    pending_confirmation = None  # Faz 4 / BUG-4 — see jarvis/voice/session.py

    async def _stop_audio_session(reason: str) -> None:
        nonlocal audio_io, audio_task
        if audio_io is None and audio_task is None:
            return
        from jarvis.voice.session_manager import release
        from jarvis.voice_api import resume_local_voice

        if audio_io is not None:
            await audio_io.stop()
        if audio_task is not None:
            audio_task.cancel()
            try:
                await audio_task
            except asyncio.CancelledError:
                pass
        audio_io = None
        audio_task = None
        release(audio_owner_id)
        resume_local_voice()
        await event_bus.send_text_to(websocket, {"type": "audio_session_end", "reason": reason})

    async def _start_audio_session(control: dict) -> None:
        nonlocal audio_io, audio_task

        if audio_task is not None:
            # Already active on THIS connection (e.g. a client retry/double
            # send) -- without this guard, try_claim() below would succeed
            # (this connection already owns the claim) and silently leak the
            # existing engine/task by overwriting these nonlocals before
            # tearing them down. Client must call audio_session_stop first.
            await event_bus.send_text_to(
                websocket,
                {"type": "audio_session_nack", "reason": "bad_request",
                 "detail": "a session is already active on this connection"},
            )
            return

        if not _settings or not _settings.jarvis_api_key:
            # Remote audio is opt-in-by-configuration, not available with zero
            # setup -- once this channel can carry live mic audio and
            # synthesized speech, an unauthenticated connection matters a lot
            # more than read-only telemetry did.
            await event_bus.send_text_to(websocket, {"type": "audio_session_nack", "reason": "unauthenticated"})
            return
        if agent is None:
            await event_bus.send_text_to(websocket, {"type": "audio_session_nack", "reason": "bad_request"})
            return
        try:
            sample_rate = int(control.get("sample_rate", 16000))
            if sample_rate <= 0:
                raise ValueError
        except (TypeError, ValueError):
            await event_bus.send_text_to(websocket, {"type": "audio_session_nack", "reason": "bad_request"})
            return

        from jarvis.voice.session_manager import try_claim
        if not try_claim(audio_owner_id):
            await event_bus.send_text_to(
                websocket,
                {"type": "audio_session_nack", "reason": "busy", "detail": "another audio session is active"},
            )
            return

        from jarvis.voice.io_remote_ws import RemoteWsAudioIO
        from jarvis.voice.engine import RealtimeVoiceEngine, get_shared_voice_models
        from jarvis.voice.session import drive_voice_session, resolve_confirmation
        from jarvis.voice_api import pause_local_voice, run_one_response

        # Electron's main process always spawns the backend with --wakeword
        # (electron/src/main/index.js) — pause that loop's next claim so it
        # doesn't compete with this remote session for the shared agent state.
        pause_local_voice()

        audio_io = RemoteWsAudioIO(websocket, event_bus, sample_rate)
        models = await get_shared_voice_models(_settings)
        engine = RealtimeVoiceEngine(audio_io, _settings, models=models)
        await engine.load()
        await engine.start()

        def _set_pending(p) -> None:
            nonlocal pending_confirmation
            pending_confirmation = p

        async def _handle_transcript(text: str, lang: str):
            nonlocal pending_confirmation
            # Faz 4 / BUG-4: a pending confirmation always consumes the *next*
            # utterance as its yes/no answer, not a new command.
            if pending_confirmation is not None:
                pending, pending_confirmation = pending_confirmation, None
                return resolve_confirmation(
                    agent, engine, pending, text, lang,
                    on_message=lambda full: event_bus.message("j", full),
                )
            return run_one_response(
                agent, engine, text, lang, transport="voice-remote", set_pending_confirmation=_set_pending,
            )

        async def _run_session() -> None:
            try:
                await drive_voice_session(engine, _handle_transcript)
            except Exception as exc:
                logger.error("[voice] remote audio session error: %s", exc, exc_info=True)
            finally:
                await engine.stop()

        audio_task = asyncio.create_task(_run_session())
        await event_bus.send_text_to(websocket, {"type": "audio_session_ack", "session_id": audio_owner_id})

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            raw_bytes = message.get("bytes")
            if raw_bytes is not None:
                if audio_io is not None:
                    audio_io.push_mic_frame(raw_bytes)
                continue

            raw_text = message.get("text")
            if raw_text is None:
                continue
            try:
                control = json.loads(raw_text)
            except (ValueError, TypeError):
                continue
            kind = control.get("type") if isinstance(control, dict) else None
            if kind == "audio_session_start":
                await _start_audio_session(control)
            elif kind == "audio_session_stop":
                await _stop_audio_session("client_requested")
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        # Covers the connection ending (disconnect/error) while a session was
        # still active -- distinct from the explicit audio_session_stop path
        # above, which already handled the normal client-requested case.
        await _stop_audio_session("connection_closed")
        event_bus.disconnect(websocket)


@app.post("/voice/ptt/start")
async def voice_ptt_start(request: Request):
    """Trigger push-to-talk: skip wakeword and listen immediately.

    Called by the HUD when the user presses Alt+Space.
    Returns 200 if voice loop is active, 503 if voice is not running.
    """
    _check_auth(request)
    ok = trigger_ptt()
    if not ok:
        raise HTTPException(status_code=503, detail="Voice loop not running")
    return {"ok": True}


@app.post("/voice/local/pause")
async def voice_local_pause(request: Request):
    """Manually stop the local wakeword/PTT loop from claiming new turns (Faz 3).

    /ws's audio_session_start already does this automatically; this endpoint is
    a manual override for testing/troubleshooting the remote-audio path without
    relying on that automatic pause (e.g. to guarantee the local mic won't
    activate at all while testing).
    """
    _check_auth(request)
    from jarvis.voice_api import pause_local_voice
    pause_local_voice()
    return {"ok": True}


@app.post("/voice/local/resume")
async def voice_local_resume(request: Request):
    """Re-enable the local wakeword/PTT loop after a manual /voice/local/pause."""
    _check_auth(request)
    from jarvis.voice_api import resume_local_voice
    resume_local_voice()
    return {"ok": True}


@app.post("/chat")
async def chat(body: ChatRequest, request: Request):
    _check_auth(request)
    agent = get_agent()

    # Async heuristic: offload long tasks to TaskExecutor
    executor = getattr(agent, "_task_executor", None)
    if executor and executor.should_async(body.message, force=body.force_async):
        task = executor.submit(body.message)
        return {"async": True, "task_id": task.task_id, "status": task.status}

    event_bus.state("thinking")
    try:
        response, model_label = await agent.chat(
            body.message,
            detected_language=body.language or "en",
            transport="api",
        )
    except ConfirmationRequired as cr:
        # BUG-confirm-payload: this used to fall through to the generic
        # `except Exception` below and come back as an opaque 500 — the
        # conf_id/payload a client needs to call /chat/confirm/{conf_id}
        # was lost entirely. Not an error: a distinct, structured response.
        event_bus.state("idle")
        return {"confirmation_required": True, "id": cr.conf_id, "payload": cr.payload}
    except Exception as e:
        event_bus.state("idle")
        raise HTTPException(status_code=500, detail=str(e))
    event_bus.state("idle")
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
        event_bus.state("thinking")
        full: list[str] = []
        try:
            async for token in agent.chat_stream(
                body.message,
                detected_language=body.language or "en",
                transport="api-stream",
            ):
                full.append(token)
                safe = token.replace("\n", "\\n")
                yield f"data: {safe}\n\n"
        except Exception as e:
            event_bus.state("idle")
            yield f"data: [ERROR] {e}\n\n"
        else:
            event_bus.state("idle")
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/chat/confirm/{conf_id}")
async def chat_confirm(conf_id: str, body: ConfirmRequest, request: Request):
    """Resume an interrupted graph after the user approves or denies an L3 tool call.

    Streams the agent's continuation response via Server-Sent Events.
    decision values: "approve" | "deny" | "deny:<optional guidance>"
    """
    _check_auth(request)
    agent = get_agent()

    async def _sse() -> AsyncGenerator[str, None]:
        try:
            async for token in agent.resume_and_stream(conf_id, body.decision):
                safe = token.replace("\n", "\\n")
                yield f"data: {safe}\n\n"
        except Exception as e:
            yield f"data: [ERROR] {e}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        _sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _upload_dir() -> Path:
    # Function, not module constant: JARVIS_HOME may be set after import
    # (test fixture / --profile test), and uploads must follow it.
    from jarvis import paths
    return paths.data_dir() / "uploads"


MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB — mirrors files.py's MAX_READ_BYTES convention

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"}
_PDF_EXTS   = {".pdf"}
_EXCEL_EXTS = {".xlsx", ".xls"}
_CSV_EXTS   = {".csv"}
_WORD_EXTS  = {".docx", ".doc"}

_IMAGE_MIME: dict[str, str] = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png",  ".webp": "image/webp",
    ".gif": "image/gif",  ".bmp": "image/bmp",
    ".tiff": "image/tiff",
}


def _upload_system_hint(filename: str, saved_path: Path) -> str:
    """Build an agent-facing file context string for non-image files."""
    ext = Path(filename).suffix.lower()
    if ext in _PDF_EXTS:
        kind = "PDF document"
        hint = f'Use the pdf_read or pdf_vision tool on "{saved_path}" to read it.'
    elif ext in _EXCEL_EXTS:
        kind = "Excel spreadsheet"
        hint = f'Use the excel_read tool on "{saved_path}" to read it.'
    elif ext in _CSV_EXTS:
        kind = "CSV file"
        hint = f'Use the csv_read tool on "{saved_path}" to read it.'
    elif ext in _WORD_EXTS:
        kind = "Word document"
        hint = f'Use the file_read tool on "{saved_path}" to read it.'
    else:
        kind = "file"
        hint = f'Use the file_read tool on "{saved_path}" to read it.'
    return f'[User uploaded a {kind}: "{filename}" — saved at "{saved_path}". {hint}]'


@app.post("/chat/upload")
async def chat_upload(
    request: Request,
    file: UploadFile = File(...),
    query: str = Form(""),
    language: str = Form(""),
):
    """Accept a file + optional text query, stream JARVIS response.

    Images are sent directly as multimodal content to the LLM — no intermediate
    tool call required. Other files (PDF, Excel, CSV, Word) are saved to disk
    and read via the appropriate tool.
    """
    _check_auth(request)
    agent = get_agent()

    # Read in bounded chunks rather than one file.read() -- an unbounded read lets
    # a single upload exhaust memory/disk (BUG-upload). Chunked so we bail out as
    # soon as the cap is crossed instead of buffering the whole oversized file first.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1 << 20)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)",
            )
        chunks.append(chunk)
    content = b"".join(chunks)
    suffix = Path(file.filename or "upload").suffix.lower()
    user_query = query.strip() or "Please analyze this file."

    # ── Images: pass bytes directly — the LLM sees the image natively ──────────
    if suffix in _IMAGE_EXTS:
        image_mime = _IMAGE_MIME.get(suffix, "image/png")

        async def _sse_image() -> AsyncGenerator[str, None]:
            try:
                async for token in agent.chat_stream(
                    user_query,
                    detected_language=language or "en",
                    image_bytes=content,
                    image_mime=image_mime,
                    transport="api-upload",
                ):
                    safe = token.replace("\n", "\\n")
                    yield f"data: {safe}\n\n"
            except Exception as e:
                yield f"data: [ERROR] {e}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            _sse_image(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Save file to disk (needed for all non-image types) ─────────────────────
    upload_dir = _upload_dir()
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_path = upload_dir / f"{_uuid.uuid4().hex}{suffix}"
    saved_path.write_bytes(content)

    # ── PDFs: marker-pdf → markdown + figures → multimodal message ─────────────
    if suffix in _PDF_EXTS:
        from jarvis.tools.pdf import read_pdf_multimodal

        md_text, figures = read_pdf_multimodal(saved_path)
        full_input = f"[PDF: {file.filename or saved_path.name}]\n\n{md_text}\n\n{user_query}"

        # read_pdf_multimodal already extracted everything into md_text/figures
        # (and its own content-hash cache) -- the raw upload copy under
        # _UPLOAD_DIR is never touched again, so it can be cleaned up now
        # instead of accumulating on disk forever (BUG-upload).
        try:
            saved_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not clean up uploaded file %s", saved_path, exc_info=True)

        async def _sse_pdf() -> AsyncGenerator[str, None]:
            try:
                async for token in agent.chat_stream(
                    full_input,
                    detected_language=language or "en",
                    extra_images=figures or None,
                    transport="api-upload",
                ):
                    safe = token.replace("\n", "\\n")
                    yield f"data: {safe}\n\n"
            except Exception as e:
                yield f"data: [ERROR] {e}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            _sse_pdf(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Other files (Excel, CSV, Word): tool-hint approach ─────────────────────
    file_hint = _upload_system_hint(file.filename or "file", saved_path)
    full_query = f'{file_hint}\n\n{user_query}'

    async def _sse() -> AsyncGenerator[str, None]:
        try:
            try:
                async for token in agent.chat_stream(
                    full_query,
                    detected_language=language or "en",
                    transport="api-upload",
                ):
                    safe = token.replace("\n", "\\n")
                    yield f"data: {safe}\n\n"
            except Exception as e:
                yield f"data: [ERROR] {e}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            # Only safe to clean up once the stream is fully drained -- the
            # agent may call file_read/excel_read/csv_read on saved_path at any
            # point while this generator is iterating (BUG-upload).
            try:
                saved_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not clean up uploaded file %s", saved_path, exc_info=True)

    return StreamingResponse(
        _sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/status", response_model=StatusResponse)
async def status(request: Request):
    _check_auth(request)
    from jarvis.providers import degraded_features
    agent = get_agent()
    trace = agent.last_turn_trace or {}
    return StatusResponse(
        session_id=agent.session_id,
        memory_turns=agent.memory.count(),
        vault_chunks=agent.memory.count_docs(),
        model=agent.current_model_label,
        session_cost_usd=agent.usage.session_cost,
        vertex_active=agent.settings.use_vertex,
        requested_role=trace.get("requested_role"),
        actual_provider=trace.get("provider"),
        actual_model=trace.get("model"),
        fallback_used=trace.get("fallback_used"),
        cloud_policy=getattr(agent.settings, "cloud_policy", "auto"),
        degraded=degraded_features(),
    )


@app.post("/reset")
async def reset(request: Request):
    _check_auth(request)
    agent = get_agent()
    # reset_async: the blocking _state_lock/SQLite work still runs off-loop
    # (to_thread), but summarization is scheduled AFTER control returns to
    # this loop. The old run_in_executor(agent.reset) shape called
    # create_task on a loopless worker thread -> RuntimeError -> HTTP 500
    # on every content-bearing session.
    await agent.reset_async()
    return {"ok": True, "message": "Conversation archived, new session started"}


# ── Runner ────────────────────────────────────────────────────────────────────

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def resolve_api_bind_host(settings: Settings) -> str:
    """Compute the actual uvicorn bind host from api_host + jarvis_api_key.

    GPT-5.6 review remediation, Faz 1 (P0): this used to be an unconditional
    host="0.0.0.0" with no relation to whether auth was even configured --
    an empty JARVIS_API_KEY meant a fully open, unauthenticated API on every
    network interface. Now: an explicit non-loopback api_host with no key is
    a fail-fast RuntimeError instead of a silent bind. When api_host is
    unset, the default follows the key -- no key -> 127.0.0.1 (local-only,
    safe by default); key set -> 0.0.0.0 (LAN/Tailscale phone access, the
    actual documented use case).
    """
    host = (settings.api_host or "").strip()
    if not host:
        return "0.0.0.0" if settings.jarvis_api_key else "127.0.0.1"
    if not settings.jarvis_api_key and host not in _LOOPBACK_HOSTS:
        raise RuntimeError(
            f"JARVIS_API_HOST={host!r} is not loopback but JARVIS_API_KEY is empty -- "
            "refusing to bind an unauthenticated API to a non-local address. "
            "Set JARVIS_API_KEY in .env, or leave JARVIS_API_HOST unset (or 127.0.0.1) "
            "for local-only use."
        )
    return host


def run_server(settings: Settings, port: int = 8000, voice: bool = False, wakeword: bool = False, monitor: bool = False) -> None:
    """Start the Uvicorn server (blocking)."""
    global _voice_enabled, _voice_wakeword, _monitor_enabled
    _voice_enabled = voice
    _voice_wakeword = wakeword
    _monitor_enabled = monitor

    try:
        import uvicorn
    except ImportError:
        raise RuntimeError(
            "uvicorn not installed. Run: pip install fastapi uvicorn[standard]"
        )

    # Resolve (and possibly fail-fast on) the bind host BEFORE constructing
    # the agent -- no point paying JarvisAgent's heavier init cost just to
    # refuse to bind afterward.
    bind_host = resolve_api_bind_host(settings)

    init_agent(settings)

    key_status = f"auth enabled (key: {settings.jarvis_api_key[:4]}…)" if settings.jarvis_api_key else "auth DISABLED"
    print(f"\n  JARVIS API  —  http://{bind_host}:{port}")
    print(f"  Docs        —  http://127.0.0.1:{port}/docs")
    print(f"  Auth        —  {key_status}")
    print(f"  Model       —  {settings.vertex_model_fast if settings.use_vertex else settings.effective_cloud_model}")
    if monitor:
        print(f"  Monitor     —  proactive: {'on' if settings.monitor_proactive_enabled else 'toast/FCM only'}")
    print()

    uvicorn.run(app, host=bind_host, port=port, log_level="warning")
