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

# Import lazily to avoid circular imports when ws module is not yet initialised
def _bus():
    try:
        from jarvis.ws import event_bus  # noqa: PLC0415
        return event_bus
    except Exception:
        return None


# Tools that hit a cloud LLM (shown as "cloud" kind in the feed)
_CLOUD_TOOLS = {
    "math_solve", "write_content", "research", "generate_code",
    "deep_web_research", "web_search",
}
# Tools that write to the vault/notes
_NOTE_TOOLS = {"note_append", "vault_search", "index_doc"}


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

def make_agent_node(llm_fast_with_tools, llm_pro_with_tools=None):
    """Return an async node that picks Flash or Pro based on state["use_pro_agent"].

    Faz 5: if use_pro_agent is True and a Pro model is available, route the agent
    to Gemini Pro for complex queries; otherwise use the fast Flash model.
    """

    async def agent_node(state: JarvisState) -> dict:
        use_pro = state.get("use_pro_agent", False) and llm_pro_with_tools is not None
        llm = llm_pro_with_tools if use_pro else llm_fast_with_tools
        response = await llm.ainvoke(state["messages"])

        # Emit tool-call events to the HUD telemetry feed
        bus = _bus()
        if bus and getattr(response, "tool_calls", None):
            for tc in response.tool_calls:
                name = tc.get("name", "tool")
                args = tc.get("args", {})
                # Build a short human-readable body: "tool_name → key: value"
                if args:
                    first_key = next(iter(args))
                    val = str(args[first_key])[:60]
                    body = f"{name} → {val}"
                else:
                    body = name
                kind = "cloud" if name in _CLOUD_TOOLS else \
                       "note"  if name in _NOTE_TOOLS  else "tool"
                bus.tool_call(body, kind)

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
    """tool calls present → 'confirmation'; else → 'critic'."""
    last_msg = state["messages"][-1]
    if isinstance(last_msg, AIMessage) and getattr(last_msg, "tool_calls", None):
        return "confirmation"
    return "critic"


def route_from_critic(state: JarvisState) -> str:
    """accept / exhausted → END; revise/redirect → agent."""
    from langgraph.graph import END
    verdict = state.get("critic_verdict", "accept")
    revise_count = state.get("revise_count", 0)
    if verdict in ("revise", "redirect") and revise_count < 2:
        return "agent"
    return END


def make_confirmation_node(settings):
    """Return a node that gates L3 tool calls behind a user interrupt (Phase 3).

    When confirmation_gate_enabled=False (default), the node is a no-op passthrough.
    When enabled, it interrupts the graph before any tool call that has
    requires_confirmation=True in TOOL_SPECS.  The resume value must be either
    "approve" or "deny" / "deny:<optional guidance>".
    """
    from langgraph.types import interrupt as _interrupt
    from langchain_core.messages import AIMessage, ToolMessage, HumanMessage
    from jarvis.tool_registry import get_spec

    async def confirmation_node(state: JarvisState) -> dict:
        # Gate disabled — immediate passthrough
        if not settings.confirmation_gate_enabled:
            return {"confirmation_result": "approved"}

        # Find last AI message with tool calls
        last_ai: AIMessage | None = None
        for msg in reversed(state["messages"]):
            if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                last_ai = msg
                break

        if last_ai is None:
            return {"confirmation_result": "approved"}

        # Only interrupt for tools explicitly marked requires_confirmation
        confirmable = [
            tc for tc in last_ai.tool_calls
            if (spec := get_spec(tc.get("name", ""))) and spec.requires_confirmation
        ]
        if not confirmable:
            return {"confirmation_result": "approved"}

        # Interrupt — pauses the graph until resume_and_stream() is called
        tools_info = [
            {"name": tc.get("name"), "args": tc.get("args", {}), "id": tc.get("id")}
            for tc in confirmable
        ]
        decision = _interrupt({"tools": tools_info, "count": len(confirmable)})

        # decision is the value passed to Command(resume=...) on resume
        if isinstance(decision, str) and decision.lower().startswith("deny"):
            guidance = decision[4:].lstrip(":").strip()
            # Inject stub ToolMessages so LangGraph state is valid, then route to agent
            stub_msgs = [
                ToolMessage(
                    content="[DENIED by user — action not authorized]",
                    tool_call_id=tc.get("id", ""),
                )
                for tc in last_ai.tool_calls
            ]
            ack_msg = HumanMessage(
                content=(
                    "Your last tool call(s) were denied by the user."
                    + (f" Reason: {guidance}." if guidance else "")
                    + " Do NOT retry them. Acknowledge that the action was not executed."
                )
            )
            return {
                "confirmation_result": "denied",
                "messages": stub_msgs + [ack_msg],
            }

        return {"confirmation_result": "approved"}

    confirmation_node.__name__ = "confirmation_node"
    return confirmation_node


def route_from_confirmation(state: JarvisState) -> str:
    """approved → 'tools'; denied → 'agent' (LLM acknowledges denial)."""
    if state.get("confirmation_result", "approved") == "denied":
        return "agent"
    return "tools"
