"""Async task executor for long-running JARVIS jobs (Faz 19A-0).

Long tasks (deep research, geo-math simulation, finance sync, report compile)
are submitted here.  They run in a background ThreadPoolExecutor while the
caller can poll /tasks/{id} or wait for an FCM push notification.

Usage:
    executor = TaskExecutor(agent, fcm_sender, drive_tool)
    task_id = executor.submit("Write a LaTeX finance report for last month")
    # returns immediately; result arrives via push + GET /tasks/{task_id}
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from jarvis.agent import ConfirmationRequired

if TYPE_CHECKING:
    from jarvis.agent import JarvisAgent
    from jarvis.fcm_sender import FcmSender

logger = logging.getLogger(__name__)


from jarvis.graph.tool_router import _fold


def _registry_async_hints() -> set[str]:
    """Faz 4: derive trigger phrases from ToolSpec.supports_background
    instead of only a hand-maintained list -- keeps this heuristic connected
    to the same metadata jarvis/tool_registry.py already tracks, so a tool
    added/changed there doesn't silently drift out of sync with what
    actually triggers background execution."""
    from jarvis.tool_registry import TOOL_SPECS
    hints: set[str] = set()
    for spec in TOOL_SPECS.values():
        if spec.supports_background:
            hints.add(spec.name)
            hints.add(spec.name.replace("_", " "))
    return hints


# Natural-language phrases that strongly imply a background-capable tool but
# don't literally contain its name (kept alongside the registry-derived set
# below rather than replaced by it, so this stays a strict superset of the
# pre-Faz-4 behavior).
#
# Narrowed 2026-07-31. These were matched as BARE SUBSTRINGS against ordinary
# Turkish, so several entries hijacked normal interactive requests:
#   "grafik"   -- the owner's own core loop ("...ve grafikle", "grafigi cizgi
#                 yap") answered {"async": true, task_id} in ~0s over HTTP while
#                 the identical sentence stayed interactive in the CLI, which
#                 never consults this heuristic. Charting is fast; it does not
#                 belong here at all.
#   "rapor"    -- fires on "raporu goster", a read.
#   "arastir"  -- an ordinary lookup; only DEEP research is long-running.
#   "finansal" -- fires on "finansal durumum nedir", a summary read.
#   "3d"       -- two characters, matched anywhere inside a word.
# The long-running intents they were meant to catch are kept below in forms a
# user does not type by accident.
_LEGACY_ASYNC_HINTS = {
    "deep_research", "deep research", "derinlemesine araştır", "derin araştırma",
    "sentez", "rapor hazırla", "rapor oluştur", "report", "simülasyon",
    "simulasyon", "3 boyutlu", "wave_simulate", "wave simulate", "finance sync",
    "index_doc", "plot_volume", "plot_3d", "plot_surface", "latex", "compile",
    "seismic", "sismik", "subagent", "sub-agent", "alt ajan",
}

# Keywords that trigger automatic async mode when found in a query
ASYNC_KEYWORDS = _registry_async_hints() | _LEGACY_ASYNC_HINTS

# Stem-anchored, diacritic-folded matching -- the same two conventions
# jarvis/graph/tool_router.py already proved out, reusing its _fold() so the two
# heuristics cannot drift:
#   * anchored at the START of a word only. Turkish agglutinates, so a trailing
#     \b would break every suffixed form ("rapor hazırla" must still match
#     "rapor hazırlar mısın"). Unanchored matching is what let the old bare "3d"
#     fire from inside an unrelated word.
#   * folded to ASCII, because casual typing and ASR routinely drop ç/ğ/ı/ö/ş/ü.
#     Measured here: "derinlemesine arastir" missed "derinlemesine araştır"
#     entirely before folding, so the one hint that most needs to reach the
#     background executor was the one that didn't.
_ASYNC_PATTERNS = [
    re.compile(r"\b" + re.escape(_fold(kw)), re.IGNORECASE) for kw in ASYNC_KEYWORDS
]


def _should_async(query: str, force: bool = False) -> bool:
    if force:
        return True
    lower = _fold(query)
    if len(lower.split()) > 40:
        return True
    return any(p.search(lower) for p in _ASYNC_PATTERNS)


@dataclass
class AsyncTask:
    task_id: str
    user_query: str
    status: str = "queued"          # queued | running | done | failed | cancelled
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result_text: str | None = None
    result_artifacts: list[dict] = field(default_factory=list)
    error: str | None = None
    progress_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "user_query": self.user_query,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "result_text": self.result_text,
            "result_artifacts": self.result_artifacts,
            "error": self.error,
            "progress_notes": self.progress_notes,
        }


class TaskExecutor:
    def __init__(
        self,
        agent: "JarvisAgent",
        fcm_sender: "FcmSender | None" = None,
    ) -> None:
        self._agent = agent
        self._fcm = fcm_sender
        self._tasks: dict[str, AsyncTask] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="jarvis-task")

    # ── Public API ─────────────────────────────────────────────────────────────

    def should_async(self, query: str, force: bool = False) -> bool:
        return _should_async(query, force)

    def submit(self, query: str) -> AsyncTask:
        task_id = uuid.uuid4().hex[:12]
        task = AsyncTask(task_id=task_id, user_query=query)
        with self._lock:
            self._tasks[task_id] = task
        self._pool.submit(self._run, task)
        logger.info("Task %s queued: %.60s…", task_id, query)
        return task

    def get(self, task_id: str) -> AsyncTask | None:
        return self._tasks.get(task_id)

    def list_tasks(self, status: str | None = None) -> list[AsyncTask]:
        with self._lock:
            tasks = list(self._tasks.values())
        if status:
            statuses = {s.strip() for s in status.split(",")}
            tasks = [t for t in tasks if t.status in statuses]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks

    def cancel(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task and task.status in ("queued",):
            task.status = "cancelled"
            task.completed_at = datetime.utcnow()
            return True
        return False

    # ── Internal runner ────────────────────────────────────────────────────────

    def _run(self, task: AsyncTask) -> None:
        if task.status == "cancelled":
            return

        task.status = "running"
        task.started_at = datetime.utcnow()
        self._emit_ws(task, note="Starting…")

        # Prevent PC from sleeping during execution (Windows only)
        _prevent_sleep()

        try:
            # GPT-5.6 review remediation, Faz 5: was self._agent.chat(), which
            # reads/writes the live self._history mid-flight (interleaving
            # this background exchange into the transcript the user is
            # looking at) and holds _state_lock for the whole ainvoke() call
            # (blocking foreground chat for as long as this task runs). See
            # JarvisAgent.background_turn()'s docstring for the isolation.
            response = asyncio.run(self._agent.background_turn(task.user_query, transport="task-async"))
            task.result_text = response
            task.result_artifacts = self._collect_artifacts(response)
            task.status = "done"
            self._emit_ws(task, note="Done.")
        except ConfirmationRequired as exc:
            # Faz 4: a background task has no interactive channel to answer a
            # confirmation prompt -- fail clearly instead of leaving a cryptic
            # "confirmation_required:<uuid>" error string, naming what needs
            # a real (interactive) turn to approve.
            tools = ", ".join(
                t.get("name", "?") for t in (exc.payload or {}).get("tools", [])
            ) or "a gated action"
            logger.info("Task %s needs confirmation for: %s", task.task_id, tools)
            task.status = "failed"
            task.error = (
                f"This needs your approval for {tools}, which isn't possible in the "
                "background. Ask JARVIS directly (chat/voice) so you can confirm it."
            )
            self._emit_ws(task, note="Needs confirmation — retry interactively.")
        except Exception as exc:
            logger.exception("Task %s failed: %s", task.task_id, exc)
            task.status = "failed"
            task.error = str(exc)
            self._emit_ws(task, note=f"Failed: {exc}")
        finally:
            task.completed_at = datetime.utcnow()
            _allow_sleep()
            self._dispatch_push(task)

    def _collect_artifacts(self, response_text: str) -> list[dict]:
        """Find file paths mentioned in the response and annotate them."""
        import re
        from pathlib import Path as P

        artifacts = []
        # Look for file paths in the response (absolute or relative with extensions)
        pattern = r'(?:saved to|output:|file:|path:)\s*([^\s\n"\']+\.(?:pdf|html|png|svg|tex|csv))'
        for m in re.finditer(pattern, response_text, re.IGNORECASE):
            raw = m.group(1)
            p = P(raw)
            if p.exists():
                ext = p.suffix.lower().lstrip(".")
                artifacts.append({
                    "type": ext,
                    "path": str(p),
                    "name": p.name,
                    "size": p.stat().st_size,
                    "drive_link": None,
                })
        return artifacts

    def _emit_ws(self, task: AsyncTask, note: str = "") -> None:
        try:
            from jarvis.ws import event_bus
            if note:
                task.progress_notes.append(note)
            elapsed = 0
            if task.started_at:
                elapsed = int((datetime.utcnow() - task.started_at).total_seconds())
            event_bus.emit({
                "type": "task_status",
                "task_id": task.task_id,
                "status": task.status,
                "progress_note": note,
                "elapsed_sec": elapsed,
            })
        except Exception:
            pass

    def _dispatch_push(self, task: AsyncTask) -> None:
        if self._fcm is None:
            return
        if task.status == "done":
            title = "JARVIS ✓ Görev tamamlandı"
            body = (task.result_text or "")[:120]
        else:
            title = "JARVIS ✗ Hata"
            body = (task.error or "Bilinmeyen hata")[:120]
        try:
            self._fcm.send_to_all(
                title=title,
                body=body,
                data={"category": "task_complete", "task_id": task.task_id},
            )
        except Exception as exc:
            logger.debug("Push dispatch failed: %s", exc)


# ── Sleep prevention (Windows) ─────────────────────────────────────────────────

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_active_prevention = False


def _prevent_sleep() -> None:
    global _active_prevention
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(
            _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED
        )
        _active_prevention = True
    except Exception:
        pass


def _allow_sleep() -> None:
    global _active_prevention
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
        _active_prevention = False
    except Exception:
        pass
