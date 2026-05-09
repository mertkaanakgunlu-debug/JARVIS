"""LangGraph node functions for JARVIS — Faz 2.

Nodes:
  agent_node   — main ReAct executor (Gemini Flash / Vertex Flash)
  planner_node — step-by-step plan generation (Gemini Pro, activated by /think)
  critic_node  — quality scoring (Gemini Pro, max 2 revision loops)

Routing:
  route_from_start  — START → planner (needs_planning) or agent
  route_from_agent  — tool calls → tools, else → critic
  route_from_critic — accept/exhausted → END, revise/redirect → agent
"""

from __future__ import annotations

import json
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from jarvis.graph.state import JarvisState


# ── Prompt constants ───────────────────────────────────────────────────────────

_PLANNER_SYSTEM = """\
You are JARVIS's planning module. For complex tasks, produce a concise execution plan.

Output plain text only — no JSON, no markdown headers, no preamble:
Step 1: [specific action; mention tools when applicable]
Step 2: ...

Rules:
- Maximum 8 steps
- Reference tools by name: shell_run, file_read, file_write, pdf_read, excel_read,
  python_run, web_search, math_solve, generate_code, write_content, etc.
- Be specific and actionable; skip trivial steps like "understand the request"\
"""

_CRITIC_SYSTEM = """\
You are a quality critic for JARVIS, an AI assistant. Evaluate whether JARVIS's response
fully and correctly answers the user's query.

Output ONLY valid JSON — no markdown, no explanation before or after:
{"score": <integer 0-10>, "verdict": "accept", "critique": ""}
{"score": <integer 0-10>, "verdict": "revise", "critique": "<specific improvement needed>"}
{"score": <integer 0-10>, "verdict": "redirect", "critique": "<what was misunderstood>"}

Scoring guide:
- 7-10: Good or excellent → accept
- 5-6: Adequate but incomplete or slightly off → revise
- 0-4: Wrong, fundamentally misunderstood, or severely incomplete → redirect

Always accept (score >= 7) if:
- The query is conversational, social, or a simple factual question
- The response is a tool execution result or acknowledgment
- The response appropriately asks for clarification on an ambiguous request
- The response is a short, direct answer (under 30 words)

Never accept if:
- The response ignores the user's actual question
- Key factual claims appear incorrect
- The response is visibly cut off or unfinished\
"""


# ── Helper ─────────────────────────────────────────────────────────────────────

