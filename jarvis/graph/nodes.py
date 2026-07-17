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

import asyncio
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

def make_agent_node(llm_fast_with_tools, llm_pro_with_tools=None, settings=None):
    """Return an async node that picks Flash or Pro based on state["use_pro_agent"].

    Faz 5: if use_pro_agent is True and a Pro model is available, route the agent
    to Gemini Pro for complex queries; otherwise use the fast Flash model.

    BUG-14 (Faz 4): the LLM call is wrapped in a timeout -- previously a
    wedged provider connection hung the whole turn (and, in voice mode, left
    JARVIS listening in silence forever) with no way to recover. This is
    distinct from ToolSpec.timeout_seconds, which only bounds tool execution,
    not the agent's own reasoning call.
    """
    timeout_sec = getattr(settings, "agent_llm_timeout_sec", 90.0) if settings is not None else 90.0

    async def agent_node(state: JarvisState) -> dict:
        use_pro = state.get("use_pro_agent", False) and llm_pro_with_tools is not None
        llm = llm_pro_with_tools if use_pro else llm_fast_with_tools
        try:
            response = await asyncio.wait_for(llm.ainvoke(state["messages"]), timeout=timeout_sec)
        except asyncio.TimeoutError:
            response = AIMessage(
                content=(
                    f"I'm sorry, the model didn't respond within {timeout_sec:.0f} seconds -- "
                    "the provider may be unreachable or overloaded. Please try again."
                )
            )

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

        # Revision budget exhausted: stop retrying regardless of quality (BUG-emptyresp:
        # this used to also fire whenever response_text was empty, on ANY revise_count --
        # silently accepting a blank turn as "success" with no retry). If the response
        # is still empty once the budget is exhausted, substitute a visible message
        # instead of ending the turn on nothing.
        if revise_count >= 2:
            return {
                "critic_verdict": "accept",
                "critique": "",
                "response": response_text or (
                    "I wasn't able to generate a response to that. "
                    "Could you rephrase, or try again?"
                ),
                "revise_count": revise_count,
            }

        # Empty response with revision budget remaining: give the agent another
        # attempt instead of accepting nothing. route_from_agent already routed
        # here (not to tools), so an empty AIMessage.content is a genuinely blank
        # final answer, not a legitimate mid-loop state.
        if not response_text:
            new_revise_count = revise_count + 1
            return {
                "critic_verdict": "redirect",
                "critique": "Response was empty.",
                "response": response_text,
                "revise_count": new_revise_count,
                "messages": [
                    HumanMessage(
                        content=(
                            f"[Quality Critic — Redirect {new_revise_count}/2]\n"
                            "Your previous response was empty. Please provide an actual "
                            "answer to the user's query."
                        )
                    )
                ],
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
    """Return a node that gates tool calls behind policy_guard (Faz 4).

    Per-action, not per-tool (BUG-6): policy_guard.evaluate() downgrades pure
    reads on the mixed-risk external_api tools (list/search/... on
    google_calendar/gmail/google_drive/itu_mail) back to no-confirm, so only
    genuinely risky actions ever interrupt.

    Kill switch: policy_guard's allowed=False is a hard veto that skips the
    interrupt entirely (asking permission is pointless once the operator has
    already said stop) and denies immediately, same message shape as a user
    "deny" -- this check runs regardless of confirmation_gate_enabled, since
    the kill switch is a stronger, unconditional stop.

    When confirmation_gate_enabled=False, non-vetoed calls pass straight
    through (no interrupt). When enabled, calls requiring confirmation
    interrupt the graph until resume_and_stream() is called with "approve" or
    "deny" / "deny:<optional guidance>".

    Every risk_level >= 2 call gets a "decision" audit_log entry regardless
    of gate state, so the audit trail is complete even with the gate off.
    """
    from langgraph.types import interrupt as _interrupt
    from langchain_core.messages import AIMessage, ToolMessage, HumanMessage
    from jarvis import audit_log, policy_guard

    async def confirmation_node(state: JarvisState) -> dict:
        last_ai: AIMessage | None = None
        for msg in reversed(state["messages"]):
            if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                last_ai = msg
                break

        if last_ai is None:
            return {"confirmation_result": "approved"}

        transport = state.get("transport") or "unknown"
        is_proactive = transport.startswith("monitor-")
        decisions = {
            tc.get("id"): policy_guard.evaluate(tc.get("name", ""), tc.get("args", {}) or {}, settings)
            for tc in last_ai.tool_calls
        }

        for tc in last_ai.tool_calls:
            d = decisions.get(tc.get("id"))
            if d is None or d.risk_level < 2:
                continue
            if not d.allowed:
                outcome = "blocked_kill_switch"
            elif d.requires_confirmation and settings.confirmation_gate_enabled:
                outcome = "confirm_required"
            else:
                outcome = "auto_approved"
            audit_log.record(
                "decision", tool=d.tool, action=d.action, risk_level=d.risk_level,
                transport=transport, outcome=outcome, reason=d.reason,
            )

        # Kill switch veto -- hard stop, no interrupt, no matter what the
        # gate's own enabled flag says.
        vetoed = [tc for tc in last_ai.tool_calls if not decisions[tc.get("id")].allowed]
        if vetoed:
            veto_reason = decisions[vetoed[0].get("id")].reason
            stub_msgs = [
                ToolMessage(
                    content=f"[BLOCKED: {veto_reason}]",
                    tool_call_id=tc.get("id", ""),
                )
                for tc in last_ai.tool_calls
            ]
            ack_msg = HumanMessage(
                content=(
                    f"The kill switch is currently off ({veto_reason}), so this action was "
                    "blocked before it could run. Do NOT retry it. Tell the user the kill "
                    "switch needs to be re-enabled first."
                )
            )
            return {"confirmation_result": "denied", "messages": stub_msgs + [ack_msg]}

        # Stabilization sprint -- --profile test's structural guarantee.
        # Same hard-stop shape as the kill switch above (no interrupt, no
        # execution, no matter what confirmation_gate_enabled says), narrower
        # in scope: only calls that actually WRITE externally (gmail send,
        # calendar create/delete, Drive upload/share/delete, ...) -- local
        # writes/shell/python stay reachable so tool-calling itself remains
        # testable under the profile. Patch 1.1: keyed on the per-CALL
        # PolicyDecision.side_effect_type, not the static ToolSpec -- the
        # mixed read/write tools (gmail/calendar/drive/itu_mail) are spec'd
        # external_write wholesale, and the old static check denied their
        # read-only actions (gmail read, calendar list) too, making the test
        # profile unable to exercise exactly the read paths it should.
        if not getattr(settings, "external_writes_enabled", True):
            blocked = [
                tc for tc in last_ai.tool_calls
                if decisions[tc.get("id")].side_effect_type == "external_write"
            ]
            if blocked:
                for tc in blocked:
                    d = decisions[tc.get("id")]
                    audit_log.record(
                        "decision", tool=d.tool, action=d.action, risk_level=d.risk_level,
                        transport=transport, outcome="blocked_external_writes_disabled",
                        reason="EXTERNAL_WRITES_ENABLED=false",
                    )
                stub_msgs = [
                    ToolMessage(
                        content="[BLOCKED: external writes are disabled in this profile -- this action was not executed]",
                        tool_call_id=tc.get("id", ""),
                    )
                    for tc in last_ai.tool_calls
                ]
                ack_msg = HumanMessage(
                    content=(
                        "External-write actions (send email, create/delete calendar events, "
                        "Drive upload/share/delete, ...) are disabled in this profile. "
                        "Do NOT retry them. Tell the user this ran with external writes off."
                    )
                )
                return {"confirmation_result": "denied", "messages": stub_msgs + [ack_msg]}

        # GPT-5.6 review remediation, Faz 2 (P0) -- "proactive turn not
        # structurally read-only". Faz 7 already discards a genuinely-
        # confirmable (requires_confirmation=True, gate enabled) L3 interrupt
        # into a "needs_confirmation" notification during a proactive turn
        # instead of executing it -- that path is untouched below, still the
        # right UX (see ProactiveOutcome in jarvis/agent.py). The gap this
        # closes: an L2 call with requires_confirmation=False (by tool-spec
        # design, since a human normally just notices it in the transcript)
        # previously sailed straight through as "auto_approved" during a
        # proactive turn too -- with no human watching. Structural block
        # instead of relying on the system prompt telling the model not to
        # (docs/SAFETY.md's "What Faz 7 changed"). Evaluated before the
        # confirmation_gate_enabled short-circuit below on purpose: that flag
        # is a user's interactive-UX preference (skip being asked), not
        # something that should also silence background/proactive safety.
        if is_proactive:
            unsupervised = [
                tc for tc in last_ai.tool_calls
                if decisions[tc.get("id")].risk_level >= 2
                and not (decisions[tc.get("id")].requires_confirmation and settings.confirmation_gate_enabled)
            ]
            if unsupervised:
                for tc in unsupervised:
                    d = decisions[tc.get("id")]
                    audit_log.record(
                        "decision", tool=d.tool, action=d.action, risk_level=d.risk_level,
                        transport=transport, outcome="blocked_proactive_readonly", reason=d.reason,
                    )
                stub_msgs = [
                    ToolMessage(
                        content="[BLOCKED: background/proactive checks are read-only -- this action was not executed]",
                        tool_call_id=tc.get("id", ""),
                    )
                    for tc in last_ai.tool_calls
                ]
                ack_msg = HumanMessage(
                    content=(
                        "This is an automated background check, not a live conversation -- "
                        "mutating or risky tool calls are not permitted here, even ones that "
                        "would normally auto-approve. Do NOT retry them. If this needs the "
                        "user's action, say so in your response instead."
                    )
                )
                return {"confirmation_result": "denied", "messages": stub_msgs + [ack_msg]}

        if not settings.confirmation_gate_enabled:
            return {"confirmation_result": "approved"}

        confirmable = [tc for tc in last_ai.tool_calls if decisions[tc.get("id")].requires_confirmation]
        if not confirmable:
            return {"confirmation_result": "approved"}

        # Interrupt — pauses the graph until resume_and_stream() is called
        tools_info = [
            {
                "name": tc.get("name"), "args": tc.get("args", {}), "id": tc.get("id"),
                "description": policy_guard.describe_call(tc.get("name", ""), tc.get("args", {}) or {}),
            }
            for tc in confirmable
        ]
        decision = _interrupt({"tools": tools_info, "count": len(confirmable)})

        # decision is the value passed to Command(resume=...) on resume
        if isinstance(decision, str) and decision.lower().startswith("deny"):
            guidance = decision[4:].lstrip(":").strip()
            for tc in confirmable:
                d = decisions[tc.get("id")]
                audit_log.record(
                    "decision", tool=d.tool, action=d.action, risk_level=d.risk_level,
                    transport=transport, outcome="user_denied", reason=guidance,
                )
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

        for tc in confirmable:
            d = decisions[tc.get("id")]
            audit_log.record(
                "decision", tool=d.tool, action=d.action, risk_level=d.risk_level,
                transport=transport, outcome="user_approved",
            )
        return {"confirmation_result": "approved"}

    confirmation_node.__name__ = "confirmation_node"
    return confirmation_node


def route_from_confirmation(state: JarvisState) -> str:
    """approved → 'tools'; denied → 'agent' (LLM acknowledges denial)."""
    if state.get("confirmation_result", "approved") == "denied":
        return "agent"
    return "tools"
