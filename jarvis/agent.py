"""JARVIS orchestrator — LangGraph-based (Faz 1).

Public API (preserved from pydantic-ai version):
  JarvisAgent(settings)
  .chat(user_input, detected_language) -> tuple[str, str]   # (response, model_label)
  .chat_stream(user_input, detected_language) -> AsyncGenerator[str, None]
  .switch_model(model_id) -> str
  .switch_session(session_id) -> int
  .reset()
  ._cloud_model     (str — display label)

cli.py and voice.py import JarvisAgent and AVAILABLE_MODELS from here.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import threading
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.errors import GraphInterrupt, GraphRecursionError

from jarvis.graph.tool_router import classify_query
from jarvis.graph.tool_accounting import (  # Faz 1.1: shared outcome judgement
    content_is_failure,
    parse_blocked_code,
)

from jarvis.config import Settings
from jarvis.context_builder import ContextBuilder
from jarvis.entity_extractor import extract_entities
from jarvis.fact_extractor import extract_facts   # Faz 2
from jarvis.memory import Memory
from jarvis.session_store import SessionStore
from jarvis.scheduler import SchedulerStore  # Faz 13-C
from jarvis.todo_store import TodoStore      # Faz 13-D
from jarvis.facts_store import FactStore           # Faz 2
from jarvis.procedure_store import ProcedureStore  # Faz 2
from jarvis.graph.graph import build_graph, make_checkpointer
from jarvis.graph.streaming import graph_stream_to_text
from jarvis.usage import UsageTracker
from jarvis.ws import event_bus
from jarvis import audit_log               # Faz 4
from jarvis import tool_trace               # Faz 2.2: test-profile L1 tool trace
from jarvis import paths                   # JARVIS_HOME isolation root
from jarvis.llm_trace import LlmTraceRecorder  # runtime truth: actual provider per turn
from jarvis.tool_registry import get_spec  # Faz 4
from jarvis.mcp_integration import McpToolManager  # Faz 5


# ── Phase 3: confirmation gate ────────────────────────────────────────────────

class ConfirmationRequired(Exception):
    """Raised by chat() when LangGraph interrupts for user confirmation of an L3 tool call."""
    def __init__(self, conf_id: str, payload: dict) -> None:
        self.conf_id = conf_id
        self.payload = payload
        super().__init__(f"confirmation_required:{conf_id}")


# ── Faz 7: proactive self-initiation ────────────────────────────────────────

@dataclass
class ProactiveOutcome:
    """Result of JarvisAgent.proactive_turn() — a background-initiated (not
    user-typed) turn from monitor.py. See that method's docstring for why
    this can never raise ConfirmationRequired the way chat() does."""
    kind: str                      # "none" | "response" | "needs_confirmation"
    text: str = ""                  # kind="response" — what JARVIS wants to surface
    tools: list[str] = field(default_factory=list)  # kind="needs_confirmation" — pending tool name(s)


# ── HUD activity feed callback ────────────────────────────────────────────────

# Faz 2.2 round 3 — the test trace's args preview is key-redacted before it
# hits disk. tool_trace writes whenever JARVIS_TOOL_TRACE=1, which anyone can
# export outside --profile test, so email bodies, file contents and
# credentials must never persist through it. Key-based on purpose (not value
# sniffing): deterministic under test. Known limit: a non-dict input arrives
# as one opaque string and is truncated but NOT scanned — a secret embedded
# in a plain-string arg is out of this helper's scope.
_TRACE_REDACT_KEYS = (
    "password", "passwd", "token", "api_key", "apikey", "secret",
    "credential", "authorization", "body", "content", "message",
)


def redact_tool_args(input_str: Any) -> str:
    """200-char args preview for tool_trace, sensitive keys masked."""
    if isinstance(input_str, dict):
        preview = {
            k: ("<redacted>" if any(s in str(k).lower() for s in _TRACE_REDACT_KEYS) else v)
            for k, v in input_str.items()
        }
        return str(preview)[:200]
    return str(input_str)[:200]


class _HudEventCallback(BaseCallbackHandler):
    """Non-blocking LangChain callback → pushes tool/LLM events to the HUD feed.

    Faz 4: also the execution-time half of the audit trail (the other half is
    policy_guard's decision-time record in confirmation_node) -- on_tool_start
    /on_tool_end/on_tool_error fire for every tool call regardless of which
    loop invoked the graph (config["callbacks"] is built the same way in
    chat()/chat_stream()/resume_and_stream()), so this is genuinely
    transport-agnostic "it happened" coverage, not just HUD telemetry.
    """

    def __init__(self, transport: str = "unknown") -> None:
        self._transport = transport
        self._audit_pending: dict[str, tuple[str, int]] = {}  # run_id -> (tool_name, risk_level)
        self._trace_pending: dict[str, dict] = {}  # run_id -> {tool, args} for ALL tools (test trace)
        self._llm_start_times: dict[str, float] = {}  # run_id -> time.monotonic() at on_llm_start

    def on_tool_start(self, serialized: dict, input_str: Any, **kwargs: Any) -> None:
        name = serialized.get("name", "tool")
        if isinstance(input_str, dict):
            args = ", ".join(f"{k}={str(v)[:60]}" for k, v in list(input_str.items())[:2])
        else:
            args = str(input_str)[:120]
        event_bus.tool_call(f"{name} → {args}", kind="tool")

        run_id = kwargs.get("run_id")
        spec = get_spec(name)
        if spec is not None and spec.risk_level >= 2 and run_id is not None:
            self._audit_pending[str(run_id)] = (name, spec.risk_level)
            audit_log.record(
                "execution_start", tool=name, risk_level=spec.risk_level,
                transport=self._transport, args_preview=str(input_str)[:200],
            )
        # Faz 2.2: trace EVERY tool (L1 included) when the test profile enabled it.
        if run_id is not None and tool_trace.is_enabled():
            self._trace_pending[str(run_id)] = {"tool": name, "args": redact_tool_args(input_str)}

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        s = str(output)[:160]
        if any(kw in s.lower() for kw in ("chroma", "vector", "recall", "memory", "retrieved")):
            event_bus.tool_call(s, kind="note")
        self._record_execution_end(output, kwargs.get("run_id"))
        self._trace_end(output, kwargs.get("run_id"))

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        self._record_execution_end(error, kwargs.get("run_id"), ok=False)
        self._trace_end(error, kwargs.get("run_id"), ok=False)

    def _trace_end(self, output: Any, run_id: Any, ok: bool | None = None) -> None:
        """Faz 2.2 — write the every-tool trace entry (test profile only)."""
        if run_id is None:
            return
        pending = self._trace_pending.pop(str(run_id), None)
        if pending is None:
            return
        content = getattr(output, "content", output)
        if ok is None:
            ok = getattr(output, "status", None) != "error" and not content_is_failure(content)
        head = content if isinstance(content, str) else str(content)
        # 2026-07-19: lift the machine code out of "[BLOCKED:<code>]" results
        # so the oracle can key on structure, not the user-facing string.
        code = parse_blocked_code(head)
        tool_trace.record(
            tool=pending["tool"], args=pending["args"], ok=ok,
            transport=self._transport,
            content_head=head[:200],
            **({"reason_code": code} if code else {}),
        )

    def _record_execution_end(self, output: Any, run_id: Any, ok: bool | None = None) -> None:
        if run_id is None:
            return
        pending = self._audit_pending.pop(str(run_id), None)
        if pending is None:
            return
        name, risk_level = pending
        # output is usually a ToolMessage object, not a raw string: str() of it
        # is "content='[ERROR]...' name=..." so the old prefix check never
        # matched and failed calls logged ok:true (the B6 false-positive, and
        # every Calendar/Gmail credential error). Judge the real content with
        # the same canonical helper the execution ledger uses.
        content = getattr(output, "content", output)
        out_s = content if isinstance(content, str) else str(content)
        if ok is None:
            ok = getattr(output, "status", None) != "error" and not content_is_failure(content)
        audit_log.record(
            "execution_end", tool=name, risk_level=risk_level,
            transport=self._transport, ok=ok, result_preview=out_s[:200],
        )

    def on_llm_start(self, serialized: dict, prompts: list, **kwargs: Any) -> None:
        model = (
            serialized.get("kwargs", {}).get("model_name")
            or serialized.get("kwargs", {}).get("model")
            or serialized.get("name", "llm")
        )
        # Faz 1: the fast role now routes to Ollama by default — "gemini" is
        # the only cloud family in play today, so its absence means local.
        kind = "cloud" if "gemini" in str(model).lower() else "local"
        event_bus.tool_call(str(model), kind=kind)

        # GPT-5.6 review remediation, Faz 7 (verdict #18, CONFIRMED): on_llm_end
        # below used to label the raw output token *count* as "tok/s" with no
        # duration involved at all. Timestamp here so on_llm_end can divide by
        # actual elapsed wall-clock time instead.
        run_id = kwargs.get("run_id")
        if run_id is not None:
            import time
            self._llm_start_times[str(run_id)] = time.monotonic()

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
                    spd = ""
                    import time
                    started = self._llm_start_times.pop(str(kwargs.get("run_id")), None)
                    if started is not None:
                        elapsed = time.monotonic() - started
                        if elapsed > 0:
                            spd = f" · {to_ / elapsed:,.1f} tok/s"
                    event_bus.tool_call(
                        f"{mname} · {ti:,} in / {to_:,} out{spd}", kind="local"
                    )
        except Exception:
            pass


# ── System prompt helpers ──────────────────────────────────────────────────────

# Sprint 2 (Faz 2A): _is_trivially_simple() and its two substring signal sets
# are retired — jarvis.graph.tool_router.classify_query() is the single query
# classifier now. The old function was cloud-first (Pro default, docstring
# said so) in direct conflict with the local-first pivot, and its bare
# `sig in q` probes false-positived on Turkish morphology ("ok" in "çok",
# "hi" in "tarihi", "hey" in "heyecanlıyım").
def _route_query(query: str, needs_planning: bool):
    """One place for every entry point: classify + derive the role choice.
    conversation ⇒ fast role, zero tools; anything tool-shaped (or /think)
    ⇒ reasoning role. Returns (route, use_pro_agent)."""
    route = classify_query(query)
    return route, needs_planning or route.primary_domain != "conversation"


def _build_env_block(workspace: Path) -> str:
    import os
    # 2026-07-19 context-leak fix: under an isolated profile (JARVIS_HOME set:
    # --profile test / the eval harness) the sandbox home is the whole world.
    # This block is injected verbatim into the system prompt, so using the REAL
    # os.path.expanduser("~") here leaked the owner's actual Desktop path
    # (C:\Users\<user>\OneDrive\Desktop) into an "isolated" run's prompt -- the
    # model then echoed it back in its answers (misdiagnosed as a hallucination
    # until the raw prompt was inspected). Same expanduser("~")-ignores-
    # JARVIS_HOME class as the files.py _effective_home fix; reuse that helper so
    # the two can't drift.
    from jarvis.tools.files import _effective_home
    home = _effective_home()
    if os.environ.get("JARVIS_HOME"):
        desktop = home / "Desktop"  # sandbox-relative; never the real profile
    else:
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
    facts_block: str = "",
    procedure_block: str = "",
) -> str:
    from jarvis.prompts.prompt_loader import PromptContext, load_system_prompt
    return load_system_prompt(PromptContext(
        user_name=settings.user_name,
        memory_context=memory_context,
        entities_block=entities_block,
        past_sessions_block=past_sessions_block,
        open_todos_block=open_todos_block,
        facts_block=facts_block,
        procedure_block=procedure_block,
        env_block=env_block,
        detected_language=detected_language,
        user_query=user_query,
    ))


# ── Faz 7: proactive self-initiation prompt ─────────────────────────────────

_NO_ACTION_MARKER = "NO_ACTION_NEEDED"


def _proactive_system_prompt(settings: Settings) -> str:
    """Minimal, dedicated system prompt for proactive_turn() — deliberately NOT
    the full _load_system_prompt() composition (persona + memory/facts/entities/
    past-sessions retrieval): that pipeline is built around a real user query,
    and running it against a synthetic internal trigger phrase like "yeni email
    geldi" would pollute semantic recall with an unrelated query. This is a
    narrow, self-contained evaluation task, not an open-ended conversation.

    Read-only-investigation constraint (added after a live finding, not
    theoretical): a real proactive_turn() call against local qwen2.5:7b-instruct
    hallucinated an unrelated procedure_save call for a mundane calendar
    trigger. procedure_save is risk_level=2 (local_write, no confirmation by
    design -- see policy_guard.py's "kill switch is L3-only" note), so it
    executed silently. The confirmation gate genuinely does cover every L3
    (external-effect) tool call here exactly like a normal conversation --
    that part was never in question -- but L2 local writes (procedure_save,
    todo, file_write, spotify, ...) bypass it by design for a REAL,
    human-driven turn, where a person is present to notice and course-correct.
    A background self-check has nobody watching, so this prompt now explicitly
    forbids using any tool that creates/saves/sends/modifies anything, on top
    of (not instead of) the existing L3 gate -- investigation must stay
    read-only; a suggested action belongs in the text response, not a live
    tool call.
    """
    name = settings.user_name
    return (
        f"You are JARVIS, {name}'s personal AI assistant, running an autonomous "
        f"background self-check -- this turn was generated by JARVIS's own monitor, "
        f"{name} did not send it and is not watching. Evaluate the situation described "
        "below and decide whether it is genuinely worth proactively surfacing "
        "(something urgent, time-sensitive, or needing a decision only they can make).\n\n"
        "You may use READ-ONLY tools to investigate first if that helps (e.g. look up a "
        "calendar event, search for a related email). Do NOT call any tool that creates, "
        "saves, sends, deletes, or modifies anything (procedure_save, todo, file_write, "
        "gmail send/reply, calendar create/update, spotify, shell_run, python_run, "
        "browser actions, or any other write/action tool) -- there is no one here to "
        "confirm or notice a mistake right now. If you conclude a real action is worth "
        "taking, DESCRIBE it in your reply so the user can ask for it explicitly "
        "afterward; do not perform it yourself in this turn.\n\n"
        f"If nothing is worth surfacing, reply with EXACTLY the single word "
        f"{_NO_ACTION_MARKER} and nothing else -- this is the common case; most routine "
        f"events do not need a proactive interruption. If something IS worth surfacing, "
        f"reply in Turkish with a short (1-3 sentence) direct summary or suggestion, as "
        f"if tapping {name} on the shoulder -- not a full conversational answer."
    )


def _trim_history(messages: list[Any], max_turns: int = 10) -> list[Any]:
    """Keep the last ``max_turns`` conversation turns (Patch 1.2, Faz 1D).

    A turn starts at each HumanMessage and runs to the next one. The old flat
    max_messages=20 cap let ONE polluted turn evict everything before it —
    live incident A2→A3 (2026-07-16): a ~20-call hallucination batch's stubs
    pushed the fact the user had just stated ("rengim mavi") out of the
    window, so the very next turn couldn't recall it.
    """
    from langchain_core.messages import SystemMessage as SM
    system_msgs = [m for m in messages if isinstance(m, SM)]
    non_system = [m for m in messages if not isinstance(m, SM)]
    starts = [i for i, m in enumerate(non_system) if isinstance(m, HumanMessage)]
    if len(starts) > max_turns:
        non_system = non_system[starts[-max_turns]:]
    return system_msgs + non_system


def _execution_summary_from_ledger(ledger: list[dict] | None) -> str:
    """One short, non-system line for history: what ran and how it ended.
    Deduped per (tool, outcome) — 10 identical failures read once, not 10x."""
    if not ledger:
        return ""
    parts: list[str] = []
    seen: set[tuple[str, bool]] = set()
    for e in ledger:
        key = (str(e.get("tool", "?")), bool(e.get("ok")))
        if key in seen:
            continue
        seen.add(key)
        parts.append(f"{key[0]} {'ok' if key[1] else 'failed/blocked'}")
    return "[Tool execution summary: " + "; ".join(parts) + "]"


def _compact_completed_turn_for_history(
    original_user_message: HumanMessage,
    final_assistant_response: str,
    execution_summary: str = "",
) -> list[Any]:
    """Canonical history form of a completed turn (Patch 1.2, Faz 1D).

    Conversation history carries ONLY what the conversation was: the user's
    real message and the answer they saw, plus at most one summary line for
    tool activity. Raw tool-call batches, stub ToolMessages, policy acks and
    critic instructions never enter it — their home is the checkpoint, the
    audit log and the execution ledger. (An orphan ToolMessage replayed into
    a later turn is also a wire-format violation on the OpenAI-compatible
    endpoint, so this doubles as protocol hygiene.)
    """
    from langchain_core.messages import AIMessage
    exchange: list[Any] = [original_user_message]
    if execution_summary:
        exchange.append(AIMessage(content=execution_summary))
    exchange.append(AIMessage(content=final_assistant_response))
    return exchange


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
    ("local/qwen2.5-7b",               "Qwen2.5 7B Instruct (local)",  "local",    "Ollama · RTX 4070 · default fast+reasoning router"),
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
        # BUG-22: switch_model() used to build a local `new_settings` copy that
        # was never persisted anywhere but the compiled graph closure — a later
        # quota-fallback rebuild had no way to see it and silently reverted to
        # construction-time `self.settings`, discarding the user's chosen model.
        # This is the settings switch_model()/the fallback rebuild actually act on.
        self._effective_settings = settings
        self.memory = Memory(settings)
        # JARVIS_HOME isolation root (stabilization sprint): unset -> cwd,
        # identical to the old Path(".") behavior; set -> tool outputs
        # (pdf_cache/plots/reports) and all stores land under it.
        self.workspace = paths.jarvis_home().resolve()
        self._env_block = _build_env_block(self.workspace)
        self._active_model_id: str | None = None
        # Faz 1: which role (fast/local vs reasoning) served the most recent
        # turn — None before the first turn. Drives _cloud_model's per-turn
        # label since the router now picks a provider per turn, not once.
        self._last_turn_used_pro: bool | None = None
        # Stabilization sprint: the most recent foreground turn's ACTUAL
        # provider/model (LlmTraceRecorder.turn_summary()) — None before the
        # first turn. When present, this is the label's source of truth; the
        # role-derived guesses above become the pre-first-turn fallback only.
        self._last_turn_trace: dict | None = None

        # BUG-8: this singleton is mutated from the main loop, TaskExecutor's
        # background thread, and API endpoints (e.g. /reset) concurrently.
        # threading.Lock (not asyncio.Lock) because those callers run on
        # independent event loops, not just independent tasks on one loop —
        # see _acquire_state_lock() and jarvis/graph/graph.py's docstring.
        self._state_lock = threading.Lock()

        db_path = paths.data_dir() / "jarvis_checkpoints.db"
        self._checkpointer = make_checkpointer(db_path)
        # Faz 5: MCP tools connect lazily via connect_mcp_tools() -- __init__
        # runs before any event loop exists in every real entry point (cli.py
        # constructs JarvisAgent before its own asyncio.run(); api.py's
        # init_agent() runs before uvicorn's loop starts), and MCP's stdio
        # transport needs a real, long-lived loop to connect on. self._mcp.tools
        # is always [] here -- see connect_mcp_tools()'s docstring.
        self._mcp = McpToolManager()
        self._mcp_graph_rebuilt = False  # guards connect_mcp_tools()'s one-time graph rebuild
        self._graph = build_graph(
            settings, self.workspace, self.memory, self._checkpointer,
            extra_tools=self._mcp.tools,
        )

        self.usage = UsageTracker(paths.data_dir() / "usage.json")

        # Faz 12-B: persistent session store — auto-resume last session
        self.session_store = SessionStore(paths.data_dir() / "sessions.db")
        last = self.session_store.latest_session()
        if last:
            self.session_id = last
            self._history: list[Any] = self.session_store.load_history(last, limit=20)
            # BUG-11: resume this session's own turn counter too — restarting
            # it at 0 would let the next turn reuse an old thread_id
            # ("{session_id}-t1") and resurrect a stale LangGraph checkpoint.
            self._turn: int = self.session_store.last_turn_idx(last)
        else:
            self.session_id = self.session_store.new_session()
            self._history: list[Any] = []
            self._turn: int = 0

        # Faz 13-C: scheduler store (same DB file, separate table)
        self.scheduler = SchedulerStore(paths.data_dir() / "sessions.db")

        # Faz 13-D: to-do store (same DB file, separate table)
        self.todo_store = TodoStore(paths.data_dir() / "sessions.db")

        # Faz 2: semantic memory (facts) + procedural memory (procedures) stores
        self.facts_store = FactStore(paths.data_dir() / "sessions.db")
        self.procedure_store = ProcedureStore(paths.data_dir() / "sessions.db")
        # Patch 1.2 (Faz 1C): the store's open-time migration may have just
        # archived legacy content-duplicate rows (F16 damage) — drop their
        # Chroma twins so semantic recall can't keep surfacing archived copies.
        # Chroma delete of an id that was never embedded is harmless.
        for _pid in self.procedure_store.archived_duplicate_ids:
            try:
                self.memory.delete_procedure(_pid)
            except Exception:
                pass
        self._seed_procedures_if_empty()

        # Phase 4: context builder (deduplicates 5-call memory retrieval)
        self._context_builder = ContextBuilder(self.memory, self.todo_store, self.session_store)

        # Phase 3: pending interrupted graphs awaiting user confirmation
        self._pending_confirmations: dict[str, dict] = {}

        # Strong refs to background tasks — prevents GC from cancelling them mid-flight
        self._bg_tasks: set[asyncio.Task] = set()

        # Faz 13-A: backfill summaries for archived sessions in background
        self._schedule_summary_backfill()

    def _seed_procedures_if_empty(self) -> None:
        """One-time migration: register the pre-Faz-2 static workflow file as the
        first procedure row, so retiring the old hardcoded keyword-trigger (see
        prompt_loader.py) is a no-op for existing behavior. No-op if
        jarvis_procedures already has content (repeat startups, or once the
        agent has learned procedures of its own via procedure_save).
        """
        if self.memory.count_procedures() > 0:
            return
        workflow_path = Path(__file__).parent / "prompts" / "workflows" / "data_report.md"
        if not workflow_path.exists():
            return
        try:
            body = workflow_path.read_text(encoding="utf-8")
            name = "data_report"
            description = (
                "Workflow for tasks involving data files (PDF/Excel/CSV) and a final "
                "PDF report: read the data, delegate plot scripting, write LaTeX, compile."
            )
            pid = self.procedure_store.add(name, description, body, source="seed")
            self.memory.store_procedure(pid, name, description, body, status="approved")
        except Exception:
            pass

    @property
    def _cloud_model(self) -> str:
        """Display label for whichever provider ACTUALLY served the last turn.

        Stabilization sprint: when a turn has completed, the label derives
        from LlmTraceRecorder's per-call metadata (_last_turn_trace) — the
        pre-sprint role-derived branches below could claim "Vertex, reasoning"
        while the Ollama fallback did the answering. Those branches now serve
        only the pre-first-turn / pinned states, where no trace exists yet.
        """
        trace = self._last_turn_trace
        if trace:
            display = {
                "ollama": "Ollama, local",
                "vertex": "Vertex",
                "aistudio": "AI Studio",
            }.get(trace["provider"], trace["provider"])
            # Response-scoped on purpose (patch 1.1): the label describes the
            # call that authored the visible answer -- a critic/planner call
            # elsewhere in the turn falling back must not relabel the answer
            # itself as "(fallback)". turn_had_any_fallback stays visible in
            # /status for the turn-wide view.
            fb = ", fallback" if trace.get("response_fallback_used") else ""
            return f"{trace['model']} ({display}{fb})"

        s = self._effective_settings

        if self._last_turn_used_pro is None:
            if self._active_model_id:
                return _label_for(self._active_model_id)
            if s.use_vertex:
                return _label_for(f"vertex/{s.vertex_model_fast}")
            return s.cloud_model_label

        if self._last_turn_used_pro:
            if s.use_vertex:
                return f"{s.vertex_model_primary} (Vertex, reasoning)"
            return f"{s.cloud_model_fallback} (AI Studio, reasoning)"

        if self._active_model_id and s.pin_cloud_model:
            return _label_for(self._active_model_id)

        return f"{s.local_model} (Ollama, local)"

    @property
    def current_model_label(self) -> str:
        return self._cloud_model

    @property
    def last_turn_trace(self) -> dict | None:
        """The last foreground turn's actual provider/model rollup (or None).

        Keys: requested_role, provider, model, billable, billing,
        fallback_used, response_fallback_used, turn_had_any_fallback, calls,
        input_tokens, output_tokens — see LlmTraceRecorder.turn_summary.
        """
        return self._last_turn_trace

    async def _acquire_state_lock(self) -> None:
        """Acquire _state_lock without blocking the calling event loop's thread."""
        await asyncio.get_running_loop().run_in_executor(None, self._state_lock.acquire)

    def _reset_state_sync(self) -> tuple[str, bool]:
        """The lock-guarded state mutation shared by reset()/reset_async().

        Pure sync work (SQLite archive + new session + in-memory clears) with
        NO event-loop interaction — safe to run on any thread, including
        asyncio.to_thread workers. Returns (old_session_id, had_content) so
        the caller decides whether/where to schedule summarization.
        """
        with self._state_lock:
            old_session_id = self.session_id
            had_content = self._turn > 0 or len(self._history) > 0
            self.session_store.archive_session(old_session_id)
            self.session_id = self.session_store.new_session()
            self._history = []
            self._turn = 0
            # Stabilization patch 1.1: per-session telemetry must not leak
            # into the fresh session -- without these, /status kept showing
            # the ARCHIVED session's provider/model/fallback rollup, and a
            # pre-reset confirmation id could resume its interrupted graph
            # into (and write history against) the new session. The user's
            # explicit model pin (_active_model_id) deliberately survives:
            # it's a preference, not per-session state.
            self._last_turn_trace = None
            self._last_turn_used_pro = None
            self._pending_confirmations.clear()
        return old_session_id, had_content

    def reset(self) -> None:
        """Archive the current session and start a fresh one (sync variant).

        NOT a delete: the old session stays in the session store and any
        conversation memory already embedded into ChromaDB stays there too.
        Summarization is fire-and-forget and only possible when the calling
        thread has a running event loop (_schedule_summarize_one no-ops with
        a warning otherwise) — a skipped summary never fails the reset.
        Async callers (API endpoints, the CLI's async REPL) should prefer
        reset_async(), which never blocks the loop on the SQLite work.
        """
        old_session_id, had_content = self._reset_state_sync()
        if had_content:
            try:
                self._schedule_summarize_one(old_session_id)
            except Exception as exc:
                print(f"[reset] summary scheduling failed (reset itself OK): {exc}")
        event_bus.session(self.session_id, None)

    async def reset_async(self) -> None:
        """Archive the current session and start a fresh one (async variant).

        Fixes the POST /reset 500: the old shape ran the whole sync reset()
        inside run_in_executor, so _schedule_summarize_one's create_task ran
        on a worker thread with no running loop and raised RuntimeError.
        Here the blocking state mutation goes to a worker thread via
        to_thread, and summarization is scheduled only after control returns
        to the event loop — where create_task is legal. A scheduling failure
        logs and moves on; the reset has already succeeded by then.
        """
        old_session_id, had_content = await asyncio.to_thread(self._reset_state_sync)
        if had_content:
            try:
                self._schedule_summarize_one(old_session_id)
            except Exception as exc:
                print(f"[reset] summary scheduling failed (reset itself OK): {exc}")
        event_bus.session(self.session_id, None)

    def switch_session(self, session_id: str) -> int:
        """Load a past session's history. Returns number of messages loaded."""
        with self._state_lock:
            self._history = self.session_store.load_history(session_id, limit=20)
            self.session_id = session_id
            # BUG-11: resume this session's own turn counter instead of
            # restarting at 0 — a fresh 0 would let the next turn reuse an
            # old thread_id ("{session_id}-t1") and resurrect that session's
            # very first LangGraph checkpoint into the current conversation.
            self._turn = self.session_store.last_turn_idx(session_id)
            n = len(self._history)
        topic = next(
            (s["topic_hint"] for s in self.session_store.list_sessions(50) if s["id"] == session_id),
            None,
        )
        event_bus.session(session_id, topic)
        return n

    def switch_model(self, model_id: str) -> str:
        import copy
        new_settings = copy.copy(self.settings)

        if model_id.startswith("local/") or model_id == "local":
            # Faz 1: explicit un-pin — return the fast role to the local-first
            # Ollama-primary default (see jarvis/providers/get_llm).
            new_settings.pin_cloud_model = False
        elif model_id.startswith("vertex/"):
            real_id = model_id.removeprefix("vertex/")
            new_settings.cloud_tier = "vertex"
            new_settings.vertex_model_fast = real_id
            new_settings.pin_cloud_model = True
        elif model_id.startswith("aistudio/"):
            real_id = model_id.removeprefix("aistudio/")
            new_settings.cloud_tier = "aistudio"
            new_settings.cloud_model = real_id
            new_settings.pin_cloud_model = True
        elif model_id.startswith("groq/"):
            raise ValueError("Groq is an optional intent classifier — not a primary orchestrator model.")
        else:
            new_settings.cloud_tier = "aistudio"
            new_settings.cloud_model = model_id
            new_settings.pin_cloud_model = True

        new_graph = build_graph(
            new_settings, self.workspace, self.memory, self._checkpointer,
            extra_tools=self._mcp.tools,  # Faz 5: don't drop already-connected MCP tools on a model switch
        )
        with self._state_lock:
            self._graph = new_graph
            self._effective_settings = new_settings
            self._active_model_id = model_id
        return _label_for(model_id)

    # ── Entity extraction (fire-and-forget) ────────────────────────────────────

    @staticmethod
    def _should_extract(user_text: str, response: str) -> bool:
        """BUG-25 guard: skip memory extraction (entities + facts) for
        trivially short exchanges (a bare "ok"/"tamam" ack) so it doesn't burn
        a Flash-Lite call on every turn. Requires BOTH sides to be short —
        resume_and_stream() legitimately passes user_text="" with a real,
        non-trivial response, and that call site must keep firing.
        """
        return not (len(user_text.split()) <= 3 and len(response.split()) <= 10)

    def _schedule_memory_extraction(self, user_text: str, response: str) -> None:
        """Fire-and-forget: extract entities + durable facts from this exchange.

        Faz 2: extended from entity-only extraction (formerly
        _schedule_entity_extraction) to also run fact extraction (semantic
        memory) concurrently, sharing one guard and one background task so
        both concerns fire/skip together at the same trigger point.
        """
        if not self._should_extract(user_text, response):
            return
        session_id = self.session_id

        async def _do() -> None:
            try:
                entities, facts = await asyncio.gather(
                    extract_entities(user_text, response, self.settings),
                    extract_facts(user_text, response, self.settings),
                )
                for e in entities:
                    self.session_store.upsert_entity(e.name, e.type, e.description, session_id)
                for f in facts:
                    existing = self.memory.find_similar_fact(f.fact_text)
                    if existing:
                        self.facts_store.bump_fact(existing["fact_id"])
                    else:
                        fid = self.facts_store.insert_fact(
                            f.subject, f.predicate, f.object, f.fact_text, session_id,
                        )
                        self.memory.store_fact(fid, f.fact_text, f.subject, f.predicate, session_id)
            except Exception:
                pass

        task = asyncio.create_task(_do())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    # ── Session summarization (Faz 13-A, fire-and-forget) ─────────────────────

    def run_startup_backfill(self) -> None:
        """Call once the real event loop is up — cli.py's _run_loop()/
        _run_voice_loop() and api.py's lifespan(), right alongside
        connect_mcp_tools() (same "must be the real long-lived loop, not
        __init__" constraint). GPT-5.6 review remediation, Faz 7 (verdict
        #17, CONFIRMED): _schedule_summary_backfill() below was only ever
        called from __init__, which both real entry points run *before*
        asyncio.run()/uvicorn start their loop — so `sessions_needing_
        summary()` was always non-empty potential work that silently never
        executed. Safe to call more than once (idempotent): fires only for
        sessions still missing a summary, so a second call from a resumed
        session (e.g. --api's lifespan running after CLI already backfilled)
        just finds nothing left to do.
        """
        self._schedule_summary_backfill()

    def _schedule_summary_backfill(self) -> None:
        """On startup: summarize+embed all archived sessions that have no summary yet.

        No-ops if there's no event loop running yet — true of the
        __init__-time call (constructed before asyncio.run()/uvicorn start
        their loop in both real entry points) but not of run_startup_
        backfill()'s later call once that loop is actually running. Checking
        for a loop *before* constructing the coroutine (rather than catching
        the RuntimeError from create_task after the fact) avoids leaking an
        unawaited coroutine object each time.
        """
        pending = self.session_store.sessions_needing_summary()
        if not pending:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
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

        task = asyncio.create_task(_backfill())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def _schedule_summarize_one(self, session_id: str) -> None:
        """Fire-and-forget: summarize a single just-archived session.

        No-ops (with a visible warning) when the calling thread has no
        running event loop — same guard idiom as _schedule_summary_backfill,
        and checked BEFORE building the coroutine to avoid leaking an
        unawaited coroutine object. This was the POST /reset 500 root cause:
        an unguarded create_task on an executor worker thread.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            print(f"[reset] no running event loop — summary for {session_id} skipped")
            return

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

    # ── Chat ───────────────────────────────────────────────────────────────────

    # ── Faz 5: MCP tool lifecycle ────────────────────────────────────────────

    async def connect_mcp_tools(self) -> None:
        """Connect configured MCP servers (Playwright, etc.) and rebuild the
        graph — ONCE — so their tools are actually offered to the LLM.
        Fully idempotent: McpToolManager.connect() only does real work once
        per process, and self._mcp_graph_rebuilt guards the graph rebuild
        itself so it also only happens once (BUG caught live, 2026-07-15 —
        `if self._mcp.tools: rebuild` alone is NOT idempotent, since
        self._mcp.tools stays truthy forever after a successful connect; every
        call after the first — including the defensive one at the top of
        chat()/chat_stream(), which fires on literally every turn — was
        silently rebuilding the ENTIRE graph, re-constructing every LLM
        provider and re-running .bind_tools() across all ~60 tools, on every
        single turn for the rest of the process's life).

        Callers: cli.py's _run_loop()/_run_voice_loop() and api.py's
        lifespan() call this explicitly, once, right as their real long-lived
        event loop starts — before any user turn or TaskExecutor background
        job can possibly run — so the MCP session is always opened on the
        loop that will actually service it for the rest of the process's
        life (see jarvis/mcp_integration.py's module docstring for why that
        matters). chat()/chat_stream() also call this as a belt-and-suspenders
        safety net for any caller that skips the explicit bootstrap (e.g. a
        script constructing JarvisAgent directly) — safe because it's a no-op
        once the real bootstrap has already run.
        """
        await self._mcp.connect(self.settings)
        if self._mcp.tools and not self._mcp_graph_rebuilt:
            self._graph = build_graph(
                self.settings, self.workspace, self.memory, self._checkpointer,
                extra_tools=self._mcp.tools,
            )
            self._mcp_graph_rebuilt = True

    async def close_mcp_tools(self) -> None:
        """Close any live MCP subprocess(es) — call on clean process shutdown
        so a launched npx/browser process tree doesn't linger."""
        await self._mcp.close()
        self._mcp_graph_rebuilt = False

    async def _recursion_stop_response(self, config: dict, transport: str) -> str:
        """Patch 1.2 (Faz 1B): honest user-facing text when GRAPH_RECURSION_LIMIT
        fires (live incident F16: 10 duplicate procedure drafts, then an opaque
        HTTP 500). What actually completed comes from the execution ledger in
        the turn's last checkpoint — a blanket "the first operation succeeded"
        would be a lie whenever the loop started before any tool ran, or every
        attempt errored. Callers must NOT persist the half-finished turn.
        """
        ledger: list[dict] = []
        try:
            snap = await self._graph.aget_state(config)
            ledger = list((snap.values or {}).get("tool_execution_ledger") or [])
        except Exception:
            pass  # no checkpoint (e.g. loop before first tool round) — report honestly below
        ok_tools = [e.get("tool", "?") for e in ledger if e.get("ok")]
        audit_log.record(
            "graph_stopped_recursion", transport=transport,
            successful_tools=len(ok_tools), ledger_size=len(ledger),
        )
        if ok_tools:
            uniq = ", ".join(dict.fromkeys(ok_tools))
            return (
                "JARVIS aynı işlemleri tekrar etmeye başladığı için turn güvenli "
                "biçimde durduruldu. Başarıyla tamamlanan işlemler korundu; tekrar "
                f"çağrıları yürütülmedi. (Tamamlanan: {uniq})"
            )
        return (
            "JARVIS tekrarlayan bir araç döngüsüne girdiği için turn güvenli "
            "biçimde durduruldu. Herhangi bir işlemin başarıyla tamamlandığı "
            "doğrulanamadı."
        )

    async def chat(
        self,
        user_input: str,
        detected_language: str = "en",
        image_bytes: bytes | None = None,
        image_mime: str = "image/png",
        extra_images: list[bytes] | None = None,
        transport: str = "unknown",
    ) -> tuple[str, str]:
        """Run one turn. Returns (response_text, model_label).

        image_bytes   — single uploaded image (PNG/JPG); sent as multimodal block.
        extra_images  — list of PNG bytes extracted from a PDF by marker-pdf;
                        appended after the text block.
        transport     — Faz 4: caller identity tag ("cli-text" | "api" | ...),
                        threaded into state["transport"] for the audit log.
        """
        needs_planning = user_input.strip().lower().startswith("/think")
        clean_input = re.sub(r"^/think\s*", "", user_input).strip()
        tool_route, use_pro_agent = _route_query(clean_input, needs_planning)

        # BUG-8: serialize the whole turn — self._history/_turn/session_id are
        # read at the start and written back at the end; a concurrent caller
        # (TaskExecutor's background thread, another request) interleaving in
        # between would clobber one turn's result with the other's.
        await self._acquire_state_lock()
        try:
            await self.connect_mcp_tools()  # Faz 5: no-op after the first real connect
            ctx = self._context_builder.build(clean_input, session_id=self.session_id)
            system_prompt = _load_system_prompt(
                self.settings, ctx.memory_ctx, detected_language,
                self._env_block, clean_input, ctx.entities_block,
                ctx.past_sessions_block, ctx.open_todos_block,
                ctx.facts_block, ctx.procedure_block,
            )

            human_msg = _build_human_message(clean_input, image_bytes, image_mime, extra_images)

            initial_messages = (
                [SystemMessage(content=system_prompt)]
                + self._history
                + [human_msg]
            )

            self._turn += 1
            # Faz 1: record which role this turn requested so _cloud_model can
            # report the provider that actually answered (see that property).
            self._last_turn_used_pro = use_pro_agent
            state = {
                "messages": initial_messages,
                "user_query": clean_input,
                "language": detected_language,
                "memory_context": ctx.memory_ctx,
                "needs_planning": needs_planning,
                "use_pro_agent": use_pro_agent,
                "plan": "",
                "response": "",
                "revise_count": 0,
                "critic_verdict": "",
                "critique": "",
                "transport": transport,
                # Sprint 2 (Faz 2A): the deterministic capability route — the
                # agent node binds only this subset's schemas for the turn.
                "tool_route": tool_route.to_dict(),
                # Patch 1.2 (Faz 1B): explicit per-turn reset of the
                # tool-accounting fields. A fresh thread_id already isolates
                # the checkpointer per turn, but safety counters get an
                # explicit zero rather than relying on that indirection.
                "tool_calls_attempted": 0,
                "tool_rounds": 0,
                "seen_tool_fingerprints": [],
                "completed_tool_fingerprints": [],
                "tool_execution_ledger": [],
            }
            recorder = LlmTraceRecorder(
                usage=self.usage,
                requested_role="reasoning" if use_pro_agent else "fast",
            )
            config = {
                "configurable": {"thread_id": f"{self.session_id}-t{self._turn}"},
                "callbacks": [_HudEventCallback(transport), recorder],
                # BUG-recursion (Faz 4): cap LangGraph super-steps per turn so a
                # model stuck retrying a tool call fails clearly instead of
                # looping unbounded.
                "recursion_limit": self.settings.graph_recursion_limit,
            }

            event_bus.state("thinking")

            try:
                result = await self._graph.ainvoke(state, config=config)
            except GraphInterrupt as exc:
                event_bus.state("idle")
                try:
                    payload = exc.args[0][0].value
                except Exception:
                    payload = {}
                conf_id = str(uuid.uuid4())
                self._pending_confirmations[conf_id] = {
                    "config": config, "recorder": recorder,
                }
                event_bus.confirmation_required(conf_id, payload)
                raise ConfirmationRequired(conf_id, payload) from exc
            except GraphRecursionError:
                # Patch 1.2 (Faz 1B): the recursion limit is the last-resort
                # loop stopper -- answer with an honest, ledger-based message
                # instead of the opaque HTTP 500 of live incident F16. The
                # half-finished turn is deliberately NOT persisted to history
                # or episodic memory (early return skips both).
                response = await self._recursion_stop_response(config, transport)
                trace = recorder.turn_summary()
                if trace:
                    self._last_turn_trace = trace
                event_bus.state("idle")
                return response, self.current_model_label
            # Patch 1.1: the pre-router "if '429' in str(exc): rebuild the
            # graph on AI Studio Flash and re-run the whole turn" block that
            # used to live here is gone. It predated the provider router --
            # per-invocation fallback is _compose(...).with_fallbacks()'s job
            # now (jarvis/providers) -- and it was actively wrong three ways:
            # a "429" from ANY tool (Tavily, ...) matched it; under
            # CLOUD_POLICY=off it announced a cloud switch the router would
            # then refuse; and re-running the full graph re-executed any tool
            # side effects that had already succeeded in the failed attempt.

            # Faz 3 live A/B finding: on this LangGraph version, a dynamic
            # interrupt() under non-streaming ainvoke() does NOT raise
            # GraphInterrupt to the caller -- the invocation RETURNS normally
            # with the pending interrupt under "__interrupt__". The except
            # branch above never fired for /chat, so the confirmation payload
            # was silently lost (latent since Faz 4: the streaming CLI/voice
            # paths raise and were the only ones ever live-verified). Handle
            # the value-style surface too, identically.
            pending_interrupts = result.get("__interrupt__") or []
            if pending_interrupts:
                event_bus.state("idle")
                try:
                    payload = pending_interrupts[0].value
                except Exception:
                    payload = {}
                conf_id = str(uuid.uuid4())
                self._pending_confirmations[conf_id] = {
                    "config": config, "recorder": recorder,
                }
                event_bus.confirmation_required(conf_id, payload)
                raise ConfirmationRequired(conf_id, payload)

            response = result.get("response", "")
            if not response:
                # Fallback: last AI text FROM THIS TURN only. Scanning the
                # whole message list reached back into replayed history and
                # returned a PREVIOUS turn's answer verbatim -- the "echo"
                # failure observed live in both manual rounds (D10/C8).
                from langchain_core.messages import AIMessage
                new_msgs = result.get("messages", [])[len(initial_messages):]
                for m in reversed(new_msgs):
                    if isinstance(m, AIMessage) and isinstance(m.content, str) and m.content.strip():
                        response = m.content
                        break
            if not response:
                response = (
                    "Bu turn bir cevap üretemeden kesildi (onay bekleyen veya "
                    "engellenen araç çağrıları olabilir). Lütfen isteği "
                    "yeniden veya daha net ifade ederek deneyin."
                )

            # Runtime truth: label the turn with the provider that ACTUALLY
            # answered (per-tier callback metadata), not the requested role.
            # Usage is recorded live by the recorder's on_llm_end callback
            # (one UsageTracker.record per real LLM call) -- no post-hoc
            # rescan of result["messages"] needed anymore.
            trace = recorder.turn_summary()
            if trace:
                self._last_turn_trace = trace

            # Patch 1.2 (Faz 1D): history gets the canonical exchange only —
            # prior turns + [user message, (tool summary), final answer]. The
            # raw graph transcript (tool batches, stubs, acks) stays in the
            # checkpoint/audit/ledger, never in conversation history.
            exchange = _compact_completed_turn_for_history(
                human_msg, response,
                _execution_summary_from_ledger(result.get("tool_execution_ledger")),
            )
            self._history = _strip_images_for_storage(
                _trim_history(self._history + exchange, self.settings.max_conversation_turns)
            )

            # Persist conversation state to SQLite
            self.session_store.save_turn(self.session_id, self._history, self._turn)
            if self._turn == 1:
                self.session_store.set_topic_hint(self.session_id, clean_input[:60])
        finally:
            self._state_lock.release()

        self.memory.store("user", clean_input, self.session_id)
        self.memory.store("assistant", response, self.session_id)
        self.memory.log_turn("user", clean_input)
        self.memory.log_turn("assistant", response)

        self._schedule_memory_extraction(clean_input, response)

        event_bus.state("speaking")
        event_bus.state("idle")

        return response, self.current_model_label

    async def chat_stream(
        self,
        user_input: str,
        detected_language: str = "en",
        image_bytes: bytes | None = None,
        image_mime: str = "image/png",
        extra_images: list[bytes] | None = None,
        transport: str = "unknown",
    ) -> AsyncGenerator[str, None]:
        """Stream one turn token-by-token. Yields text deltas for voice.speak_stream().

        image_bytes   — single uploaded image (PNG/JPG); sent as multimodal block.
        extra_images  — list of PNG bytes extracted from a PDF by marker-pdf;
                        appended after the text block.
        transport     — Faz 4: caller identity tag ("voice-cli" | "api-stream" | ...),
                        threaded into state["transport"] for the audit log.
        """
        needs_planning = user_input.strip().lower().startswith("/think")
        clean_input = re.sub(r"^/think\s*", "", user_input).strip()
        tool_route, use_pro_agent = _route_query(clean_input, needs_planning)

        # BUG-8: serialize the whole turn (see chat() for why) — held across
        # the yields too, since the generator can sit parked mid-stream while
        # self._history/_turn still reflect the *previous* completed turn.
        await self._acquire_state_lock()
        try:
            await self.connect_mcp_tools()  # Faz 5: no-op after the first real connect
            ctx = self._context_builder.build(clean_input, session_id=self.session_id)
            system_prompt = _load_system_prompt(
                self.settings, ctx.memory_ctx, detected_language,
                self._env_block, clean_input, ctx.entities_block,
                ctx.past_sessions_block, ctx.open_todos_block,
                ctx.facts_block, ctx.procedure_block,
            )

            human_msg = _build_human_message(clean_input, image_bytes, image_mime, extra_images)

            initial_messages = (
                [SystemMessage(content=system_prompt)]
                + self._history
                + [human_msg]
            )

            self._turn += 1
            # Faz 1: record which role this turn requested so _cloud_model can
            # report the provider that actually answered (see that property).
            self._last_turn_used_pro = use_pro_agent
            state = {
                "messages": initial_messages,
                "user_query": clean_input,
                "language": detected_language,
                "memory_context": ctx.memory_ctx,
                "needs_planning": needs_planning,
                "use_pro_agent": use_pro_agent,
                "plan": "",
                "response": "",
                "revise_count": 0,
                "critic_verdict": "",
                "critique": "",
                "transport": transport,
                # Sprint 2 (Faz 2A): the deterministic capability route — the
                # agent node binds only this subset's schemas for the turn.
                "tool_route": tool_route.to_dict(),
                # Patch 1.2 (Faz 1B): explicit per-turn reset of the
                # tool-accounting fields. A fresh thread_id already isolates
                # the checkpointer per turn, but safety counters get an
                # explicit zero rather than relying on that indirection.
                "tool_calls_attempted": 0,
                "tool_rounds": 0,
                "seen_tool_fingerprints": [],
                "completed_tool_fingerprints": [],
                "tool_execution_ledger": [],
            }
            recorder = LlmTraceRecorder(
                usage=self.usage,
                requested_role="reasoning" if use_pro_agent else "fast",
            )
            config = {
                "configurable": {"thread_id": f"{self.session_id}-t{self._turn}"},
                "callbacks": [_HudEventCallback(transport), recorder],
                # BUG-recursion (Faz 4): cap LangGraph super-steps per turn so a
                # model stuck retrying a tool call fails clearly instead of
                # looping unbounded.
                "recursion_limit": self.settings.graph_recursion_limit,
            }

            event_bus.state("thinking")

            chunks: list[str] = []
            _first_chunk = True
            _confirmation_issued = False
            interrupted = False

            try:
                async for delta in graph_stream_to_text(self._graph, state, config):
                    if _first_chunk:
                        event_bus.state("speaking")
                        _first_chunk = False
                    chunks.append(delta)
                    yield delta
            except GraphInterrupt as exc:
                _confirmation_issued = True
                try:
                    payload = exc.args[0][0].value
                except Exception:
                    payload = {}
                conf_id = str(uuid.uuid4())
                self._pending_confirmations[conf_id] = {
                    "config": config, "recorder": recorder,
                }
                event_bus.confirmation_required(conf_id, payload)
                yield json.dumps({"__jarvis_confirm__": True, "id": conf_id, "payload": payload})
            except GraphRecursionError:
                # Patch 1.2 (Faz 1B): same controlled stop as chat() -- the
                # message flows out as ordinary stream tokens (no 500, no
                # [ERROR] frame), and the half-finished turn is not persisted.
                msg = await self._recursion_stop_response(config, transport)
                stream_trace = recorder.turn_summary()
                if stream_trace:
                    self._last_turn_trace = stream_trace
                event_bus.state("idle")
                yield msg
                return
            except (asyncio.CancelledError, GeneratorExit):
                # BUG-13: barge-in (or any other cancellation of the task driving this
                # generator) must propagate — never swallow — so the caller's await
                # correctly observes it. The turn is discarded (no history/session_store
                # write below), but event_bus still needs to leave "idle", not stuck on
                # "thinking"/"speaking" — handled in the finally block below.
                interrupted = True
                raise

            if _confirmation_issued:
                event_bus.state("idle")
                return

            full_response = "".join(chunks)

            # Runtime truth: streaming fires the same on_chat_model_start/
            # on_llm_end callbacks, so the trace is just as real here.
            stream_trace = recorder.turn_summary()
            if stream_trace:
                self._last_turn_trace = stream_trace

            # Patch 1.2 (Faz 1D): same canonical-exchange compaction as chat().
            # (Pre-1.2 this rebuilt history from the checkpoint to PRESERVE raw
            # tool messages — Faz 12-B; deliberately inverted now, the ledger
            # summary is what history keeps.) The checkpoint is read only for
            # the execution ledger.
            ledger: list[dict] = []
            try:
                checkpoint_tuple = self._checkpointer.get_tuple(config)
                if checkpoint_tuple:
                    ledger = list(
                        checkpoint_tuple.checkpoint["channel_values"].get("tool_execution_ledger") or []
                    )
            except Exception:
                pass
            exchange = _compact_completed_turn_for_history(
                human_msg, full_response, _execution_summary_from_ledger(ledger),
            )
            self._history = _strip_images_for_storage(
                _trim_history(self._history + exchange, self.settings.max_conversation_turns)
            )

            # Usage is recorded live by the recorder's on_llm_end callback
            # (real usage_metadata when the provider reports it mid-stream —
            # stream_usage=True on the Ollama tier, see providers/__init__.py).
            # The old len//4 estimate always priced the turn as the requested
            # Vertex model regardless of which provider actually answered
            # (the exact bug this sprint fixes) -- a provider that reports no
            # usage during streaming now records 0 tokens rather than a wrong
            # guess; cost is never overstated, only possibly under-reported.

            # Persist conversation state to SQLite
            self.session_store.save_turn(self.session_id, self._history, self._turn)
            if self._turn == 1:
                self.session_store.set_topic_hint(self.session_id, clean_input[:60])
        finally:
            self._state_lock.release()
            if interrupted:
                event_bus.state("idle")

        self.memory.store("user", clean_input, self.session_id)
        self.memory.store("assistant", full_response, self.session_id)
        self.memory.log_turn("user", clean_input)
        self.memory.log_turn("assistant", full_response)

        self._schedule_memory_extraction(clean_input, full_response)

        event_bus.state("idle")

    async def resume_and_stream(
        self,
        conf_id: str,
        decision: str,
    ) -> AsyncGenerator[str, None]:
        """Resume a graph interrupted for confirmation and stream the agent's response.

        decision: "approve" to proceed, "deny" or "deny:<guidance>" to cancel.
        """
        from langgraph.types import Command

        pending = self._pending_confirmations.pop(conf_id, None)
        if pending is None:
            yield "[ERROR: confirmation session expired or not found]"
            return
        config = pending["config"]
        # The recorder chat()/chat_stream() registered in config["callbacks"]
        # before the interrupt -- the resumed half of the turn keeps feeding
        # it, so its rollup below is the WHOLE turn's, not just the tail's.
        recorder = pending.get("recorder")

        # BUG-8: same turn-serialization as chat()/chat_stream() — this
        # resumes and finishes the SAME turn that chat_stream() started
        # (and unlocked at its confirmation-interrupt yield), so it needs
        # the lock for the same reason: self._history/_turn get written here.
        await self._acquire_state_lock()
        try:
            event_bus.state("thinking")
            chunks: list[str] = []
            _first_chunk = True
            interrupted = False

            # BUG-12: reuse the same helper chat_stream() uses instead of an inline
            # copy-pasted filter — this method independently had the same
            # draft+revision concatenation bug (both untagged by pass boundary),
            # now fixed once, in one place.
            try:
                async for text in graph_stream_to_text(
                    self._graph, Command(resume=decision), config
                ):
                    if _first_chunk:
                        event_bus.state("speaking")
                        _first_chunk = False
                    chunks.append(text)
                    yield text
            except (asyncio.CancelledError, GeneratorExit):
                # BUG-13: see chat_stream()'s identical fix — must propagate, not
                # swallow, so a barge-in cancellation is correctly observed.
                interrupted = True
                raise
            except GraphRecursionError:
                # Patch 1.2 (Faz 1B): a loop after an approved confirmation
                # (F16's exact shape) ends with the same honest, ledger-based
                # message as chat()/chat_stream() -- not an [ERROR] frame.
                msg = await self._recursion_stop_response(config, "resume")
                if recorder is not None:
                    resumed_trace = recorder.turn_summary()
                    if resumed_trace:
                        self._last_turn_trace = resumed_trace
                event_bus.state("idle")
                yield msg
                return
            except Exception as exc:
                event_bus.state("idle")
                yield f"[ERROR: {exc}]"
                return

            full_response = "".join(chunks)

            # Patch 1.1: without this, a confirmed turn's /status headline
            # kept showing the PREVIOUS turn's provider -- chat()/chat_stream()
            # never got to their own turn_summary() call (the interrupt raised
            # first), and this method didn't roll the recorder up either.
            if recorder is not None:
                resumed_trace = recorder.turn_summary()
                if resumed_trace:
                    self._last_turn_trace = resumed_trace

            # Patch 1.2 (Faz 1D): canonical-exchange compaction. The user
            # message of THIS turn isn't a local here (the turn began in
            # chat()/chat_stream(), which persisted nothing before the
            # interrupt) — user_query travels in the graph state, so read it
            # and the ledger from the turn's checkpoint.
            ledger: list[dict] = []
            resumed_user_query = ""
            try:
                checkpoint_tuple = self._checkpointer.get_tuple(config)
                if checkpoint_tuple:
                    vals = checkpoint_tuple.checkpoint["channel_values"]
                    ledger = list(vals.get("tool_execution_ledger") or [])
                    resumed_user_query = str(vals.get("user_query") or "")
            except Exception:
                pass
            exchange = _compact_completed_turn_for_history(
                HumanMessage(content=resumed_user_query or "[onaylanan araç eylemi]"),
                full_response,
                _execution_summary_from_ledger(ledger),
            )
            self._history = _strip_images_for_storage(
                _trim_history(self._history + exchange, self.settings.max_conversation_turns)
            )

            self.session_store.save_turn(self.session_id, self._history, self._turn)
        finally:
            self._state_lock.release()
            if interrupted:
                event_bus.state("idle")

        self.memory.store("assistant", full_response, self.session_id)
        self.memory.log_turn("assistant", full_response)
        self._schedule_memory_extraction("", full_response)

        event_bus.state("idle")

    # ── Faz 7: proactive self-initiation ────────────────────────────────────

    async def proactive_turn(self, prompt: str, *, source: str) -> ProactiveOutcome:
        """Entry point for monitor.py's self-initiated (not user-typed) turns —
        e.g. "a new email arrived, is this worth surfacing?" — via the same
        compiled graph (same tools, same policy_guard/kill-switch/audit_log
        gate; zero new gating code, identical to every other caller).

        Deliberately ISOLATED from the real conversation: runs its own
        message list and its own LangGraph thread_id, but never touches
        self._history/self._turn/session_store.save_turn or episodic memory —
        otherwise the user's next real turn would see JARVIS's internal
        "should I say anything?" self-talk as if it were a real prior
        exchange, and it would get persisted to SQLite as one.

        Still serialized via _state_lock (BUG-8) like every other entry
        point, so a proactive check can never interleave with a real turn's
        read-modify-write of shared agent state (self._history/_turn/...).

        Never raises ConfirmationRequired — there is no interactive channel
        for a background thread to answer it (same constraint
        TaskExecutor._run() hits for user-initiated background tasks). If the
        graph interrupts, the pending confirmation is discarded (never
        silently executed, never resumed) and reported back as
        kind="needs_confirmation" instead — "confirm-or-notify, not silent
        execution" per ROADMAP.md's Faz 7 constraint.
        """
        await self._acquire_state_lock()
        try:
            await self.connect_mcp_tools()  # no-op after the first real connect
            needs_planning = False
            tool_route, use_pro_agent = _route_query(prompt, needs_planning)
            state = {
                "messages": [
                    SystemMessage(content=_proactive_system_prompt(self.settings)),
                    HumanMessage(content=prompt),
                ],
                "user_query": prompt,
                "language": "tr",
                "memory_context": "",
                "needs_planning": needs_planning,
                "use_pro_agent": use_pro_agent,
                "plan": "",
                "response": "",
                "revise_count": 0,
                "critic_verdict": "",
                "critique": "",
                "transport": f"monitor-{source}",
                # Faz 2A: a background check gets the same scoped subset as a
                # live turn — Faz 7's live incident (an unrelated
                # procedure_save hallucinated during a proactive email check)
                # becomes structurally unlikely when the model never sees that
                # schema in the first place.
                "tool_route": tool_route.to_dict(),
            }
            # Usage IS recorded for a proactive turn (real tokens were really
            # spent) — only the foreground /status label is left untouched
            # (_last_turn_trace is never written below), so a background
            # self-check can never clobber what the user sees as "the last
            # thing I asked".
            recorder = LlmTraceRecorder(
                usage=self.usage,
                requested_role="reasoning" if use_pro_agent else "fast",
            )
            config = {
                "configurable": {"thread_id": f"{self.session_id}-proactive-{uuid.uuid4().hex[:8]}"},
                "callbacks": [_HudEventCallback(f"monitor-{source}"), recorder],
                "recursion_limit": self.settings.graph_recursion_limit,
            }
            try:
                result = await self._graph.ainvoke(state, config=config)
            except GraphInterrupt as exc:
                try:
                    payload = exc.args[0][0].value
                except Exception:
                    payload = {}
                tools = [t.get("name", "?") for t in (payload or {}).get("tools", [])]
                return ProactiveOutcome(kind="needs_confirmation", tools=tools or ["gated action"])
            except Exception:
                # A background self-check must never take the monitor thread
                # down — swallow and report nothing-to-say, same posture as
                # this file's other fire-and-forget background paths
                # (_schedule_memory_extraction et al.).
                return ProactiveOutcome(kind="none")

            # Faz 3 finding: non-streaming ainvoke surfaces a dynamic
            # interrupt as result["__interrupt__"] instead of raising on this
            # LangGraph version -- same confirm-or-notify handling.
            pending_interrupts = result.get("__interrupt__") or []
            if pending_interrupts:
                try:
                    payload = pending_interrupts[0].value
                except Exception:
                    payload = {}
                tools = [t.get("name", "?") for t in (payload or {}).get("tools", [])]
                return ProactiveOutcome(kind="needs_confirmation", tools=tools or ["gated action"])

            response = result.get("response", "")
            if not response:
                from langchain_core.messages import AIMessage
                for m in reversed(result.get("messages", [])):
                    if isinstance(m, AIMessage) and isinstance(m.content, str) and m.content.strip():
                        response = m.content
                        break
        finally:
            self._state_lock.release()

        text = response.strip()
        if not text or text.upper().startswith(_NO_ACTION_MARKER):
            return ProactiveOutcome(kind="none")
        return ProactiveOutcome(kind="response", text=text)

    async def background_turn(self, user_query: str, *, transport: str = "task-async") -> str:
        """Entry point for TaskExecutor's user-initiated (not proactive)
        background jobs -- deep research, geo-math sims, finance reports,
        anything long enough to run off-thread while the user keeps chatting.

        GPT-5.6 review remediation, Faz 5: TaskExecutor._run() used to call
        self.chat() directly, which (a) read/appended self._history mid-flight
        -- interleaving the background task's own exchange into the live
        conversation transcript the user is looking at, mid-turn -- and (b)
        held _state_lock for the *entire* LLM/tool loop, so a long background
        task blocked every foreground chat()/chat_stream() call for its whole
        duration. Both fixed the same way proactive_turn() already fixes the
        "don't touch live state mid-flight" half: this runs on its own
        LangGraph thread_id with its own local message list, never reading
        self._history/self._turn during the (potentially long) ainvoke() call
        -- so _state_lock is only ever held for the brief final append below,
        not the task itself. Unlike proactive_turn(), this DOES get the full
        system prompt (real memory/facts/procedure/vault context) since it's
        a genuine user-requested task, not a background self-check -- and it
        DOES raise ConfirmationRequired on an L3 interrupt (TaskExecutor
        already catches this and reports "ask me interactively instead",
        same as chat() callers do; there is no resumption path for a
        background thread_id, so the interrupt is not registered into
        self._pending_confirmations).

        On success, the exchange is appended to the *real* self._history as a
        synthetic user/assistant pair (not the raw isolated message list) so
        the user's next live turn has it as context and it survives a
        session_store reload -- "confirm-or-notify, not silent execution"
        cuts both ways: a background result must be visible later, just not
        while it's still running. Patch 1.1: "real history" means the session
        that SUBMITTED the task (origin_session_id, pinned at entry) -- if a
        /reset or session switch happened mid-task, the result is persisted
        into that origin session's store instead and the live conversation is
        left alone (only the task-completion notification fires).
        """
        await self.connect_mcp_tools()  # no-op after the first real connect

        # Patch 1.1: pin the session this task belongs to NOW. The user can
        # /reset or /session-switch while the (long) task runs -- the result
        # must land in the session that ASKED for it, never whichever one
        # happens to be live at completion time (session contamination).
        origin_session_id = self.session_id

        ctx = self._context_builder.build(user_query, session_id=origin_session_id)
        system_prompt = _load_system_prompt(
            self.settings, ctx.memory_ctx, "tr",
            self._env_block, user_query, ctx.entities_block,
            ctx.past_sessions_block, ctx.open_todos_block,
            ctx.facts_block, ctx.procedure_block,
        )
        tool_route, use_pro_agent = _route_query(user_query, False)
        state = {
            "messages": [SystemMessage(content=system_prompt), HumanMessage(content=user_query)],
            "user_query": user_query,
            "language": "tr",
            "memory_context": ctx.memory_ctx,
            "needs_planning": False,
            "use_pro_agent": use_pro_agent,
            "plan": "",
            "response": "",
            "revise_count": 0,
            "critic_verdict": "",
            "critique": "",
            "transport": transport,
            "tool_route": tool_route.to_dict(),  # Faz 2A: scoped subset
        }
        # Usage IS recorded for a background turn — only the foreground
        # /status label is left untouched (same rule as proactive_turn).
        recorder = LlmTraceRecorder(
            usage=self.usage,
            requested_role="reasoning" if use_pro_agent else "fast",
        )
        config = {
            "configurable": {"thread_id": f"{self.session_id}-task-{uuid.uuid4().hex[:8]}"},
            "callbacks": [_HudEventCallback(transport), recorder],
            "recursion_limit": self.settings.graph_recursion_limit,
        }

        try:
            result = await self._graph.ainvoke(state, config=config)
        except GraphInterrupt as exc:
            try:
                payload = exc.args[0][0].value
            except Exception:
                payload = {}
            raise ConfirmationRequired(str(uuid.uuid4()), payload) from exc
        # Faz 3 finding: value-style interrupt surface (see chat()) -- a
        # background task cannot resolve a confirmation either way, so the
        # same clear failure as the raise path.
        pending_interrupts = result.get("__interrupt__") or []
        if pending_interrupts:
            try:
                payload = pending_interrupts[0].value
            except Exception:
                payload = {}
            raise ConfirmationRequired(str(uuid.uuid4()), payload)

        response = result.get("response", "")
        if not response:
            from langchain_core.messages import AIMessage
            for m in reversed(result.get("messages", [])):
                if isinstance(m, AIMessage) and isinstance(m.content, str) and m.content.strip():
                    response = m.content
                    break

        # Brief critical section -- not held across the ainvoke() above -- to
        # append this result into the *real* history, same shared-state
        # invariant _state_lock protects everywhere else (BUG-8).
        from langchain_core.messages import AIMessage
        exchange = [
            HumanMessage(content=f"[Background task] {user_query}"),
            AIMessage(content=response),
        ]
        await self._acquire_state_lock()
        try:
            if self.session_id == origin_session_id:
                self._history = self._history + exchange
                self.session_store.save_turn(origin_session_id, self._history, self._turn)
            else:
                # The conversation moved on (reset / session switch) while
                # this ran. Persist into the ORIGIN session's store -- in its
                # own fresh turn_idx bucket, past whatever that session last
                # saved -- so the result is retrievable via /session, and
                # leave the live conversation completely untouched. The user
                # still hears about it through TaskExecutor's completion
                # notification (ws/FCM), same as always.
                origin_history = (
                    self.session_store.load_history(origin_session_id) + exchange
                )
                self.session_store.save_turn(
                    origin_session_id,
                    origin_history,
                    self.session_store.last_turn_idx(origin_session_id) + 1,
                )
        finally:
            self._state_lock.release()

        self.memory.store("user", user_query, origin_session_id)
        self.memory.store("assistant", response, origin_session_id)
        return response
