"""Async entity extractor for JARVIS (Faz 12-B).

Sends a single Flash-Lite structured-output call after each turn to extract
named entities (people, projects, files, organizations, topics) from the
conversation exchange. Results are upserted into the entities table.

Design: fire-and-forget — never blocks the main chat response.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from jarvis.config import Settings


class Entity(BaseModel):
    name: str
    type: Literal["person", "project", "file", "organization", "topic"]
    description: str  # 1-sentence, e.g. "user's project manager at Geophysics Inc."


class EntityList(BaseModel):
    entities: list[Entity]


_PROMPT_TEMPLATE = (
    "Extract notable named entities from this single conversation exchange.\n"
    "Rules:\n"
    "- Only extract specific, named items (NOT generic words like 'file', 'user', 'data')\n"
    "- person: named humans mentioned (e.g. 'Ahmet', 'Dr. Smith')\n"
    "- project: named projects, products, codebases (e.g. 'JARVIS', 'Horizon Survey')\n"
    "- file: specific file/document names (e.g. 'deprem_verisi.pdf', 'report_q1.xlsx')\n"
    "- organization: companies, universities, institutions (e.g. 'TPAO', 'MTA')\n"
    "- topic: significant technical/domain topics introduced (e.g. 'seismic inversion')\n"
    "- Return entities: [] if nothing specific is mentioned.\n"
    "- description: write in English, 1 short sentence.\n\n"
    "User: {user_text}\n\n"
    "Assistant: {response_text}"
)


async def extract_entities(
    user_text: str,
    response_text: str,
    settings: "Settings",
) -> list[Entity]:
    """Return entities extracted from one exchange. Returns [] on any error."""
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI

        api_key = settings.gemini_api_key or None
        llm = ChatGoogleGenerativeAI(
            model=getattr(settings, "triage_model", "gemini-2.5-flash"),
            google_api_key=api_key,
            max_output_tokens=512,
        ).with_structured_output(EntityList)

        prompt = _PROMPT_TEMPLATE.format(
            user_text=user_text[:1000],
            response_text=response_text[:1500],
        )
        result: EntityList = await llm.ainvoke(prompt)
        return result.entities if result else []
    except Exception:
        return []
