"""Execution-verification rollout metrics -- Post-MVP Faz 1, plan item 1.

The plan's gate for turning `execution_contract_mode` up from shadow to
enforce is a MEASUREMENT, not a decision: *"100 gercek artifact isleminde 0
false block gorulmeden enforce yok."* That sentence is unrunnable unless
something counts real artifact operations and real blocks, so this module
counts them.

One JSON object per line at `data/execution_verification.jsonl`, same
append-only shape and never-raises discipline as jarvis/audit_log.py, and
for the same reason -- a metric that can break a turn is worse than no
metric. It is deliberately a SEPARATE file from audit_log.jsonl: the audit
log answers "what did this system do to the outside world", this answers
"how well is the verification layer performing", and mixing rollout
telemetry into a safety audit trail would make the audit trail harder to
read for its actual purpose.

**On `false_positive`.** The plan lists it alongside the other counters, and
it is the one number this module refuses to infer. A false positive means
"verification contradicted a claim that was actually true" -- if code could
detect that, the verification would simply not have fired. The detector's
own precision guard already removes the auto-detectable class (a named file
that really is on disk is never called unbacked; see evidence.py), so what
remains needs a human to look. mark_false_positive() writes that human
judgment into the same stream, and summarize() reports the count as
"false_positive_known", never as a measured rate. An honest zero here means
"none reported", not "none happened", and enforce_gate_status() says so.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from jarvis import paths

_lock = threading.Lock()

# postcondition_verdict values, mirrored from
# jarvis.execution.summary._postcondition_verdict -- the counter names the
# plan asked for (verified / unverified / failed) are exactly these.
_VERDICTS = ("verified", "unverified", "failed", "not_applicable")


def _path() -> Path:
    # Resolved per call, not at import -- JARVIS_HOME may be set by a test
    # fixture or --profile test after this module loads (audit_log's note).
    return paths.data_dir() / "execution_verification.jsonl"


def _append(entry: dict[str, Any]) -> None:
    entry = {"ts": datetime.now().isoformat(), **entry}
    try:
        line = json.dumps(entry, ensure_ascii=False, default=str)
    except Exception:
        return
    try:
        with _lock:
            target = _path()
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


def record_verification(
    *,
    mode: str,
    capability: str,
    display_status: str,
    postcondition_verdict: str,
    artifacts_declared: int = 0,
) -> None:
    """One row per verified operation. Called for every operation in a turn's
    summary whenever the contract mode is not "off"."""
    _append({
        "event": "verification",
        "mode": mode,
        "capability": capability,
        "display_status": display_status,
        "postcondition_verdict": postcondition_verdict,
        "artifacts_declared": artifacts_declared,
    })


def record_claim_gate(
    *,
    mode: str,
    enforced: bool,
    fired: bool,
    reasons: list[str] | None = None,
    unbacked_files: list[str] | None = None,
    repaired: bool | None = None,
    blocked: bool = False,
) -> None:
    """One row per turn the unbacked-claim gate evaluated (evidence.py).

    fired    -- the response contradicted the evidence.
    repaired -- the single bounded repair round produced a clean answer.
    blocked  -- repair failed too, so the user got the honest failure report
                instead of the model's text. `blocked` is the number the
                enforce gate's "0 false block" clause is about.
    """
    _append({
        "event": "claim_gate",
        "mode": mode,
        "enforced": enforced,
        "fired": fired,
        "reasons": reasons or [],
        "unbacked_files": unbacked_files or [],
        "repaired": repaired,
        "blocked": blocked,
    })


def record_output_contract(
    *,
    mode: str,
    event: str,
    status: str,
    requirement: str = "",
    action: str = "",
    reason: str = "",
    anomaly: bool = False,
    initial_status: str = "",
    repair_reason: str = "",
    repair_success: bool | None = None,
) -> None:
    """The completion contract's own rows (Post-MVP Faz 6).

    Two events, deliberately separate. `decision` is written on every pass of
    the node -- so a turn that repairs writes one for the repair pass and one
    `terminal` when it comes back. Folding them into a single row would make
    `repair_triggered` and `repair_success` unreconstructable, which are two
    of the A/B's pre-registered secondary metrics.

    Kept apart from record_claim_gate/record_verification for the same reason
    the node is separate from `verify`: those must stay exactly one row per
    turn each, and they would not if a repaired turn's second pass could add
    to them.
    """
    _append({
        "event": f"output_contract_{event}",
        "mode": mode,
        "status": status,
        "requirement": requirement,
        "action": action,
        "reason": reason,
        "anomaly": anomaly,
        **({"initial_status": initial_status} if initial_status else {}),
        **({"repair_reason": repair_reason} if repair_reason else {}),
        **({"repair_success": repair_success} if repair_success is not None else {}),
    })


def mark_false_positive(*, note: str, unbacked_files: list[str] | None = None) -> None:
    """Record an operator's judgment that a fired gate was wrong. See this
    module's docstring for why this cannot be automated."""
    _append({
        "event": "false_positive",
        "note": note,
        "unbacked_files": unbacked_files or [],
    })


