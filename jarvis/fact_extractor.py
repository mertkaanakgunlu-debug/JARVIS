"""Async fact-triple extractor for JARVIS (Faz 2 — semantic memory).

Sends a single Flash-Lite structured-output call after each turn to extract
durable, cross-session-relevant facts (stable preferences, relationships,
recurring constraints) from the conversation exchange — deliberately NOT
ephemeral task-local detail (today's weather, a one-off file path, a single
task's intermediate result). Results are deduped/consolidated by the caller
via embedding similarity (see jarvis.memory.Memory.find_similar_fact) before
being upserted into the facts table.

Design: fire-and-forget — never blocks the main chat response. Mirrors
jarvis/entity_extractor.py's shape closely; kept as a separate module (rather
than folded into entity extraction) because the extraction target is a
different, orthogonal concept — an entity is "what/who was mentioned", a fact
is "what is durably true" — and the two prompts would otherwise fight each
other inside one structured-output call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from jarvis.config import Settings


class Fact(BaseModel):
    subject: str
    predicate: str
    object: str
    fact_text: str  # natural-language rendering, e.g. "User prefers dim lighting in the evenings."


class FactList(BaseModel):
    facts: list[Fact]


_PROMPT_TEMPLATE = (
    "Extract durable, long-term-relevant facts from this single conversation exchange.\n"
    "A fact is a stable preference, relationship, constraint, or attribute that would still "
    "be true and worth remembering weeks from now.\n"
    "Rules:\n"
    "- DO extract: stated preferences ('user prefers X'), relationships ('X works at Y'), "
    "recurring constraints ('user is allergic to X'), durable attributes ('user's project is X').\n"
    "- do NOT extract: one-off task details, today's weather/time, a single file path, "
    "intermediate results of a computation, or anything only relevant to this exchange.\n"
    "- subject/predicate/object: short, normalized (e.g. subject='user', predicate='prefers', "
    "object='dim evening lighting').\n"
    "- fact_text: one short natural-language sentence rendering the triple.\n"
    "- Return facts: [] if nothing durable is stated.\n\n"
    "User: {user_text}\n\n"
    "Assistant: {response_text}"
)


async def extract_facts(
    user_text: str,
    response_text: str,
    settings: "Settings",
) -> list[Fact]:
    """Return facts extracted from one exchange. Returns [] on any error.

    Not yet migrated onto jarvis/providers/get_llm() -- see the stabilization
    sprint's report -- so it gets its own CLOUD_POLICY gate instead.
    """
    from jarvis.providers import cloud_extractors_enabled, note_degraded
    if not cloud_extractors_enabled(settings):
        note_degraded("fact_extractor")
        return []
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI

        api_key = settings.gemini_api_key or None
        llm = ChatGoogleGenerativeAI(
            model=getattr(settings, "triage_model", "gemini-2.5-flash"),
            google_api_key=api_key,
            max_output_tokens=512,
        ).with_structured_output(FactList)

        prompt = _PROMPT_TEMPLATE.format(
            user_text=user_text[:1000],
            response_text=response_text[:1500],
        )
        result: FactList = await llm.ainvoke(prompt)
        return result.facts if result else []
    except Exception:
        return []
