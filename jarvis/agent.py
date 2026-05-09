"""JARVIS orchestrator — LangGraph-based (Faz 1).

Public API (preserved from pydantic-ai version):
  JarvisAgent(settings)
  .chat(user_input, detected_language) -> tuple[str, str]   # (response, model_label)
  .chat_stream(user_input, detected_language) -> AsyncGenerator[str, None]
  .switch_model(model_id) -> str
  .reset()
  ._using_fallback  (bool)
  ._cloud_model     (str — display label)

cli.py and voice.py import JarvisAgent and AVAILABLE_MODELS from here.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from jarvis.config import Settings, LANG_NAMES
from jarvis.memory import Memory
from jarvis.graph.graph import build_graph
from jarvis.graph.streaming import graph_stream_to_text


# ── System prompt helpers (unchanged from pydantic-ai version) ─────────────────

_DATA_REPORT_KEYWORDS = frozenset([
    "pdf", "excel", "xlsx", "xls", "csv", "report", "rapor", "plot", "grafik",
    "chart", "data", "veri", "analiz", "analysis", "hw", "odev", "ödev",
])


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
) -> str:
    prompt_path = Path(__file__).parent / "prompts" / "system.md"
    raw = prompt_path.read_text(encoding="utf-8")
    raw = raw.replace("{user_name}", settings.user_name)
    raw = raw.replace("{memory_context}", memory_context or "(no prior context retrieved)")

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
    """Keep only the last max_messages, preserving System messages."""
    from langchain_core.messages import SystemMessage as SM
    system_msgs = [m for m in messages if isinstance(m, SM)]
    non_system = [m for m in messages if not isinstance(m, SM)]
    trimmed = non_system[-max_messages:]
    return system_msgs + trimmed


# ── Model catalogue (Revizyon 2 — Gemini-only, 5 entries) ─────────────────────

AVAILABLE_MODELS: list[tuple[str, str, str, str]] = [
    ("vertex/gemini-2.5-pro",       "Gemini 2.5 Pro (Vertex)",      "vertex",   "Vertex credits · orchestrator + vision"),
    ("vertex/gemini-2.5-flash",     "Gemini 2.5 Flash (Vertex)",    "vertex",   "Vertex credits · sub-agent executor"),
    ("aistudio/gemini-2.5-flash",   "Gemini 2.5 Flash (AI Studio)", "aistudio", "50 RPD free · mid fallback"),
    ("aistudio/gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite",     "aistudio", "1000 RPD free · emergency fallback"),
    ("groq/llama-4-scout",          "Llama 4 Scout (Groq)",         "groq",     "optional fast intent classifier"),
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
        self.session_id = str(uuid.uuid4())[:8]
        self._env_block = _build_env_block(self.workspace)
        self._history: list[Any] = []
        self._using_fallback = False
        self._active_model_id: str | None = None

        self._graph = build_graph(settings, self.workspace, self.memory)

    @property
    def _cloud_model(self) -> str:
        """Display label for the currently active model."""
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
        """Clear conversation history."""
        self._history = []

    def switch_model(self, model_id: str) -> str:
        """Switch active model and rebuild graph.

        Accepts IDs from AVAILABLE_MODELS or raw Gemini model strings.
        """
        # Rebuild graph with a tweaked settings override
        # For Faz 1: we rebuild the full graph (cheap operation)
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
            # Groq: only if GROQ_API_KEY set (used as sub-agent, not orchestrator in Faz 1)
            raise ValueError("Groq is an optional intent classifier in Faz 1 — not a primary orchestrator model.")
        else:
            # Raw Gemini model string
            new_settings.cloud_tier = "aistudio"
            new_settings.cloud_model = model_id

        self._graph = build_graph(new_settings, self.workspace, self.memory)
        self._active_model_id = model_id
        self._using_fallback = False
        return _label_for(model_id)

    async def chat(
        self, user_input: str, detected_language: str = "en"
    ) -> tuple[str, str]:
        """Run one turn. Returns (response_text, model_label)."""
        clean_input = re.sub(r"^/think\s*", "", user_input).strip()

        memory_ctx = self.memory.recall(clean_input, n=1)
        system_prompt = _load_system_prompt(
            self.settings, memory_ctx, detected_language,
            self._env_block, clean_input,
        )

        initial_messages = (
            [SystemMessage(content=system_prompt)]
            + self._history
            + [HumanMessage(content=clean_input)]
        )

        state = {
            "messages": initial_messages,
            "user_query": clean_input,
            "language": detected_language,
            "memory_context": memory_ctx,
            "needs_planning": False,
            "response": "",
            "revise_count": 0,
            "critic_verdict": "",
            "critique": "",
        }

        try:
            result = await self._graph.ainvoke(state)
        except Exception as exc:
            msg = str(exc)
            if not self._using_fallback and ("PerDay" in msg or "RequestsPerDay" in msg or "429" in msg):
                print(f"\n[JARVIS] Quota hit — falling back to AI Studio Flash-Lite.")
                self._using_fallback = True
                # Switch graph to AI Studio fallback
                import copy
                fb_settings = copy.copy(self.settings)
                fb_settings.cloud_tier = "aistudio"
                fb_settings.cloud_model = "gemini-2.5-flash-lite"
                self._graph = build_graph(fb_settings, self.workspace, self.memory)
                result = await self._graph.ainvoke(state)
            else:
                raise

        response = result.get("response", "")
        if not response:
            # Fallback: extract from last AI message if critic didn't populate
            from langchain_core.messages import AIMessage
            for m in reversed(result.get("messages", [])):
                if isinstance(m, AIMessage) and isinstance(m.content, str) and m.content.strip():
                    response = m.content
                    break

        # Update history (exclude system message — regenerated each turn)
        all_msgs = result.get("messages", [])
        non_system = [m for m in all_msgs if not isinstance(m, SystemMessage)]
        self._history = _trim_history(non_system)

        self.memory.store("user", clean_input, self.session_id)
        self.memory.store("assistant", response, self.session_id)
        self.memory.log_turn("user", clean_input)
        self.memory.log_turn("assistant", response)

        return response, self.current_model_label

    async def chat_stream(
        self,
        user_input: str,
        detected_language: str = "en",
    ) -> AsyncGenerator[str, None]:
        """Stream one turn token-by-token. Yields text deltas for voice.speak_stream()."""
        clean_input = re.sub(r"^/think\s*", "", user_input).strip()

        memory_ctx = self.memory.recall(clean_input, n=1)
        system_prompt = _load_system_prompt(
            self.settings, memory_ctx, detected_language,
            self._env_block, clean_input,
        )

        initial_messages = (
            [SystemMessage(content=system_prompt)]
            + self._history
            + [HumanMessage(content=clean_input)]
        )

        state = {
            "messages": initial_messages,
            "user_query": clean_input,
            "language": detected_language,
            "memory_context": memory_ctx,
            "needs_planning": False,
            "response": "",
            "revise_count": 0,
            "critic_verdict": "",
            "critique": "",
        }

        chunks: list[str] = []
        config: dict = {}

        async for delta in graph_stream_to_text(self._graph, state, config):
            chunks.append(delta)
            yield delta

        full_response = "".join(chunks)

        # Rebuild history from non-streaming invoke to capture tool messages
        # (streaming only captures text chunks; do a quick non-streaming pass for history)
        # For simplicity in Faz 1: rebuild history with the known response appended
        from langchain_core.messages import AIMessage
        non_system = [m for m in initial_messages if not isinstance(m, SystemMessage)]
        non_system.append(AIMessage(content=full_response))
        self._history = _trim_history(non_system)

        self.memory.store("user", clean_input, self.session_id)
        self.memory.store("assistant", full_response, self.session_id)
        self.memory.log_turn("user", clean_input)
        self.memory.log_turn("assistant", full_response)
