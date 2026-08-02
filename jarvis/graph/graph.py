"""LangGraph StateGraph builder for JARVIS — Faz 2 (topology reworked Sprint 2).

Graph topology (Agent Runtime rev.2, Faz 2 added prepare_execution):
  START → route_from_start → planner (needs_planning=True) → agent
                           → agent (otherwise)
  agent → prepare_execution (tool calls) | critic (direct final answer)
  prepare_execution → confirmation (unconditional -- mints a signed
                       ExecutionRequest per pending call, see nodes.py)
  confirmation → tools (approved) | agent (denied/blocked)
                | END (Faz 6 Part 2 -- bounded-repair budget for invalid
                  tool-call args spent; confirmation_node already composed
                  the final answer)
  tools → tool_result_accounting → compose (default)
                                 → agent   (multi-step shape + round budget left)
  compose → critic
  critic → END (accept or revise_count >= 2)
  critic → compose (revise/redirect, revise_count < 2 — bare regeneration)

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

from jarvis.providers import get_llm
from jarvis.graph.safe_tools import make_safe_tool_node
from jarvis.graph.tool_accounting import make_tool_result_accounting_node
from jarvis.graph.state import JarvisState
from jarvis.graph.nodes import (
    make_agent_node,
    make_compose_node,
    make_verification_node,
    make_confirmation_node,
    make_planner_node,
    make_prepare_execution_node,
    make_critic_node,
    make_route_after_tool_accounting,
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
    extra_tools: list | None = None,
    working_set=None,
):
    """Build and compile the JARVIS LangGraph state machine (Faz 2).

    Args:
        settings:     App settings (model IDs, API keys, etc.)
        workspace:    Current working directory (passed to tools)
        memory:       ChromaDB + vault memory instance
        checkpointer: Optional LangGraph checkpointer (SqliteSaver)
        extra_tools:  Faz 5 -- already-connected MCP tools (JarvisAgent
                      connects these asynchronously via connect_mcp_tools()
                      before rebuilding the graph with them; build_graph()
                      itself stays synchronous, unchanged for every existing
                      caller that doesn't pass this). Dual-layer: appended
                      alongside, never replacing, make_tools()'s ~34 wrappers.
        working_set:  Post-MVP Faz 4 / Faz 5 hazırlığı -- the WorkingSetStore
                      the chart tools mutate. Passed through so the agent's
                      long-lived store IS the tools' store; see make_tools().
    """
    tools = make_tools(workspace, settings, memory, working_set)
    if extra_tools:
        tools = [*tools, *extra_tools]

    # Faz 2A: the agent node now composes its own per-(role, tool-subset)
    # models on demand via get_llm() (see make_agent_node) — nothing binds
    # all ~36 schemas up front anymore. Only the bare reasoning model for
    # critic/planner is still built here. get_llm() keeps handling the
    # bind-before-with_fallbacks ordering internally (RunnableWithFallbacks
    # has no bind_tools — see that module's docstring).
    llm_pro = get_llm("reasoning", settings)                          # bare — critic/planner

    agent_node = make_agent_node(tools, settings)
    prepare_execution_node = make_prepare_execution_node(settings)
    confirmation_node = make_confirmation_node(settings)
    planner_node = make_planner_node(llm_pro)
    critic_node = make_critic_node(llm_pro)
    # Patch 1.2 (Faz 1A): tool-body exceptions become structured [TOOL_ERROR]
    # ToolMessages instead of killing the graph run — see safe_tools.py.
    tools_node = make_safe_tool_node(tools)

    builder = StateGraph(JarvisState)
    builder.add_node("agent", agent_node)
    builder.add_node("prepare_execution", prepare_execution_node)
    builder.add_node("confirmation", confirmation_node)
    builder.add_node("planner", planner_node)
    builder.add_node("tools", tools_node)
    builder.add_node("tool_result_accounting", make_tool_result_accounting_node(settings, workspace))
    builder.add_node("compose", make_compose_node(settings))
    builder.add_node("critic", critic_node)
    # Post-MVP Faz 1: terminal honesty gate. Deliberately a node of its own
    # rather than logic inside compose -- compose is not on every path to
    # END (a conversation turn goes agent -> critic -> END), and a gate that
    # only sees tool turns cannot see the case it exists for. See
    # make_verification_node's docstring.
    builder.add_node("verify", make_verification_node(settings))

    # START → planner (if /think) or directly to agent
    builder.add_conditional_edges(
        START,
        route_from_start,
        {"planner": "planner", "agent": "agent"},
    )
    builder.add_edge("planner", "agent")

    # agent → prepare_execution (tool calls) or critic (final response)
    builder.add_conditional_edges(
        "agent",
        route_from_agent,
        {"prepare_execution": "prepare_execution", "critic": "critic"},
    )
    # prepare_execution → confirmation is unconditional -- it only ever mints
    # ExecutionRequests and signs them, never itself decides approve/deny.
    builder.add_edge("prepare_execution", "confirmation")

    # confirmation → tools (approved) or agent (denied — LLM acknowledges) or
    # END (Agent Runtime rev.2, Faz 6 Part 2 — the bounded-repair budget for
    # invalid tool-call args is spent; confirmation_node already composed
    # the final honest answer, nothing left for the agent/critic to do).
    builder.add_conditional_edges(
        "confirmation",
        route_from_confirmation,
        # END routes through "verify" for the same single-choke-point reason
        # as the critic edge below: confirmation_node's honest answer is
        # code-authored and will never trip the gate, but "every path to END
        # passes verify" has to be literally true or the rollout metric is
        # measured over an unknown fraction of turns.
        {"tools": "tools", "agent": "agent", END: "verify"},
    )
    # Patch 1.2 (Faz 1B): completed-fingerprint/ledger bookkeeping happens
    # AFTER execution -- the only point that knows how a call actually ended.
    builder.add_edge("tools", "tool_result_accounting")

    # Faz 2B: budgeted post-tool routing replaces the unconditional
    # tools→agent edge (F16's structural loop). Multi-step-shaped turns may
    # re-enter the tool-bound agent within the round budget; everything else
    # goes to the BARE composer, which cannot re-issue tool calls at all.
    builder.add_conditional_edges(
        "tool_result_accounting",
        make_route_after_tool_accounting(settings),
        {"compose": "compose", "agent": "agent"},
    )
    builder.add_edge("compose", "critic")

    # critic → compose (revise, bare regeneration) or verify → END
    # (accept / exhausted). route_from_critic itself is unchanged and still
    # returns END; only the destination that value maps to moves, so the
    # single choke point is added without touching the routing decision.
    builder.add_conditional_edges(
        "critic",
        route_from_critic,
        {"compose": "compose", END: "verify"},
    )
    builder.add_edge("verify", END)

    return builder.compile(checkpointer=checkpointer)
