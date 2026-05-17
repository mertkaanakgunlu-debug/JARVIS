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

    def todos(self, items: list) -> None:
        """Open todo items for HUD ProjectTracker panel."""
        self.emit({"type": "todos", "items": items})

    def show_hud(self) -> None:
        """Tell Electron to bring the HUD to front (visual output ready)."""
        self.emit({"type": "show_hud"})

    def session(self, session_id: str, topic: str | None) -> None:
        """Session change event — lets Electron HUD display current session name."""
        self.emit({"type": "session", "id": session_id, "topic": topic})

    def task_status(
        self,
        task_id: str,
        status: str,
        progress_note: str = "",
        elapsed_sec: int = 0,
    ) -> None:
        """Async task progress event — consumed by mobile app task cards."""
        self.emit({
            "type": "task_status",
            "task_id": task_id,
            "status": status,
            "progress_note": progress_note,
            "elapsed_sec": elapsed_sec,
        })


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


# ── Live data background task (calendar / todos / vault) ──────────────────────
_live_data_task: Optional[asyncio.Task] = None
_live_agent = None
_live_settings = None


def _fetch_calendar_events(settings) -> list:
    """Sync helper — fetches today's Google Calendar events (run in executor)."""
    if settings is None:
        return []
    try:
        from jarvis.tools.calendar import _get_service  # type: ignore
        from datetime import datetime, timedelta, timezone
        svc = _get_service(settings)
        now = datetime.now(timezone.utc)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        res = (
            svc.events()
            .list(
                calendarId="primary",
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=20,
            )
            .execute()
        )
        events = []
        for ev in res.get("items", []):
            start_raw = ev.get("start", {})
            dt_str = start_raw.get("dateTime") or start_raw.get("date") or ""
            time_str = ""
            if "T" in dt_str:
                try:
                    from datetime import datetime as _dt
                    parsed = _dt.fromisoformat(dt_str.replace("Z", "+00:00"))
                    time_str = parsed.astimezone().strftime("%H:%M")
                except Exception:
                    pass
            events.append({
                "time": time_str,
                "title": ev.get("summary", ""),
                "where": ev.get("location", ""),
                "kind": "live",
            })
        return events
    except Exception as exc:
        logger.debug("Calendar fetch error: %s", exc)
        return []


def _fetch_vault_entries(agent) -> list:
    """Return the 5 most recently indexed vault sources as HUD vault entries."""
    try:
        sources = agent.memory.list_indexed()[-5:]
        entries = []
        for src in reversed(sources):
            name = src.replace("\\", "/").split("/")[-1]
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else "doc"
            tag = {"pdf": "pdf", "md": "note", "txt": "note"}.get(ext, "doc")
            entries.append({"title": name, "tag": tag, "ts": "indexed"})
        return entries
    except Exception as exc:
        logger.debug("Vault entries fetch error: %s", exc)
        return []


def _build_usage_stats(agent) -> dict | None:
    """Build a progress event dict from the agent's session usage tracker."""
    try:
        s = agent.usage._session
        total_turns = s.get("flash_turns", 0) + s.get("pro_turns", 0)
        cost        = s.get("cost_usd", 0.0)
        cost_saved  = cost * 7.0   # rough: local routing saves ~7× vs direct API
        return {
            "type":        "progress",
            "jobsDone":    total_turns,
            "jobsTotal":   max(total_turns + 1, 1),
            "tokensIn":    s.get("tokens_in", 0),
            "tokensOut":   s.get("tokens_out", 0),
            "cloudSpend":  f"{cost:.4f}",
            "costSaved":   f"{cost_saved:.4f}",
        }
    except Exception as exc:
        logger.debug("Usage stats error: %s", exc)
        return None


def _build_todo_items(agent) -> list:
    """Fetch top 8 open todos formatted for HUD ProjectTracker."""
    try:
        rows = agent.todo_store.top_open(n=8)
        return [
            {
                "id": t.get("id", ""),
                "title": t.get("title", ""),
                "priority": t.get("priority", "low"),
                "priority_score": t.get("priority_score", 0.0),
                "category": t.get("category", "other"),
                "due": t.get("due_date") or "",
            }
            for t in rows
        ]
    except Exception as exc:
        logger.debug("Todo fetch error: %s", exc)
        return []


async def _push_live_data_loop() -> None:
    """Push todos (30 s), calendar (60 s), vault (120 s) to all HUD clients."""
    await asyncio.sleep(8)   # let agent finish init before first tick
    tick = 0
    while True:
        await asyncio.sleep(30)
        if not event_bus._clients:
            continue
        agent   = _live_agent
        settings = _live_settings
        if agent is None:
            continue
        tick += 1

        # Todos — every 30 s
        items = _build_todo_items(agent)
        if items is not None:
            event_bus.todos(items)

        # Usage / progress stats — every 30 s
        stats = _build_usage_stats(agent)
        if stats:
            event_bus.emit(stats)

        # Calendar — every ~60 s
        if tick % 2 == 0:
            loop = asyncio.get_event_loop()
            cal_events = await loop.run_in_executor(None, _fetch_calendar_events, settings)
            event_bus.calendar(cal_events)

        # Vault — every ~120 s
        if tick % 4 == 0:
            entries = _fetch_vault_entries(agent)
            event_bus.vault(entries, agent.memory.count_docs())


async def live_data_snapshot(agent, settings) -> None:
    """Send a one-shot snapshot to all connected clients (call on new connection)."""
    try:
        items = _build_todo_items(agent)
        await event_bus.broadcast({"type": "todos", "items": items})
    except Exception:
        pass
    try:
        entries = _fetch_vault_entries(agent)
        await event_bus.broadcast({
            "type": "vault",
            "entries": entries,
            "count": agent.memory.count_docs(),
        })
    except Exception:
        pass
    # Calendar — fetch immediately on connect (don't wait 60 s for the loop)
    try:
        loop = asyncio.get_event_loop()
        cal_events = await loop.run_in_executor(None, _fetch_calendar_events, settings)
        if cal_events:
            await event_bus.broadcast({"type": "calendar", "events": cal_events})
    except Exception:
        pass
    # Usage / progress stats — send on connect
    try:
        stats = _build_usage_stats(agent)
        if stats:
            await event_bus.broadcast(stats)
    except Exception:
        pass


def start_live_data_task(agent, settings) -> None:
    """Start the live data push loop (call once from api.py lifespan)."""
    global _live_data_task, _live_agent, _live_settings
    _live_agent = agent
    _live_settings = settings
    if _live_data_task is None or _live_data_task.done():
        _live_data_task = asyncio.create_task(_push_live_data_loop())