def _extract_ai_text(state: JarvisState) -> str:
    """Return the last non-empty, non-tool-call AI text from state messages."""
    for msg in reversed(state["messages"]):
        if not isinstance(msg, AIMessage):
            continue
        # Skip messages that are purely tool-call dispatches
        if getattr(msg, "tool_calls", None) and not msg.content:
            continue
        content = msg.content
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            parts = [
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            text = "\n".join(parts).strip()
            if text:
                return text
    return ""


def _is_simple_exchange(user_query: str, response_text: str) -> bool:
    """Return True if this looks like a short conversational exchange.

    Simple exchanges skip the Pro critic call to save cost and latency.
    """
    _COMPLEX_EXTS = {".pdf", ".xlsx", ".xls", ".py", ".tex", ".csv", ".docx", ".json"}
    _COMPLEX_KEYWORDS = {
        "analyze", "analiz", "research", "araştır", "explain", "açıkla",
        "calculate", "hesapla", "compare", "karşılaştır", "write", "yaz",
        "generate", "oluştur", "code", "kod", "summarize", "özetle",
        "report", "rapor", "plan", "implement",
    }
    q_lower = user_query.lower()
    if any(ext in q_lower for ext in _COMPLEX_EXTS):
        return False
    if any(kw in q_lower for kw in _COMPLEX_KEYWORDS):
        return False
    if len(user_query.split()) > 15:
        return False
    # Short response on a simple query → definitely conversational
    return len(response_text.split()) < 40


# ── Nodes ──────────────────────────────────────────────────────────────────────

def make_agent_node(llm_with_tools):
    """Return an async node that calls the LLM with tools bound."""

    async def agent_node(state: JarvisState) -> dict:
        response = await llm_with_tools.ainvoke(state["messages"])
        return {"messages": [response]}

    agent_node.__name__ = "agent_node"
    return agent_node


def make_planner_node(llm_pro):
    """Return a node that generates an execution plan for complex tasks (/think)."""

    async def planner_node(state: JarvisState) -> dict:
        user_query = state.get("user_query", "")
        if not user_query:
            return {"plan": ""}

        try:
            plan_resp = await llm_pro.ainvoke([
                SystemMessage(content=_PLANNER_SYSTEM),
                HumanMessage(content=user_query),
            ])
            plan_text = plan_resp.content
            if isinstance(plan_text, list):
                plan_text = "\n".join(
                    p.get("text", "") for p in plan_text if isinstance(p, dict)
                ).strip()
            else:
                plan_text = (plan_text or "").strip()
        except Exception:
            plan_text = ""

        if plan_text:
            injection = HumanMessage(
                content=(
                    f"[Planning Mode — Execution Plan]\n{plan_text}\n\n"
                    "Execute this plan step by step."
                )
            )
            return {"plan": plan_text, "messages": [injection]}
        return {"plan": ""}

    planner_node.__name__ = "planner_node"
    return planner_node


def make_critic_node(llm_pro):
    """Return a critic node that scores the agent's output with Gemini Pro.

    Fast-path: simple conversational exchanges are auto-accepted without a Pro call.
    Full path: structured JSON verdict {score, verdict, critique} from Gemini Pro.
    Max revisions: 2. After that the critic always accepts.
    """

    async def critic_node(state: JarvisState) -> dict:
        response_text = _extract_ai_text(state)
        revise_count = state.get("revise_count", 0)

        # Fast-path: no response or revision budget exhausted
        if not response_text or revise_count >= 2:
            return {
                "critic_verdict": "accept",
                "critique": "",
                "response": response_text,
                "revise_count": revise_count,
            }

        user_query = state.get("user_query", "")

        # Fast-path: simple conversational exchange — skip Pro call
        if _is_simple_exchange(user_query, response_text):
            return {
                "critic_verdict": "accept",
                "critique": "",
                "response": response_text,
                "revise_count": revise_count,
            }

        # Full path: call Gemini Pro for quality scoring
        try:
            critic_resp = await llm_pro.ainvoke([
                SystemMessage(content=_CRITIC_SYSTEM),
                HumanMessage(
                    content=f"User query: {user_query}\n\nJARVIS response:\n{response_text}"
                ),
            ])
            raw = critic_resp.content
            if isinstance(raw, list):
                raw = "\n".join(p.get("text", "") for p in raw if isinstance(p, dict))

            # Extract JSON — handle markdown code-fence wrapping
            match = re.search(r'\{[^{}]*"verdict"[^{}]*\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
                verdict = str(data.get("verdict", "accept")).lower().strip()
                critique = str(data.get("critique", "")).strip()
                if verdict not in ("accept", "revise", "redirect"):
                    verdict = "accept"
            else:
                verdict, critique = "accept", ""
        except Exception:
            verdict, critique = "accept", ""

        extra_messages: list = []
        new_revise_count = revise_count

        if verdict in ("revise", "redirect"):
            new_revise_count = revise_count + 1
            label = "Revision" if verdict == "revise" else "Redirect"
            extra_messages = [
                HumanMessage(
                    content=(
                        f"[Quality Critic — {label} {new_revise_count}/2]\n"
                        f"{critique}\n\n"
                        "Please revise your response addressing the above feedback."
                    )
                )
            ]

        return {
            "critic_verdict": verdict,
            "critique": critique,
            "response": response_text,
            "revise_count": new_revise_count,
            "messages": extra_messages,
        }

    critic_node.__name__ = "critic_node"
    return critic_node


# ── Routing ────────────────────────────────────────────────────────────────────

def route_from_start(state: JarvisState) -> str:
    """START → planner (if needs_planning) or agent."""
    return "planner" if state.get("needs_planning", False) else "agent"


def route_from_agent(state: JarvisState) -> str:
    """tool calls present → 'tools'; else → 'critic'."""
    last_msg = state["messages"][-1]
    if isinstance(last_msg, AIMessage) and getattr(last_msg, "tool_calls", None):
        return "tools"
    return "critic"


def route_from_critic(state: JarvisState) -> str:
    """accept / exhausted → END; revise/redirect → agent."""
    from langgraph.graph import END
    verdict = state.get("critic_verdict", "accept")
    revise_count = state.get("revise_count", 0)
    if verdict in ("revise", "redirect") and revise_count < 2:
        return "agent"
    return END
