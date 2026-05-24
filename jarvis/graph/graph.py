"""LangGraph StateGraph builder for JARVIS — Faz 2.

Graph topology:
  START → route_from_start → planner (needs_planning=True) → agent
                           → agent (otherwise)
  agent ↔ tools (tool-call loop)
  agent → critic
  critic → END (accept or revise_count >= 2)
  critic → agent (revise/redirect, revise_count < 2)

LLMs:
  llm_fast — executor (Vertex Flash, fallback AI Studio Flash → Flash-Lite)
  llm_pro  — critic + planner (Vertex Pro or AI Studio Pro)

Checkpointing:
  MemorySaver — in-process, supports both sync and async (ainvoke compatible).
  Cross-turn history is managed by JarvisAgent._history, not the checkpointer.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from langchain_core.language_models import BaseChatModel
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode

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


# ── LLM factories ──────────────────────────────────────────────────────────────

def make_llm_fast(settings: "Settings") -> BaseChatModel:
    """Executor LLM: Vertex Flash with AI Studio fallback chain."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    if settings.use_vertex:
        primary = ChatGoogleGenerativeAI(
            model=settings.vertex_model_fast,
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.google_cloud_region,
            max_output_tokens=4096,
        )
        fallback_flash = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            google_api_key=settings.gemini_api_key or None,
            max_output_tokens=4096,
        )
        return primary.with_fallbacks([fallback_flash])

    model_id = settings.effective_cloud_model
    return ChatGoogleGenerativeAI(
        model=model_id,
        google_api_key=settings.gemini_api_key or None,
        max_output_tokens=4096,
    )


def make_llm_pro(settings: "Settings") -> BaseChatModel:
    """Critic + planner LLM: Vertex Pro or AI Studio Pro."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    if settings.use_vertex:
        return ChatGoogleGenerativeAI(
            model=settings.vertex_model_primary,  # gemini-2.5-pro
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.google_cloud_region,
            max_output_tokens=2048,
        )

    return ChatGoogleGenerativeAI(
        model=settings.cloud_model_pro,  # gemini-2.5-pro
        google_api_key=settings.gemini_api_key or None,
        max_output_tokens=2048,
    )


# ── Checkpointer ───────────────────────────────────────────────────────────────

def make_checkpointer(db_path: Path = None):
    """Return an in-memory checkpointer (MemorySaver).

    MemorySaver supports both sync and async graph invocation (ainvoke),
    which is required when running under FastAPI/uvicorn. Cross-turn
    conversation history is maintained by JarvisAgent._history, not here.
    """
    from langgraph.checkpoint.memory import MemorySaver
    return MemorySaver()


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
    llm_fast = make_llm_fast(settings)
    llm_pro = make_llm_pro(settings)

    llm_fast_with_tools = llm_fast.bind_tools(tools)
    llm_pro_with_tools = llm_pro.bind_tools(tools)   # Faz 5: Pro agent for complex queries

    agent_node = make_agent_node(llm_fast_with_tools, llm_pro_with_tools)
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
