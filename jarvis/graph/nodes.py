"""LangGraph node functions for JARVIS — Faz 2 (topology reworked Sprint 2/Faz 2B).

Nodes:
  agent_node             — tool-issuing executor; binds the TURN-SCOPED subset
                            from state["tool_route"] (Faz 2A), bare for
                            conversation turns
  prepare_execution_node — Agent Runtime rev.2, Faz 2: risk-classifies each
                            pending tool call and mints a signed, immutable
                            ExecutionRequest per call BEFORE confirmation sees
                            it (agent → prepare_execution → confirmation)
  compose_node — BARE final-answer composer (Faz 2B) — no tool schemas bound,
                 consumes critic feedback ephemerally
  planner_node — step-by-step plan generation (activated by /think)
  critic_node  — quality scoring (max 2 revision loops; feedback via state only)

Routing:
  route_from_start             — START → planner (needs_planning) or agent
  route_from_agent             — tool calls → prepare_execution, else → critic
  route_after_tool_accounting  — budgeted: compose (default) or agent (multi-step)
  route_from_critic            — accept/exhausted → END, revise/redirect → compose
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from jarvis.execution.context import ExecutionContext
from jarvis.graph.repair_budget import (  # Post-MVP Faz 6: one corrective repair per turn
    claim_budget,
    completion_repair_subset,
    corrective_repair_spent,
    shared_budget_spent,
)
from jarvis.graph.state import JarvisState
from jarvis.graph.tool_router import ToolRoute  # Faz 1.4: history-echo guard reads the turn's route

logger = logging.getLogger(__name__)

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

    Simple exchanges skip the critic's model call to save cost and latency.

    Worth knowing what "not simple" costs, because it is easy to miss: a
    response of 40+ words, a query over 15 words, or one naming a data file or
    a keyword like "analiz"/"hesapla"/"yaz" all take the full path -- and the
    full path always runs on the REASONING tier, whatever tier the turn itself
    is on (see make_critic_node).
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
        # Faz 2.75 (Paket F): the query orders each domain's tools by relevance,
        # so a request naming a .csv does not spend its slots on pdf_vision.
        subset_names = select_tool_names(route, all_names, state.get("user_query") or "")
        if state.get("output_contract_action") == "repair":
            # Post-MVP Faz 6: a completion repair re-enters the agent to
            # produce a MISSING output, so it needs nothing that changes
            # anything else. Structural rather than a "do not modify the
            # source file" line in the directive: the 2×2 experiment ran with
            # file_write in the production subset, and a prompt-level wish is
            # not a control. The cache key already includes the subset, so the
            # narrowed round gets its own binding for free.
            from jarvis.execution.output_contract import first_requirement
            from jarvis.tool_registry import tools_producing

            requirement = first_requirement(state.get("required_outputs"))
            subset_names = completion_repair_subset(
                subset_names,
                tools_producing(*requirement) if requirement else frozenset(),
            )
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


# Post-MVP Faz 1 helpers for compose_node's unbacked-claim gate.

# LangChain tag on the bounded repair round's LLM call. Defence in depth:
# graph_stream_to_text already filters by node name and "verify" is not in
# its allow-list, so today this tag is a second lock on the same door -- but
# the gate lived inside compose_node for part of its life, where the node
# filter did NOT separate them (same node, same langgraph_step, so BUG-12's
# step-boundary rule could not tell the discarded draft from its
# replacement). Keeping the tag means moving this logic again cannot
# silently reintroduce spliced output.
#
# Honest limit neither lock fixes: the DRAFT has already streamed by the time
# the gate runs, so on a streaming transport an enforce-mode block corrects
# the final state and the saved history but cannot un-send what the user
# already watched. Buffering the answer until verification finishes is the
# real fix and belongs with the enforce promotion, not with shadow. Recorded
# in docs/SAFETY.md as a blocker on that promotion.
REPAIR_STREAM_TAG = "jarvis:verification_repair"

# Turkish-specific letters, plus function words that are unambiguously
# Turkish (no English homographs -- "bu"/"bir" are safe, "an"/"at"/"o" are
# not and are deliberately absent). Used only to pick the LANGUAGE of the
# honest-failure fallback, so a miss costs a reply in the wrong language,
# never a wrong verdict.
_TURKISH_HINT_RE = re.compile(
    r"[şğıçöüŞĞİÇÖÜ]|\b(?:bir|bu|şu|için|ile|nedir|nasıl|lütfen|bana|beni|"
    r"var|yok|oluştur|göster|yap|kaydet|hazırla|çiz|efendim)\b",
    re.IGNORECASE,
)


def _is_turkish(state: JarvisState) -> bool:
    """Which language to answer the honest-failure fallback in.

    Falls back to the last human message when state["user_query"] is absent:
    it is populated on the normal entry path but not on every one (a
    resumed checkpoint, a direct-node call), and defaulting a Turkish
    owner's assistant to English because one state key was missing is a
    silly way to lose a sentence.
    """
    query = str(state.get("user_query") or "")
    if not query:
        for message in reversed(state.get("messages") or []):
            if isinstance(message, HumanMessage):
                content = message.content
                query = content if isinstance(content, str) else str(content)
                break
    return bool(_TURKISH_HINT_RE.search(query))


def _context_of(state: JarvisState) -> ExecutionContext:
    """The turn's ExecutionContext: from state if the entry point put one
    there, otherwise derived from `transport`.

    The fallback is for states this node can legitimately receive without one
    -- an old checkpoint mid-resume, a direct-node unit test, any future caller
    that builds state by hand. Deriving is safe because for_transport() fails
    closed on anything it does not recognize; assuming the dataclass default
    would NOT be, since that default is "unattended" and would silently
    downgrade a resumed foreground turn.
    """
    ctx = ExecutionContext.from_dict(state.get("execution_context"))
    if ctx is not None:
        return ctx
    return ExecutionContext.for_transport(state.get("transport"))


def _gate_inputs(state: JarvisState) -> tuple[bool, str]:
    """(interactive, utterance) for policy_guard.evaluate().

    Post-MVP Faz 2. Extracted rather than written twice because
    prepare_execution_node and confirmation_node each call evaluate()
    independently, and evaluate() being a pure function only guarantees they
    agree if they actually pass the same inputs.

    `user_query` is populated on the normal entry path but not on every one --
    a resumed checkpoint or a direct-node call can lack it, which the
    _is_turkish() helper above already had to work around. An absent utterance
    would silently give up the protection against a model that pre-resolved an
    ambiguous request, so the same last-human-message fallback applies here.
    Picking up one of this node's own synthetic ack messages instead is
    harmless: they contain no date, time or weekday, so every check on them
    returns "no objection".

    Faz 2.75 (Paket C): `interactive` is now read off the turn's
    ExecutionContext rather than pattern-matched on the transport string here.
    See jarvis/execution/context.py for the unattended-write incident that
    motivated moving the decision out of this function.
    """
    interactive = _context_of(state).may_act_without_asking
    utterance = str(state.get("user_query") or "")
    if not utterance:
        for message in reversed(state.get("messages") or []):
            if isinstance(message, HumanMessage):
                content = message.content
                utterance = content if isinstance(content, str) else str(content)
                break
    return interactive, utterance


def _declared_count(envelopes_raw: list, execution_id: str) -> int:
    """How many artifacts the envelope for this operation declared. Read from
    the raw envelope dicts because VerifiedOperation deliberately carries
    only the verdict, not the payload."""
    for raw in envelopes_raw or []:
        if isinstance(raw, dict) and raw.get("execution_id") == execution_id:
            return len(raw.get("artifacts") or [])
    return 0


def make_compose_node(settings=None):
    """Tool-free response composer (Faz 2B).

    Produces the final user-facing answer from the turn's transcript with a
    BARE model — no tool schemas bound, so it structurally cannot re-issue
    the call it just watched succeed (live incident F16: agent→procedure_save
    →agent→procedure_save… ×10 until the recursion limit). Critic feedback is
    consumed here as a node-local SystemMessage appended to THIS invocation
    only — it is never returned into graph state, so no fake user/system
    turns leak into the transcript (external review, item 9).

    Agent Runtime rev.2, Faz 4 — verified response composition. Reads
    state["execution_envelopes"] (Faz 1, absent/empty whenever
    execution_contract_mode="off" — its own rollback contract) into a
    jarvis.execution.summary.VerifiedExecutionSummary. In any enforce_* mode:
    raw ToolMessages are dropped from the LLM invocation in favor of a
    deterministic, code-authored status block (the model can no longer
    independently narrate whether a call succeeded), and — independent of
    what the model actually said — the same facts are unconditionally
    appended to the outgoing response whenever any operation did not cleanly
    succeed. That unconditional append, not claim-text detection, is what
    backs the phase's acceptance test ("an unverified operation claim does
    not reach the user"); audit_claims() is logged alongside it as a
    secondary, observation-only signal (see jarvis/execution/summary.py).
    In "shadow" mode (and "off", trivially — summary is always None there)
    neither the invocation nor the response is touched, only audit_claims is
    computed and logged — this is the plan's "annotate, then enforce" step,
    and it is what keeps this phase's off/shadow behavior covered by
    test_shadow_replay_equivalence.py's bit-identical contract.

    Post-MVP Faz 1 — honesty kernel. The unbacked-claim gate is NOT here.
    It lives in make_verification_node (a terminal node) because compose is
    not on every path to END — a plain conversational turn goes
    agent → critic → END and never touches this node, which is exactly the
    kind of turn the gate's headline case ("claimed a file, called no tool")
    occurs on. See that node's docstring.
    """
    from jarvis.providers import get_llm
    from langchain_core.messages import ToolMessage
    from jarvis import audit_log
    from jarvis.execution.summary import (
        audit_claims,
        build_verified_summary,
        render_operation_status_for_model,
        render_operation_status_for_user,
    )

    timeout_sec = getattr(settings, "agent_llm_timeout_sec", 90.0) if settings is not None else 90.0
    mode = getattr(settings, "execution_contract_mode", "off") if settings is not None else "off"
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

        # Agent Runtime rev.2, Faz 4 (see docstring above). envelopes_raw is
        # only ever non-empty when execution_contract_mode != "off" (Faz 1's
        # own gate in tool_result_accounting) -- the explicit mode check here
        # (rather than relying on that upstream contract alone) guarantees
        # "off" stays a true no-op, no new code path at all, even against a
        # state dict built by hand (a direct-node unit test, an old
        # checkpoint) that happens to carry envelopes anyway.
        envelopes_raw = state.get("execution_envelopes") or []
        # Post-MVP Faz 1 deliberately left this condition alone. The
        # zero-tool case it excludes is real and is the whole point of the
        # honesty kernel -- but it is handled in verification_node, where
        # every path to END passes, rather than here, where only tool turns
        # do. Nothing below would act on an empty summary anyway:
        # audit_claims returns early unless any_failed, which an empty
        # operation list can never be.
        summary = build_verified_summary(envelopes_raw) if (envelopes_raw and mode != "off") else None
        enforce = mode.startswith("enforce_")
        if summary is not None and enforce:
            invocation = [m for m in invocation if not isinstance(m, ToolMessage)]
            invocation.append(SystemMessage(content=render_operation_status_for_model(summary)))

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

        if summary is not None and mode != "off":
            violations = audit_claims(text, summary)
            if violations:
                audit_log.record("claim_audit", mode=mode, enforced=enforce, reasons=violations)

            if enforce and summary.any_failed:
                text = f"{text}\n\n{render_operation_status_for_user(summary)}"
                response = AIMessage(content=text)

        return {"messages": [response], "response": text}

    compose_node.__name__ = "compose_node"
    return compose_node


def make_verification_node(settings=None):
    """TERMINAL node: the turn's final answer, checked against the evidence.

    Post-MVP Faz 1 (honesty kernel). This started life inside compose_node and
    was moved here after a live run showed the obvious home was the wrong one.
    The graph has more than one way to finish a turn:

        agent -> critic -> END                      (no tool calls)
        agent -> ... -> tools -> ... -> compose -> critic -> END
        agent -> ... -> tools -> ... -> agent -> critic -> END

    Only the middle one passes through compose. So a gate living in compose
    could not see a plain conversational turn at all -- and "the model claims
    a file with no tool call behind it" IS most often a plain conversational
    turn. The check was structurally blind to its own headline case. It is a
    terminal node now so there is exactly ONE place where the answer meets
    the evidence, whatever produced the answer, and exactly one gate
    evaluation recorded per turn.

    Per-operation verification rows are recorded here for the same reason:
    a tool turn that loops back through the agent and ends at the critic
    never reaches compose either, and its operations would have gone
    uncounted -- silently deflating the very rollout metric the enforce
    promotion is decided on.

    Shadow does not touch the answer; only an enforce_* mode runs the single
    bounded repair round and, failing that, replaces the text with an honest
    report. "off" returns {} -- no new code path at all.
    """
    from langchain_core.messages import ToolMessage

    from jarvis.providers import get_llm
    from jarvis import audit_log
    from jarvis.execution import rollout
    from jarvis.execution.evidence import (
        build_evidence_set,
        detect_unbacked_claims,
        honest_failure_report,
        repair_instruction,
    )
    from jarvis.execution.summary import USER_STATUS_MARKER, build_verified_summary

    timeout_sec = getattr(settings, "agent_llm_timeout_sec", 90.0) if settings is not None else 90.0
    mode = getattr(settings, "execution_contract_mode", "off") if settings is not None else "off"
    enforce = mode.startswith("enforce_")
    _repair_llm: dict[str, object] = {}

    async def verification_node(state: JarvisState) -> dict:
        if mode == "off":
            return {}

        text = (state.get("response") or "").strip()
        messages = state.get("messages") or []
        if not text:
            # A turn that ended at the critic never set state["response"] --
            # the answer is the last AI message. Same fallback agent.py's
            # chat() uses to read the final text.
            for message in reversed(messages):
                if isinstance(message, AIMessage) and isinstance(message.content, str):
                    text = message.content.strip()
                    break
        if not text:
            return {}

        # Judge only what the MODEL said. In enforce mode compose_node appends
        # its own code-authored status block, whose per-operation detail is
        # the tool's normalized output -- which for an artifact tool IS a
        # path, and for the verification-failed case is a path that is
        # deliberately NOT on disk. Left in, the gate would read the system's
        # own honest report of a missing file as a fabricated claim about it.
        # (The negation guard happens to catch it today, because the block
        # contains the word FAILED -- but relying on that is relying on a
        # coincidence between two unrelated pieces of wording.)
        model_text = text.split(USER_STATUS_MARKER, 1)[0].strip() or text

        envelopes_raw = state.get("execution_envelopes") or []
        summary = build_verified_summary(envelopes_raw)
        for op in summary.operations:
            rollout.record_verification(
                mode=mode, capability=op.capability,
                display_status=op.display_status,
                postcondition_verdict=op.postcondition_verdict,
                artifacts_declared=_declared_count(envelopes_raw, op.execution_id),
            )

        # Post-MVP Faz 6: this answer was written by output_contract_node, not
        # by a model. Detecting "unbacked claims" in it is a category error --
        # and an actively harmful one for the class it most often carries:
        # "the tool ran and honestly failed", which enforce mode could then
        # spend a repair round re-writing. The contract has already decided
        # that class must never go back to the model.
        #
        # Operation verification above still runs (those rows describe what
        # the TOOLS did, which is unaffected by who wrote the prose), and a
        # claim_gate row is still written so the per-turn count stays exactly
        # one whoever authored the text.
        if state.get("response_origin") == "output_contract":
            rollout.record_claim_gate(
                mode=mode, enforced=enforce, fired=False,
                reasons=["code_authored_output_contract_response"],
                unbacked_files=[], repaired=None, blocked=False,
            )
            return {}

        evidence = build_evidence_set(summary, envelopes_raw)
        verdict = detect_unbacked_claims(model_text, evidence)
        repaired: bool | None = None
        blocked = False
        out: dict = {}

        if verdict.unbacked:
            audit_log.record(
                "unbacked_claim", mode=mode, enforced=enforce,
                reasons=verdict.reasons, files=verdict.unbacked_files,
            )
            # Post-MVP Faz 6: under an enforced output contract the turn's ONE
            # corrective repair is shared, so an invalid-args or completion
            # repair already spent leaves this gate with nothing to spend --
            # and that is safe, because this gate's fallback is not "let the
            # claim through", it is honest_failure_report() + blocked. The
            # answer is still never allowed to be wrong; it just does not get
            # a second draft. Deliberately reads the CONTRACT-SCOPED
            # predicate: on an unscoped turn (no required output, or the
            # feature off/shadow) an args repair must NOT close this gate,
            # which is exactly how it behaved before the budget was shared.
            budget_free = not shared_budget_spent(state, settings)
            if enforce and budget_free:
                # Exactly one bounded repair round (plan item 5) -- never a
                # loop. If the second draft is still contradicted the user
                # gets an honest report rather than a third attempt: this is
                # on the turn's critical path, and "keep asking the model
                # until it stops lying" is not a bounded operation.
                if "llm" not in _repair_llm:
                    # Always the reasoning tier, never the turn's own role.
                    #
                    # Post-MVP Faz 2.5 made most single-tool turns fast, and
                    # inheriting that here would retry a contradicted answer on
                    # the exact tier that just produced it -- with one round
                    # allowed and an honest failure report as the alternative,
                    # spending the retry on the same model is spending it on
                    # nothing. The plan's own role table says so directly:
                    # "critical error repair" is reasoning work.
                    #
                    # Cost note: with a cloud tier configured this makes the
                    # repair a paid call. It is bounded to one, and only fires
                    # in enforce mode after the gate has already caught a
                    # provably wrong answer.
                    _repair_llm["llm"] = get_llm("reasoning", settings)
                repair_messages = [
                    m for m in messages if not isinstance(m, ToolMessage)
                ] + [SystemMessage(content=repair_instruction(verdict))]
                try:
                    reply = await asyncio.wait_for(
                        _repair_llm["llm"].ainvoke(
                            repair_messages, config={"tags": [REPAIR_STREAM_TAG]}
                        ),
                        timeout=timeout_sec,
                    )
                    repair_text = (
                        reply.content if isinstance(reply.content, str) else str(reply.content)
                    )
                except Exception:  # noqa: BLE001 -- timeout, provider error, anything
                    # A verification layer must never be the thing that kills
                    # the turn (audit_log/postcondition_runner discipline). An
                    # unavailable repair falls through to the honest report
                    # below, which is the safe direction: the contradicted
                    # draft does not survive just because the retry failed.
                    repair_text = ""
                second = detect_unbacked_claims(repair_text, evidence) if repair_text else verdict
                repaired = bool(repair_text) and not second.unbacked
                if repaired:
                    text = repair_text
                else:
                    blocked = True
                    text = honest_failure_report(second, _is_turkish(state))
                out = {
                    "messages": [AIMessage(content=text)], "response": text,
                    **claim_budget(state, settings, "unbacked_claim"),
                }
            elif enforce:
                # Enforcing, but the turn's one corrective repair is already
                # spent. The claim still does not get through: no second
                # draft, straight to the honest report. Losing the retry
                # costs a better-worded answer, never a truthful one.
                blocked = True
                text = honest_failure_report(verdict, _is_turkish(state))
                out = {"messages": [AIMessage(content=text)], "response": text}

        rollout.record_claim_gate(
            mode=mode, enforced=enforce, fired=verdict.unbacked,
            reasons=verdict.reasons, unbacked_files=verdict.unbacked_files,
            repaired=repaired, blocked=blocked,
        )
        return out

    verification_node.__name__ = "verification_node"
    return verification_node


# ── the completion contract (Post-MVP Faz 6) ───────────────────────────────

#: Code-authored, so the model cannot soften them into a claim. One per class
#: the contract must never retry -- the point of separating the classes is
#: lost if they all produce the same sentence.
_CONTRACT_TEXT: dict[str, tuple[str, str]] = {
    "MISSING_TOOL_FAILURE": (
        "İstediğiniz grafiği oluşturamadım: çizim aracı bir hata bildirdi{detail}. "
        "Tekrar denemedim, çünkü hata kendini tekrar edecektir — eksik bilgiyi "
        "söylerseniz doğrudan çizebilirim.",
        "I could not produce the chart you asked for: the plotting tool reported "
        "an error{detail}. I did not retry, since the same call would fail the same "
        "way — tell me what is missing and I will draw it.",
    ),
    "MISSING_INVALID_ARGS": (
        "Grafiği oluşturamadım: çizim çağrısının argümanları geçerli değildi{detail}. "
        "Hangi dosya ve hangi sütunlar kullanılsın?",
        "I could not produce the chart: the plotting call's arguments were not "
        "valid{detail}. Which file and which columns should I use?",
    ),
    "MISSING_PREEXECUTION_BLOCK": (
        "Grafiği çizmeyi denedim ama çağrı çalıştırılmadan durduruldu{detail}. "
        "Kendiliğimden tekrar denemiyorum.",
        "I did try to draw the chart, but the call was stopped before it ran"
        "{detail}. I am not retrying on my own.",
    ),
    "EXECUTED_NO_OBJECT": (
        "Grafik çizildi, ancak düzenlenebilir bir nesne olarak kaydedilemedi — "
        "yani üzerinde değişiklik isteyemezsiniz. Bu bende bir kayıt hatası, "
        "sizin isteğinizde değil.",
        "The chart was drawn but could not be kept as an editable object, so you "
        "cannot ask me to revise it. That is a registration fault on my side, not "
        "a problem with your request.",
    ),
    "OUTPUT_EVIDENCE_MISMATCH": (
        "Çizim aracı başarılı döndü ama ürettiği dosyayı bildirmedi, bu yüzden "
        "grafiğin gerçekten oluştuğunu doğrulayamıyorum.",
        "The plotting tool reported success but declared no file, so I cannot "
        "confirm that the chart was actually produced.",
    ),
    "EVIDENCE_UNAVAILABLE": (
        "Grafiğin oluşup oluşmadığını doğrulayamadım — kayıt defterini "
        "okuyamadım{detail}. Oluştuğunu da oluşmadığını da iddia etmiyorum.",
        "I could not verify whether the chart was produced — the working set was "
        "unreadable{detail}. I am claiming neither that it exists nor that it does not.",
    ),
    "MISSING_NO_ATTEMPT": (
        "Açıkça grafik istediniz ama bu turda bir grafik oluşturmadım ve "
        "yeniden deneme hakkım kalmadı{detail}.",
        "You explicitly asked for a chart, I did not produce one this turn, and I "
        "have no retry left{detail}.",
    ),
}

#: Given to the agent for its single completion-repair round, as a
#: HumanMessage rather than a SystemMessage -- following planner_node's own
#: "[Planning Mode — Execution Plan]" precedent, since a system message in the
#: MIDDLE of a message list is the most provider-fragile shape available.
#:
#: The text itself lives in jarvis/execution/output_contract.py, next to the
#: sanitiser that strips it back out if the model echoes it into its own
#: answer. Two copies of the same block, one to emit and one to match, is
#: exactly the drift that would leave half a directive in a user's answer.
from jarvis.execution.output_contract import (  # noqa: E402 -- see the note above
    REPAIR_DIRECTIVE as _REPAIR_DIRECTIVE,
)


def _contract_failure_text(status: str, detail: str, turkish: bool) -> str:
    turkish_text, english_text = _CONTRACT_TEXT[status]
    suffix = f" ({detail})" if detail else ""
    return (turkish_text if turkish else english_text).format(detail=suffix)


def make_output_contract_node(settings=None, working_set=None):
    """TERMINAL node: was the output the user explicitly asked for produced?

    Post-MVP Faz 6, and a sibling of make_verification_node rather than part
    of it. They answer different questions -- that one asks whether the
    ANSWER is backed by evidence, this one asks whether the OUTPUT exists at
    all -- and keeping them apart buys three things that folding them in
    would have cost: `required_outputs_mode` stays independent of
    `execution_contract_mode` (whose node returns early on "off", which would
    have silently disabled this one); the claim gate keeps its "exactly one
    evaluation per turn" invariant even when this node runs twice; and this
    node's own code-authored answer still passes THROUGH the claim gate
    rather than around it.

    Ordering: everything terminal now routes here first, and `continue` hands
    off to `verify`. A repair routes back to `agent` instead -- the one place
    in the graph that can still call a tool.

    `off` is not handled here at all: build_graph() omits the node entirely,
    so the node set, edge map, initial state and checkpoint shape are
    bit-for-bit what they were before this phase. A `{}`-returning node would
    still cost a super-step and change the checkpoint.
    """
    from jarvis import audit_log
    from jarvis.execution import rollout
    from jarvis.execution.output_contract import classify, first_requirement
    from jarvis.tool_registry import tools_producing

    mode = getattr(settings, "required_outputs_mode", "off") if settings is not None else "off"
    max_rounds = getattr(settings, "max_tool_rounds_per_turn", 4) if settings is not None else 4
    max_calls = getattr(settings, "max_tool_calls_per_turn", 6) if settings is not None else 6

    async def output_contract_node(state: JarvisState, config=None) -> dict:
        required = state.get("required_outputs") or []
        requirement = first_requirement(required)
        if mode == "off" or requirement is None:
            # No contract on this turn: no evidence read, no telemetry row.
            # Recording NOT_REQUIRED for every conversational turn would bury
            # the rows that matter under the ones that never can.
            return {"output_contract_action": "continue"}

        kind, operation = requirement
        verdict = classify(
            required=required,
            capabilities=tools_producing(kind, operation),
            messages=state.get("messages") or [],
            invalid_args_history=state.get("invalid_args_history"),
            preexecution_history=state.get("preexecution_history"),
            **_read_evidence(working_set, config, kind),
            baseline_artifacts=(
                (state.get("required_output_baseline") or {}).get(kind, {}) or {}
            ).get("artifacts"),
        )

        # A completion repair is the only thing that writes this reason, and
        # MISSING_NO_ATTEMPT is the only status it fires on -- so the turn's
        # starting point is recoverable without a second state field to keep
        # in sync.
        repaired_this_turn = state.get("repair_reason") == "missing_required_output"
        initial_status = "MISSING_NO_ATTEMPT" if repaired_this_turn else verdict.status

        if mode != "enforce":
            # Shadow: observe and record, mutate NOTHING. Not the answer, not
            # the messages, not the repair budget. The single returned key is
            # the router's own transient signal, which off does not have a
            # node to write at all.
            rollout.record_output_contract(
                mode=mode, event="decision", status=verdict.status,
                requirement=verdict.requirement, action="continue",
                reason=verdict.reason, anomaly=verdict.anomaly,
            )
            return {"output_contract_action": "continue"}

        if verdict.anomaly:
            audit_log.record(
                "output_contract_anomaly", requirement=verdict.requirement,
                status=verdict.status, reason=verdict.reason,
            )

        if verdict.satisfied:
            rollout.record_output_contract(
                mode=mode, event="terminal", status=verdict.status,
                requirement=verdict.requirement, action="continue",
                initial_status=initial_status,
                repair_reason=str(state.get("repair_reason") or ""),
                repair_success=True if repaired_this_turn else None,
            )
            return {"output_contract_action": "continue"}

        unavailable = _repair_unavailable_reason(state, settings, max_rounds, max_calls)
        if verdict.repairable and not unavailable:
            rollout.record_output_contract(
                mode=mode, event="decision", status=verdict.status,
                requirement=verdict.requirement, action="repair",
                reason=verdict.reason, repair_reason="missing_required_output",
            )
            return {
                "messages": [HumanMessage(content=_REPAIR_DIRECTIVE)],
                "output_contract_action": "repair",
                **claim_budget(state, settings, "missing_required_output"),
            }

        # Everything else is terminal: someone else's layer (invalid args, a
        # pre-execution block), a tool that honestly failed, a postcondition
        # defect of ours, or a repair we have no budget for. All of them get
        # a code-authored answer -- the model's own draft got here by
        # asserting or implying an output that does not exist.
        if verdict.status == "EXECUTED_NO_OBJECT":
            audit_log.record(
                "output_postcondition_failed", requirement=verdict.requirement,
                capability=",".join(sorted(tools_producing(kind, operation))),
                declared=list(verdict.declared), reason=verdict.reason,
            )
        text = _contract_failure_text(
            verdict.status, unavailable or verdict.reason, _is_turkish(state),
        )
        rollout.record_output_contract(
            mode=mode, event="terminal", status=verdict.status,
            requirement=verdict.requirement, action="continue",
            reason=unavailable or verdict.reason, anomaly=verdict.anomaly,
            initial_status=initial_status,
            repair_reason=str(state.get("repair_reason") or ""),
            repair_success=False if repaired_this_turn else None,
        )
        return {
            "messages": [AIMessage(content=text)],
            "response": text,
            # Structural, not a marker in the text: `verify` must be able to
            # tell a code-authored answer from a model's one, and a marker is
            # part of the string it would be judging.
            "response_origin": "output_contract",
            "output_contract_action": "continue",
        }

    output_contract_node.__name__ = "output_contract_node"
    return output_contract_node


def _read_evidence(working_set, config, kind: str) -> dict:
    """The working set's artifacts for `kind`, or the error that stopped us.

    Never raises. An unreadable store must not be reported as "the model
    produced nothing" -- that would blame a model for our own SQLite, and
    (worse) offer it a repair for a chart it may already have drawn.
    """
    try:
        conversation_id = str(((config or {}).get("configurable") or {}).get("conversation_id") or "")
        if not conversation_id:
            raise KeyError("conversation_id")
        store = working_set
        if store is None:
            from jarvis.working_set import default_store
            store = default_store()
        obj = store.active(conversation_id, kind)
        return {"registered_artifacts": tuple(obj.source_artifacts) if obj is not None else ()}
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        logger.warning("output contract could not read the working set", exc_info=True)
        return {"registered_artifacts": (), "evidence_error": type(exc).__name__}


def _repair_unavailable_reason(state: JarvisState, settings, max_rounds: int, max_calls: int) -> str:
    """Why a completion repair cannot be offered, or "" if it can.

    The tool budgets matter as much as the repair budget: MISSING_NO_ATTEMPT
    means no CHART tool was called, not that no tool was called at all. A turn
    that spent its rounds on file_list/csv_read would take the directive,
    re-enter the agent, and have the call rejected deterministically by
    confirmation_node -- one wasted round for the same outcome, and a user
    message that promised a retry which never happened.
    """
    if corrective_repair_spent(state, settings):
        return "repair_unavailable: budget_spent"
    if int(state.get("tool_rounds") or 0) >= max_rounds:
        return "repair_unavailable: tool_round_budget_exhausted"
    if int(state.get("tool_calls_attempted") or 0) >= max_calls:
        return "repair_unavailable: tool_call_budget_exhausted"
    return ""


def route_from_output_contract(state: JarvisState) -> str:
    """output_contract → 'repair' (back to the agent) | 'continue' (→ verify).

    Reads ONLY the transient action the node just wrote. `repair_reason` and
    `repair_attempts_total` persist for the rest of the turn by design, so a
    router keyed on them would send the post-repair pass straight back into
    another repair -- the same value it saw the first time, forever.
    """
    return "repair" if state.get("output_contract_action") == "repair" else "continue"


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
    """Return a critic node that scores the agent's output on the reasoning tier.

    Fast-path: simple conversational exchanges are auto-accepted with no model
    call at all (see _is_simple_exchange).
    Full path: structured JSON verdict {score, verdict, critique}.
    Max revisions: 2. After that the critic always accepts.

    The docstring used to say "Gemini Pro". It is whatever `reasoning` resolves
    to -- on a local-only install (cloud_policy="off", this owner's config) that
    is qwen3:8b with its thinking channel on, not a cloud model.

    **Known inconsistency, measured not assumed (2026-08-01).** `llm_pro` is
    built ONCE in graph.py as get_llm("reasoning", ...), so the critic cannot
    follow the turn's role the way the agent and compose nodes do. Post-MVP Faz
    2.5 made most single-tool turns run `fast`, and on 10/10 live runs of
    "Yarın saat 15:00'te Baran'la toplantı ekle" -- a fast turn -- the answer
    cleared 40 words and this node spent a reasoning-tier call anyway.

    Left alone on purpose. Changing which model judges an answer is a change to
    a quality gate and needs its own before/after measurement (scripts/role_ab.py
    reports the two axes it would need); and "judge with the weaker tier" is not
    obviously right just because it is faster. Recorded so the cost is visible
    rather than surprising.
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

        # Fast-path: simple conversational exchange — no model call at all
        if _is_simple_exchange(user_query, response_text):
            return {
                "critic_verdict": "accept",
                "critique": "",
                "response": response_text,
                "revise_count": revise_count,
            }

        # Full path: score on the reasoning tier (see this factory's docstring
        # for why that is not the turn's own tier)
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
    """tool calls present → 'prepare_execution' (Agent Runtime rev.2, Faz 2);
    else → 'critic'."""
    last_msg = state["messages"][-1]
    if isinstance(last_msg, AIMessage) and getattr(last_msg, "tool_calls", None):
        return "prepare_execution"
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


# ── Agent Runtime rev.2, Faz 2 ──────────────────────────────────────────────

def make_prepare_execution_node(settings=None):
    """Return a node that mints one signed ExecutionRequest per pending tool
    call (Agent Runtime rev.2, Faz 2, reviewer #2/#6) -- new routing:
    agent → prepare_execution → confirmation.

    Pipeline per call: capability resolve (get_spec) → schema validate
    (Agent Runtime rev.2, Faz 6 Part 2 -- jarvis.execution.args_schemas.
    validate_args(), only for the 12 tools that have a registered
    args_schema; a REJECT-ONLY gate, see that module's own docstring for
    why it never substitutes canonical args back into the call) → risk
    classify (policy_guard.evaluate) → best-effort canonical resource
    (_resolve_target_resource) → TaskContract match (always "no_contract"
    -- nothing produces one yet, reported honestly rather than silently
    "verified") → sign. The result is an immutable ExecutionRequest
    (jarvis.execution.request) that confirmation_node now binds its
    approval to instead of raw tool_call args.

    A call that fails schema validation gets NO ExecutionRequest minted --
    it is recorded in the returned "invalid_args_calls" list instead
    (tool_call_id, capability, and the trimmed pydantic error list) so
    confirmation_node's own pre-gate can reject the whole batch (same
    "an over-limit/duplicate-bearing batch is rejected wholesale, not
    partially executed" precedent every other pre-gate already follows)
    rather than silently letting a malformed call fall through to policy
    evaluation and signing.

    Deliberately calls policy_guard.evaluate() again here even though
    confirmation_node ALSO calls it independently for its own pre-gate
    (batch/turn limits, duplicate fingerprints, kill-switch veto, the
    --profile test external-writes gate, the proactive-readonly gate):
    evaluate() is a pure function of (tool_name, args, settings), so the two
    calls can never diverge -- this keeps confirmation_node's already-
    heavily-tested pre-gate logic completely untouched rather than
    threading a second data path through it. A future phase may consolidate
    to a single evaluation if the duplication ever becomes a real cost.

    execution_id is minted FRESH every call (tool_call_id + a random
    suffix), never reused across calls or turns -- this is what makes the
    idempotency journal (jarvis.execution.idempotency) safe to key on it
    directly: two independent requests can never collide here by
    construction, so a journal hit only ever means a genuine replay of one
    specific already-minted request (see idempotency.py's own docstring).

    No pending tool call (last_ai is None) → {} -- same no-op convention
    make_confirmation_node uses, so an old checkpoint or a direct-node unit
    test that never runs this node leaves confirmation_node's behavior
    completely unchanged (state["execution_requests"] simply absent).
    """
    from jarvis import policy_guard
    from jarvis.execution import approval
    from jarvis.execution.args_schemas import validate_args
    from jarvis.execution.request import ExecutionRequest, resolve_target_resource
    from jarvis.graph.tool_accounting import tool_call_fingerprint
    from jarvis.tool_registry import get_spec

    ttl = getattr(settings, "approval_ttl_sec", 300) if settings is not None else 300

    async def prepare_execution_node(state: JarvisState) -> dict:
        last_ai: AIMessage | None = None
        for msg in reversed(state["messages"]):
            if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                last_ai = msg
                break
        if last_ai is None:
            return {}

        now_iso = datetime.now(timezone.utc).isoformat()
        interactive, utterance = _gate_inputs(state)  # Post-MVP Faz 2 -- see the helper
        # The round this batch belongs to. tool_rounds is incremented by
        # confirmation_node AFTER this node runs, so the batch being prepared
        # now is round N+1 -- stamped so the contract can tell a first-round
        # rejection from a repair round's.
        round_index = int(state.get("tool_rounds") or 0) + 1
        requests: list[dict] = []
        invalid_calls: list[dict] = []
        for tc in last_ai.tool_calls:
            name = tc.get("name", "")
            args = tc.get("args") or {}
            spec = get_spec(name)

            if spec is not None and spec.args_schema is not None:
                ok, errors = validate_args(spec.args_schema, args)
                if not ok:
                    invalid_calls.append({
                        "tool_call_id": tc.get("id", ""),
                        "capability": name,
                        "errors": errors,
                    })
                    continue

            decision = policy_guard.evaluate(
                name, args, settings, interactive=interactive, utterance=utterance,
            )

            req = ExecutionRequest(
                execution_id=f"{tc.get('id', 'call')}-{secrets.token_hex(6)}",
                capability=name,
                action=decision.action,
                normalized_args_digest=tool_call_fingerprint(name, args),
                target_resource=resolve_target_resource(name, args),
                risk_level=decision.risk_level,
                requires_confirmation=decision.requires_confirmation,
                allowed=decision.allowed,
                side_effect_type=decision.side_effect_type,
                idempotency=spec.idempotency if spec is not None else "none",
                created_at=now_iso,
                expiry=approval.new_expiry(ttl),
                single_use_nonce=approval.new_nonce(),
            )
            requests.append({
                "tool_call_id": tc.get("id", ""),
                "request": req.model_dump(),
                "signature": approval.sign(req),
            })

        # Post-MVP Faz 6: invalid_args_calls is OVERWRITTEN each round on
        # purpose -- it describes the batch confirmation_node is deciding on
        # right now. The completion contract needs the whole turn instead, and
        # needs it keyed by tool_call_id: a blocked call and a call that
        # genuinely failed both come back as a failing ToolMessage stub, so
        # only an id-keyed record tells them apart. Read-extend-return because
        # JarvisState has no reducer outside `messages` -- a returned list
        # REPLACES, it does not append (same pattern the ledger uses in
        # tool_accounting.py).
        history = list(state.get("invalid_args_history") or [])
        history.extend(
            {"round": round_index, **c} for c in invalid_calls
        )
        return {
            "execution_requests": requests,
            "invalid_args_calls": invalid_calls,
            "invalid_args_history": history,
        }

    prepare_execution_node.__name__ = "prepare_execution_node"
    return prepare_execution_node


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

    Agent Runtime rev.2, Faz 2: right before the final approve (both the
    post-interrupt "user_approved" path -- see the bottom of this function),
    each confirmable call's ExecutionRequest (state["execution_requests"],
    built by the new prepare_execution node upstream) is re-verified against
    the CURRENT tool_call args and the idempotency journal: a signature/
    digest/expiry mismatch (e.g. a repair changed the args after the user
    was shown the prompt) or a replay of an already-committed execution_id
    is denied instead of silently approved. No-ops per call when
    prepare_execution never ran for it (old checkpoint / a test that calls
    this node directly, as every pre-Faz-2 test in this suite does) --
    confirmation_node's behavior is then unchanged from before this phase.
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
        # Faz 2.75 (Paket C): ORIGIN, not "unattended".
        #
        # The read-only clamp below exists for turns JARVIS started by itself
        # — a monitor poll nobody asked for. A background TaskExecutor job is
        # also unattended, but the user did ask for it ("bunu arka planda
        # yap"), and clamping its local writes would break the reports and
        # files such jobs exist to produce. Same behaviour as the old
        # startswith("monitor-") test, now stated as the thing it means.
        ctx = _context_of(state)
        is_proactive = ctx.origin == "monitor"
        # Post-MVP Faz 2: `interactive` means a human is present in this turn
        # to see what happens; `utterance` is what they actually said. Both
        # come from the shared _gate_inputs() helper so this node and
        # prepare_execution_node cannot derive them differently.
        interactive, utterance = _gate_inputs(state)
        decisions = {
            tc.get("id"): policy_guard.evaluate(
                tc.get("name", ""), tc.get("args", {}) or {}, settings,
                interactive=interactive, utterance=utterance,
            )
            for tc in last_ai.tool_calls
        }
        # Agent Runtime rev.2, Faz 2: prepare_execution's signed
        # ExecutionRequests for this same batch, if that node ran (absent for
        # an old checkpoint or a direct-node unit test that skips straight to
        # confirmation_node -- every read below tolerates that via .get()).
        requests_by_id: dict[str, dict] = {
            r["tool_call_id"]: r for r in (state.get("execution_requests") or [])
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

        def _reject_batch(outcome: str, stub_text: str, ack_text: str, *, per_call_stubs: list[ToolMessage] | None = None, extra: dict | None = None) -> dict:
            """Whole-batch refusal: executing 'just the safe part' of an
            over-limit or duplicate-bearing batch would be guessing which part
            of a hallucination was safe. Same stub+ack shape as the kill-switch
            path so LangGraph state stays valid and the agent must acknowledge.
            extra: additional state keys to merge in (Faz 6 Part 2's
            args_repair_attempted flag) -- additive, every pre-existing
            caller passes nothing and is unaffected."""
            audit_log.record("decision", tool="*batch*", action="", risk_level=0,
                             transport=transport, outcome=outcome, reason=ack_text[:120], **rich)
            stubs = per_call_stubs or [
                ToolMessage(content=stub_text, tool_call_id=tc.get("id", "")) for tc in batch
            ]
            # Post-MVP Faz 6: the ONE structured record of why a call never
            # reached the tools node. It cannot be recovered downstream: the
            # audit log is not in graph state, no ledger row is written for a
            # call that never ran, and the stub text a blocked call leaves is
            # indistinguishable from a real tool error. Written here because
            # this closure is the single chokepoint every whole-batch refusal
            # goes through -- including the kill switch and the user's own
            # denial, which must never be retried.
            #
            # Append, not replace: JarvisState has no reducer outside
            # `messages`.
            history = list(state.get("preexecution_history") or [])
            history.extend({
                "round": int(state.get("tool_rounds") or 0) + 1,
                "tool_call_id": tc.get("id", ""),
                "capability": tc.get("name", ""),
                "outcome": outcome,
                "reason": ack_text[:200],
            } for tc in batch)
            return {
                "confirmation_result": "denied",
                "messages": stubs + [HumanMessage(content=ack_text)],
                "preexecution_history": history,
                **counter_updates,
                **(extra or {}),
            }

        def _blocked_history(outcome: str, calls: list, reason: str = "") -> dict:
            """The same structured record for the PER-CALL refusal paths.

            Six of them return their own dict rather than going through
            _reject_batch (kill switch, capability disabled, external writes
            off, proactive read-only clamp, the user's own denial, stale or
            duplicate approval). Every one of those is a terminal "this never
            reached the tools node" -- the class the completion contract must
            never offer a retry for, and the user's denial most of all."""
            history = list(state.get("preexecution_history") or [])
            history.extend({
                "round": int(state.get("tool_rounds") or 0) + 1,
                "tool_call_id": tc.get("id", ""),
                "capability": tc.get("name", ""),
                "outcome": outcome,
                "reason": reason[:200],
            } for tc in calls)
            return {"preexecution_history": history}

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

        # Agent Runtime rev.2, Faz 6 Part 2 -- bounded repair for calls that
        # failed jarvis.execution.args_schemas.validate_args() in
        # prepare_execution_node. Whole-batch reject (same reasoning as the
        # duplicate-batch case above: a partially-invalid batch is rejected
        # wholesale, never partially executed). "Bounded" is an EXPLICIT
        # state flag, not an incidental side effect of max_tool_rounds_per_turn
        # (which a rejected batch also consumes, and which a config change
        # would silently alter) -- an external review of Faz 6 Part 1 caught
        # that the plan's "normalize -> validate -> one repair -> ..."
        # wording needed exactly this guarantee.
        invalid_calls = list(state.get("invalid_args_calls") or [])
        if invalid_calls:
            invalid_by_id = {c["tool_call_id"]: c for c in invalid_calls}

            def _first_error(c: dict) -> dict:
                return c["errors"][0] if c["errors"] else {"loc": [], "msg": "invalid arguments"}

            for c in invalid_calls:
                first = _first_error(c)
                audit_log.record(
                    "decision", tool=c["capability"], action="", risk_level=0,
                    transport=transport, outcome="blocked_invalid_args",
                    reason=f"{first.get('loc')}: {first.get('msg')}"[:200], **rich,
                )

            # Post-MVP Faz 6: `args_repair_attempted` still owns this path,
            # but under an enforced output contract the turn's ONE corrective
            # repair is shared with the completion contract and the claim
            # gate -- so a completion repair already spent closes this one
            # too. Contract-scoped: on every other turn this reduces to the
            # pre-contract flag exactly. See jarvis/graph/repair_budget.py.
            already_repaired = corrective_repair_spent(state, settings)
            if not already_repaired:
                def _stub_for(tc: dict) -> ToolMessage:
                    entry = invalid_by_id.get(tc.get("id", ""))
                    if entry is None:
                        return ToolMessage(
                            content="[SKIPPED: batched with an invalid call -- re-issue this one alone if still needed]",
                            tool_call_id=tc.get("id", ""),
                        )
                    first = _first_error(entry)
                    field = str(first["loc"][0]) if first.get("loc") else "_"
                    return ToolMessage(
                        content=f"[INVALID_ARGS:{field}] {first.get('msg', 'invalid arguments')}",
                        tool_call_id=tc.get("id", ""),
                    )

                return _reject_batch(
                    "blocked_invalid_args",
                    "",  # unused -- per_call_stubs given
                    "One or more of these tool calls had invalid arguments (see the "
                    "[INVALID_ARGS:...] message(s) for exactly what's wrong). You get "
                    "ONE corrected retry this turn -- re-issue the SAME action(s) with "
                    "the missing/fixed field(s). If it fails again, stop and tell the "
                    "user what information is missing instead of retrying further.",
                    per_call_stubs=[_stub_for(tc) for tc in batch],
                    extra={
                        "args_repair_attempted": True,
                        **claim_budget(state, settings, "invalid_args"),
                    },
                )

            # Repair already used once this turn -- exhausted. Compose the
            # final honest answer directly and route straight to END (never
            # back through the tool-bound agent a second time) -- see
            # route_from_confirmation's "invalid_args_exhausted" branch.
            detail_lines = []
            for c in invalid_calls:
                first = _first_error(c)
                field = first["loc"][0] if first.get("loc") else None
                where = f" ({field})" if field else ""
                detail_lines.append(f"- {c['capability']}{where}: {first.get('msg', 'invalid arguments')}")
            final_text = (
                "I couldn't complete this -- the arguments were still invalid after one "
                "corrected attempt:\n" + "\n".join(detail_lines)
            )
            audit_log.record(
                "decision", tool="*batch*", action="", risk_level=0,
                transport=transport, outcome="blocked_invalid_args_exhausted",
                reason=final_text[:200], **rich,
            )
            return {
                "confirmation_result": "invalid_args_exhausted",
                "messages": [AIMessage(content=final_text)],
                "response": final_text,
                **counter_updates,
            }

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
            return {"confirmation_result": "denied", "messages": stub_msgs + [ack_msg],
                    **_blocked_history(outcome, last_ai.tool_calls, veto_reason), **counter_updates}

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
                return {"confirmation_result": "denied", "messages": stub_msgs + [ack_msg],
                        **_blocked_history("blocked_external_writes_disabled", blocked),
                        **counter_updates}

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
                return {"confirmation_result": "denied", "messages": stub_msgs + [ack_msg],
                        **_blocked_history("blocked_proactive_readonly", last_ai.tool_calls),
                        **counter_updates}

        if not settings.confirmation_gate_enabled:
            return {"confirmation_result": "approved", **counter_updates}

        confirmable = [tc for tc in last_ai.tool_calls if decisions[tc.get("id")].requires_confirmation]
        if not confirmable:
            return {"confirmation_result": "approved", **counter_updates}

        # Interrupt — pauses the graph until resume_and_stream() is called.
        # execution_id (Agent Runtime rev.2, Faz 2), when present, names
        # EXACTLY the ExecutionRequest this specific prompt is bound to --
        # confirmation ← "TAM OLARAK bu ExecutionRequest onaylanır" (the
        # plan's own words). None for old checkpoints / direct-node tests
        # that never ran prepare_execution.
        tools_info = [
            {
                "name": tc.get("name"), "args": tc.get("args", {}), "id": tc.get("id"),
                "description": policy_guard.describe_call(tc.get("name", ""), tc.get("args", {}) or {}),
                "execution_id": (requests_by_id.get(tc.get("id")) or {}).get("request", {}).get("execution_id"),
            }
            for tc in confirmable
        ]
        decision = _interrupt({"tools": tools_info, "count": len(confirmable)})

        # decision is the value passed to Command(resume=...) on resume.
        # Fail-closed vocabulary check (review remediation): before this,
        # anything that wasn't a string starting with "deny" silently fell
        # through to the approve path below -- "yes", "", a typo, a stray
        # non-string value all executed the pending L3 action. This is the
        # exact "anything not starting with deny approves" bug
        # jarvis.execution.workflow_approval's exact decision allowlist was
        # written to close for the workflow-engine gate; this gate is the
        # one POST /chat/confirm's unvalidated `decision: str` field (and
        # every other resume_and_stream() caller) actually drives, so it
        # needs the identical fix. An invalid decision is normalized into an
        # explicit deny (with the original value quoted as the reason) and
        # falls through into the SAME deny-handling branch below rather than
        # duplicating it.
        dl = decision.strip().lower() if isinstance(decision, str) else ""
        if dl != "approve" and dl != "deny" and not dl.startswith("deny:"):
            decision = f"deny:invalid confirmation response {decision!r}"

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
                # The one block that must NEVER be retried on the system's own
                # initiative -- a completion contract that re-drove the model
                # here would be overriding the user in their own name.
                **_blocked_history("user_denied", last_ai.tool_calls, guidance),
                **counter_updates,
            }

        for tc in confirmable:
            d = decisions[tc.get("id")]
            audit_log.record(
                "decision", tool=d.tool, action=d.action, risk_level=d.risk_level,
                transport=transport, outcome="user_approved",
            )

        # Agent Runtime rev.2, Faz 2 -- re-verify the approval right before
        # handing off to "tools": closes the TOCTOU gap between "the user saw
        # these args" and "these args actually execute" (a repair that
        # changed the args between interrupt and resume must not silently run
        # under the old yes), and refuses a replayed approval via the
        # idempotency journal -- the plan's own acceptance test for this
        # phase: "approve -> retry -> journal reddi". Skips per call when
        # prepare_execution didn't produce an entry for it (old checkpoint /
        # direct-node unit test) -- same backward-compat convention as every
        # other new field in this node.
        from jarvis.execution import approval as _approval, idempotency as _idempotency
        from jarvis.execution.request import ExecutionRequest as _ExecutionRequest

        for tc in confirmable:
            entry = requests_by_id.get(tc.get("id"))
            if entry is None:
                continue
            req = _ExecutionRequest(**entry["request"])
            current_digest = tool_call_fingerprint(tc.get("name", ""), tc.get("args") or {})
            ok, why = _approval.verify(req, entry["signature"], current_args_digest=current_digest)
            if not ok:
                audit_log.record(
                    "decision", tool=req.capability, action=req.action, risk_level=req.risk_level,
                    transport=transport, outcome="blocked_stale_approval", reason=why,
                )
                tool_trace.record(
                    event="policy_decision", tool=req.capability, action=req.action,
                    risk_level=req.risk_level, ok=False, transport=transport,
                    outcome="blocked_stale_approval", reason=why,
                )
                stub_msgs = [
                    ToolMessage(
                        content=f"[BLOCKED: approval no longer valid -- {why}]",
                        tool_call_id=t.get("id", ""),
                    )
                    for t in last_ai.tool_calls
                ]
                ack_msg = HumanMessage(content=(
                    f"The approval for this action is no longer valid ({why}) -- most likely "
                    "the arguments changed after the user was shown the confirmation prompt, "
                    "or too much time passed. Do NOT assume it ran. Re-issue the call fresh so "
                    "it can be shown to the user and approved again."
                ))
                return {"confirmation_result": "denied", "messages": stub_msgs + [ack_msg],
                        **_blocked_history("blocked_stale_approval", last_ai.tool_calls, why),
                        **counter_updates}
            if _idempotency.is_committed(req.execution_id):
                audit_log.record(
                    "decision", tool=req.capability, action=req.action, risk_level=req.risk_level,
                    transport=transport, outcome="blocked_duplicate_execution",
                    reason="execution_id already committed",
                )
                tool_trace.record(
                    event="policy_decision", tool=req.capability, action=req.action,
                    risk_level=req.risk_level, ok=False, transport=transport,
                    outcome="blocked_duplicate_execution", reason="execution_id already committed",
                )
                stub_msgs = [
                    ToolMessage(
                        content="[BLOCKED: duplicate execution -- this action already ran once]",
                        tool_call_id=t.get("id", ""),
                    )
                    for t in last_ai.tool_calls
                ]
                ack_msg = HumanMessage(content=(
                    "This exact approved action already ran once and its side effect is "
                    "already applied -- running it again was refused to avoid duplicating "
                    "that side effect (e.g. sending the same email twice). Do NOT retry it; "
                    "tell the user it already completed."
                ))
                return {"confirmation_result": "denied", "messages": stub_msgs + [ack_msg],
                        **_blocked_history("blocked_duplicate_execution", last_ai.tool_calls),
                        **counter_updates}
            _approval.consume(req)

        return {"confirmation_result": "approved", **counter_updates}

    confirmation_node.__name__ = "confirmation_node"
    return confirmation_node


def route_from_confirmation(state: JarvisState) -> str:
    """approved → 'tools'; denied → 'agent' (LLM acknowledges denial);
    invalid_args_exhausted → END (Agent Runtime rev.2, Faz 6 Part 2 -- the
    bounded-repair budget is spent; confirmation_node already composed the
    final honest answer directly, so there is nothing left for the
    tool-bound agent OR the critic to do with this turn)."""
    from langgraph.graph import END
    result = state.get("confirmation_result", "approved")
    if result == "invalid_args_exhausted":
        return END
    if result == "denied":
        return "agent"
    return "tools"
