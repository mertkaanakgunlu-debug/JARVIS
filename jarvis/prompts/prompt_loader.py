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


# Rule 6 of core/02_tool_policy.md, chosen by surface.
#
# The voice text was applied UNCONDITIONALLY until 2026-07-31: every core/*.md
# file is globbed into one prompt, so the CLI, the Electron HUD and the mobile
# app were all told "no markdown — no bold, no headers, no bullet lists, no
# emojis" and could not render a table, a heading or a list. That rule is right
# for speech, where "##" is read aloud as noise, and wrong everywhere else — it
# was the single cheapest thing making JARVIS feel worse than it is in text.
_RESPONSE_FORMAT_POLICY = {
    "voice": (
        "**Voice-first language:** Respond in plain, natural spoken language. No markdown "
        "formatting — no `**bold**`, `*italic*`, `##` headers, bullet lists with `- `, or "
        "backticks. No emojis. Write as you would speak: complete sentences, natural rhythm, "
        "no special characters that sound odd when read aloud. Reserve lists and formatting "
        "only for written documents or code output explicitly requested by the user."
    ),
    "text": (
        "**Written formatting:** This answer is read on a screen, so use markdown where it "
        "genuinely helps comprehension — tables for figures and comparisons, `code` and fenced "
        "blocks for paths/commands/output, short bold for a key number, headings only when the "
        "answer is long enough to need them. Prefer prose for anything conversational: a "
        "two-sentence reply should not become a bulleted list. Never format for decoration."
    ),
}

VOICE_SURFACES = ("voice",)


def surface_for_transport(transport: str) -> str:
    """Map a transport id to a prompt surface.

    Voice transports are `voice-cli`, `voice-local` and `voice-remote`; every
    other caller (cli-text, api, api-stream, api-upload, task-async) is read on
    a screen. Derived rather than passed separately so a new transport cannot
    forget to declare itself — and defaults to "text", which is the safe side:
    a spoken markdown header is ugly, a text answer forbidden from using a table
    is worse.
    """
    return "voice" if str(transport or "").startswith("voice") else "text"


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
    surface: str = "text"       # "text" | "voice" — see _RESPONSE_FORMAT_POLICY


def load_system_prompt(ctx: PromptContext) -> str:
    """Assemble and return the rendered system prompt."""
    parts = [
        f.read_text(encoding="utf-8").rstrip("\n")
        for f in sorted(_CORE_DIR.glob("*.md"))
    ]
    raw = "\n\n".join(parts)

    raw = raw.replace(
        "{response_format_policy}",
        _RESPONSE_FORMAT_POLICY.get(ctx.surface, _RESPONSE_FORMAT_POLICY["text"]),
    )
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
