"""LLM-based to-do analyzer for JARVIS (Faz 13-D).

Uses Gemini Flash with structured output to:
1. Assign Eisenhower-matrix priority to new todos
2. Generate short "how-to" instructions (3–5 steps)
3. Batch re-prioritize all open todos relative to each other

Errors are caught silently — analyzer failures never break the main flow.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from jarvis.config import Settings
    from jarvis.todo_store import TodoStore

logger = logging.getLogger(__name__)


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class TodoAnalysis(BaseModel):
    priority: str = Field(
        description="Eisenhower quadrant: urgent_important | important | urgent | low"
    )
    priority_score: float = Field(
        description="Numeric priority 0.0 (lowest) to 1.0 (highest) for sorting"
    )
    category: str = Field(
        description="work | personal | research | health | finance | other"
    )
    instructions: str = Field(
        description="Short actionable how-to: 3–5 numbered steps in the same language as the title"
    )


class BatchTodoItem(BaseModel):
    id: str
    priority: str
    priority_score: float
    category: str
    instructions: str


class BatchAnalysis(BaseModel):
    todos: list[BatchTodoItem]


# ── Single todo analysis ──────────────────────────────────────────────────────

async def analyze_todo(
    title: str,
    description: str,
    settings: "Settings",
) -> TodoAnalysis | None:
    """Analyze a single new todo and return priority + instructions."""
    from jarvis.providers import cloud_extractors_enabled, note_degraded
    if not cloud_extractors_enabled(settings):
        note_degraded("todo_analyzer")
        return None
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(
            model=getattr(settings, "triage_model", "gemini-2.5-flash"),
            google_api_key=settings.gemini_api_key or None,
        ).with_structured_output(TodoAnalysis)

        prompt = (
            f"You are a personal productivity assistant. Analyze this to-do task and respond in JSON.\n\n"
            f"Task: {title}\n"
            f"Details: {description or '(none)'}\n\n"
            "Classify using the Eisenhower matrix:\n"
            "  urgent_important = do now (deadline + high impact)\n"
            "  important = schedule (high impact, no immediate deadline)\n"
            "  urgent = delegate (low impact but time-sensitive)\n"
            "  low = eliminate or do last\n\n"
            "priority_score: 0.0–1.0 (urgent_important≈0.9, important≈0.7, urgent≈0.5, low≈0.2)\n"
            "instructions: 3–5 numbered steps on HOW to complete this task. "
            "Write in the same language as the title (Turkish if title is Turkish)."
        )
        result = await llm.ainvoke(prompt)
        return result
    except Exception as exc:
        logger.debug("TodoAnalyzer single error: %s", exc)
        return None


# ── Batch re-prioritization ────────────────────────────────────────────────────

async def analyze_all_todos(
    todos: list[dict],
    settings: "Settings",
) -> list[BatchTodoItem] | None:
    """Re-prioritize all open todos relative to each other."""
    if not todos:
        return []
    from jarvis.providers import cloud_extractors_enabled, note_degraded
    if not cloud_extractors_enabled(settings):
        note_degraded("todo_analyzer")
        return None
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            google_api_key=settings.gemini_api_key or None,
        ).with_structured_output(BatchAnalysis)

        todo_list = "\n".join(
            f"- id={t['id']}: {t['title']}"
            + (f" (due: {t['due_date']})" if t.get("due_date") else "")
            + (f" | {t['description']}" if t.get("description") else "")
            for t in todos
        )
        prompt = (
            "Re-prioritize these open to-do items relative to each other.\n"
            "Consider deadlines, impact, and urgency together.\n"
            "For each, provide: id, priority (Eisenhower quadrant), "
            "priority_score (0–1, unique values to allow proper sorting), "
            "category, instructions (3–5 steps, same language as title).\n\n"
            f"Tasks:\n{todo_list}"
        )
        result = await llm.ainvoke(prompt)
        return result.todos
    except Exception as exc:
        logger.debug("TodoAnalyzer batch error: %s", exc)
        return None


# ── High-level helpers ────────────────────────────────────────────────────────

async def analyze_and_save(
    todo_id: str,
    title: str,
    description: str,
    settings: "Settings",
    store: "TodoStore",
) -> None:
    """Analyze a single new todo and persist the result. Fire-and-forget safe."""
    analysis = await analyze_todo(title, description, settings)
    if analysis:
        store.update(
            todo_id,
            priority=analysis.priority,
            priority_score=analysis.priority_score,
            category=analysis.category,
            instructions=analysis.instructions,
        )
        logger.debug("TodoAnalyzer: saved analysis for %s (%s)", todo_id, analysis.priority)


async def reanalyze_all(settings: "Settings", store: "TodoStore") -> int:
    """Batch re-analyze all open todos. Returns count updated."""
    todos = store.list_open()
    if not todos:
        return 0
    results = await analyze_all_todos(todos, settings)
    if not results:
        return 0
    count = 0
    for item in results:
        ok = store.update(
            item.id,
            priority=item.priority,
            priority_score=item.priority_score,
            category=item.category,
            instructions=item.instructions,
        )
        if ok:
            count += 1
    return count
