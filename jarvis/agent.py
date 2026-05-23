"""JARVIS orchestrator — LangGraph-based (Faz 1).

Public API (preserved from pydantic-ai version):
  JarvisAgent(settings)
  .chat(user_input, detected_language) -> tuple[str, str]   # (response, model_label)
  .chat_stream(user_input, detected_language) -> AsyncGenerator[str, None]
  .switch_model(model_id) -> str
  .switch_session(session_id) -> int
  .reset()
  ._using_fallback  (bool)
  ._cloud_model     (str — display label)

cli.py and voice.py import JarvisAgent and AVAILABLE_MODELS from here.
"""

from __future__ import annotations

import asyncio
import base64
import re
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage, SystemMessage

from jarvis.config import Settings, LANG_NAMES
from jarvis.entity_extractor import extract_entities
from jarvis.memory import Memory
from jarvis.session_store import SessionStore
from jarvis.scheduler import SchedulerStore  # Faz 13-C
from jarvis.todo_store import TodoStore      # Faz 13-D
from jarvis.graph.graph import build_graph, make_checkpointer
from jarvis.graph.streaming import graph_stream_to_text
from jarvis.usage import UsageTracker
from jarvis.ws import event_bus


# ── HUD activity feed callback ────────────────────────────────────────────────

class _HudEventCallback(BaseCallbackHandler):
    """Non-blocking LangChain callback → pushes tool/LLM events to the HUD feed."""

    def on_tool_start(self, serialized: dict, input_str: Any, **kwargs: Any) -> None:
        name = serialized.get("name", "tool")
        if isinstance(input_str, dict):
            args = ", ".join(f"{k}={str(v)[:60]}" for k, v in list(input_str.items())[:2])
        else:
            args = str(input_str)[:120]
        event_bus.tool_call(f"{name} → {args}", kind="tool")

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        s = str(output)[:160]
        if any(kw in s.lower() for kw in ("chroma", "vector", "recall", "memory", "retrieved")):
            event_bus.tool_call(s, kind="note")

    def on_llm_start(self, serialized: dict, prompts: list, **kwargs: Any) -> None:
        model = (
            serialized.get("kwargs", {}).get("model_name")
            or serialized.get("kwargs", {}).get("model")
            or serialized.get("name", "llm")
        )
        event_bus.tool_call(str(model), kind="cloud")

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        try:
            gen = response.generations[0][0]
            msg = getattr(gen, "message", None)
            usage = getattr(msg, "usage_metadata", None) if msg else None
            if usage:
                ti   = usage.get("input_tokens", 0)
                to_  = usage.get("output_tokens", 0)
                mname = getattr(msg, "response_metadata", {}).get("model_name", "Gemini")
                if to_ > 0:
                    spd = f" · {to_:,} tok/s"
                    event_bus.tool_call(
                        f"{mname} · {ti:,} in / {to_:,} out{spd}", kind="local"
                    )
        except Exception:
            pass


# ── System prompt helpers ──────────────────────────────────────────────────────

_DATA_REPORT_KEYWORDS = frozenset([
    "pdf", "excel", "xlsx", "xls", "csv", "report", "rapor", "plot", "grafik",
    "chart", "data", "veri", "analiz", "analysis", "hw", "odev", "ödev",
])

_FLASH_TRIVIAL_SIGNALS = frozenset([
    # Pure status lookups — no reasoning required
    "hava", "weather", "saat kaç", "what time", "tarih ne", "what date",
    "merhaba", "hello", "hi", "hey", "selam", "naber", "nasılsın",
    "tamam", "ok", "teşekkür", "thanks", "thank you", "sağ ol",
])

_ANALYSIS_SIGNALS = frozenset([
    # Any of these → definitely Pro
    "analiz", "analyze", "araştır", "research", "karşılaştır", "compare",
    "açıkla", "explain", "özetle", "summarize", "incele", "investigate",
    "hesapla", "calculate", "derive", "prove", "derin", "deep",
    "kapsamlı", "comprehensive", "ayrıntılı", "detailed", "rapor", "report",
    "pdf", "xlsx", "xls", "csv", "docx",
    "neden", "why", "nasıl", "how", "ne zaman", "when",
    "bugün ne var", "bugün", "today", "takvim", "calendar",
    "görevlerim", "todo", "yapılacak", "mail", "e-posta", "email",
    "spotify", "çal", "play", "drive", "dosya", "file",
])


