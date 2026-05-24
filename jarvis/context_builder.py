"""Context builder — extracts and formats memory/entity/todo blocks for system prompt injection.

Eliminates the duplicated 5-call retrieval block that previously lived inline
in both JarvisAgent.chat() and JarvisAgent.chat_stream().

Usage:
    cb = ContextBuilder(memory, todo_store, session_store)
    ctx = cb.build(user_query)
    # ctx.memory_ctx, ctx.past_sessions_block, ctx.entities_block, ctx.open_todos_block
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MemoryPolicy:
    recall_n: int = 3            # how many memory chunks to pull per turn
    recall_summaries_n: int = 3  # how many past session summaries to pull


@dataclass
class ContextData:
    memory_ctx: str
    past_sessions_block: str
    entities_block: str
    open_todos_block: str


class ContextBuilder:
    """Encapsulates the 5-call memory retrieval pipeline used every turn."""

    def __init__(self, memory, todo_store, session_store, policy: MemoryPolicy | None = None):
        self._memory = memory
        self._todo_store = todo_store
        self._session_store = session_store
        self.policy = policy or MemoryPolicy()

    def build(self, query: str) -> ContextData:
        """Run all retrieval calls and return formatted context blocks."""
        memory_ctx = self._memory.recall(query, n=self.policy.recall_n)
        past_hits = self._memory.recall_summaries(query, n=self.policy.recall_summaries_n)
        return ContextData(
            memory_ctx=memory_ctx,
            past_sessions_block=self._format_past_sessions(past_hits),
            entities_block=self._format_entities(),
            open_todos_block=self._format_todos(),
        )

    # ── Formatters (extracted from JarvisAgent private methods) ─────────────────

    def _format_past_sessions(self, hits: list[dict]) -> str:
        if not hits:
            return "(no relevant past sessions)"
        lines = []
        for h in hits:
            topic = h.get("topic_hint") or "(no topic)"
            when = h.get("last_active", "")[:10]
            lines.append(f"- **{when} · {topic}** (id={h['session_id']}):\n  {h['summary']}")
        return "\n".join(lines)

    def _format_entities(self, n: int = 5) -> str:
        entities = self._session_store.top_entities(n=n)
        if not entities:
            return "(none yet)"
        return "\n".join(
            f"- **{e['name']}** ({e['type']}): {e.get('description') or 'mentioned in conversation'}"
            for e in entities
        )

    def _format_todos(self, n: int = 5) -> str:
        try:
            todos = self._todo_store.top_open(n=n)
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
