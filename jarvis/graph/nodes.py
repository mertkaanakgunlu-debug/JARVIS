"""LangGraph node functions for JARVIS — Faz 2 (topology reworked Sprint 2/Faz 2B).

Nodes:
  agent_node   — tool-issuing executor; binds the TURN-SCOPED subset from
                 state["tool_route"] (Faz 2A), bare for conversation turns
  compose_node — BARE final-answer composer (Faz 2B) — no tool schemas bound,
                 consumes critic feedback ephemerally
  planner_node — step-by-step plan generation (activated by /think)
  critic_node  — quality scoring (max 2 revision loops; feedback via state only)

Routing:
  route_from_start             — START → planner (needs_planning) or agent
  route_from_agent             — tool calls → confirmation, else → critic
  route_after_tool_accounting  — budgeted: compose (default) or agent (multi-step)
  route_from_critic            — accept/exhausted → END, revise/redirect → compose
"""

from __future__ import annotations

import asyncio
import json
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from jarvis.graph.state import JarvisState
from jarvis.graph.tool_router import ToolRoute  # Faz 1.4: history-echo guard reads the turn's route

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

def make_agent_node(tools: list, settings=None):
    """Return an async node that binds a TURN-SCOPED tool subset (Faz 2A).

    Pre-2A this received two pre-bound models carrying all ~36 schemas; the
    live rounds showed the local model collapsing under exactly that load
    (raw-JSON-as-text, answer echoing) while handling a handful of tools
    fine. Now the node resolves state["tool_route"] → subset → a per-(role,
    subset) model composed on demand via get_llm() (binding is a client-side
    wrapper — no network) and cached for the process's life. conversation
    routes get a BARE model (zero schemas); a missing route (background
    paths, old checkpoints) falls back to the full pre-2A toolset.

    BUG-14 (Faz 4): the LLM call is wrapped in a timeout -- previously a
    wedged provider connection hung the whole turn (and, in voice mode, left
    JARVIS listening in silence forever) with no way to recover. This is
    distinct from ToolSpec.timeout_seconds, which only bounds tool execution,
    not the agent's own reasoning call.
    """
    from jarvis.providers import get_llm
    from jarvis.graph.tool_router import ToolRoute, select_tool_names

    timeout_sec = getattr(settings, "agent_llm_timeout_sec", 90.0) if settings is not None else 90.0
    tools_by_name = {t.name: t for t in tools}
    all_names = list(tools_by_name)
    _bound_cache: dict[tuple[str, tuple[str, ...]], object] = {}

    def _llm_for(role: str, subset_names: list[str]):
        key = (role, tuple(subset_names))
        if key not in _bound_cache:
            subset = [tools_by_name[n] for n in subset_names if n in tools_by_name]
            _bound_cache[key] = get_llm(role, settings, tools=subset or None)
        return _bound_cache[key]

    async def agent_node(state: JarvisState) -> dict:
        role = "reasoning" if state.get("use_pro_agent", False) else "fast"
        route = ToolRoute.from_dict(state.get("tool_route"))
        subset_names = select_tool_names(route, all_names)
        llm = _llm_for(role, subset_names)
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