def _read() -> list[dict[str, Any]]:
    target = _path()
    if not target.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in target.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        return []
    return out


def summarize() -> dict[str, Any]:
    """The plan's counters, computed from the stream.

    verification_total / verified / unverified / failed are per-OPERATION.
    artifact_operations counts only operations that actually declared an
    artifact -- that is the population the enforce gate's "100 real artifact
    operations" clause refers to, and it is much smaller than the total.
    """
    rows = _read()
    counts = {v: 0 for v in _VERDICTS}
    verification_total = 0
    artifact_operations = 0
    gate_fired = 0
    gate_repaired = 0
    gate_blocked = 0
    false_positive_known = 0
    for row in rows:
        event = row.get("event")
        if event == "verification":
            verification_total += 1
            verdict = str(row.get("postcondition_verdict") or "")
            if verdict in counts:
                counts[verdict] += 1
            if int(row.get("artifacts_declared") or 0) > 0:
                artifact_operations += 1
        elif event == "claim_gate":
            if row.get("fired"):
                gate_fired += 1
            if row.get("repaired"):
                gate_repaired += 1
            if row.get("blocked"):
                gate_blocked += 1
        elif event == "false_positive":
            false_positive_known += 1
    return {
        "verification_total": verification_total,
        "verified": counts["verified"],
        "unverified": counts["unverified"],
        "failed": counts["failed"],
        "not_applicable": counts["not_applicable"],
        "artifact_operations": artifact_operations,
        "claim_gate_fired": gate_fired,
        "claim_gate_repaired": gate_repaired,
        "claim_gate_blocked": gate_blocked,
        "false_positive_known": false_positive_known,
    }


# The plan's threshold, as a number rather than a sentence.
ENFORCE_GATE_MIN_ARTIFACT_OPERATIONS = 100


def enforce_gate_status() -> dict[str, Any]:
    """Is the shadow->enforce promotion earned yet?

    `ready` is True only when at least
    ENFORCE_GATE_MIN_ARTIFACT_OPERATIONS real artifact operations have been
    observed AND no false positive has been reported. The returned `caveat`
    is not decoration: zero reported false positives is an absence of
    reports, and a reader deciding to flip a production switch should see
    that stated rather than infer a clean record.
    """
    s = summarize()
    ready = (
        s["artifact_operations"] >= ENFORCE_GATE_MIN_ARTIFACT_OPERATIONS
        and s["false_positive_known"] == 0
    )
    return {
        **s,
        "required_artifact_operations": ENFORCE_GATE_MIN_ARTIFACT_OPERATIONS,
        "ready": ready,
        "caveat": (
            "false_positive_known counts operator-reported false blocks only; "
            "it is never inferred, so 0 means 'none reported', not 'none occurred'"
        ),
    }
