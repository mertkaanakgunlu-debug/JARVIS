"""Agent Runtime rev.2, Faz 4 -- compose_node's own wiring.

Covers mode gating (off / shadow / enforce_*), raw ToolMessage removal plus
the deterministic ground-truth block injected in enforce mode, the
unconditional user-facing append when any operation did not cleanly succeed,
and the secondary (observation-only) claim-audit log call. The pure-function
behavior of VerifiedExecutionSummary itself (postcondition aggregation,
display_status derivation, the renderers, audit_claims' pattern matching) is
tests/test_execution_summary.py's job -- these tests only check that
compose_node calls into that module correctly and respects the mode ladder.
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import jarvis.providers as providers
from jarvis.config import Settings
from jarvis.execution.envelope import ExecutionEnvelope
from jarvis.graph.nodes import make_compose_node


class _FakeLLM:
    def __init__(self, captured: dict, response_text: str):
        self._captured = captured
        self._response_text = response_text

    async def ainvoke(self, messages):
        self._captured["messages"] = list(messages)
        return AIMessage(content=self._response_text)


def _patch_llm(monkeypatch, response_text: str) -> dict:
    captured: dict = {}
    monkeypatch.setattr(providers, "get_llm", lambda role, settings=None, **k: _FakeLLM(captured, response_text))
    return captured


def _patch_audit_log(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr("jarvis.audit_log.record", lambda event, **fields: calls.append((event, fields)))
    return calls


def _settings(mode: str) -> Settings:
    return Settings(_env_file=None, execution_contract_mode=mode)


def _envelope(*, capability: str, status: str, execution_id: str) -> dict:
    return ExecutionEnvelope(
        execution_id=execution_id, capability=capability, status=status,
        inputs_digest="digest", normalized_output=f"{capability} output",
    ).model_dump()


def _state(*, envelopes: list[dict], extra_messages: list | None = None) -> dict:
    messages = [HumanMessage(content="dosyayı oluştur ve e-postayı gönder")]
    if extra_messages:
        messages += extra_messages
    return {
        "messages": messages,
        # Faz 1.4's own guard is route-gated; None keeps it silent so these
        # assertions isolate Faz 4 behavior from that pre-existing guard.
        "tool_route": None,
        "completed_tool_fingerprints": ["fp-a"],
        "use_pro_agent": False,
        "execution_envelopes": envelopes,
    }


_ONE_FAILED = [
    _envelope(capability="file_write", status="success", execution_id="a"),
    _envelope(capability="gmail_send", status="failed", execution_id="b"),
]
_ALL_SUCCESS = [
    _envelope(capability="file_write", status="success", execution_id="a"),
]


def _has_ground_truth_message(msgs) -> bool:
    return any(
        isinstance(m, SystemMessage) and "Verified operation status" in m.content
        for m in msgs
    )


# ── mode="off": true no-op even with envelopes present in state ────────────

async def test_off_mode_ignores_envelopes_entirely(monkeypatch):
    captured = _patch_llm(monkeypatch, "Dosyayı oluşturdum ve e-postayı gönderdim.")
    node = make_compose_node(settings=_settings("off"))
    out = await node(_state(envelopes=_ONE_FAILED))
    assert out["response"] == "Dosyayı oluşturdum ve e-postayı gönderdim."
    assert not _has_ground_truth_message(captured["messages"])


async def test_settings_none_also_ignores_envelopes(monkeypatch):
    _patch_llm(monkeypatch, "ok")
    node = make_compose_node(settings=None)
    out = await node(_state(envelopes=_ONE_FAILED))
    assert out["response"] == "ok"


# ── mode="shadow": invocation and response untouched, audit is observe-only ─

async def test_shadow_mode_does_not_alter_invocation_or_response(monkeypatch):
    tool_msg = ToolMessage(content="raw tool output", tool_call_id="call_0")
    captured = _patch_llm(monkeypatch, "Dosyayı oluşturdum ve e-postayı gönderdim.")
    node = make_compose_node(settings=_settings("shadow"))
    out = await node(_state(envelopes=_ONE_FAILED, extra_messages=[tool_msg]))
    assert out["response"] == "Dosyayı oluşturdum ve e-postayı gönderdim."
    assert any(isinstance(m, ToolMessage) for m in captured["messages"]), (
        "shadow mode must not strip raw ToolMessages -- that only happens in enforce mode"
    )
    assert not _has_ground_truth_message(captured["messages"])


async def test_shadow_mode_logs_a_violation_but_does_not_gate(monkeypatch):
    _patch_llm(monkeypatch, "Her ikisini de başarıyla tamamladım.")
    audit_calls = _patch_audit_log(monkeypatch)
    node = make_compose_node(settings=_settings("shadow"))
    out = await node(_state(envelopes=_ONE_FAILED))
    # unchanged despite the overclaim -- shadow never gates
    assert out["response"] == "Her ikisini de başarıyla tamamladım."
    matches = [f for e, f in audit_calls if e == "claim_audit"]
    assert matches, "shadow mode should still log the secondary claim-audit signal"
    assert matches[0]["mode"] == "shadow"
    assert matches[0]["enforced"] is False


async def test_shadow_mode_stays_silent_when_response_is_accurate(monkeypatch):
    _patch_llm(monkeypatch, "I created the file, but the email failed to send.")
    audit_calls = _patch_audit_log(monkeypatch)
    node = make_compose_node(settings=_settings("shadow"))
    await node(_state(envelopes=_ONE_FAILED))
    assert not any(e == "claim_audit" for e, _ in audit_calls)


async def test_shadow_mode_with_all_success_is_a_no_op(monkeypatch):
    captured = _patch_llm(monkeypatch, "Dosyayı oluşturdum.")
    audit_calls = _patch_audit_log(monkeypatch)
    node = make_compose_node(settings=_settings("shadow"))
    out = await node(_state(envelopes=_ALL_SUCCESS))
    assert out["response"] == "Dosyayı oluşturdum."
    assert not _has_ground_truth_message(captured["messages"])
    assert not any(e == "claim_audit" for e, _ in audit_calls)


# ── mode="enforce_all": ground truth fed to the model, unconditional append ─

async def test_enforce_mode_strips_tool_messages_and_injects_ground_truth(monkeypatch):
    tool_msg = ToolMessage(content="raw tool output", tool_call_id="call_0")
    captured = _patch_llm(monkeypatch, "ok")
    node = make_compose_node(settings=_settings("enforce_all"))
    await node(_state(envelopes=_ONE_FAILED, extra_messages=[tool_msg]))
    assert not any(isinstance(m, ToolMessage) for m in captured["messages"])
    ground_truth = [m for m in captured["messages"] if isinstance(m, SystemMessage)
                     and "Verified operation status" in m.content]
    assert len(ground_truth) == 1
    assert "gmail_send" in ground_truth[0].content
    assert "FAILED" in ground_truth[0].content


async def test_enforce_mode_appends_true_status_when_model_overclaims(monkeypatch):
    """The plan's own acceptance scenario: 2 tools, 1 succeeded, the model
    claims both did -- the false claim must not reach the user unaccompanied
    by the true, system-verified status."""
    _patch_llm(monkeypatch, "Dosyayı oluşturdum ve e-postayı gönderdim, ikisini de başarıyla tamamladım.")
    node = make_compose_node(settings=_settings("enforce_all"))
    out = await node(_state(envelopes=_ONE_FAILED))
    assert "[System-verified status]" in out["response"]
    assert "gmail_send" in out["response"]
    assert "FAILED" in out["response"]
    # the corrected text also rides in the returned AIMessage, not just the
    # response field, so a later turn's history can't re-echo the
    # uncorrected claim (Faz 1.4's own concern, restated for Faz 4's output).
    assert out["messages"][0].content == out["response"]


async def test_enforce_mode_does_not_append_when_everything_succeeded(monkeypatch):
    _patch_llm(monkeypatch, "Dosyayı oluşturdum.")
    node = make_compose_node(settings=_settings("enforce_all"))
    out = await node(_state(envelopes=_ALL_SUCCESS))
    assert out["response"] == "Dosyayı oluşturdum."
    assert "[System-verified status]" not in out["response"]


async def test_enforce_mode_appends_even_when_the_text_pattern_audit_stays_silent(monkeypatch):
    """The structural guarantee must not depend on the secondary text-pattern
    detector recognizing the model's exact phrasing -- this response text
    deliberately matches none of audit_claims' patterns."""
    _patch_llm(monkeypatch, "Bir kısmını yaptım, detaylar asagida degil.")
    audit_calls = _patch_audit_log(monkeypatch)
    node = make_compose_node(settings=_settings("enforce_all"))
    out = await node(_state(envelopes=_ONE_FAILED))
    assert not any(e == "claim_audit" for e, _ in audit_calls), (
        "test premise broken: this text should not trip the secondary pattern audit"
    )
    assert "[System-verified status]" in out["response"]


async def test_enforce_mode_logs_correction_with_enforced_true(monkeypatch):
    _patch_llm(monkeypatch, "Her ikisini de başarıyla tamamladım.")
    audit_calls = _patch_audit_log(monkeypatch)
    node = make_compose_node(settings=_settings("enforce_all"))
    await node(_state(envelopes=_ONE_FAILED))
    matches = [f for e, f in audit_calls if e == "claim_audit"]
    assert matches
    assert matches[0]["mode"] == "enforce_all"
    assert matches[0]["enforced"] is True