def make_compose_node(settings=None):
    """Tool-free response composer (Faz 2B).

    Produces the final user-facing answer from the turn's transcript with a
    BARE model — no tool schemas bound, so it structurally cannot re-issue
    the call it just watched succeed (live incident F16: agent→procedure_save
    →agent→procedure_save… ×10 until the recursion limit). Critic feedback is
    consumed here as a node-local SystemMessage appended to THIS invocation
    only — it is never returned into graph state, so no fake user/system
    turns leak into the transcript (external review, item 9).
    """
    from jarvis.providers import get_llm

    timeout_sec = getattr(settings, "agent_llm_timeout_sec", 90.0) if settings is not None else 90.0
    _bare_cache: dict[str, object] = {}

    def _bare(role: str):
        if role not in _bare_cache:
            _bare_cache[role] = get_llm(role, settings)
        return _bare_cache[role]

    async def compose_node(state: JarvisState) -> dict:
        role = "reasoning" if state.get("use_pro_agent", False) else "fast"
        llm = _bare(role)
        invocation = list(state["messages"])

        # Faz 1.4 — history-echo / claim-on-failure guard. The bare composer can
        # echo a PRIOR turn's "dosyayı oluşturdum" answer (it survives in the
        # compacted history) with no tool running this turn, or gloss a tool that
        # FAILED this turn as success (the B6 hallucination). completed_tool_
        # fingerprints is reset per turn (agent.py), so an empty one on a tool-
        # routed turn means nothing actually succeeded now. Forbid any completion
        # claim in that case. Node-local SystemMessage only — never returned into
        # graph state (same discipline as the critic feedback below).
        route = ToolRoute.from_dict(state.get("tool_route"))
        expected_tool = route is not None and route.primary_domain != "conversation"
        nothing_succeeded = not (state.get("completed_tool_fingerprints") or [])
        if expected_tool and nothing_succeeded:
            invocation.append(SystemMessage(content=(
                "No tool completed successfully in THIS turn. Do not state or imply "
                "that any action (file created, email sent, chart drawn, command run, "
                "calendar event added, etc.) was carried out or succeeded — earlier "
                "turns in the history do NOT count as this turn's result. If a tool "
                "failed, report the failure plainly; if none ran, either the request "
                "still needs a tool call or you must ask the user for what's missing. "
                "Never present a previous turn's success as if it happened now."
            )))

        critique = (state.get("critique") or "").strip()
        if critique and state.get("critic_verdict") in ("revise", "redirect"):
            invocation.append(SystemMessage(content=(
                "Revise your previous draft using this critique; reply with the "
                f"improved answer only, never mention the critique: {critique}"
            )))
        try:
            response = await asyncio.wait_for(llm.ainvoke(invocation), timeout=timeout_sec)
        except asyncio.TimeoutError:
            response = AIMessage(
                content=(
                    f"I'm sorry, the model didn't respond within {timeout_sec:.0f} seconds -- "
                    "the provider may be unreachable or overloaded. Please try again."
                )
            )
        text = response.content if isinstance(response.content, str) else str(response.content)
        return {"messages": [response], "response": text}

    compose_node.__name__ = "compose_node"
    return compose_node


