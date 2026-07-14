"""LangGraph StateGraph builder for JARVIS — Faz 2.

Graph topology:
  START → route_from_start → planner (needs_planning=True) → agent
                           → agent (otherwise)
  agent ↔ tools (tool-call loop)
  agent → critic
  critic → END (accept or revise_count >= 2)
  critic → agent (revise/redirect, revise_count < 2)

LLMs (Faz 1 — resolved via jarvis.providers.get_llm(role, settings)):
  fast role      — executor; Ollama local model primary, cloud Flash fallback
                   on invocation error (see jarvis/providers/__init__.py)
  reasoning role — critic + planner + complex-query agent; Vertex Pro if
                   configured, else AI Studio Gemini Flash free tier

Checkpointing:
  SqliteSaver (jarvis_checkpoints.db) — persists across restarts, needed so a
  turn interrupted for confirmation (Phase 3 gate) can be resumed later.
  Cross-turn conversation history is still managed by JarvisAgent._history /
  SessionStore, not the checkpointer — this only covers intra-turn graph state.

  SqliteSaver's own async methods raise NotImplementedError (it's sync-only;
  see langgraph.checkpoint.sqlite). AsyncSqliteSaver isn't a fit either: it
  binds to whichever asyncio loop is running at construction time, but the
  same JarvisAgent singleton is invoked from several independent loops (the
  CLI's/uvicorn's main loop, and TaskExecutor's per-call `asyncio.run()` on a
  background thread) — a loop-bound saver would break across that boundary.
  _AsyncCompatibleSqliteSaver below instead delegates the async methods to
  the *currently running* loop's default executor; the underlying
  sqlite3.Connection (check_same_thread=False) + SqliteSaver's own
  threading.Lock make that safe from any thread.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode

from jarvis.providers import get_llm
from jarvis.graph.state import JarvisState
from jarvis.graph.nodes import (
    make_agent_node,
    make_confirmation_node,
    make_planner_node,
    make_critic_node,
    route_from_start,
    route_from_agent,
    route_from_confirmation,
    route_from_critic,
)
from jarvis.graph.tools import make_tools

if TYPE_CHECKING:
    from jarvis.config import Settings
    from jarvis.memory import Memory


# ── Checkpointer ───────────────────────────────────────────────────────────────

class _AsyncCompatibleSqliteSaver:
    """Mixin: run SqliteSaver's async checkpoint methods on a thread executor.

    Delegates to whichever event loop is *currently running* rather than
    binding to one at construction time — safe when the same saver instance
    is called from multiple independent loops (see module docstring).

    Each method must be a real `async def` (not a plain `def` returning the
    executor's Future) — LangGraph's eager task factory calls
    `asyncio.iscoroutine()` on what these return, which a bare Future fails.
    """

    async def aget_tuple(self, config):
        return await asyncio.get_running_loop().run_in_executor(None, self.get_tuple, config)

    async def alist(
        self,
        config,
        *,
        filter: dict | None = None,
        before=None,
        limit: int | None = None,
    ) -> AsyncIterator:
        loop = asyncio.get_running_loop()
        items: Iterator = await loop.run_in_executor(
            None, lambda: list(self.list(config, filter=filter, before=before, limit=limit))
        )
        for item in items:
            yield item

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await asyncio.get_running_loop().run_in_executor(
            None, self.put, config, checkpoint, metadata, new_versions
        )

    async def aput_writes(self, config, writes, task_id, task_path=""):
        await asyncio.get_running_loop().run_in_executor(
            None, self.put_writes, config, writes, task_id, task_path
        )

    async def adelete_thread(self, thread_id):
        await asyncio.get_running_loop().run_in_executor(None, self.delete_thread, thread_id)


def make_checkpointer(db_path: Path):
    """Return a SQLite-backed checkpointer that persists across restarts.

    See the module docstring for why this is a custom executor-backed
    subclass rather than AsyncSqliteSaver directly.
    """
    import sqlite3
    from langgraph.checkpoint.sqlite import SqliteSaver

    class _Saver(_AsyncCompatibleSqliteSaver, SqliteSaver):
        pass

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    saver = _Saver(conn)
    saver.setup()
    return saver


# ── Graph builder ──────────────────────────────────────────────────────────────

def build_graph(
    settings: "Settings",
    workspace: Path,
    memory: "Memory",
    checkpointer=None,
):
    """Build and compile the JARVIS LangGraph state machine (Faz 2).

    Args:
        settings:     App settings (model IDs, API keys, etc.)
        workspace:    Current working directory (passed to tools)
        memory:       ChromaDB + vault memory instance
        checkpointer: Optional LangGraph checkpointer (SqliteSaver)
    """
    tools = make_tools(workspace, settings, memory)

    # Faz 1: role→provider router (jarvis/providers/) — fast is Ollama-primary
    # with a cloud fallback; reasoning is cloud-first with local as its own
    # last resort. Tools must be bound *before* get_llm() wraps a role in
    # .with_fallbacks() (RunnableWithFallbacks has no bind_tools), so the
    # tool-bound and bare reasoning variants are requested separately rather
    # than binding tools onto the bare one after the fact — see that module's
    # docstring for why the order matters.
    llm_fast_with_tools = get_llm("fast", settings, tools=tools)
    llm_pro = get_llm("reasoning", settings)                          # bare — critic/planner
    llm_pro_with_tools = get_llm("reasoning", settings, tools=tools)   # Faz 5: reasoning agent for complex queries

    agent_node = make_agent_node(llm_fast_with_tools, llm_pro_with_tools, settings)
    confirmation_node = make_confirmation_node(settings)
    planner_node = make_planner_node(llm_pro)
    critic_node = make_critic_node(llm_pro)
    tools_node = ToolNode(tools)

    builder = StateGraph(JarvisState)
    builder.add_node("agent", agent_node)
    builder.add_node("confirmation", confirmation_node)
    builder.add_node("planner", planner_node)
    builder.add_node("tools", tools_node)
    builder.add_node("critic", critic_node)

    # START → planner (if /think) or directly to agent
    builder.add_conditional_edges(
        START,
        route_from_start,
        {"planner": "planner", "agent": "agent"},
    )
    builder.add_edge("planner", "agent")

    # agent → confirmation (tool calls) or critic (final response)
    builder.add_conditional_edges(
        "agent",
        route_from_agent,
        {"confirmation": "confirmation", "critic": "critic"},
    )

    # confirmation → tools (approved) or agent (denied — LLM acknowledges)
    builder.add_conditional_edges(
        "confirmation",
        route_from_confirmation,
        {"tools": "tools", "agent": "agent"},
    )
    builder.add_edge("tools", "agent")

    # critic → agent (revise) or END (accept / exhausted)
    builder.add_conditional_edges(
        "critic",
        route_from_critic,
        {"agent": "agent", END: END},
    )

    return builder.compile(checkpointer=checkpointer)
