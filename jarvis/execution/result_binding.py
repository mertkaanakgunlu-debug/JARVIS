"""Deterministic terminal receipts for user-approved external writes.

The model may explain a result, but it is not the authority on whether an
approved side effect completed. This module consumes the always-on execution
ledger and replaces model narration only for calls whose authorization
provenance is structurally known to be ``user_approved``.

Raw arguments, tool output, fingerprints, signatures, nonces, and execution
identifiers are deliberately never rendered.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any


_USER_APPROVED = "user_approved"
_EXTERNAL_WRITE = "external_write"


def _is_bound_row(row: dict[str, Any]) -> bool:
    return (
        row.get("authorization") == _USER_APPROVED
        and row.get("side_effect_type") == _EXTERNAL_WRITE
        and row.get("confirmation_required") is True
    )


def checkpoint_requires_result_buffering(
    values: dict[str, Any] | None,
    *,
    include_pending: bool = True,
) -> bool:
    """Whether a resumed confirmation turn must withhold model prose.

    Existing bound ledger rows cover later confirmation rounds (approved A,
    then pending B). Pending requests cover the first approval before its
    execution row exists. This decides buffering only; a pending request is
    never itself treated as proof that the user approved it.
    """
    values = values or {}
    if any(_is_bound_row(row) for row in (values.get("tool_execution_ledger") or [])):
        return True
    if not include_pending:
        return False
    for entry in values.get("execution_requests") or []:
        request = entry.get("request") if isinstance(entry, dict) else None
        if not isinstance(request, dict):
            continue
        if (
            request.get("requires_confirmation") is True
            and request.get("side_effect_type") == _EXTERNAL_WRITE
        ):
            return True
    return False


def _safe_operation_label(row: dict[str, Any]) -> str:
    """Render one bounded capability identifier, never argument-derived text."""
    capability = str(row.get("tool") or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", capability):
        return capability
    return "operation"


def _verified_statuses(
    execution_envelopes: list[dict] | None,
) -> dict[str, tuple[str, str]]:
    if not execution_envelopes:
        return {}
    from jarvis.execution.summary import build_verified_summary

    summary = build_verified_summary(execution_envelopes)
    counts = Counter(op.execution_id for op in summary.operations)
    return {
        op.execution_id: (op.capability, op.display_status)
        for op in summary.operations
        if op.execution_id and counts[op.execution_id] == 1
    }


def _outcome(row: dict[str, Any], verified: dict[str, tuple[str, str]]) -> str:
    if row.get("outcome") == "unknown":
        return "unknown"
    verified_fact = verified.get(str(row.get("tool_call_id") or ""))
    verified_status = (
        verified_fact[1]
        if verified_fact and verified_fact[0] == row.get("tool")
        else None
    )
    if verified_status == "confirmed" and row.get("ok") is True:
        return "verified_success"
    if verified_status in {
        "failed", "partial", "reported_success_verification_failed",
    }:
        return "failed"
    return "success" if row.get("ok") is True else "failed"


def _denied_operations(preexecution_history: list[dict] | None) -> list[str]:
    labels: list[str] = []
    for row in preexecution_history or []:
        if not isinstance(row, dict) or row.get("outcome") != "user_denied":
            continue
        label = _safe_operation_label({"tool": row.get("capability") or "operation"})
        if label not in labels:
            labels.append(label)
    return labels


def bind_approved_external_write_result(
    model_text: str,
    *,
    execution_ledger: list[dict] | None = None,
    execution_envelopes: list[dict] | None = None,
    preexecution_history: list[dict] | None = None,
    execution_contract_mode: str = "off",
    language: str = "",
) -> str:
    """Return a code-authored receipt when approved external writes ran.

    No matching row means byte-for-byte pass-through, preserving read-only,
    auto-approved, local, legacy, and conversational turns. Envelope-derived
    verification is authoritative only in an ``enforce_*`` rollout mode;
    ``off`` and ``shadow`` remain observation-only. Unknown outcomes are never
    upgraded and no execution or retry is performed here.
    """
    bound_rows = [
        row for row in (execution_ledger or [])
        if isinstance(row, dict) and _is_bound_row(row)
    ]
    if not bound_rows:
        return model_text

    turkish = str(language).lower().startswith("tr")
    verified = (
        _verified_statuses(execution_envelopes)
        if str(execution_contract_mode).startswith("enforce_")
        else {}
    )
    ledger_id_counts = Counter(
        str(row.get("tool_call_id") or "")
        for row in (execution_ledger or [])
        if isinstance(row, dict)
    )
    verified = {
        execution_id: fact
        for execution_id, fact in verified.items()
        if ledger_id_counts[execution_id] == 1
    }
    operations = [
        (_safe_operation_label(row), _outcome(row, verified))
        for row in bound_rows
    ]
    denied = _denied_operations(preexecution_history)

    if turkish:
        status_text = {
            "success": "tamamlandı; araç/API başarılı bir sonuç döndürdü",
            "verified_success": "tamamlandı ve bağımsız sonkoşul doğrulaması geçti",
            "failed": "tamamlanmadı; yürütme veya doğrulama başarısız oldu",
            "unknown": (
                "sonucu bilinmiyor; zaman aşımı nedeniyle dış sistemde gerçekleşmiş "
                "olabilir. Otomatik olarak yeniden denenmedi"
            ),
        }
        if len(operations) == 1 and not denied:
            label, status = operations[0]
            if label == "operation":
                return f"Onayladığınız işlem {status_text[status]}."
            return f"Onayladığınız {label} işlemi {status_text[status]}."
        lines = [f"- {label}: {status_text[status]}." for label, status in operations]
        lines.extend(f"- {label}: reddettiniz; çalıştırılmadı." for label in denied)
        return "Onay akışındaki işlemlerin yürütme sonucu:\n" + "\n".join(lines)

    status_text = {
        "success": "completed; the tool/API returned success",
        "verified_success": "completed and passed an independent postcondition check",
        "failed": "did not complete; execution or verification failed",
        "unknown": (
            "has an unknown outcome; it may have reached the external service after "
            "the timeout. It was not retried automatically"
        ),
    }
    if len(operations) == 1 and not denied:
        label, status = operations[0]
        if label == "operation":
            return f"The approved operation {status_text[status]}."
        return f"The approved {label} operation {status_text[status]}."
    lines = [f"- {label}: {status_text[status]}." for label, status in operations]
    lines.extend(f"- {label}: you denied it; it was not executed." for label in denied)
    return "Execution results for the confirmation flow:\n" + "\n".join(lines)
