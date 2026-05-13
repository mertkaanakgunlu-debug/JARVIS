"""WebSocket event broadcaster for the JARVIS HUD (Faz 11).

All connected clients receive JSON events pushed from:
  - Agent state changes (idle / listening / speaking / thinking / working)
  - Tool call events → activity feed
  - New messages → conversation transcript
  - System metrics (every 5 s via background task)
  - Calendar / vault snapshots (pushed after agent turn)

Event schema (server → client):
  {"type": "state",     "value": "listening"}
  {"type": "message",   "who": "u"|"j", "text": "..."}
  {"type": "tool_call", "kind": "tool"|"cloud"|"local"|"note", "body": "..."}
  {"type": "task",      "name": "...", "steps": [...]}
  {"type": "metrics",   "cpu": N, "gpu": N, "ram": N, "vram": N, "latency": N}
  {"type": "calendar",  "events": [...]}
  {"type": "vault",     "entries": [...], "count": N}
  {"type": "progress",  "jobsDone": N, "jobsTotal": N, "runtime": "...",
                         "tokensIn": N, "tokensOut": N}
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional, Set

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class JarvisEventBus:
    """Singleton broadcaster: push JSON events to all HUD clients."""

    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── Connection management ──────────────────────────────────────────────────

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        self._loop = asyncio.get_event_loop()
        logger.debug("HUD client connected (%d total)", len(self._clients))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        logger.debug("HUD client disconnected (%d remaining)", len(self._clients))

    # ── Broadcasting ───────────────────────────────────────────────────────────

    async def broadcast(self, event: dict) -> None:
        """Send event dict to all connected WebSocket clients."""
        if not self._clients:
            return
        msg  = json.dumps(event, ensure_ascii=False)
        dead: Set[WebSocket] = set()
        for ws in list(self._clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    def emit(self, event: dict) -> None:
        """Thread-safe fire-and-forget broadcast (callable from sync code)."""
        loop = self._loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcast(event), loop)
        # If no loop yet (no client connected), silently drop — that's fine.

    # ── Convenience helpers ────────────────────────────────────────────────────

    def state(self, value: str) -> None:
        """JARVIS state change: idle / listening / speaking / thinking / working."""
        self.emit({"type": "state", "value": value})

    def message(self, who: str, text: str) -> None:
        """New conversation message. who='u' (user) or 'j' (JARVIS)."""
        self.emit({"type": "message", "who": who, "text": text})

    def tool_call(self, body: str, kind: str = "tool") -> None:
        """Tool invocation event for the activity feed.
        kind: 'tool' | 'cloud' | 'local' | 'note'
        """
        self.emit({"type": "tool_call", "kind": kind, "body": body})

    def task_update(self, name: str, steps: list) -> None:
        self.emit({"type": "task", "name": name, "steps": steps})

    def metrics(self, cpu: float, gpu: float, ram: float, vram: float, latency: int) -> None:
        self.emit({"type": "metrics", "cpu": cpu, "gpu": gpu,
                   "ram": ram, "vram": vram, "latency": latency})

    def calendar(self, events: list) -> None:
        self.emit({"type": "calendar", "events": events})

    def vault(self, entries: list, count: int) -> None:
        self.emit({"type": "vault", "entries": entries, "count": count})

    def progress(self, jobs_done: int, jobs_total: int,
                 runtime: str, tokens_in: int, tokens_out: int) -> None:
        self.emit({"type": "progress", "jobsDone": jobs_done, "jobsTotal": jobs_total,
                   "runtime": runtime, "tokensIn": tokens_in, "tokensOut": tokens_out})


# ── Singleton ──────────────────────────────────────────────────────────────────
event_bus = JarvisEventBus()


# ── System metrics background task ────────────────────────────────────────────
_metrics_task: Optional[asyncio.Task] = None
_start_time = time.time()


async def _push_metrics_loop() -> None:
    """Push CPU/RAM/etc. to HUD every 5 s using psutil (if available)."""
    try:
        import psutil  # type: ignore
        has_psutil = True
    except ImportError:
        has_psutil = False

    while True:
        await asyncio.sleep(5)
        if not event_bus._clients:
            continue
        try:
            if has_psutil:
                cpu  = psutil.cpu_percent(interval=None)
                ram  = psutil.virtual_memory().used / 1024 ** 3
                # GPU via nvidia-ml-py3 (optional)
                gpu  = 0.0
                vram = 0.0
                try:
                    import pynvml  # type: ignore
                    pynvml.nvmlInit()
                    h    = pynvml.nvmlDeviceGetHandleByIndex(0)
                    util = pynvml.nvmlDeviceGetUtilizationRates(h)
                    mem  = pynvml.nvmlDeviceGetMemoryInfo(h)
                    gpu  = float(util.gpu)
                    vram = mem.used / 1024 ** 3
                except Exception:
                    pass
            else:
                import random
                cpu  = random.uniform(20, 50)
                ram  = random.uniform(12, 18)
                gpu  = random.uniform(30, 70)
                vram = random.uniform(4, 7)

            event_bus.metrics(
                cpu=round(cpu, 1), gpu=round(gpu, 1),
                ram=round(ram, 2), vram=round(vram, 2),
                latency=0,
            )
        except Exception as exc:
            logger.debug("Metrics push error: %s", exc)


def start_metrics_task() -> None:
    """Start the background metrics push loop (call once from api.py lifespan)."""
    global _metrics_task
    if _metrics_task is None or _metrics_task.done():
        _metrics_task = asyncio.create_task(_push_metrics_loop())