def _is_trivially_simple(query: str, needs_planning: bool) -> bool:
    """Return True only for queries so simple that Flash is fully adequate.

    Pro is the default; this is the narrow exception path.
    Criteria: very short query, no analysis/tool signals, pure social/status.
    """
    if needs_planning:
        return False
    q = query.lower().strip()
    words = q.split()
    if len(words) > 8:
        return False
    if any(sig in q for sig in _ANALYSIS_SIGNALS):
        return False
    return any(sig in q for sig in _FLASH_TRIVIAL_SIGNALS)


def _build_env_block(workspace: Path) -> str:
    import os
    home = Path(os.path.expanduser("~"))
    desktop_candidates = [home / "OneDrive" / "Desktop", home / "Desktop"]
    desktop = next((p for p in desktop_candidates if p.exists()), desktop_candidates[0])
    return (
        f"\n\n## User environment (Windows)\n"
        f"- Home: `{home}`\n"
        f"- Desktop: `{desktop}`\n"
        f"- Workspace (current working dir): `{workspace}`\n\n"
        "When the user mentions **masaüstü / Desktop / my desktop**, use the Desktop "
        "path above (absolute) with `file_list`, `file_read`, `pdf_read`, or `shell_run`. "
        "Never assume files are inside the Workspace unless the user explicitly said so."
    )


def _load_system_prompt(
    settings: Settings,
    memory_context: str = "",
    detected_language: str = "en",
    env_block: str = "",
    user_query: str = "",
    entities_block: str = "",
    past_sessions_block: str = "",
    open_todos_block: str = "",
) -> str:
    prompt_path = Path(__file__).parent / "prompts" / "system.md"
    raw = prompt_path.read_text(encoding="utf-8")
    raw = raw.replace("{user_name}", settings.user_name)
    raw = raw.replace("{memory_context}", memory_context or "(no prior context retrieved)")
    raw = raw.replace("{entities_block}", entities_block or "(none yet)")
    raw = raw.replace("{past_sessions_block}", past_sessions_block or "(no relevant past sessions)")
    raw = raw.replace("{open_todos_block}", open_todos_block or "(no open tasks)")

    if any(kw in user_query.lower() for kw in _DATA_REPORT_KEYWORDS):
        workflow_path = Path(__file__).parent / "prompts" / "workflows" / "data_report.md"
        raw += "\n\n" + workflow_path.read_text(encoding="utf-8")

    raw += env_block

    if detected_language != "en":
        lang_name = LANG_NAMES.get(detected_language[:2], detected_language)
        raw += (
            f"\n\nIMPORTANT: The user is speaking {lang_name}. "
            f"You MUST respond entirely in {lang_name}."
        )
    return raw


def _trim_history(messages: list[Any], max_messages: int = 20) -> list[Any]:
    from langchain_core.messages import SystemMessage as SM
    system_msgs = [m for m in messages if isinstance(m, SM)]
    non_system = [m for m in messages if not isinstance(m, SM)]
    trimmed = non_system[-max_messages:]
    return system_msgs + trimmed


def _build_human_message(
    text: str,
    image_bytes: bytes | None = None,
    image_mime: str = "image/png",
    extra_images: list[bytes] | None = None,
) -> HumanMessage:
    """Build a HumanMessage — plain text or multimodal, depending on attachments.

    image_bytes   — single image (PNG/JPG upload); placed before the text.
    extra_images  — PDF figures (PNG bytes); placed after the text so the model
                    reads the markdown context first, then sees the visuals.
    """
    # No attachments → plain text message
    if not image_bytes and not extra_images:
        return HumanMessage(content=text)

    content: list[dict] = []

    # Single image upload: image first, then the user's question
    if image_bytes:
        b64 = base64.b64encode(image_bytes).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{b64}"}})

    # Text block (markdown from PDF, or plain user query)
    content.append({"type": "text", "text": text})

    # PDF figures: appended after text so markdown context comes first
    for img in (extra_images or []):
        b64 = base64.b64encode(img).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})

    return HumanMessage(content=content)


