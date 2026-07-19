"""Context builder — extracts and formats memory/entity/todo blocks for system prompt injection.

Eliminates the duplicated retrieval block that previously lived inline in both
JarvisAgent.chat() and JarvisAgent.chat_stream(). Faz 2 added facts_block
(semantic memory) and procedure_block (procedural memory) alongside the
original four.

Usage:
    cb = ContextBuilder(memory, todo_store, session_store)
    ctx = cb.build(user_query, session_id=session_id)
    # ctx.memory_ctx, ctx.past_sessions_block, ctx.entities_block,
    # ctx.open_todos_block, ctx.facts_block, ctx.procedure_block
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MemoryPolicy:
    recall_n: int = 3            # how many memory chunks to pull per turn
    recall_summaries_n: int = 3  # how many past session summaries to pull
    recall_facts_n: int = 5      # how many semantic-memory facts to pull (Faz 2)
    # See jarvis/memory.py's comment above find_similar_fact for why these are
    # this loose — calibrated against the default-ONNX-EF fallback, not just
    # the best-case Ollama embedder.
    facts_distance_max: float = 1.1     # Faz 2
    procedure_distance_max: float = 1.0  # Faz 2


@dataclass
class ContextData:
    memory_ctx: str
    past_sessions_block: str
    entities_block: str
    open_todos_block: str
    facts_block: str
    procedure_block: str


class ContextBuilder:
    """Encapsulates the 5-call memory retrieval pipeline used every turn."""

    def __init__(self, memory, todo_store, session_store, policy: MemoryPolicy | None = None):
        self._memory = memory
        self._todo_store = todo_store
        self._session_store = session_store
        self.policy = policy or MemoryPolicy()

    def build(self, query: str, session_id: str | None = None) -> ContextData:
        """Run all retrieval calls and return formatted context blocks.

        session_id (Faz 2): scopes episodic recall (memory_ctx) to this
        session only, so raw past turns from other sessions never leak into
        the current one. Facts and past-session summaries stay cross-session
        by design — that's the whole point of those two layers.
        """
        memory_ctx = self._memory.recall(query, n=self.policy.recall_n, session_id=session_id)
        past_hits = self._memory.recall_summaries(query, n=self.policy.recall_summaries_n)
        fact_hits = self._memory.recall_facts(
            query, n=self.policy.recall_facts_n, distance_max=self.policy.facts_distance_max,
        )
        procedure_hits = self._memory.recall_procedures(
            query, n=1, distance_max=self.policy.procedure_distance_max,
        )
        return ContextData(
            memory_ctx=memory_ctx,
            past_sessions_block=self._format_past_sessions(past_hits),
            entities_block=self._format_entities(),
            open_todos_block=self._format_todos(),
            facts_block=self._format_facts(fact_hits),
            procedure_block=procedure_hits[0]["body"] if procedure_hits else "",
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

    def _format_facts(self, hits: list[dict]) -> str:
        # Lazy import, matching fact_extractor's own providers import: keeps
        # this module light for tests that stub the stores.
        from jarvis.providers import degraded_features

        degraded = "fact_extractor" in degraded_features()
        if not hits:
            if degraded:
                # 2026-07-19 (reviewer's G17b contract): "(none yet)" is
                # indistinguishable from "extraction never ran", and the model
                # filled that ambiguity by INVENTING personal facts (a city).
                # When extraction is degraded, say so explicitly and carry the
                # no-guessing directive right next to the empty block.
                return (
                    "(MEMORY EXTRACTION UNAVAILABLE this session — long-term "
                    "fact storage is not running, so facts the user told you "
                    "before may be missing here. If asked about a personal "
                    "detail that is not in this context, say you cannot access "
                    "your records right now — do NOT guess or invent one.)"
                )
            return "(none yet)"
        lines = [f"- {h['fact_text']}" for h in hits]
        if degraded:
            lines.append(
                "- (note: memory extraction is currently UNAVAILABLE — this "
                "list may be incomplete; do not guess personal details beyond it)"
            )
        return "\n".join(lines)

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
