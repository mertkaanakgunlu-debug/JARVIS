"""The shared corrective-repair budget, and the scope that keeps it honest.

The system has three bounded repairs: invalid tool-call args
(confirmation_node), a missing required output (the completion contract), and
an unbacked claim (verification_node). Left independent, one turn could spend
all three; merged unconditionally, the merge itself would change behaviour on
turns that have no contract at all -- an invalid-args repair would silently
consume the claim gate's one chance, with `required_outputs_mode="off"`.

So the shared budget is CONTRACT-SCOPED: it exists only on a turn that is
under an enforced output contract. Everywhere else the pre-contract rules
apply verbatim. These tests pin both halves of that, because only the second
one can regress silently.
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import jarvis.providers as providers
from jarvis.config import Settings
from jarvis.graph.nodes import make_verification_node
from jarvis.graph.repair_budget import (
    claim_budget,
    contract_scoped,
    corrective_repair_spent,
    shared_budget_spent,
)

CHART = [{"kind": "chart", "operation": "create"}]


def _settings(mode: str = "enforce") -> Settings:
    return Settings(_env_file=None, required_outputs_mode=mode)


def _state(**overrides) -> dict:
    state = {"required_outputs": CHART, "repair_attempts_total": 0,
             "repair_reason": "", "args_repair_attempted": False}
    state.update(overrides)
    return state


# ── when the shared budget is in force at all ──────────────────────────────

def test_the_budget_is_shared_only_under_an_enforced_contract():
    assert contract_scoped(_state(), _settings("enforce")) is True


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_shadow_and_off_never_share_a_budget(mode):
    """shadow observes; it must not change which repairs are available."""
    assert contract_scoped(_state(), _settings(mode)) is False


@pytest.mark.parametrize("required", [None, [], {}])
def test_a_turn_with_no_required_output_is_not_scoped(required):
    """"Analyse the CSV" carries no contract, so it keeps the old rules even
    while the feature is enforced for chart requests."""
    assert contract_scoped(_state(required_outputs=required), _settings()) is False


def test_missing_settings_degrade_to_unscoped():
    assert contract_scoped(_state(), None) is False


# ── the claim gate: the half that could regress in silence ─────────────────

def test_an_args_repair_alone_does_not_close_the_claim_gate():
    """THE regression guard. Before contract scoping, a turn that spent its
    invalid-args repair could still have a contradicted answer repaired. If
    merging the budgets took that away from every turn -- including with the
    contract switched off -- the merge would be a behaviour change wearing a
    refactor's clothes."""
    state = _state(required_outputs=[], args_repair_attempted=True)
    assert shared_budget_spent(state, _settings()) is False


def test_inside_a_contract_the_args_repair_does_close_the_claim_gate():
    """Under the contract, one corrective repair per turn is the whole
    point: two LLM correction rounds on one turn is what the budget exists
    to prevent."""
    state = _state(args_repair_attempted=True, repair_attempts_total=1,
                   repair_reason="invalid_args")
    assert shared_budget_spent(state, _settings()) is True


def test_an_unspent_contract_turn_leaves_every_gate_open():
    assert shared_budget_spent(_state(), _settings()) is False
    assert corrective_repair_spent(_state(), _settings()) is False


# ── the args gate and the completion node: both count args_repair_attempted ─

def test_the_args_gate_still_honours_its_own_pre_contract_flag():
    """args_repair_attempted stays authoritative for the invalid-args path
    itself, including on a checkpoint written before this field existed --
    the `or` is what makes an old resumed turn behave correctly."""
    state = _state(required_outputs=[], args_repair_attempted=True)
    assert corrective_repair_spent(state, _settings()) is True


def test_a_completion_repair_blocks_a_later_args_repair_within_the_contract():
    state = _state(repair_attempts_total=1, repair_reason="missing_required_output")
    assert corrective_repair_spent(state, _settings()) is True


def test_a_stale_total_from_an_unscoped_turn_is_ignored():
    """repair_attempts_total can only be written while scoped, but a resumed
    checkpoint or a future caller could carry one anyway. Unscoped, it must
    not silently start closing gates."""
    state = _state(required_outputs=[], repair_attempts_total=1)
    assert shared_budget_spent(state, _settings()) is False
    assert corrective_repair_spent(state, _settings()) is False


