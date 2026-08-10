"""Approve-side terminal result binding regression coverage.

These tests keep three facts separate: what the tool runtime recorded, what
the model narrated, and what the shared terminal finalizer exposes to the
user.  No real external service is called.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.errors import GraphInterrupt

from jarvis.agent import finalize_terminal_response
from jarvis.config import Settings
from jarvis.execution.request import ExecutionRequest
from jarvis.execution.result_binding import checkpoint_requires_result_buffering
from jarvis.graph.tool_accounting import make_tool_result_accounting_node


def _request(
    *,
    requires_confirmation: bool = True,
    action: str = "send",
    side_effect_type: str = "external_write",
    risk_level: int = 3,
) -> ExecutionRequest:
    now = datetime.now(timezone.utc)
    return ExecutionRequest(
        execution_id="exec-approved-1",
        capability="gmail",
        action=action,
        normalized_args_digest="digest",
        target_resource="gmail",
        risk_level=risk_level,
        requires_confirmation=requires_confirmation,
        allowed=True,
        side_effect_type=side_effect_type,
        created_at=now.isoformat(),
        expiry=(now + timedelta(minutes=5)).isoformat(),
        single_use_nonce="nonce",
    )


async def _accounted_result(
    content: str,
    *,
    status: str | None = None,
    requires_confirmation: bool = True,
    user_approved: bool = True,
    action: str = "send",
    side_effect_type: str = "external_write",
    confirmation_gate_enabled: bool = True,
) -> dict:
    call_id = "call-1"
    request = _request(
        requires_confirmation=requires_confirmation,
        action=action,
        side_effect_type=side_effect_type,
    )
    ai = AIMessage(
        content="",
        tool_calls=[{
            "name": "gmail",
            "args": {
                "action": action,
                "to": "recipient@example.com",
                "subject": "Status",
                "body": "secret body must not reach the receipt",
            },
            "id": call_id,
            "type": "tool_call",
        }],
    )
    tool_message = ToolMessage(
        content=content,
        tool_call_id=call_id,
        name="gmail",
        **({"status": status} if status else {}),
    )
    state = {
        "messages": [ai, tool_message],
        "execution_requests": [{
            "tool_call_id": call_id,
            "request": request.model_dump(),
            "signature": "test-signature",
        }],
        "user_approved_execution_ids": (
            [request.execution_id] if user_approved else []
        ),
    }
    node = make_tool_result_accounting_node(
        Settings(
            _env_file=None,
            execution_contract_mode="shadow",
            confirmation_gate_enabled=confirmation_gate_enabled,
        )
    )
    return await node(state)


@pytest.mark.asyncio
async def test_reproducer_separates_execution_model_narration_and_visible_text(
    isolated_cwd,
):
    accounted = await _accounted_result("[Gmail] Email sent")
    actual_execution = accounted["tool_execution_ledger"][0]
    model_narration = "E-postanın gerçekten gönderildiğinden emin değilim."
    visible_text_without_facts = finalize_terminal_response(model_narration)

    assert actual_execution["ok"] is True
    assert "emin değilim" in model_narration
    assert visible_text_without_facts == model_narration


@pytest.mark.asyncio
async def test_approved_success_replaces_fabricated_uncertainty(isolated_cwd):
    accounted = await _accounted_result("[Gmail] Email sent")
    model_narration = "E-postanın gerçekten gönderildiğinden emin değilim."

    visible_text = finalize_terminal_response(
        model_narration,
        execution_ledger=accounted["tool_execution_ledger"],
        execution_envelopes=accounted.get("execution_envelopes"),
        language="tr",
    )

    assert "emin değilim" not in visible_text
    assert "başarılı" in visible_text.lower()
    assert "bağımsız" not in visible_text.lower()
    assert "secret body" not in visible_text


@pytest.mark.asyncio
async def test_approved_failure_replaces_fabricated_success(isolated_cwd):
    accounted = await _accounted_result(
        "[TOOL_ERROR]\ncategory=provider_error\nretryable=false",
        status="error",
    )

    visible_text = finalize_terminal_response(
        "E-posta başarıyla gönderildi.",
        execution_ledger=accounted["tool_execution_ledger"],
        execution_envelopes=accounted.get("execution_envelopes"),
        language="tr",
    )

    assert "başarıyla gönderildi" not in visible_text
    assert "tamamlanmadı" in visible_text
    assert "başarısız" in visible_text


@pytest.mark.asyncio
async def test_approved_timeout_is_unknown_and_never_rendered_as_success(isolated_cwd):
    accounted = await _accounted_result(
        "[TOOL_ERROR]\ncategory=timeout\nretryable=false\n"
        "execution_may_still_be_running=true\noutcome=unknown",
        status="error",
    )

    visible_text = finalize_terminal_response(
        "E-posta başarıyla gönderildi.",
        execution_ledger=accounted["tool_execution_ledger"],
        execution_envelopes=accounted.get("execution_envelopes"),
        language="tr",
    )

    assert accounted["tool_execution_ledger"][0]["outcome"] == "unknown"
    assert "sonucu bilinmiyor" in visible_text
    assert "yeniden denenmedi" in visible_text
    assert "başarıyla gönderildi" not in visible_text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requires_confirmation", "user_approved", "gate_enabled"),
    [
        (False, True, True),
        (True, False, True),
        (True, True, False),
    ],
)
async def test_noninteractive_or_unconfirmed_calls_are_never_user_approved(
    isolated_cwd,
    requires_confirmation,
    user_approved,
    gate_enabled,
):
    accounted = await _accounted_result(
        "[Gmail] Email sent",
        requires_confirmation=requires_confirmation,
        user_approved=user_approved,
        confirmation_gate_enabled=gate_enabled,
    )
    row = accounted["tool_execution_ledger"][0]

    visible_text = finalize_terminal_response(
        "original narration",
        execution_ledger=accounted["tool_execution_ledger"],
        language="en",
    )

    assert row["authorization"] == "auto_approved"
    assert visible_text == "original narration"


@pytest.mark.asyncio
async def test_read_only_and_local_operations_remain_unchanged(isolated_cwd):
    read_only = await _accounted_result(
        "[Gmail] No unread messages",
        requires_confirmation=False,
        user_approved=False,
        action="list_unread",
        side_effect_type="external_read",
    )
    local = await _accounted_result(
        "local write done",
        action="write",
        side_effect_type="local_write",
    )

    for accounted in (read_only, local):
        original = "Model narration stays intact."
        assert finalize_terminal_response(
            original,
            execution_ledger=accounted["tool_execution_ledger"],
            language="en",
        ) == original


@pytest.mark.asyncio
async def test_approved_a_then_denied_b_preserves_both_facts(isolated_cwd):
    approved_a = await _accounted_result("[Gmail] Email sent")

    visible_text = finalize_terminal_response(
        "İşlemi reddettiniz. Komut çalıştırılmadı.",
        execution_ledger=approved_a["tool_execution_ledger"],
        execution_envelopes=approved_a.get("execution_envelopes"),
        preexecution_history=[{
            "round": 2,
            "tool_call_id": "call-b",
            "capability": "google_drive",
            "outcome": "user_denied",
            "reason": "",
        }],
        language="tr",
    )

    assert "gmail" in visible_text
    assert "başarılı" in visible_text
    assert "google_drive" in visible_text
    assert "reddettiniz" in visible_text
    assert "Komut çalıştırılmadı" not in visible_text


@pytest.mark.parametrize(
    ("b_extra", "expected_b_status"),
    [
        ({"ok": False}, "did not complete"),
        ({"ok": False, "outcome": "unknown"}, "has an unknown outcome"),
    ],
)
def test_approved_a_success_then_b_non_success_keeps_per_operation_truth(
    b_extra,
    expected_b_status,
):
    common = {
        "authorization": "user_approved",
        "side_effect_type": "external_write",
        "confirmation_required": True,
    }
    ledger = [
        {**common, "tool": "gmail", "tool_call_id": "call-a", "ok": True},
        {**common, "tool": "google_drive", "tool_call_id": "call-b", **b_extra},
    ]

    visible_text = finalize_terminal_response(
        "Everything succeeded.", execution_ledger=ledger, language="en",
    )

    assert "gmail: completed" in visible_text
    assert f"google_drive: {expected_b_status}" in visible_text
    assert "Everything succeeded" not in visible_text


@pytest.mark.asyncio
async def test_bound_receipt_never_leaks_protocol_or_sensitive_fields(isolated_cwd):
    accounted = await _accounted_result("[Gmail] Email sent")
    visible_text = finalize_terminal_response(
        '[Tool execution summary: gmail ok] {"__jarvis_final__": true}',
        execution_ledger=accounted["tool_execution_ledger"],
        execution_envelopes=accounted.get("execution_envelopes"),
        language="tr",
    )

    forbidden = (
        "__jarvis", "execution_id", "fingerprint", "signature", "nonce",
        "recipient@example.com", "secret body",
    )
    assert all(value not in visible_text for value in forbidden)


def test_bound_receipt_rejects_argument_derived_labels_and_sensitive_paths():
    visible_text = finalize_terminal_response(
        "model text",
        execution_ledger=[{
            "tool": "C:/Users/owner/Secrets/token.txt",
            "action": "token_live_secret_value",
            "tool_call_id": "call-sensitive",
            "ok": True,
            "authorization": "user_approved",
            "side_effect_type": "external_write",
            "confirmation_required": True,
        }],
        language="en",
    )

    assert "approved operation completed" in visible_text
    assert "C:/Users" not in visible_text
    assert "token_live_secret_value" not in visible_text


def test_duplicate_tool_call_ids_cannot_cross_bind_independent_verification():
    ledger = [
        {
            "tool": "gmail", "tool_call_id": "reused-call", "ok": False,
            "authorization": "user_approved", "side_effect_type": "external_write",
            "confirmation_required": True,
        },
        {
            "tool": "google_drive", "tool_call_id": "reused-call", "ok": True,
            "authorization": "user_approved", "side_effect_type": "external_write",
            "confirmation_required": True,
        },
    ]
    verified_envelope = {
        "execution_id": "reused-call",
        "capability": "google_drive",
        "status": "success",
        "inputs_digest": "digest",
        "postconditions": [{
            "spec": {
                "kind": "record_exists",
                "source": "tool_contract",
                "severity": "required",
            },
            "status": "verified",
        }],
    }

    visible_text = finalize_terminal_response(
        "model text",
        execution_ledger=ledger,
        execution_envelopes=[verified_envelope],
        execution_contract_mode="enforce_reversible",
        language="en",
    )

    assert "gmail: did not complete" in visible_text
    assert "independent postcondition" not in visible_text


def test_independent_verification_wording_requires_unique_real_evidence():
    row = {
        "tool": "record_writer", "tool_call_id": "unique-call", "ok": True,
        "authorization": "user_approved", "side_effect_type": "external_write",
        "confirmation_required": True,
    }
    envelope = {
        "execution_id": "unique-call",
        "capability": "record_writer",
        "status": "success",
        "inputs_digest": "digest",
        "postconditions": [{
            "spec": {
                "kind": "record_exists",
                "source": "tool_contract",
                "severity": "required",
            },
            "status": "verified",
        }],
    }

    unverified = finalize_terminal_response(
        "model text", execution_ledger=[row], language="en",
    )
    mismatched = finalize_terminal_response(
        "model text",
        execution_ledger=[row],
        execution_envelopes=[{**envelope, "capability": "different_tool"}],
        execution_contract_mode="enforce_reversible",
        language="en",
    )
    verified = finalize_terminal_response(
        "model text",
        execution_ledger=[row],
        execution_envelopes=[envelope],
        execution_contract_mode="enforce_reversible",
        language="en",
    )

    assert "independent postcondition" not in unverified
    assert "independent postcondition" not in mismatched
    assert "passed an independent postcondition check" in verified


def _rollout_binding_facts(*, outcome: str | None = None) -> tuple[dict, dict, dict]:
    row = {
        "tool": "record_writer",
        "tool_call_id": "rollout-call",
        "ok": True,
        "authorization": "user_approved",
        "side_effect_type": "external_write",
        "confirmation_required": True,
        **({"outcome": outcome} if outcome else {}),
    }
    base = {
        "execution_id": "rollout-call",
        "capability": "record_writer",
        "status": "success",
        "inputs_digest": "digest",
    }
    confirmed = {
        **base,
        "postconditions": [{
            "spec": {
                "kind": "record_exists",
                "source": "tool_contract",
                "severity": "required",
            },
            "status": "verified",
        }],
    }
    verification_failed = {
        **base,
        "postconditions": [{
            "spec": {
                "kind": "record_exists",
                "source": "tool_contract",
                "severity": "required",
            },
            "status": "failed",
        }],
    }
    return row, confirmed, verification_failed


@pytest.mark.parametrize(
    ("mode", "envelope_kind", "expected", "forbidden"),
    [
        ("shadow", "failed", "tool/API returned success", "did not complete"),
        ("shadow", "confirmed", "tool/API returned success", "independent postcondition"),
        ("enforce_reversible", "failed", "did not complete", "tool/API returned success"),
        (
            "enforce_reversible",
            "confirmed",
            "passed an independent postcondition check",
            "did not complete",
        ),
        ("off", "failed", "tool/API returned success", "did not complete"),
    ],
)
def test_result_binding_respects_execution_contract_rollout(
    mode,
    envelope_kind,
    expected,
    forbidden,
):
    row, confirmed, verification_failed = _rollout_binding_facts()
    envelope = confirmed if envelope_kind == "confirmed" else verification_failed

    visible_text = finalize_terminal_response(
        "model text",
        execution_ledger=[row],
        execution_envelopes=[envelope],
        execution_contract_mode=mode,
        language="en",
    )

    assert expected in visible_text
    assert forbidden not in visible_text


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce_reversible"])
@pytest.mark.parametrize("envelope_kind", ["confirmed", "failed"])
def test_unknown_outcome_is_never_upgraded_by_rollout_mode(mode, envelope_kind):
    row, confirmed, verification_failed = _rollout_binding_facts(outcome="unknown")
    envelope = confirmed if envelope_kind == "confirmed" else verification_failed

    visible_text = finalize_terminal_response(
        "model text",
        execution_ledger=[row],
        execution_envelopes=[envelope],
        execution_contract_mode=mode,
        language="en",
    )

    assert "has an unknown outcome" in visible_text
    assert "tool/API returned success" not in visible_text
    assert "did not complete" not in visible_text
    assert "independent postcondition" not in visible_text


def test_a_first_round_deny_is_not_described_as_an_approved_action():
    pending_only = {
        "execution_requests": [{
            "request": {
                "requires_confirmation": True,
                "side_effect_type": "external_write",
            },
        }],
    }
    assert checkpoint_requires_result_buffering(pending_only) is True
    assert checkpoint_requires_result_buffering(
        pending_only, include_pending=False,
    ) is False


class _ScriptedLLM:
    def __init__(self, script: list):
        self.script = list(script)
        self.consumed = 0

    def bind_tools(self, *args, **kwargs):
        return self

    def with_fallbacks(self, *args, **kwargs):
        return self

    def bind(self, *args, **kwargs):
        return self

    async def ainvoke(self, messages, **kwargs):
        item = self.script[min(self.consumed, len(self.script) - 1)]
        self.consumed += 1
        return item if isinstance(item, AIMessage) else AIMessage(content=str(item))


async def _interrupt_payload(graph, state, config):
    try:
        result = await graph.ainvoke(state, config)
    except GraphInterrupt as exc:
        return exc.args[0][0].value
    pending = (result or {}).get("__interrupt__") or []
    return pending[0].value if pending else None


@pytest.mark.asyncio
async def test_real_approval_executes_fake_external_write_exactly_once_and_binds_result(
    isolated_cwd,
    tmp_path,
    monkeypatch,
):
    """The full graph path uses a deterministic fake, never Gmail itself."""
    from langgraph.types import Command

    from jarvis.graph.graph import build_graph, make_checkpointer
    from jarvis.memory import Memory

    calls: list[dict] = []

    def _fake_gmail_control(**kwargs):
        calls.append(dict(kwargs))
        return "[Gmail] Email sent"

    tool_call = AIMessage(content="", tool_calls=[{
        "name": "gmail",
        "args": {
            "action": "send",
            "to": "recipient@example.com",
            "subject": "Status",
            "body": "test-only body",
        },
        "id": "call-gmail-1",
        "type": "tool_call",
    }])
    uncertain = "E-postanın gerçekten gönderildiğinden emin değilim."
    llm = _ScriptedLLM([
        tool_call,
        uncertain,
        '{"verdict":"accept","critique":"","score":9}',
    ])
    monkeypatch.setattr("jarvis.graph.tools.gmail_control", _fake_gmail_control)
    monkeypatch.setattr("jarvis.providers.get_llm", lambda *args, **kwargs: llm)
    monkeypatch.setattr("jarvis.graph.graph.get_llm", lambda *args, **kwargs: llm)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = Settings(_env_file=None)
    memory = Memory(settings)
    checkpointer = make_checkpointer(workspace / "cp" / "checkpoints.db")
    graph = build_graph(settings, workspace, memory, checkpointer=checkpointer)
    state = {
        "messages": [
            SystemMessage(content="test"),
            HumanMessage(content="Bu test e-postasını gönder"),
        ],
        "user_query": "Bu test e-postasını gönder",
        "language": "tr",
        "memory_context": "",
        "needs_planning": False,
        "use_pro_agent": False,
        "plan": "",
        "response": "",
        "revise_count": 0,
        "critic_verdict": "",
        "critique": "",
        "transport": "api-stream",
        "tool_route": None,
        "tool_calls_attempted": 0,
        "tool_rounds": 0,
        "seen_tool_fingerprints": [],
        "completed_tool_fingerprints": [],
        "tool_execution_ledger": [],
        "user_approved_execution_ids": [],
    }
    config = {
        "configurable": {"thread_id": "approve-result-binding-real-graph"},
        "recursion_limit": 25,
    }

    payload = await _interrupt_payload(graph, state, config)
    assert payload and payload["tools"][0]["name"] == "gmail"
    assert calls == []

    result = await graph.ainvoke(Command(resume="approve"), config)
    visible_text = finalize_terminal_response(
        result["response"],
        execution_ledger=result["tool_execution_ledger"],
        execution_envelopes=result.get("execution_envelopes"),
        preexecution_history=result.get("preexecution_history"),
        language=result["language"],
    )

    assert len(calls) == 1
    assert result["tool_execution_ledger"][0]["authorization"] == "user_approved"
    assert uncertain == result["response"], "sanity: the model did fabricate uncertainty"
    assert "emin değilim" not in visible_text
    assert "başarılı" in visible_text


@pytest.mark.asyncio
async def test_real_approved_a_then_denied_b_keeps_a_result_and_never_executes_b(
    isolated_cwd,
    tmp_path,
    monkeypatch,
):
    from langgraph.types import Command

    from jarvis.graph.graph import build_graph, make_checkpointer
    from jarvis.memory import Memory

    gmail_calls: list[dict] = []
    drive_calls: list[dict] = []

    def _fake_gmail_control(**kwargs):
        gmail_calls.append(dict(kwargs))
        return "[Gmail] Email sent"

    def _fake_drive_control(**kwargs):
        drive_calls.append(dict(kwargs))
        return "[Drive] File deleted"

    first = AIMessage(content="", tool_calls=[{
        "name": "gmail",
        "args": {
            "action": "send", "to": "recipient@example.com",
            "subject": "Status", "body": "test-only body",
        },
        "id": "call-a",
        "type": "tool_call",
    }])
    second = AIMessage(content="", tool_calls=[{
        "name": "google_drive",
        "args": {"action": "delete", "file_id": "fake-file-id"},
        "id": "call-b",
        "type": "tool_call",
    }])
    llm = _ScriptedLLM([first, second])
    monkeypatch.setattr("jarvis.graph.tools.gmail_control", _fake_gmail_control)
    monkeypatch.setattr("jarvis.graph.tools.drive_control", _fake_drive_control)
    monkeypatch.setattr("jarvis.providers.get_llm", lambda *args, **kwargs: llm)
    monkeypatch.setattr("jarvis.graph.graph.get_llm", lambda *args, **kwargs: llm)

    workspace = tmp_path / "multi-workspace"
    workspace.mkdir()
    settings = Settings(_env_file=None)
    memory = Memory(settings)
    checkpointer = make_checkpointer(workspace / "cp" / "checkpoints.db")
    graph = build_graph(settings, workspace, memory, checkpointer=checkpointer)
    state = {
        "messages": [
            SystemMessage(content="test"),
            HumanMessage(content="E-postayı gönder, sonra Drive dosyasını sil"),
        ],
        "user_query": "E-postayı gönder, sonra Drive dosyasını sil",
        "language": "tr",
        "memory_context": "",
        "needs_planning": False,
        "use_pro_agent": False,
        "plan": "",
        "response": "",
        "revise_count": 0,
        "critic_verdict": "",
        "critique": "",
        "transport": "api-stream",
        "tool_route": {
            "primary_domain": "mail",
            "domains": ["mail", "drive"],
            "confidence": 0.5,
            "explicit_tool_intent": True,
        },
        "tool_calls_attempted": 0,
        "tool_rounds": 0,
        "seen_tool_fingerprints": [],
        "completed_tool_fingerprints": [],
        "tool_execution_ledger": [],
        "user_approved_execution_ids": [],
    }
    config = {
        "configurable": {"thread_id": "approve-a-deny-b-real-graph"},
        "recursion_limit": 25,
    }

    first_payload = await _interrupt_payload(graph, state, config)
    assert first_payload["tools"][0]["name"] == "gmail"
    second_payload = await _interrupt_payload(graph, Command(resume="approve"), config)
    assert second_payload["tools"][0]["name"] == "google_drive"
    assert len(gmail_calls) == 1
    assert drive_calls == []

    result = await graph.ainvoke(Command(resume="deny"), config)
    visible_text = finalize_terminal_response(
        result["response"],
        execution_ledger=result["tool_execution_ledger"],
        execution_envelopes=result.get("execution_envelopes"),
        preexecution_history=result.get("preexecution_history"),
        language=result["language"],
    )

    assert len(gmail_calls) == 1
    assert drive_calls == []
    assert "gmail" in visible_text and "başarılı" in visible_text
    assert "google_drive" in visible_text and "reddettiniz" in visible_text
