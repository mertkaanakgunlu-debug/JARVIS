"""Compose the JARVIS system prompt from modular concern files.

Core files (jarvis/prompts/core/01_persona.md … 06_context_injection.md)
are read in alphabetical order and joined with a blank line between each.
The resulting string is semantically identical to the legacy system.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jarvis.config import LANG_NAMES

_CORE_DIR = Path(__file__).parent / "core"


@dataclass
class PromptContext:
    user_name: str = "User"
    memory_context: str = ""
    entities_block: str = ""
    past_sessions_block: str = ""
    open_todos_block: str = ""
    facts_block: str = ""       # Faz 2 — semantic memory
    procedure_block: str = ""   # Faz 2 — procedural memory
    env_block: str = ""
    detected_language: str = "en"
    user_query: str = ""


def load_system_prompt(ctx: PromptContext) -> str:
    """Assemble and return the rendered system prompt."""
    parts = [
        f.read_text(encoding="utf-8").rstrip("\n")
        for f in sorted(_CORE_DIR.glob("*.md"))
    ]
    raw = "\n\n".join(parts)

    raw = raw.replace("{user_name}", ctx.user_name)
    raw = raw.replace("{memory_context}", ctx.memory_context or "(no prior context retrieved)")
    raw = raw.replace("{entities_block}", ctx.entities_block or "(none yet)")
    raw = raw.replace("{past_sessions_block}", ctx.past_sessions_block or "(no relevant past sessions)")
    raw = raw.replace("{open_todos_block}", ctx.open_todos_block or "(no open tasks)")
    raw = raw.replace("{facts_block}", ctx.facts_block or "(none yet)")
    raw = raw.replace(
        "{procedure_block}",
        ctx.procedure_block or "(no specific workflow matched — proceed normally)",
    )

    raw += ctx.env_block

    if ctx.detected_language != "en":
        lang_name = LANG_NAMES.get(ctx.detected_language[:2], ctx.detected_language)
        raw += (
            f"\n\nIMPORTANT: The user is speaking {lang_name}. "
            f"You MUST respond entirely in {lang_name}."
        )

    return raw
