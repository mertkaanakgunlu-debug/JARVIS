"""LangGraph StateGraph builder for JARVIS.

Faz 1 graph topology:
  START → agent ↔ tools → critic → END

The LLM is configured based on CLOUD_TIER:
  vertex   — ChatVertexAI (uses ADC, billing via Vertex credits)
  aistudio / flash / pro  — ChatGoogleGenerativeAI (API key, free/paid tier)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from langchain_core.language_models import BaseChatModel
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode

from jarvis.graph.state import JarvisState
from jarvis.graph.nodes import make_agent_node, critic_node, route_from_agent
from jarvis.graph.tools import make_tools

if TYPE_CHECKING:
    from jarvis.config import Settings
    from jarvis.memory import Memory


def make_llm(settings: "Settings") -> BaseChatModel:
    """Instantiate the right LangChain LLM based on CLOUD_TIER.

    Vertex (credits) → ChatGoogleGenerativeAI with vertexai=True (ADC)
    Anything else    → ChatGoogleGenerativeAI with GEMINI_API_KEY
    """
    from langchain_google_genai import ChatGoogleGenerativeAI

    if settings.use_vertex:
        primary = ChatGoogleGenerativeAI(
            model=settings.vertex_model_fast,
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.google_cloud_region,
            max_output_tokens=4096,
        )
        # Fallback chain: Vertex Flash → AI Studio Flash → AI Studio Flash-Lite
        fallback_flash = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            google_api_key=settings.gemini_api_key or None,
            max_output_tokens=4096,
        )
        fallback_lite = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash-lite",
            google_api_key=settings.gemini_api_key or None,
            max_output_tokens=4096,
        )
        return primary.with_fallbacks([fallback_flash, fallback_lite])

    model_id = settings.effective_cloud_model
    return ChatGoogleGenerativeAI(
        model=model_id,
        google_api_key=settings.gemini_api_key or None,
        max_output_tokens=4096,
    )


def build_graph(settings: "Settings", workspace: Path, memory: "Memory"):
    """Build and compile the JARVIS LangGraph state machine.

    Returns a compiled graph that accepts JarvisState and produces JarvisState.
    The graph is stateless across turns; history is managed by JarvisAgent.
    """
    tools = make_tools(workspace, settings, memory)
    llm = make_llm(settings)
    llm_with_tools = llm.bind_tools(tools)

    agent_node = make_agent_node(llm_with_tools)
    tools_node = ToolNode(tools)

    builder = StateGraph(JarvisState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tools_node)
    builder.add_node("critic", critic_node)

    builder.add_edge(START, "agent")
    builder.add_conditional_edges(
        "agent",
        route_from_agent,
        {"tools": "tools", "critic": "critic"},
    )
    builder.add_edge("tools", "agent")
    builder.add_edge("critic", END)

    return builder.compile()
