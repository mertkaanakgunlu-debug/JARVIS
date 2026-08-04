"""Completion contract â€” did the turn produce the output the user asked for?

Post-MVP Faz 6. The measured problem, from the 2Ã—2 experiment of 2026-08-03
(n=10/arm, 40 trials): in the production configuration, *both* failures on an
explicit chart request were `not_attempted` â€” the model answered without ever
calling a chart tool. Reordering the toolset does not fix that (offering
`plot_data` earlier raised selection by +0.20 but lowered completion).

So this module answers exactly one question, in one place, from evidence:
**was a required output produced this turn?** â€” and it separates the ways the
answer can be "no", because each one belongs to a different layer:

    MISSING_NO_ATTEMPT          the model never reached for the tool   <- repairable
    MISSING_INVALID_ARGS        args validation rejected the call      <- confirmation_node's job
    MISSING_PREEXECUTION_BLOCK  a budget/policy/denial stopped it      <- never retry
    MISSING_TOOL_FAILURE        the tool ran and honestly said no      <- never retry
    EXECUTED_NO_OBJECT          it ran, declared output, nothing kept  <- our postcondition bug
    OUTPUT_EVIDENCE_MISMATCH    it ran but declared no output at all   <- instrumentation gap
    EVIDENCE_UNAVAILABLE        we could not read the evidence         <- never blame the model

A contract that does not draw these lines repairs the wrong layer: it would
re-prompt a model that behaved correctly, or paper over a wiring defect of
ours by asking the model to try again.

This module is PURE. It reads no files, opens no store and calls no LLM; the
node (jarvis/graph/nodes.py) does the IO and hands the results in. That is
what makes the whole table testable without a live model.

Evidence rules worth stating outright, because each replaced a wrong one:

* **Success needs a declared artifact that the working set actually kept.**
  An earlier cut compared the active chart's `version` against a pre-turn
  snapshot. That is wrong in both directions: `record_artifact()` does not
  bump `version` (jarvis/working_set.py) and `register_chart()` skips
  `patch()` entirely when the spec is unchanged, so a genuine redraw â€” new
  PNG, updated object â€” scored as EXECUTED_NO_OBJECT; meanwhile any
  concurrent background turn touching the same conversation could bump the
  version and score as success. The artifact path is the turn's own identity:
  `plot_data` writes into a fresh run directory per call.
* **Artifacts are read off `ToolMessage.artifact`, never the ledger.**
  safe_tools attaches them on every call regardless of settings, but
  tool_accounting only parses them when `execution_contract_mode != "off"`,
  and ledger rows carry neither the artifact list nor a `tool_call_id`. The
  messages are the only mode-independent, per-call source.
* **A blocked call also leaves a failing ToolMessage stub.** So "not ok" alone
  cannot tell a blocked call from an executed one that failed â€” the id-keyed
  block/invalid histories do, and calls they name are excluded from the
  executed set rather than reordered around.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

from langchain_core.messages import AIMessage, ToolMessage

from jarvis.execution.artifacts import parse_refs
from jarvis.graph.tool_accounting import tool_message_ok

ContractStatus = Literal[
    "NOT_REQUIRED",
    "SATISFIED",
    "MISSING_NO_ATTEMPT",
    "MISSING_INVALID_ARGS",
    "MISSING_PREEXECUTION_BLOCK",
    "MISSING_TOOL_FAILURE",
    "EXECUTED_NO_OBJECT",
    "OUTPUT_EVIDENCE_MISMATCH",
    "EVIDENCE_UNAVAILABLE",
]

#: The only status a completion repair may ever be offered for. Everything
#: else is either already satisfied, someone else's layer, or a defect of
#: ours -- see the module docstring.
REPAIRABLE: frozenset[str] = frozenset({"MISSING_NO_ATTEMPT"})


@dataclass(frozen=True)
class ContractVerdict:
    status: ContractStatus
    #: Which requirement this verdict is about ("chart/create"), for telemetry.
    requirement: str = ""
    #: Free-text detail: the block outcome code, the exception type, the
    #: tool's own error head. Never parsed, only reported and logged.
    reason: str = ""
    declared: tuple[str, ...] = ()
    matched: tuple[str, ...] = ()
    #: Set when the evidence contradicts itself -- nothing this turn produced
    #: the output, yet the working set gained one anyway (a concurrent writer,
    #: or accounting that lost a call). Suppresses repair on its own: asking
    #: the model to draw again while an unobserved writer is active would race
    #: it. Audited, never silently folded into the status.
    anomaly: bool = False

    @property
    def satisfied(self) -> bool:
        return self.status in ("SATISFIED", "NOT_REQUIRED")

    @property
    def repairable(self) -> bool:
        return self.status in REPAIRABLE and not self.anomaly


@dataclass(frozen=True)
class _Call:
    """One tool call of this turn, joined across the AIMessage that issued it
    and the ToolMessage that answered it (if any)."""
    call_id: str
    capability: str
    answered: bool = False
    ok: bool = False
    artifacts: tuple[str, ...] = field(default_factory=tuple)


# â”€â”€ path identity â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def canonical(path: object) -> str:
    """Compare-safe form of a filesystem path.

    Windows needs all three steps: `resolve` for `..`/relative segments and
    the short-8.3 form, `normpath` for separator direction, `normcase` for
    the case-insensitive filesystem. `strict=False` because the comparison
    must still work for a file that has since been moved or cleaned up --
    this asks "are these the same path", not "does it exist".
    """
    text = str(path or "").strip()
    if not text:
        return ""
    try:
        resolved = str(Path(text).resolve(strict=False))
    except (OSError, ValueError):  # pragma: no cover -- malformed path
        resolved = text
    return os.path.normcase(os.path.normpath(resolved))


# â”€â”€ reading the turn out of its messages â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def turn_tool_calls(messages: Iterable[Any], capabilities: Iterable[str]) -> list[_Call]:
    """Every call to `capabilities` in this turn, across ALL rounds.

    tool_accounting._last_executed_round() deliberately looks at the last
    round only -- it accounts for what just ran. A completion contract has to
    see the whole turn: a first round that failed validation and a second
    that executed are two different facts about the same requirement.

    Turn-scoped without a cutoff, because conversation history never carries
    tool calls: every turn gets a fresh thread_id, and history is compacted
    to plain text by agent._compact_completed_turn_for_history().
    """
    wanted = frozenset(capabilities)
    issued: list[tuple[str, str]] = []          # (call_id, capability), in order
    answers: dict[str, ToolMessage] = {}
    for message in messages or ():
        if isinstance(message, AIMessage):
            for call in getattr(message, "tool_calls", None) or ():
                name = str(call.get("name") or "")
                if name in wanted:
                    issued.append((str(call.get("id") or ""), name))
        elif isinstance(message, ToolMessage):
            answers[str(getattr(message, "tool_call_id", "") or "")] = message

    calls: list[_Call] = []
    for call_id, capability in issued:
        answer = answers.get(call_id)
        if answer is None:
            calls.append(_Call(call_id=call_id, capability=capability))
            continue
        ok = tool_message_ok(answer)
        calls.append(_Call(
            call_id=call_id, capability=capability, answered=True, ok=ok,
            # Only a SUCCESSFUL call's declaration is evidence of an output.
            # A failed call can still have declared something before it threw.
            artifacts=tuple(
                canonical(ref.path)
                for ref in parse_refs(getattr(answer, "artifact", None))
                if ref.kind == "chart"
            ) if ok else (),
        ))
    return calls


def _ids(history: Iterable[Any] | None, capabilities: Iterable[str]) -> set[str]:
    """tool_call_ids named by an append-only history, for the given tools."""
    wanted = frozenset(capabilities)
    out: set[str] = set()
    for entry in history or ():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("capability") or "") in wanted:
            out.add(str(entry.get("tool_call_id") or ""))
    return out


def _first_reason(history: Iterable[Any] | None, ids: set[str]) -> str:
    for entry in history or ():
        if isinstance(entry, dict) and str(entry.get("tool_call_id") or "") in ids:
            return str(entry.get("outcome") or entry.get("reason") or "")
    return ""


# â”€â”€ the table â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def classify(
    *,
    required: list[dict] | None,
    capabilities: Iterable[str],
    messages: Iterable[Any] | None = None,
    invalid_args_history: Iterable[Any] | None = None,
    preexecution_history: Iterable[Any] | None = None,
    registered_artifacts: Iterable[str] | None = None,
    baseline_artifacts: Iterable[str] | None = None,
    evidence_error: str = "",
) -> ContractVerdict:
    """One requirement in, one verdict out. See the module docstring.

    capabilities         tools that SATISFY the requirement (creation-only in
                         this phase: `plot_data`, never `chart_revise`).
    registered_artifacts the working set's artifacts for the requirement's
                         kind, read AFTER the turn.
    baseline_artifacts   the same, read BEFORE it. Diagnostic only -- it never
                         decides success, it only lets the anomaly below be
                         distinguished from "nothing happened".
    evidence_error       set by the caller when reading any of the above
                         raised; short-circuits to EVIDENCE_UNAVAILABLE.
    """
    requirement = _requirement_label(required)
    if not requirement:
        return ContractVerdict(status="NOT_REQUIRED")
    if evidence_error:
        return ContractVerdict(
            status="EVIDENCE_UNAVAILABLE", requirement=requirement, reason=evidence_error,
        )

    calls = turn_tool_calls(messages or (), capabilities)
    invalid_ids = _ids(invalid_args_history, capabilities)
    blocked_ids = _ids(preexecution_history, capabilities)

    # A blocked or rejected call still gets a stub ToolMessage, so it would
    # otherwise read as an execution that failed. Exclude by id rather than by
    # parsing the stub's text -- the marker vocabulary is not a contract.
    executed = [
        c for c in calls
        if c.answered and c.call_id not in invalid_ids and c.call_id not in blocked_ids
    ]
    succeeded = [c for c in executed if c.ok]

    registered = {canonical(p) for p in (registered_artifacts or ())}
    declared: set[str] = set()
    for call in succeeded:
        declared.update(call.artifacts)
    matched = declared & registered

    if succeeded:
        if matched:
            return ContractVerdict(
                status="SATISFIED", requirement=requirement,
                declared=tuple(sorted(declared)), matched=tuple(sorted(matched)),
            )
        if declared:
            return ContractVerdict(
                status="EXECUTED_NO_OBJECT", requirement=requirement,
                reason="declared artifact is not in the working set",
                declared=tuple(sorted(declared)),
            )
        return ContractVerdict(
            status="OUTPUT_EVIDENCE_MISMATCH", requirement=requirement,
            reason="tool reported success but declared no artifact",
        )

    if executed:
        return ContractVerdict(
            status="MISSING_TOOL_FAILURE", requirement=requirement,
            reason="the tool ran and reported an error",
        )
    if invalid_ids and any(c.call_id in invalid_ids for c in calls):
        return ContractVerdict(
            status="MISSING_INVALID_ARGS", requirement=requirement,
            reason=_first_reason(invalid_args_history, invalid_ids),
        )
    if calls:
        return ContractVerdict(
            status="MISSING_PREEXECUTION_BLOCK", requirement=requirement,
            reason=_first_reason(preexecution_history, blocked_ids),
        )

    # Nothing this turn produced the output. If the working set gained one
    # anyway, the evidence contradicts itself -- report it, and do NOT offer a
    # repair into a conversation something else is concurrently writing.
    baseline = {canonical(p) for p in (baseline_artifacts or ())}
    gained = registered - baseline if baseline_artifacts is not None else set()
    return ContractVerdict(
        status="MISSING_NO_ATTEMPT", requirement=requirement,
        reason="unattributed working-set artifact appeared" if gained else "",
        anomaly=bool(gained),
    )


#: The directive handed to the agent for its single completion-repair round.
#: It lives HERE, in the pure module, rather than beside the node that emits
#: it, because two places consume it and they must not drift: the node writes
#: it into graph state, and jarvis/agent.py strips it back out if the model
#: echoes it into its own answer. A sanitiser that guessed at the block's
#: extent instead of matching these exact lines would either leave half a
#: directive in the user's answer or eat the answer itself.
#:
#: Graph-internal, never conversation history: the entry points rebuild
#: history from the user's own message plus the final answer.
COMPLETION_REPAIR_MARKER = "[Completion Contract]"

REPAIR_DIRECTIVE_BODY: tuple[str, ...] = (
    "The user explicitly asked for a chart and no chart was produced this turn.",
    "Use your one remaining repair attempt to produce one.",
    "Infer the missing details (file path, column names) from the tool results "
    "already in this turn; do not ask the user a question.",
)

REPAIR_DIRECTIVE = "\n".join((COMPLETION_REPAIR_MARKER, *REPAIR_DIRECTIVE_BODY))


def strip_repair_directive(response: str) -> str:
    """Remove an echoed repair directive from the front of an answer.

    Exact-line matching, not a block regex. The directive has no blank line
    terminating it, so "everything until the next blank line" swallowed the
    answer whole -- caught by a test whose fixture simply lacked the blank
    line the first fixture happened to have.
    """
    if not response or COMPLETION_REPAIR_MARKER.casefold() not in response.casefold():
        return response
    lines = response.splitlines()
    start = next(
        (i for i, line in enumerate(lines)
         if line.strip().casefold().startswith(COMPLETION_REPAIR_MARKER.casefold())),
        None,
    )
    if start is None:
        return response
    end = start + 1
    known = {body.casefold() for body in REPAIR_DIRECTIVE_BODY}
    while end < len(lines) and (
        not lines[end].strip() or lines[end].strip().casefold() in known
    ):
        end += 1
    return "\n".join(lines[:start] + lines[end:]).lstrip("\n")


#: What this build knows how to verify. A requirement outside this set reads
#: as NOT_REQUIRED rather than raising -- an old checkpoint, or a requirement
#: a later phase adds, must resume as if the contract were absent instead of
#: crashing the turn or looping on a repair nothing can satisfy.
SUPPORTED_REQUIREMENTS: frozenset[tuple[str, str]] = frozenset({("chart", "create")})


def first_requirement(required: list[dict] | None) -> tuple[str, str] | None:
    """(kind, operation) of the first requirement this build understands."""
    for entry in required or ():
        if not isinstance(entry, dict):
            continue
        pair = (str(entry.get("kind") or ""), str(entry.get("operation") or ""))
        if pair in SUPPORTED_REQUIREMENTS:
            return pair
    return None


def _requirement_label(required: list[dict] | None) -> str:
    pair = first_requirement(required)
    return f"{pair[0]}/{pair[1]}" if pair else ""