def make_route_after_tool_accounting(settings=None):
    """tool_result_accounting → 'compose' | 'agent' (Faz 2B).

    Budgeted, deterministic — replaces the pre-2B unconditional tools→agent
    edge (the structural cause of F16's loop) without collapsing every task
    to a single tool round (the external review's objection to a blanket
    tools→compose): multi-step-shaped turns (planner path, or a multi-domain
    route like "PDF'teki toplantıları takvime ekle") may re-enter the agent
    while the round budget lasts; everything else composes the answer.
    """
    max_rounds = getattr(settings, "max_tool_rounds_per_turn", 2) if settings is not None else 2

    def route_after_tool_accounting(state: JarvisState) -> str:
        from jarvis.graph.tool_accounting import last_round_results

        if int(state.get("tool_rounds") or 0) >= max_rounds:
            return "compose"
        outcomes = last_round_results(state)
        if not outcomes:
            return "compose"
        any_ok = any(ok for ok, _ in outcomes)
        if any_ok:
            route = state.get("tool_route") or {}
            multi_step = bool(state.get("needs_planning")) or len(route.get("domains") or []) > 1
            return "agent" if multi_step else "compose"
        # nothing succeeded: one more round only if EVERY failure is
        # transient (safe_tools' retryable=true) — identical-args retries are
        # still blocked upstream by the Faz 1B fingerprint dedup, so a retry
        # round must change something to execute at all.
        if all(retryable for _, retryable in outcomes):
            return "agent"
        return "compose"

    return route_after_tool_accounting


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

        # Empty response with revision budget remaining: give the composer
        # another attempt instead of accepting nothing. Faz 2B: the critique
        # travels ONLY in state — compose_node consumes it as a node-local
        # SystemMessage; nothing is injected into the transcript anymore
        # (the old fake "[Quality Critic — Redirect]" HumanMessage polluted
        # history as a user turn that never happened).
        if not response_text:
            return {
                "critic_verdict": "redirect",
                "critique": (
                    "The previous response was empty. Provide an actual answer "
                    "to the user's query."
                ),
                "response": response_text,
                "revise_count": revise_count + 1,
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

        # Faz 2B: no transcript injection — verdict/critique ride in state and
        # compose_node applies them ephemerally (external review, item 9).
        new_revise_count = revise_count + 1 if verdict in ("revise", "redirect") else revise_count

        return {
            "critic_verdict": verdict,
            "critique": critique,
            "response": response_text,
            "revise_count": new_revise_count,
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
    """accept / exhausted → END; revise/redirect → compose (Faz 2B).

    Revisions regenerate through the BARE composer, never back through the
    tool-bound agent — a revision is a wording problem, not a reason to give
    the model another shot at issuing tool calls.
    """
    from langgraph.graph import END
    verdict = state.get("critic_verdict", "accept")
    revise_count = state.get("revise_count", 0)
    if verdict in ("revise", "redirect") and revise_count < 2:
        return "compose"
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
    from jarvis import audit_log, policy_guard, tool_trace
    from jarvis.graph.tool_accounting import tool_call_fingerprint

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

        # ── Patch 1.2 (Faz 1B): deterministic pre-gate, BEFORE policy audit ──
        # LLM-independent limits (live incident A2: ~20 hallucinated calls,
        # 2 email sends, off a one-line smalltalk turn). Router-independent by
        # design: even if Faz 2's capability router misroutes, these hold.
        # Check order per the external review: (1) batch size (2) turn budget
        # (3) duplicate fingerprints (4) policy/risk/confirmation below.
        batch = list(last_ai.tool_calls)
        batch_fps = [
            tool_call_fingerprint(tc.get("name", ""), tc.get("args") or {})
            for tc in batch
        ]
        rich = {  # measurement fields on every block record (A2 stays quantifiable)
            "batch_size": len(batch),
            "turn_attempted_count": int(state.get("tool_calls_attempted") or 0) + len(batch),
            "unique_tool_count": len({tc.get("name", "") for tc in batch}),
            "external_write_count": sum(
                1 for tc in batch
                if decisions[tc.get("id")].side_effect_type == "external_write"
            ),
        }
        # Counters advance on EVERY processed batch — blocked ones included
        # ("attempted" deliberately counts blocked calls; rounds count any
        # batch the agent produced). Applied only via a completed return, so
        # a confirmation interrupt+resume can't double-count.
        counter_updates = {
            "tool_calls_attempted": rich["turn_attempted_count"],
            "tool_rounds": int(state.get("tool_rounds") or 0) + 1,
        }

        def _reject_batch(outcome: str, stub_text: str, ack_text: str, *, per_call_stubs: list[ToolMessage] | None = None) -> dict:
            """Whole-batch refusal: executing 'just the safe part' of an
            over-limit or duplicate-bearing batch would be guessing which part
            of a hallucination was safe. Same stub+ack shape as the kill-switch
            path so LangGraph state stays valid and the agent must acknowledge."""
            audit_log.record("decision", tool="*batch*", action="", risk_level=0,
                             transport=transport, outcome=outcome, reason=ack_text[:120], **rich)
            stubs = per_call_stubs or [
                ToolMessage(content=stub_text, tool_call_id=tc.get("id", "")) for tc in batch
            ]
            return {
                "confirmation_result": "denied",
                "messages": stubs + [HumanMessage(content=ack_text)],
                **counter_updates,
            }

        max_batch = getattr(settings, "max_tool_calls_per_ai_message", 4)
        if len(batch) > max_batch:
            return _reject_batch(
                "blocked_batch_limit",
                "[BLOCKED: tool-call batch over limit -- nothing in this batch was executed]",
                f"You issued {len(batch)} tool calls in one message; the limit is "
                f"{max_batch}. The ENTIRE batch was rejected -- none of it ran. If tools "
                f"are genuinely needed, re-issue at most {max_batch} essential call(s).",
            )

        max_turn = getattr(settings, "max_tool_calls_per_turn", 6)
        already_attempted = int(state.get("tool_calls_attempted") or 0)
        if already_attempted + len(batch) > max_turn:
            return _reject_batch(
                "blocked_turn_limit",
                "[BLOCKED: per-turn tool-call budget exhausted -- this call was not executed]",
                f"The tool-call budget for this turn ({max_turn}) is exhausted "
                f"({already_attempted} already attempted). No further tool calls will run "
                "this turn. Answer the user with what you already have.",
            )

        max_rounds = getattr(settings, "max_tool_rounds_per_turn", 2)
        if int(state.get("tool_rounds") or 0) >= max_rounds:
            return _reject_batch(
                "blocked_round_limit",
                "[BLOCKED: tool-round budget exhausted -- this call was not executed]",
                f"You already used {max_rounds} tool round(s) this turn -- the budget is "
                "spent. Do NOT issue more tool calls. Answer the user with what you have.",
            )

        seen_fps = list(state.get("seen_tool_fingerprints") or [])
        completed_fps = set(state.get("completed_tool_fingerprints") or [])
        dup_flags: list[bool] = []
        within_batch: set[str] = set()
        for fp in batch_fps:
            dup_flags.append(fp in seen_fps or fp in within_batch)
            within_batch.add(fp)
        if any(dup_flags):
            per_call = [
                ToolMessage(
                    content=(
                        "[DUPLICATE_TOOL_CALL_BLOCKED] The identical call already "
                        + ("completed successfully" if fp in completed_fps else "was attempted")
                        + " in this turn. Do not retry it."
                    ) if dup else
                    "[SKIPPED: batched with a duplicate call -- re-issue this one alone if still needed]",
                    tool_call_id=tc.get("id", ""),
                )
                for tc, fp, dup in zip(batch, batch_fps, dup_flags)
            ]
            for tc, fp, dup in zip(batch, batch_fps, dup_flags):
                if dup:
                    audit_log.record(
                        "decision", tool=tc.get("name", ""), action="", risk_level=0,
                        transport=transport, outcome="blocked_duplicate_call",
                        reason="identical tool+args already seen this turn", **rich,
                    )
            return _reject_batch(
                "blocked_duplicate_batch",
                "",  # unused -- per_call_stubs given
                "One or more of these tool calls were exact repeats of calls already made "
                "this turn. Repeats never execute. Do NOT retry them; if another call in "
                "the batch was genuinely new, re-issue only that one.",
                per_call_stubs=per_call,
            )

        # Past the pre-gate: these calls now reach the policy layer, so their
        # fingerprints become 'seen' (exact repeats are blocked from here on,
        # whether this batch ends up approved, denied or vetoed).
        counter_updates["seen_tool_fingerprints"] = seen_fps + [
            fp for fp in batch_fps if fp not in seen_fps
        ]

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

        # Hard veto -- no interrupt, no matter what the gate's own enabled
        # flag says. Two independent veto paths share this shape (Agent
        # Runtime rev.2, Faz 0 added the second): the kill switch (owner can
        # lift it) and a disabled alpha capability (build-time decision, the
        # kill switch does not touch it) — PolicyDecision.veto_kind tells
        # them apart so the ack message names the right one instead of
        # always blaming the kill switch.
        vetoed = [tc for tc in last_ai.tool_calls if not decisions[tc.get("id")].allowed]
        if vetoed:
            veto_reason = decisions[vetoed[0].get("id")].reason
            veto_kind = decisions[vetoed[0].get("id")].veto_kind
            outcome = "blocked_kill_switch" if veto_kind == "kill_switch" else "blocked_capability_disabled"
            # A veto happens before any tool executes, so the on_tool_start/end
            # callbacks — and with them tool_trace's execution rows — never
            # fire. This policy_decision row is the only structural evidence of
            # the block the eval oracle can read; without it a BLOCKED verdict
            # would have to trust the model's own response text (the exact
            # failure mode the oracle exists to close).
            for tc in vetoed:
                d = decisions[tc.get("id")]
                tool_trace.record(
                    event="policy_decision", tool=d.tool, action=d.action,
                    risk_level=d.risk_level, ok=False, transport=transport,
                    outcome=outcome, reason=veto_reason,
                )
            stub_msgs = [
                ToolMessage(
                    content=f"[BLOCKED: {veto_reason}]",
                    tool_call_id=tc.get("id", ""),
                )
                for tc in last_ai.tool_calls
            ]
            if veto_kind == "kill_switch":
                ack_text = (
                    f"The kill switch is currently off ({veto_reason}), so this action was "
                    "blocked before it could run. Do NOT retry it. Tell the user the kill "
                    "switch needs to be re-enabled first."
                )
            else:
                ack_text = (
                    f"This capability is disabled in this build ({veto_reason}), so this "
                    "action was blocked before it could run. Do NOT retry it — no re-enable "
                    "step exists for the user to take here. Tell the user this action is not "
                    "available."
                )
            ack_msg = HumanMessage(content=ack_text)
            return {"confirmation_result": "denied", "messages": stub_msgs + [ack_msg], **counter_updates}

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
                    # Same reason as the kill-switch veto above: pre-execution
                    # block, no tool callbacks, so the trace row here is the
                    # oracle's only structural block evidence (D12).
                    tool_trace.record(
                        event="policy_decision", tool=d.tool, action=d.action,
                        risk_level=d.risk_level, ok=False, transport=transport,
                        outcome="blocked_external_writes_disabled",
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
                return {"confirmation_result": "denied", "messages": stub_msgs + [ack_msg], **counter_updates}

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
                return {"confirmation_result": "denied", "messages": stub_msgs + [ack_msg], **counter_updates}

        if not settings.confirmation_gate_enabled:
            return {"confirmation_result": "approved", **counter_updates}

        confirmable = [tc for tc in last_ai.tool_calls if decisions[tc.get("id")].requires_confirmation]
        if not confirmable:
            return {"confirmation_result": "approved", **counter_updates}

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
                **counter_updates,
            }

        for tc in confirmable:
            d = decisions[tc.get("id")]
            audit_log.record(
                "decision", tool=d.tool, action=d.action, risk_level=d.risk_level,
                transport=transport, outcome="user_approved",
            )
        return {"confirmation_result": "approved", **counter_updates}

    confirmation_node.__name__ = "confirmation_node"
    return confirmation_node


def route_from_confirmation(state: JarvisState) -> str:
    """approved → 'tools'; denied → 'agent' (LLM acknowledges denial)."""
    if state.get("confirmation_result", "approved") == "denied":
        return "agent"
    return "tools"
