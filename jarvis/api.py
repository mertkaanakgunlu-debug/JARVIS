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
import os
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

def _confirmation_sse_frame(marker: dict) -> str:
    """One structured SSE frame for a confirmation interrupt -- Faz 7.3 (P1).

    chat_stream() surfaces an L3 confirmation as a single yielded
    __jarvis_confirm__ JSON marker (not a raised ConfirmationRequired --
    that's chat()'s contract). Before this, every SSE endpoint passed that
    internal marker straight through as if it were response text, so a
    streaming client had no reliable way to render an approval prompt.
    This is the same structured DTO /chat's non-stream path returns
    ({"confirmation_required": true, id, payload}), tagged with "type" so
    a stream client can tell it apart from ordinary tokens."""
    payload = json.dumps(
        {
            "type": "confirmation_required",
            "id": marker.get("id"),
            "payload": marker.get("payload") or {},
        },
        ensure_ascii=False,
    )
    return f"data: {payload}\n\n"


async def _sse_frames(token_stream: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
    """Wrap a raw agent token stream (chat_stream()/resume_and_stream()) into
    SSE `data: ...` frames, reframing any __jarvis_confirm__ marker into the
    structured confirmation_required frame instead of leaking it as text.

    Review remediation: this used to be copy-pasted per endpoint, and the
    copy was missed entirely at /chat/confirm's resume_and_stream() consumer
    -- a second same-turn confirmation leaked as raw JSON there. One shared
    wrapper used by every SSE endpoint in this file makes that omission
    structurally impossible for the next one too."""
    from jarvis.voice.session import parse_confirm_marker, parse_final_marker, parse_progress_marker

    async for token in token_stream:
        marker = parse_confirm_marker(token)
        if marker is not None:
            yield _confirmation_sse_frame(marker)
            continue
        # Completion-contract TTFB: the one signal a contracted+enforce turn
        # may put on the wire before the graph finishes. Reframed exactly
        # like the confirmation frame -- and for the same reason: a raw
        # __jarvis_progress__ marker is not a token, so a client that didn't
        # know to look for it would render it as literal JSON.
        progress = parse_progress_marker(token)
        if progress is not None:
            payload = json.dumps(
                {
                    "type": "progress",
                    "phase": progress.get("phase"),
                    **({"kind": progress["kind"]} if progress.get("kind") else {}),
                },
                ensure_ascii=False,
            )
            yield f"data: {payload}\n\n"
            continue
        # Paket A: the graph's terminal answer, when it differs from what was
        # already streamed. Structured like the confirmation frame for the same
        # reason -- a client must be able to tell it from ordinary tokens, or
        # it would append the correction to the text it is correcting.
        final = parse_final_marker(token)
        if final is not None:
            payload = json.dumps(
                {"type": "final_answer", "text": final}, ensure_ascii=False,
            )
            yield f"data: {payload}\n\n"
            continue
        safe = token.replace("\n", "\\n")
        yield f"data: {safe}\n\n"

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
        # CI-FLAKE-CHROMA-01: release this process's reference to chromadb's
        # System on a clean shutdown, same rationale as close_mcp_tools above
        # (don't leave a resource behind). Memory.close() never raises (see
        # its own docstring in jarvis/memory.py), so no try/except needed.
        _agent.memory.close()

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
    if os.environ.get("JARVIS_TEST_MODE") == "1":
        from jarvis.api_routers import test_identity as _ti
        _ti.init_test_identity(settings)

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

# Test-only instance handshake -- the route does not exist in a normal run.
# JARVIS_TEST_MODE is set exclusively by __main__'s --profile test pre-scan, so
# this is gated by the same decision as every other test-only isolation rather
# than by a flag a production process could pick up by accident.
if os.environ.get("JARVIS_TEST_MODE") == "1":
    from jarvis.api_routers import test_identity as test_identity_router
    app.include_router(test_identity_router.router)


# ── Request / Response models ─────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    language: str = ""
    force_async: bool = False
    # The counterpart to force_async, added 2026-07-30. The async heuristic
    # (jarvis/task_executor._should_async) matches bare substrings against short
    # everyday Turkish words -- "grafik" is one -- so an ordinary interactive
    # request like "...ve grafikle" is silently shunted to the background
    # executor and the caller gets {"async": true, "task_id": ...} instead of an
    # answer. cli.py never consults that heuristic, so the SAME sentence behaves
    # one way in the CLI and another over HTTP.
    #
    # force_sync makes the interactive path explicitly reachable, which is what
    # lets the MVP gate measure the same JarvisAgent.chat() entry point the CLI
    # uses instead of background_turn() (a different path with different
    # confirmation semantics -- see MEMORY.md on TaskExecutor and
    # ConfirmationRequired). force_async wins if both are set: an explicit
    # request for background execution is more specific than a request to skip
    # the guess.
    force_sync: bool = False
    # Agent Runtime rev.2, Faz 5 follow-up: real per-client conversation
    # support. Empty (every pre-existing client -- Electron HUD, mobile app,
    # anything predating this field) is a complete no-op, preserving today's
    # single-shared-active-session behavior exactly. A client that wants
    # independent conversations mints its own id (e.g. a UUID on first
    # launch) and echoes it back on every subsequent call -- see
    # JarvisAgent.chat()'s own docstring for why the switch happens INSIDE
    # the agent's turn lock rather than as a separate endpoint-level step.
    conversation_id: str = ""


class ConfirmRequest(BaseModel):
    decision: str  # "approve" | "deny" | "deny:<optional guidance>"
    # Post-MVP Faz 2.75 (Paket B). Optional so every existing client keeps
    # working; when supplied it must match the conversation the confirmation
    # was raised in, or the resume is refused. A shared JarvisAgent serves
    # every client, so "approve whatever is pending" was previously a sentence
    # a client could say about someone else's L3 external write.
    conversation_id: str = ""


class ChatResponse(BaseModel):
    response: str
    model: str
    conversation_id: str = ""


class AsyncChatResponse(BaseModel):
    async_: bool = True
    task_id: str
    status: str


def _should_offload(executor, body: "ChatRequest") -> bool:
    """Whether this request goes to the background TaskExecutor.

    One function rather than the same condition inlined in /chat and
    /chat/stream: they must not be able to disagree about whether a request is
    interactive. Precedence: no executor -> never; force_async -> always;
    force_sync -> never; otherwise the keyword heuristic decides.
    """
    if executor is None:
        return False
    if body.force_async:
        return True
    if body.force_sync:
        return False
    return bool(executor.should_async(body.message))


class StatusResponse(BaseModel):
    session_id: str
    memory_turns: int
    vault_chunks: int
    model: str
    session_cost_usd: float
    # Legacy name (kept for older Electron HUD / Flutter clients): actually
    # means "Vertex is CONFIGURED" (cloud_tier + project set), not "cloud
    # calls can happen" — prefer vertex_configured + cloud_calls_allowed.
    vertex_active: bool
    # Stabilization sprint — runtime truth fields. None before the first
    # completed foreground turn; `model`/`session_cost_usd` stay for older
    # clients (Electron HUD / Flutter).
    requested_role: str | None = None
    # Faz 2.5 — which rule in role_router.py chose that role.
    role_reason: str | None = None
    actual_provider: str | None = None
    actual_model: str | None = None
    # Response-scoped (patch 1.1): did a fallback tier author the visible
    # answer? turn_had_any_fallback is the turn-wide health view.
    fallback_used: bool | None = None
    turn_had_any_fallback: bool | None = None
    cloud_policy: str = "auto"
    # Patch 1.1 — billing truth: tokens whose rate is unknowable this session
    # (AI Studio key, ai_studio_billing_mode=unknown). Nonzero means
    # session_cost_usd is a lower bound, not the whole spend picture.
    session_unpriced_tokens: int = 0
    # Patch 1.1 — clearer replacements for vertex_active's overloaded name.
    vertex_configured: bool | None = None
    cloud_calls_allowed: bool | None = None
    # Features currently running in degraded (no-LLM) mode because the cloud
    # policy (off/explicit) disabled their direct-Gemini call — see
    # jarvis/providers.degraded_features().
    degraded: list[str] = []
    # Faz 3.2 — the response-authoring call's own latency diagnostics, so a
    # thinking-on/off A/B run can separate cold-load from thinking from real
    # generation instead of eyeballing one wall-clock number. None before the
    # first completed foreground turn, same as the runtime-truth fields above.
    last_latency_ms: float | None = None
    last_ttft_ms: float | None = None
    last_call_cold_start: bool | None = None
    # 2026-07-19 Faz 4 (model-selection metrics): whole-turn LLM aggregates —
    # token counts for verbosity/thinking comparisons, call count and summed
    # LLM ms so the driver's e2e wall-clock splits into LLM vs tool/overhead.
    last_turn_llm_calls: int | None = None
    last_turn_input_tokens: int | None = None
    last_turn_output_tokens: int | None = None
    last_turn_llm_total_ms: float | None = None


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
    # GET /voice/status reporter displaced by this session. A distinct
    # sentinel, not None: a session that failed BEFORE registering (e.g.
    # engine.load() raised) still reaches _stop_audio_session with audio_io
    # set, and restoring a plain None there would clear the LOCAL loop's
    # registration -- which registers once at startup and would then never
    # re-register, leaving /voice/status reporting "no session" for the rest
    # of the process's life while one is running.
    _voice_diag_unregistered = object()
    previous_voice_diag = _voice_diag_unregistered

    async def _stop_audio_session(reason: str) -> None:
        nonlocal audio_io, audio_task, previous_voice_diag
        if audio_io is None and audio_task is None:
            return
        if previous_voice_diag is not _voice_diag_unregistered:
            from jarvis.voice.diagnostics import restore_session
            restore_session(previous_voice_diag)
            previous_voice_diag = _voice_diag_unregistered
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
        from jarvis.voice.state import VoiceState, hud_state_emitter
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

        # Transport parity (2026-07-25): the remote /ws session gets the same
        # single reducer per session as cli.py's --voice loop and
        # voice_api.py's local loop -- see jarvis/voice/state.py. This was the
        # third transport left driving the HUD from ad-hoc per-site literals.
        voice_state = VoiceState(on_change=hud_state_emitter(event_bus.state))

        # A remote session takes over reporting for GET /voice/status while
        # it runs (the local loop is paused above, so there is no contest),
        # and _stop_audio_session hands reporting back to whatever was
        # registered before -- normally the local loop, which is still
        # running underneath, merely paused.
        from jarvis.voice.diagnostics import register_session as _register_voice_session
        nonlocal previous_voice_diag
        previous_voice_diag = _register_voice_session(engine, voice_state)

        async def _handle_transcript(text: str, lang: str):
            nonlocal pending_confirmation
            # Faz 4 / BUG-4: a pending confirmation always consumes the *next*
            # utterance as its yes/no answer, not a new command -- UNLESS it
            # was already resolved through a different transport (the
            # Electron HUD's /chat/confirm, the local wakeword/PTT loop) in
            # the meantime. Review remediation (2026-07-23): this remote /ws
            # session was the one transport that never got even a
            # cross-transport staleness check -- fixed straight to the
            # atomic form (see claim_pending_confirmation()'s docstring):
            # claim FIRST (a single synchronous dict.pop(), so nothing else
            # can interleave on this event loop), proceed only if we
            # actually got it.
            if pending_confirmation is not None:
                pending, pending_confirmation = pending_confirmation, None
                claimed = agent.claim_pending_confirmation(pending.conf_id)
                if claimed is not None:
                    return resolve_confirmation(
                        agent, engine, pending, text, lang,
                        on_message=lambda full: event_bus.message("j", full),
                        set_pending_confirmation=_set_pending,
                        pre_claimed=claimed,
                        state=voice_state,
                    )
                # Resolved elsewhere or TTL-evicted -- fall through as a new turn.
            return run_one_response(
                agent, engine, text, lang, transport="voice-remote",
                set_pending_confirmation=_set_pending, state=voice_state,
            )

        async def _run_session() -> None:
            try:
                await drive_voice_session(engine, _handle_transcript, state=voice_state)
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


@app.get("/voice/status")
async def voice_status(request: Request):
    """Live voice diagnostics — the observable half of Faz D's telemetry.

    Faz D added the counters (queue depth, input status/overflow, output
    underruns, per-turn VAD probabilities, the device Whisper really loaded
    onto) but exposed none of them: the only reader was a four-line CLI
    print at startup, before anything had happened. This serves the exact
    same jarvis.voice.diagnostics snapshot the CLI's /voice-status renders,
    so the two surfaces cannot drift.

    200 with session_active=False when no voice session is running in this
    process — "voice isn't running" is a diagnostic answer, not an error,
    and a HUD polling this shouldn't have to treat it as a failure.
    """
    _check_auth(request)
    from jarvis.voice.diagnostics import current_snapshot
    return current_snapshot().to_dict()


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
    if _should_offload(executor, body):
        # Paket B: the request's own conversation, captured now. Reading it
        # off the shared agent when a worker later starts would attribute the
        # result to whoever spoke while the task sat queued.
        task = executor.submit(body.message, getattr(body, "conversation_id", "") or "")
        return {"async": True, "task_id": task.task_id, "status": task.status}

    event_bus.state("thinking")
    try:
        response, model_label = await agent.chat(
            body.message,
            detected_language=body.language or "en",
            transport="api",
            conversation_id=body.conversation_id,
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
    return ChatResponse(response=response, model=model_label, conversation_id=agent.session_id)


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

    # Async heuristic check (force_sync: see ChatRequest's field comment)
    executor = getattr(agent, "_task_executor", None)
    if _should_offload(executor, body):
        # Paket B: the request's own conversation, captured now. Reading it
        # off the shared agent when a worker later starts would attribute the
        # result to whoever spoke while the task sat queued.
        task = executor.submit(body.message, getattr(body, "conversation_id", "") or "")
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
        try:
            async for frame in _sse_frames(agent.chat_stream(
                body.message,
                detected_language=body.language or "en",
                transport="api-stream",
                conversation_id=body.conversation_id,
            )):
                yield frame
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
            async for frame in _sse_frames(agent.resume_and_stream(
                conf_id, body.decision, conversation_id=body.conversation_id,
            )):
                yield frame
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
    conversation_id: str = Form(""),
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
                async for frame in _sse_frames(agent.chat_stream(
                    user_query,
                    detected_language=language or "en",
                    image_bytes=content,
                    image_mime=image_mime,
                    transport="api-upload",
                    conversation_id=conversation_id,
                )):
                    yield frame
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
                async for frame in _sse_frames(agent.chat_stream(
                    full_input,
                    detected_language=language or "en",
                    extra_images=figures or None,
                    transport="api-upload",
                    conversation_id=conversation_id,
                )):
                    yield frame
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
                async for frame in _sse_frames(agent.chat_stream(
                    full_query,
                    detected_language=language or "en",
                    transport="api-upload",
                    conversation_id=conversation_id,
                )):
                    yield frame
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
    policy = getattr(agent.settings, "cloud_policy", "auto")
    return StatusResponse(
        session_id=agent.session_id,
        memory_turns=agent.memory.count(),
        vault_chunks=agent.memory.count_docs(),
        model=agent.current_model_label,
        session_cost_usd=agent.usage.session_cost,
        vertex_active=agent.settings.use_vertex,
        requested_role=trace.get("requested_role"),
        role_reason=trace.get("role_reason"),
        actual_provider=trace.get("provider"),
        actual_model=trace.get("model"),
        fallback_used=trace.get("fallback_used"),
        turn_had_any_fallback=trace.get("turn_had_any_fallback"),
        cloud_policy=policy,
        session_unpriced_tokens=agent.usage.session_unpriced_tokens,
        vertex_configured=agent.settings.use_vertex,
        cloud_calls_allowed=(
            policy == "auto"
            or (policy == "explicit" and agent.settings.pin_cloud_model)
        ),
        degraded=degraded_features(),
        last_latency_ms=trace.get("latency_ms"),
        last_ttft_ms=trace.get("ttft_ms"),
        last_call_cold_start=trace.get("cold_start"),
        last_turn_llm_calls=trace.get("calls"),
        last_turn_input_tokens=trace.get("input_tokens"),
        last_turn_output_tokens=trace.get("output_tokens"),
        last_turn_llm_total_ms=trace.get("total_llm_ms"),
    )


# ── Workflow approval (Faz 7.3, P1) ──────────────────────────────────────────
# The transport-agnostic human approval surface the CLI's /workflow command
# already had: list / show / resolve. Same shared service layer
# (jarvis.execution.workflow_approval), same audit vocabulary, same exact
# decision allowlist. Authenticated like every other endpoint; deliberately
# NOT reachable by the model (no @tool wraps approval -- see
# workflow_approval.py's docstring).


class WorkflowResolveRequest(BaseModel):
    decision: str  # "approve" | "deny" | "deny:<reason>"


@app.get("/workflow")
async def workflow_list(request: Request):
    _check_auth(request)
    from jarvis.execution import workflow_store

    # Review remediation (efficiency): workflow_store's sqlite3 calls are
    # synchronous (connect/PRAGMA/query per call, no async wrapping) --
    # offload so a list request doesn't stall the loop for any concurrently
    # streaming SSE/voice client, same as this file's existing /reset
    # to_thread precedent.
    workflows = await asyncio.to_thread(workflow_store.list_workflows, limit=50)
    return {"workflows": workflows}


@app.get("/workflow/{workflow_id}")
async def workflow_show(workflow_id: str, request: Request):
    _check_auth(request)
    from jarvis.execution import workflow_store
    from jarvis.execution.workflow_engine import render_workflow_report

    plan = await asyncio.to_thread(workflow_store.load, workflow_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"workflow not found: {workflow_id}")
    return {
        "workflow_id": plan.workflow_id,
        "status": plan.status,
        "pending_approval_step_id": plan.pending_approval_step_id,
        "report": render_workflow_report(plan),
        # Agent Runtime rev.2, Faz 8 (alpha-gate acceptance matrix, B1.2a):
        # structured parallel to _step_table's own text rendering (same
        # fields, same source -- jarvis.execution.workflow.WorkflowStep),
        # added so a driver/oracle can assert per-step status (e.g. "step s2
        # is skipped because s1 failed") without regex-parsing the
        # pipe-delimited report text. `report` is unchanged -- this is
        # purely additive.
        "steps": [
            {
                "step_id": s.step_id,
                "capability": s.capability,
                "status": s.status,
                "error": s.error,
                "execution_id": s.execution_id,
                "compensation_note": s.compensation_note,
            }
            for s in plan.steps
        ],
    }


@app.post("/workflow/{workflow_id}/resolve")
async def workflow_resolve(workflow_id: str, body: WorkflowResolveRequest, request: Request):
    _check_auth(request)
    agent = get_agent()
    from jarvis.execution.workflow_approval import resolve_workflow_approval

    tools = agent.get_workflow_tools()
    outcome = await resolve_workflow_approval(
        workflow_id, body.decision,
        tools=tools, settings=agent.settings,
        workspace=agent.workspace, transport="api",
    )
    # Review remediation: `plan` is populated for the "not currently
    # awaiting approval" outcome too (ok=False, reapproval_required=False),
    # so gating on `outcome.plan is None` let that failure fall through and
    # return a 200 with ok:false buried in the body instead of a 4xx. Any
    # ok=False + reapproval_required=False outcome is a hard failure
    # regardless of whether plan/report happen to be populated.
    if not outcome.ok and not outcome.reapproval_required:
        status = 404 if outcome.message.startswith("workflow not found") else 400
        raise HTTPException(status_code=status, detail=outcome.message)
    return {
        "ok": outcome.ok,
        "reapproval_required": outcome.reapproval_required,
        "message": outcome.message,
        "status": outcome.plan.status if outcome.plan is not None else None,
        "report": outcome.report,
    }


@app.get("/tasks/{task_id}")
async def get_task(task_id: str, request: Request):
    """Poll a background task submitted via /chat's or /chat/stream's async-
    heuristic diversion ({"async": true, "task_id": ...}) -- task_executor.py's
    own module docstring has always promised this endpoint ("result arrives
    via push + GET /tasks/{task_id}"), but it was never actually wired up
    here. Discovered (Faz 8, B1.2c) while building the W18/R24 alpha-gate
    workflow scenarios: a realistic multi-step prompt routinely exceeds
    _should_async()'s 40-word threshold, or mentions a legacy async hint
    like "rapor hazırla", and diverts -- with no HTTP path to ever retrieve
    the eventual result, a pure-HTTP client (the driver, or any client that
    isn't the CLI/FCM-push path) had no way to complete such a turn at all.
    (The example used to be "grafik", which was removed from the hint list on
    2026-07-31 for firing on ordinary interactive requests.)
    """
    _check_auth(request)
    agent = get_agent()
    executor = getattr(agent, "_task_executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="Task executor not initialized")
    task = executor.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    return task.to_dict()


class ResetRequest(BaseModel):
    # Faz 7.3 (P1): "" (every pre-existing client) resets whichever session
    # is currently active on the shared agent -- byte-identical to the old
    # no-body behavior. A non-empty value resets THAT conversation
    # specifically, regardless of what else is currently active -- see
    # JarvisAgent.reset_conversation_async()'s docstring for the
    # cross-client race this closes (client A's /reset no longer archives
    # client B's conversation just because B's request happened to switch
    # the shared agent's active pointer in between).
    conversation_id: str = ""


@app.post("/reset")
async def reset(request: Request, body: ResetRequest = ResetRequest()):
    _check_auth(request)
    agent = get_agent()
    # reset_conversation_async: the blocking _state_lock/SQLite work still
    # runs off-loop (to_thread), but summarization is scheduled AFTER
    # control returns to this loop. The old run_in_executor(agent.reset)
    # shape called create_task on a loopless worker thread -> RuntimeError
    # -> HTTP 500 on every content-bearing session.
    archived_id, new_session_id = await agent.reset_conversation_async(body.conversation_id)
    return {
        "ok": True,
        "message": "Conversation archived, new session started",
        "archived_conversation_id": archived_id,
        # The new id for THIS conversation specifically -- never
        # agent.session_id, which may belong to a different client's
        # currently-active turn when body.conversation_id targeted a
        # non-active session.
        "session_id": new_session_id,
    }


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