def _strip_images_for_storage(messages: list[Any]) -> list[Any]:
    """Replace multimodal HumanMessage content with text-only version before SQLite storage.

    Prevents base64 image blobs from bloating the session history and being
    replayed in future turns where they're irrelevant.
    """
    result = []
    for m in messages:
        if isinstance(m, HumanMessage) and isinstance(m.content, list):
            text_parts = [
                p.get("text", "")
                for p in m.content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            text_only = " ".join(t for t in text_parts if t).strip()
            result.append(HumanMessage(content=f"[image attached] {text_only}" if text_only else "[image attached]"))
        else:
            result.append(m)
    return result


# ── Model catalogue (Revizyon 2 — Gemini-only, 5 entries) ─────────────────────

AVAILABLE_MODELS: list[tuple[str, str, str, str]] = [
    ("vertex/gemini-2.5-pro",          "Gemini 2.5 Pro (Vertex)",      "vertex",   "Vertex credits · orchestrator + vision"),
    ("vertex/gemini-2.5-flash",        "Gemini 2.5 Flash (Vertex)",    "vertex",   "Vertex credits · sub-agent executor"),
    ("aistudio/gemini-2.5-flash",      "Gemini 2.5 Flash (AI Studio)", "aistudio", "50 RPD free · mid fallback"),
    ("aistudio/gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite",        "aistudio", "1000 RPD free · emergency fallback"),
    ("groq/llama-4-scout",             "Llama 4 Scout (Groq)",         "groq",     "optional fast intent classifier"),
]


def _label_for(model_id: str) -> str:
    for mid, label, _, _ in AVAILABLE_MODELS:
        if mid == model_id:
            return label
    return model_id


# ── JarvisAgent ────────────────────────────────────────────────────────────────

class JarvisAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.memory = Memory(settings)
        self.workspace = Path(".").resolve()
        self._env_block = _build_env_block(self.workspace)
        self._using_fallback = False
        self._active_model_id: str | None = None
        self._turn: int = 0

        db_path = Path("data") / "jarvis_checkpoints.db"
        self._checkpointer = make_checkpointer(db_path)
        self._graph = build_graph(settings, self.workspace, self.memory, self._checkpointer)

        self.usage = UsageTracker(Path("data") / "usage.json")

        # Faz 12-B: persistent session store — auto-resume last session
        self.session_store = SessionStore(Path("data") / "sessions.db")
        last = self.session_store.latest_session()
        if last:
            self.session_id = last
            self._history: list[Any] = self.session_store.load_history(last, limit=20)
        else:
            self.session_id = self.session_store.new_session()
            self._history: list[Any] = []

        # Faz 13-C: scheduler store (same DB file, separate table)
        self.scheduler = SchedulerStore(Path("data") / "sessions.db")

        # Faz 13-D: to-do store (same DB file, separate table)
        self.todo_store = TodoStore(Path("data") / "sessions.db")

        # Strong refs to background tasks — prevents GC from cancelling them mid-flight
        self._bg_tasks: set[asyncio.Task] = set()

        # Faz 13-A: backfill summaries for archived sessions in background
        self._schedule_summary_backfill()

    @property
    def _cloud_model(self) -> str:
        if self._active_model_id:
            return _label_for(self._active_model_id)
        if self.settings.use_vertex:
            return _label_for(f"vertex/{self.settings.vertex_model_fast}")
        suffix = " (fallback)" if self._using_fallback else ""
        return self.settings.cloud_model_label + suffix

    @property
    def current_model_label(self) -> str:
        return self._cloud_model

    def reset(self) -> None:
        """Archive current session and start a fresh one. Triggers summarization if content exists."""
        old_session_id = self.session_id
        had_content = self._turn > 0 or len(self._history) > 0
        self.session_store.archive_session(old_session_id)
        if had_content:
            self._schedule_summarize_one(old_session_id)
        self.session_id = self.session_store.new_session()
        self._history = []
        self._turn = 0
        event_bus.session(self.session_id, None)

    def switch_session(self, session_id: str) -> int:
        """Load a past session's history. Returns number of messages loaded."""
        self._history = self.session_store.load_history(session_id, limit=20)
        self.session_id = session_id
        self._turn = 0  # reset to avoid thread_id namespace collisions
        topic = next(
            (s["topic_hint"] for s in self.session_store.list_sessions(50) if s["id"] == session_id),
            None,
        )
        event_bus.session(session_id, topic)
        return len(self._history)

    def switch_model(self, model_id: str) -> str:
        import copy
        new_settings = copy.copy(self.settings)

        if model_id.startswith("vertex/"):
            real_id = model_id.removeprefix("vertex/")
            new_settings.cloud_tier = "vertex"
            new_settings.vertex_model_fast = real_id
        elif model_id.startswith("aistudio/"):
            real_id = model_id.removeprefix("aistudio/")
            new_settings.cloud_tier = "aistudio"
            new_settings.cloud_model = real_id
        elif model_id.startswith("groq/"):
            raise ValueError("Groq is an optional intent classifier — not a primary orchestrator model.")
        else:
            new_settings.cloud_tier = "aistudio"
            new_settings.cloud_model = model_id

        self._graph = build_graph(new_settings, self.workspace, self.memory, self._checkpointer)
        self._active_model_id = model_id
        self._using_fallback = False
        return _label_for(model_id)

    # ── Entity extraction (fire-and-forget) ────────────────────────────────────

    def _schedule_entity_extraction(self, user_text: str, response: str) -> None:
        session_id = self.session_id

        async def _do() -> None:
            try:
                entities = await extract_entities(user_text, response, self.settings)
                for e in entities:
                    self.session_store.upsert_entity(e.name, e.type, e.description, session_id)
            except Exception:
                pass

        task = asyncio.create_task(_do())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    # ── Session summarization (Faz 13-A, fire-and-forget) ─────────────────────

    def _schedule_summary_backfill(self) -> None:
        """On startup: summarize+embed all archived sessions that have no summary yet."""
        pending = self.session_store.sessions_needing_summary()
        if not pending:
            return

        async def _backfill() -> None:
            from jarvis.session_summarizer import summarize_session
            from datetime import datetime as _dt
            for row in pending:
                sid = row["id"]
                try:
                    msgs = self.session_store.load_full_history(sid)
                    if not msgs:
                        continue
                    summary = await summarize_session(msgs, self.settings)
                    if not summary:
                        continue
                    now = _dt.now().isoformat()
                    self.session_store.set_summary(sid, summary, embedded_at=now)
                    self.memory.store_summary(
                        sid, summary,
                        row.get("topic_hint"),
                        row.get("last_active") or now,
                    )
                except Exception:
                    continue

        try:
            task = asyncio.create_task(_backfill())
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
        except RuntimeError:
            # No running event loop yet (e.g. unit-test context) — skip silently.
            pass

    def _schedule_summarize_one(self, session_id: str) -> None:
        """Fire-and-forget: summarize a single just-archived session."""
        async def _do() -> None:
            from jarvis.session_summarizer import summarize_session
            from datetime import datetime as _dt
            try:
                msgs = self.session_store.load_full_history(session_id)
                if not msgs:
                    return
                summary = await summarize_session(msgs, self.settings)
                if not summary:
                    return
                now = _dt.now().isoformat()
                self.session_store.set_summary(session_id, summary, embedded_at=now)
                topic = next(
                    (s["topic_hint"] for s in self.session_store.list_sessions(50)
                     if s["id"] == session_id),
                    None,
                )
                self.memory.store_summary(session_id, summary, topic, now)
            except Exception:
                pass

        task = asyncio.create_task(_do())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def _build_todos_block(self, n: int = 5) -> str:
        """Build a compact open-todos block for system prompt injection (Faz 13-D)."""
        try:
            todos = self.todo_store.top_open(n=n)
        except Exception:
            return "(no open tasks)"
        if not todos:
            return "(no open tasks)"
        from jarvis.todo_store import PRIORITY_LABELS
        lines = [f"Open tasks ({len(todos)} shown, priority order):"]
        for t in todos:
            pri = PRIORITY_LABELS.get(t.get("priority") or "", "⬜ pending analysis")
            due = f" [due {t['due_date']}]" if t.get("due_date") else ""
            lines.append(f"- [{t['id']}] {t['title']}{due} — {pri}")
        return "\n".join(lines)

    def _build_past_sessions_block(self, hits: list[dict]) -> str:
        if not hits:
            return "(no relevant past sessions)"
        lines = []
        for h in hits:
            topic = h.get("topic_hint") or "(no topic)"
            when = h.get("last_active", "")[:10]   # YYYY-MM-DD
            lines.append(
                f"- **{when} · {topic}** (id={h['session_id']}):\n  {h['summary']}"
            )
        return "\n".join(lines)

    def _build_entities_block(self, n: int = 5) -> str:
        entities = self.session_store.top_entities(n=n)
        if not entities:
            return "(none yet)"
        return "\n".join(
            f"- **{e['name']}** ({e['type']}): {e['description'] or 'mentioned in conversation'}"
            for e in entities
        )

    # ── Chat ───────────────────────────────────────────────────────────────────

    async def chat(
        self,
        user_input: str,
        detected_language: str = "en",
        image_bytes: bytes | None = None,
        image_mime: str = "image/png",
        extra_images: list[bytes] | None = None,
    ) -> tuple[str, str]:
        """Run one turn. Returns (response_text, model_label).

        image_bytes   — single uploaded image (PNG/JPG); sent as multimodal block.
        extra_images  — list of PNG bytes extracted from a PDF by marker-pdf;
                        appended after the text block.
        """
        needs_planning = user_input.strip().lower().startswith("/think")
        clean_input = re.sub(r"^/think\s*", "", user_input).strip()
        use_pro_agent = not _is_trivially_simple(clean_input, needs_planning)

        memory_ctx = self.memory.recall(clean_input, n=3)
        past_sessions = self.memory.recall_summaries(clean_input, n=3)
        past_sessions_block = self._build_past_sessions_block(past_sessions)
        entities_block = self._build_entities_block()
        open_todos_block = self._build_todos_block()   # Faz 13-D
        system_prompt = _load_system_prompt(
            self.settings, memory_ctx, detected_language,
            self._env_block, clean_input, entities_block,
            past_sessions_block, open_todos_block,
        )

        human_msg = _build_human_message(clean_input, image_bytes, image_mime, extra_images)

        initial_messages = (
            [SystemMessage(content=system_prompt)]
            + self._history
            + [human_msg]
        )

        self._turn += 1
        state = {
            "messages": initial_messages,
            "user_query": clean_input,
            "language": detected_language,
            "memory_context": memory_ctx,
            "needs_planning": needs_planning,
            "use_pro_agent": use_pro_agent,
            "plan": "",
            "response": "",
            "revise_count": 0,
            "critic_verdict": "",
            "critique": "",
        }
        config = {
            "configurable": {"thread_id": f"{self.session_id}-t{self._turn}"},
            "callbacks": [_HudEventCallback()],
        }

        event_bus.state("thinking")

        try:
            result = await self._graph.ainvoke(state, config=config)
        except Exception as exc:
            msg = str(exc)
            if not self._using_fallback and ("PerDay" in msg or "RequestsPerDay" in msg or "429" in msg):
                print(f"\n[JARVIS] Quota hit — falling back to AI Studio Flash.")
                self._using_fallback = True
                import copy
                fb_settings = copy.copy(self.settings)
                fb_settings.cloud_tier = "aistudio"
                fb_settings.cloud_model = self.settings.cloud_model_fallback
                self._graph = build_graph(fb_settings, self.workspace, self.memory, self._checkpointer)
                result = await self._graph.ainvoke(state, config=config)
            else:
                raise

        response = result.get("response", "")
        if not response:
            from langchain_core.messages import AIMessage
            for m in reversed(result.get("messages", [])):
                if isinstance(m, AIMessage) and isinstance(m.content, str) and m.content.strip():
                    response = m.content
                    break

        self._record_usage_from_result(result, use_pro_agent)

        all_msgs = result.get("messages", [])
        non_system = [m for m in all_msgs if not isinstance(m, SystemMessage)]
        self._history = _strip_images_for_storage(_trim_history(non_system))

        # Persist conversation state to SQLite
        self.session_store.save_turn(self.session_id, self._history, self._turn)
        if self._turn == 1:
            self.session_store.set_topic_hint(self.session_id, clean_input[:60])

        self.memory.store("user", clean_input, self.session_id)
        self.memory.store("assistant", response, self.session_id)
        self.memory.log_turn("user", clean_input)
        self.memory.log_turn("assistant", response)

        self._schedule_entity_extraction(clean_input, response)

        event_bus.state("speaking")
        event_bus.state("idle")

        return response, self.current_model_label

    def _record_usage_from_result(self, result: dict, use_pro_agent: bool) -> None:
        from langchain_core.messages import AIMessage as LCAIMessage
        model_id = self._active_model_id or (
            f"vertex/{self.settings.vertex_model_primary}" if use_pro_agent
            else f"vertex/{self.settings.vertex_model_fast}"
        )
        for msg in result.get("messages", []):
            if not isinstance(msg, LCAIMessage):
                continue
            um = getattr(msg, "usage_metadata", None)
            if not um:
                continue
            msg_model = getattr(msg, "response_metadata", {}).get("model_name", model_id)
            self.usage.record(
                msg_model,
                um.get("input_tokens", 0),
                um.get("output_tokens", 0),
            )

    async def chat_stream(
        self,
        user_input: str,
        detected_language: str = "en",
        image_bytes: bytes | None = None,
        image_mime: str = "image/png",
        extra_images: list[bytes] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream one turn token-by-token. Yields text deltas for voice.speak_stream().

        image_bytes   — single uploaded image (PNG/JPG); sent as multimodal block.
        extra_images  — list of PNG bytes extracted from a PDF by marker-pdf;
                        appended after the text block.
        """
        needs_planning = user_input.strip().lower().startswith("/think")
        clean_input = re.sub(r"^/think\s*", "", user_input).strip()
        use_pro_agent = not _is_trivially_simple(clean_input, needs_planning)

        memory_ctx = self.memory.recall(clean_input, n=3)
        past_sessions = self.memory.recall_summaries(clean_input, n=3)
        past_sessions_block = self._build_past_sessions_block(past_sessions)
        entities_block = self._build_entities_block()
        open_todos_block = self._build_todos_block()   # Faz 13-D
        system_prompt = _load_system_prompt(
            self.settings, memory_ctx, detected_language,
            self._env_block, clean_input, entities_block,
            past_sessions_block, open_todos_block,
        )

        human_msg = _build_human_message(clean_input, image_bytes, image_mime, extra_images)

        initial_messages = (
            [SystemMessage(content=system_prompt)]
            + self._history
            + [human_msg]
        )

        self._turn += 1
        state = {
            "messages": initial_messages,
            "user_query": clean_input,
            "language": detected_language,
            "memory_context": memory_ctx,
            "needs_planning": needs_planning,
            "use_pro_agent": use_pro_agent,
            "plan": "",
            "response": "",
            "revise_count": 0,
            "critic_verdict": "",
            "critique": "",
        }
        config = {
            "configurable": {"thread_id": f"{self.session_id}-t{self._turn}"},
            "callbacks": [_HudEventCallback()],
        }

        event_bus.state("thinking")

        chunks: list[str] = []
        _first_chunk = True

        async for delta in graph_stream_to_text(self._graph, state, config):
            if _first_chunk:
                event_bus.state("speaking")
                _first_chunk = False
            chunks.append(delta)
            yield delta

        full_response = "".join(chunks)

        # Rebuild history from checkpointer to preserve tool messages (Faz 12-B fix)
        try:
            checkpoint_tuple = self._checkpointer.get_tuple(config)
            if checkpoint_tuple:
                real_messages = checkpoint_tuple.checkpoint["channel_values"].get("messages", [])
                non_system = [m for m in real_messages if not isinstance(m, SystemMessage)]
                self._history = _strip_images_for_storage(_trim_history(non_system))
            else:
                raise ValueError("no checkpoint")
        except Exception:
            # Fallback: rebuild manually (loses tool messages, but doesn't crash)
            from langchain_core.messages import AIMessage
            non_system = [m for m in initial_messages if not isinstance(m, SystemMessage)]
            non_system.append(AIMessage(content=full_response))
            self._history = _strip_images_for_storage(_trim_history(non_system))

        # Token usage estimate (streaming doesn't return metadata)
        all_text = " ".join(
            m.content for m in initial_messages
            if hasattr(m, "content") and isinstance(m.content, str)
        )
        est_in = max(1, len(all_text) // 4)
        est_out = max(1, len(full_response) // 4)
        model_id = self._active_model_id or (
            f"vertex/{self.settings.vertex_model_primary}" if use_pro_agent
            else f"vertex/{self.settings.vertex_model_fast}"
        )
        self.usage.record(model_id, est_in, est_out)

        # Persist conversation state to SQLite
        self.session_store.save_turn(self.session_id, self._history, self._turn)
        if self._turn == 1:
            self.session_store.set_topic_hint(self.session_id, clean_input[:60])

        self.memory.store("user", clean_input, self.session_id)
        self.memory.store("assistant", full_response, self.session_id)
        self.memory.log_turn("user", clean_input)
        self.memory.log_turn("assistant", full_response)

        self._schedule_entity_extraction(clean_input, full_response)

        event_bus.state("idle")