# ── recording the spend ────────────────────────────────────────────────────

def test_claiming_the_budget_records_what_it_was_spent_on():
    assert claim_budget(_state(), _settings(), "missing_required_output") == {
        "repair_attempts_total": 1, "repair_reason": "missing_required_output",
    }


@pytest.mark.parametrize("state,settings", [
    (_state(required_outputs=[]), _settings()),
    (_state(), _settings("shadow")),
    (_state(), _settings("off")),
])
def test_an_unscoped_turn_records_nothing(state, settings):
    """Writing the field outside the contract would make `off` observable in
    the checkpoint -- the one thing `off` promises not to be."""
    assert claim_budget(state, settings, "invalid_args") == {}


# ── the same rule driven through the real claim gate ───────────────────────

class _ScriptedLLM:
    """The verification node only ever calls an LLM to repair, so len(log)
    IS the repair-round count. Same stand-in shape as
    test_unbacked_claim_gate.py, kept local rather than imported across test
    modules."""

    def __init__(self, log: list, reply: str):
        self._log, self._reply = log, reply

    async def ainvoke(self, messages, config=None):
        self._log.append({"messages": list(messages), "config": config})
        return AIMessage(content=self._reply)


HALLUCINATION = "Grafiği oluşturdum ve C:/nope/uydurma_chart.png konumuna kaydettim."
HONEST = "Grafiği çizemedim çünkü hangi veriyi kullanacağımı bilmiyorum."


def _verify(monkeypatch, *, mode: str, state_extra: dict) -> tuple[dict, list]:
    log: list = []
    monkeypatch.setattr(
        providers, "get_llm", lambda role, settings=None, **k: _ScriptedLLM(log, HONEST)
    )
    settings = Settings(
        _env_file=None, execution_contract_mode="enforce_reversible",
        required_outputs_mode=mode,
    )
    state = {
        "messages": [HumanMessage(content="grafiği çiz"), AIMessage(content=HALLUCINATION)],
        "response": HALLUCINATION, "use_pro_agent": False, "execution_envelopes": [],
        **state_extra,
    }
    return make_verification_node(settings=settings), (state, log)


@pytest.mark.asyncio
async def test_a_spent_contract_budget_blocks_the_claim_repair_but_not_the_block(
    monkeypatch, rollout_metrics_file,
):
    """Test 19. A completion repair already ran this turn, and the answer it
    produced is contradicted. No second model round -- but the contradicted
    text must NOT survive: the gate falls straight to its honest report."""
    node, (state, log) = _verify(monkeypatch, mode="enforce", state_extra={
        "required_outputs": CHART, "repair_attempts_total": 1,
        "repair_reason": "missing_required_output",
    })

    out = await node(state)

    assert log == [], "the shared budget was already spent -- no second draft"
    assert out["response"] != HALLUCINATION, "the claim must still not get through"


@pytest.mark.asyncio
async def test_an_unscoped_turn_keeps_its_claim_repair_after_an_args_repair(
    monkeypatch, rollout_metrics_file,
):
    """Test 19b, and the reason the budget is scoped at all. This turn has no
    required output, so merging the budgets must not reach it: the
    invalid-args repair it already spent is none of the claim gate's
    business, exactly as before the contract existed."""
    node, (state, log) = _verify(monkeypatch, mode="enforce", state_extra={
        "required_outputs": [], "args_repair_attempted": True,
    })

    out = await node(state)

    assert len(log) == 1, "the pre-contract claim repair must still be available"
    assert out["response"] == HONEST


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "shadow"])
async def test_with_the_contract_not_enforced_the_claim_gate_is_untouched(
    monkeypatch, rollout_metrics_file, mode,
):
    """Even carrying a required_outputs list and a spent counter -- which only
    an enforced turn should ever write -- off/shadow must leave the gate
    exactly as it was."""
    node, (state, log) = _verify(monkeypatch, mode=mode, state_extra={
        "required_outputs": CHART, "repair_attempts_total": 1,
    })

    out = await node(state)

    assert len(log) == 1
    assert out["response"] == HONEST
