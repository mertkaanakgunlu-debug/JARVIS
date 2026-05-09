"""LangGraph node functions for JARVIS.

Faz 1 nodes:
  agent_node  — main ReAct executor (Gemini Flash or Vertex Flash)
  critic_node — placeholder; always accepts (Faz 2 will add real quality scoring)

Memory recall and system-prompt injection happen in JarvisAgent.chat() before
invoking the graph, keeping the graph itself stateless between turns.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage

from jarvis.graph.state import JarvisState


def make_agent_node(llm_with_tools):
    """Return an async node that calls the LLM with tools bound."""

    async def agent_node(state: JarvisState) -> dict:
        response = await llm_with_tools.ainvoke(state["messages"])
        return {"messages": [response]}

    agent_node.__name__ = "agent_node"
    return agent_node


def critic_node(state: JarvisState) -> dict:
    """Faz 1 placeholder: always accepts executor output.

    Extracts the last non-tool-call AI message as the final response text.
    Faz 2 will replace this with a real Gemini Pro quality-scoring call.
    """
    response_text = ""
    for msg in reversed(state["messages"]):
        if isinstance(msg, AIMessage):
            # Skip messages that are purely tool-call requests (empty or list content)
            content = msg.content
            if isinstance(content, str) and content.strip():
                response_text = content
                break
            if isinstance(content, list):
                text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                if text_parts:
                    response_text = "\n".join(text_parts)
                    break

    return {
        "critic_verdict": "accept",
        "critique": "",
        "response": response_text,
        "revise_count": 0,
    }


def route_from_agent(state: JarvisState) -> str:
    """Route: tool calls present → 'tools' node; otherwise → 'critic' node."""
    last_msg = state["messages"][-1]
    if isinstance(last_msg, AIMessage) and getattr(last_msg, "tool_calls", None):
        return "tools"
    return "critic"
