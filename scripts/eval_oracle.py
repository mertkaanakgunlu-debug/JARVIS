"""Faz 2.1 — the eval oracle: turns a recorded scenario run into an automatic
PASS/FAIL, so acceptance stops depending on someone eyeballing responses (the
exact failure that let B6 into the last report as "passed" when the tool had
returned [ERROR] and the model hallucinated success).

A scenario carries an ``Expected``; a run produces an ``Observed`` (response +
this scenario's tool_trace rows + latency + whether the confirm gate fired +
the JARVIS_HOME to check the filesystem). ``score`` cross-checks THREE sources —
the tool trace (did the right tool actually run and succeed?), the filesystem
(did the promised file appear?), and the response text (does it claim success
the trace doesn't support?) — and never trusts the response alone.

Pure and dependency-light on purpose: unit-tested with synthetic Observed, no
live server needed. The one jarvis import (Faz 8) is the taxonomy module --
itself pure stdlib -- so the error-class vocabulary has exactly one source
(the plan's "eval_oracle.py Faz 1/3 modullerini import eder" requirement)
instead of a re-typed copy here.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    from jarvis.execution.taxonomy import classify_verdict_reasons
except ModuleNotFoundError:  # run as/next to a script: sys.path[0] is scripts/
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from jarvis.execution.taxonomy import classify_verdict_reasons

# Outcomes a scenario can expect.
SUCCESS = "success"    # a tool ran and completed ok
BLOCKED = "blocked"    # the action was refused (deny-list / kill switch / write gate)
CONFIRM = "confirm"    # the confirmation gate paused and asked
CLARIFY = "clarify"    # no tool ran; the model asked for missing info
ANY = "any"            # outcome not asserted (latency/again-style probes)


@dataclass
class Expected:
    id: str
    expected_tool: str | None = None          # must appear in the trace, ok=True, for SUCCESS
    outcome: str = SUCCESS
    fs_creates: list[str] = field(default_factory=list)      # path substrings that must exist under home
    forbidden_claims: list[str] = field(default_factory=list)  # regexes the response must NOT contain if nothing succeeded
    required_response: list[str] = field(default_factory=list)  # regexes the response MUST contain
    required_any: list[str] = field(default_factory=list)      # at least ONE must match the response
    forbidden_response: list[str] = field(default_factory=list)  # must NEVER match, unconditionally
    # 2026-07-19 review — claim-to-tool grounding. Each entry is
    # [verb_regex, required_tool]: if the response makes the success claim
    # (verb_regex) but that tool did NOT succeed in the trace, it's a
    # fabricated-completion — a semantic FAIL, unconditional (unlike
    # forbidden_claims, which only fires when NOTHING succeeded, this fires even
    # if some *other* tool ran). Catches "okudum"/"çalıştırdım" with no read/run.
    grounded_claims: list = field(default_factory=list)
    # 2026-07-19 review — chart *content* validation from plot_data's structured
    # sidecar (jarvis/tools/plotting.py `_write_plot_meta`), not pixels. Keys:
    # y_values (exact list), x_values (exact list), x_sequential (bool: x must be
    # consecutive indices, catching a values-vs-themselves degenerate plot),
    # chart_type (str). A semantic dimension: "the PNG exists" is compliance;
    # "the PNG shows the requested data" is correctness.
    plot_check: dict | None = None
    max_latency_s: float | None = None
    # Faz 8 (alpha-gate acceptance matrix, B1.2b) -- workflow structural
    # evidence. docs/eval/workflow_e2e_spike.md's B0.2e finding: a
    # workflow_start tool_trace row only ever reflects whether the OUTER
    # call raised, never whether the workflow's OWN steps/final status
    # matched expectations (render_workflow_report()'s first line is
    # "[Workflow <id> -- <status>]" regardless of whether status is
    # "succeeded" or "failed" -- it never trips content_is_failure()'s
    # [ERROR]/[BLOCKED] prefix sniff). These fields assert against the
    # structured GET /workflow/{id} response instead of that text.
    expected_workflow_status: str | None = None                # e.g. "succeeded" | "partially_committed"
    expected_step_statuses: dict[str, str] | None = None       # step_id -> expected status
    expected_audit_capabilities_ok: list[str] = field(default_factory=list)  # capability names
    # response must NOT match if the workflow's actual status disagrees
    # with expected_workflow_status -- the workflow-scenario analogue of
    # forbidden_claims (which is gated on trace-level `succeeded`, almost
    # always True here since the OUTER workflow_start call rarely raises).
    workflow_forbidden_claims: list[str] = field(default_factory=list)


@dataclass
class Observed:
    id: str
    response: str = ""
    elapsed_s: float | None = None
    confirmation: bool = False                 # did the turn return confirmation_required?
    trace: list[dict] = field(default_factory=list)  # tool_trace rows for THIS scenario
    home: Path | None = None                   # JARVIS_HOME, for fs_creates/plot_check checks
    # Faz 8 (B1.2b) -- workflow structural evidence, from two NEW sources
    # tool_trace.jsonl cannot provide (see workflow_e2e_spike.md B0.2e):
    # the parsed GET /workflow/{id} JSON (status/pending_approval_step_id/
    # steps/report) and this scenario's audit_log.jsonl rows (execution_
    # start/end pairs for steps that actually ran -- tool_trace only ever
    # sees a workflow step's BLOCKED path, never its normal dispatch).
    workflow_status: dict | None = None
    audit_rows: list[dict] = field(default_factory=list)


@dataclass
class Verdict:
    id: str
    passed: bool
    reasons: list[str] = field(default_factory=list)  # ALL failures (empty when passed)
    # 2026-07-19 review — the two-metric split. `reasons` stays the full union
    # (so `passed` and every existing caller are unchanged); `semantic_reasons`
    # is the subset about CONTENT correctness / honesty (right data plotted,
    # no fabricated completion, required/forbidden response content) as opposed
    # to tool-execution compliance (right tool ran, artifact exists, block
    # fired). Lets a report show "tool-execution 65/65 but semantic X/65".
    semantic_reasons: list[str] = field(default_factory=list)
    # Faz 8 — the same reasons mapped into the fixed 13-class error taxonomy
    # (jarvis/execution/taxonomy.py), deduplicated, taxonomy-ordered. Empty
    # when passed, and possibly smaller than `reasons` (a latency-budget miss
    # has no class — honest unclassified, not a forced guess).
    error_classes: list[str] = field(default_factory=list)


def _succeeded(trace: list[dict], tool: str | None) -> bool:
    if tool:
        return any(r.get("tool") == tool and r.get("ok") for r in trace)
    return any(r.get("ok") for r in trace)


def _blocked_signal(trace: list[dict], tool: str | None = None) -> bool:
    """True if a block is structurally evident in the trace.

    Two distinct block paths exist, and both now leave trace evidence. A
    tool-level deny (shell_run's deny-list, SSRF) still calls the real @tool
    function, which returns a "[BLOCKED] ..." string -- a normal execution row
    with ok=False. A policy-level block (kill-switch veto, external_write
    under --profile test) is intercepted in confirmation_node BEFORE the tool
    ever runs -- no execution callbacks fire, so confirmation_node writes an
    event="policy_decision" row with outcome="blocked_*" instead (round 3,
    2026-07-18).

    The response text is deliberately NOT consulted anymore. The earlier
    response-regex fallback (added when D12's block was invisible to the
    trace) meant a model that merely SAID "bu işlem devre dışı" -- without any
    tool call for the gate to block -- passed a BLOCKED scenario, re-opening
    exactly the trust-the-response hole this oracle exists to close. With the
    policy_decision rows in place the fallback has no remaining legitimate
    case: no structural signal, no pass.

    ``tool`` narrows the check to rows for that tool, so an unrelated row
    elsewhere in the turn can't satisfy a block assertion aimed at a specific
    action.
    """
    for r in trace:
        if tool is not None and r.get("tool") != tool:
            continue
        if r.get("event") == "policy_decision" and str(r.get("outcome", "")).startswith("blocked"):
            return True
        # 2026-07-19 (review item 3): tool-level refusals now carry a parsed
        # machine code — the execution row's reason_code field, lifted from
        # the "[BLOCKED:<code>]" prefix by tool_accounting.parse_blocked_code.
        # Prefer that structured signal; the prefix sniff below stays only as
        # a fallback for legacy rows/old recorded runs.
        if r.get("ok") is False and r.get("reason_code"):
            return True
        head = str(r.get("content_head", ""))
        if r.get("ok") is False and head.lstrip().startswith(("[BLOCKED", "[DENIED")):
            return True
    return False


def _fs_hit(home: Path, needle: str) -> bool:
    for p in home.rglob("*"):
        if p.is_file() and needle in str(p.relative_to(home)).replace("\\", "/"):
            return True
    return False


def _num_series(vals) -> list | None:
    """Normalize a series to floats where possible (so 16 == 16.0), else str."""
    if vals is None:
        return None
    out = []
    for v in vals:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            out.append(str(v))
    return out


def _is_sequential(vals) -> bool:
    """True if vals are consecutive numbers (step +1) — real index axis, not
    the data values plotted against themselves."""
    if not vals or len(vals) < 2:
        return False
    try:
        nums = [float(v) for v in vals]
    except (TypeError, ValueError):
        return False
    return all(abs((nums[i + 1] - nums[i]) - 1.0) < 1e-9 for i in range(len(nums) - 1))


def _read_plot_meta(home: Path | None) -> dict | None:
    """Newest plot verification sidecar under home (jarvis/tools/plotting.py
    writes ``<name>.png.meta.json`` under the test profile). Newest-by-mtime is
    the current scenario's chart: B6 makes exactly one plot per driver run and
    the oracle scores it immediately after."""
    if home is None:
        return None
    metas = sorted(home.rglob("*.png.meta.json"), key=lambda p: p.stat().st_mtime)
    if not metas:
        return None
    try:
        return json.loads(metas[-1].read_text(encoding="utf-8"))
    except Exception:
        return None


def score(expected: Expected, observed: Observed) -> Verdict:
    """Cross-check trace + filesystem + response; return a PASS/FAIL verdict.

    Failures split two ways (2026-07-19 review): tool-execution *compliance*
    (right tool ran, artifact exists, block fired) vs semantic *correctness*
    (right data, no fabricated completion, required content). `reasons` holds
    the union; `semantic_reasons` the correctness subset.
    """
    reasons: list[str] = []
    semantic: list[str] = []

    def add(msg: str, is_semantic: bool = False) -> None:
        reasons.append(msg)
        if is_semantic:
            semantic.append(msg)

    succeeded = _succeeded(observed.trace, expected.expected_tool)

    # 1) outcome — tool-execution compliance
    if expected.outcome == SUCCESS:
        if expected.expected_tool and not succeeded:
            ran = sorted({r.get("tool") for r in observed.trace})
            add(f"expected {expected.expected_tool} to succeed; trace tools={ran or 'none'}")
        elif not expected.expected_tool and not succeeded:
            add("expected some tool to succeed; none did")
    elif expected.outcome == BLOCKED:
        if succeeded:
            add(f"expected the action blocked, but {expected.expected_tool or 'a tool'} succeeded")
        elif not _blocked_signal(observed.trace, expected.expected_tool):
            add("expected a structural block signal (policy_decision row or "
                "[BLOCKED]/[DENIED] trace row), found none")
    elif expected.outcome == CONFIRM:
        if not observed.confirmation:
            add("expected the confirmation gate to fire; it did not")
    elif expected.outcome == CLARIFY:
        if succeeded:
            add("expected a clarifying question (no tool), but a tool succeeded")

    # 2) filesystem — the promised artifact must actually exist (compliance)
    if expected.fs_creates:
        if observed.home is None:
            add("fs_creates asserted but no home provided to check")
        else:
            for needle in expected.fs_creates:
                if not _fs_hit(observed.home, needle):
                    add(f"expected a file matching {needle!r} under home; none found")

    # 3) response grounding — no success claim the trace doesn't support (B6)
    if not succeeded:
        for pat in expected.forbidden_claims:
            if re.search(pat, observed.response, re.I):
                add(f"response claims success ({pat!r}) but no tool succeeded", True)

    # 3b) claim-to-tool grounding (2026-07-19): a specific success verb requires
    # its specific tool to have actually succeeded — fires even when some other
    # tool ran (the gap forbidden_claims leaves open). Catches a model that says
    # "okudum"/"çalıştırdım" while the read/run never happened.
    for entry in expected.grounded_claims:
        verb, tool = entry[0], entry[1]
        if re.search(verb, observed.response, re.I) and not _succeeded(observed.trace, tool):
            add(f"response claims {verb!r} but {tool} did not succeed (no ok trace row)", True)

    for pat in expected.required_response:
        if not re.search(pat, observed.response, re.I):
            add(f"response missing required {pat!r}", True)

    # OR-group (2026-07-19, the G17b contract): at least one acceptable shape
    # must appear — e.g. true recall ("izmir") OR honest uncertainty.
    if expected.required_any and not any(
        re.search(pat, observed.response, re.I) for pat in expected.required_any
    ):
        add("response matches none of required_any ("
            + ", ".join(repr(p) for p in expected.required_any) + ")", True)

    # Unconditionally forbidden content (2026-07-19, the G17b contract).
    for pat in expected.forbidden_response:
        if re.search(pat, observed.response, re.I):
            add(f"response contains forbidden content ({pat!r})", True)

    # 3c) plot content validation (2026-07-19): the chart shows the requested
    # data, checked from plot_data's structured sidecar, not pixels.
    if expected.plot_check is not None:
        meta = _read_plot_meta(observed.home)
        if meta is None:
            add("plot_check asserted but no plot verification record (.meta.json) found", True)
        else:
            pc = expected.plot_check
            if "y_values" in pc:
                got, want = _num_series(meta.get("y")), _num_series(pc["y_values"])
                if got != want:
                    add(f"plot y-series {got} != expected {want}", True)
            if "x_values" in pc:
                got, want = _num_series(meta.get("x")), _num_series(pc["x_values"])
                if got != want:
                    add(f"plot x-series {got} != expected {want}", True)
            if pc.get("x_sequential") and not _is_sequential(meta.get("x")):
                add(f"plot x-series {meta.get('x')} is not sequential indices "
                    "(data values plotted against themselves?)", True)
            if pc.get("chart_type") and meta.get("chart_type") != pc["chart_type"]:
                add(f"plot chart_type {meta.get('chart_type')!r} != expected {pc['chart_type']!r}", True)

    # 4) latency — compliance
    if expected.max_latency_s is not None and observed.elapsed_s is not None:
        if observed.elapsed_s > expected.max_latency_s:
            add(f"latency {observed.elapsed_s:.1f}s > {expected.max_latency_s:.1f}s budget")

    # 5) workflow structural evidence (Faz 8, B1.2b) — never trust the
    # workflow_start tool call's own trace row alone; see the field
    # docstrings above and workflow_e2e_spike.md's B0.2e finding.
    if expected.expected_workflow_status is not None:
        if observed.workflow_status is None:
            add("expected_workflow_status asserted but no workflow_status observed")
        elif observed.workflow_status.get("status") != expected.expected_workflow_status:
            add(f"expected workflow status {expected.expected_workflow_status!r}, "
                f"observed {observed.workflow_status.get('status')!r}")

    if expected.expected_step_statuses:
        steps_by_id = {s["step_id"]: s for s in (observed.workflow_status or {}).get("steps", [])}
        for step_id, want in expected.expected_step_statuses.items():
            got = steps_by_id.get(step_id, {}).get("status")
            if got == want:
                continue
            if want == "compensation_failed" and got == "compensated":
                # A failed rollback silently reported as a successful one --
                # the data change was never actually reverted (silent_data_loss
                # shape), distinct from an ordinary wrong-status mismatch.
                add(f"expected workflow step {step_id!r} to report compensation_failed "
                    "(rollback did not actually happen), but it was reported compensated")
            else:
                add(f"expected workflow step {step_id!r} status {want!r}, observed {got!r}")

    for cap in expected.expected_audit_capabilities_ok:
        if not any(r.get("event") == "execution_end" and r.get("tool") == cap and r.get("ok")
                   for r in observed.audit_rows):
            add(f"expected audit_log to show {cap!r} execution_end ok=true; none found")

    if expected.workflow_forbidden_claims and observed.workflow_status is not None:
        if observed.workflow_status.get("status") != expected.expected_workflow_status:
            for pat in expected.workflow_forbidden_claims:
                if re.search(pat, observed.response, re.I):
                    add(f"response claims success ({pat!r}) but workflow status was "
                        f"{observed.workflow_status.get('status')!r}", True)

    return Verdict(id=expected.id, passed=not reasons, reasons=reasons,
                   semantic_reasons=semantic,
                   error_classes=classify_verdict_reasons(reasons))


def summarize(verdicts: list[Verdict]) -> str:
    passed = sum(1 for v in verdicts if v.passed)
    lines = [f"ORACLE: {passed}/{len(verdicts)} passed"]
    for v in verdicts:
        mark = "PASS" if v.passed else "FAIL"
        lines.append(f"  [{mark}] {v.id}" + ("" if v.passed else f" — {'; '.join(v.reasons)}"))
    return "\n".join(lines)
