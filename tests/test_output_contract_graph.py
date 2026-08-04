"""The completion contract as wired: the node, the routing, the provenance.

tests/test_output_contract.py is the classifier's truth table. This file is
about everything the table cannot see on its own -- that `off` really is a
no-op down to the edge map, that the router cannot re-enter a repair it just
finished, that a repair round cannot reach a write tool, and that the honest
answer this code writes is not then handed to a model to "repair".
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import jarvis.providers as providers
from jarvis.config import Settings
from jarvis.graph.graph import build_graph
from jarvis.execution.output_contract import COMPLETION_REPAIR_MARKER
from jarvis.graph.nodes import (
    make_output_contract_node,
    make_verification_node,
    route_from_output_contract,
)
from jarvis.graph.repair_budget import completion_repair_subset
from jarvis.tool_registry import TOOL_SPECS, tools_producing

CHART = [{"kind": "chart", "operation": "create"}]
CONFIG = {"configurable": {"conversation_id": "conv-1"}}
PNG = "C:/tmp/run-1/chart.png"


class _Store:
    """The one working-set call the node makes. `boom` turns the read into the
    failure the EVIDENCE_UNAVAILABLE branch exists for."""

    def __init__(self, artifacts=(), boom: Exception | None = None):
        self._artifacts, self._boom = tuple(artifacts), boom

    def active(self, conversation_id, kind=""):
        if self._boom is not None:
            raise self._boom
        if not self._artifacts:
            return None
        return MagicMock(source_artifacts=self._artifacts)


def _node(mode="enforce", store=None):
    return make_output_contract_node(
        Settings(_env_file=None, required_outputs_mode=mode),
        store if store is not None else _Store(),
    )


def _ai(*calls):
    return AIMessage(content="", tool_calls=[
        {"name": n, "args": {}, "id": i} for n, i in calls
    ])


def _drew(call_id="c1", path=PNG):
    return [_ai(("plot_data", call_id)),
            ToolMessage(content="chart drawn", tool_call_id=call_id,
                        artifact=[{"path": path, "kind": "chart"}])]


def _state(messages=None, **overrides):
    state = {
        "messages": messages if messages is not None else [
            HumanMessage(content="satis.csv grafiğini çiz"),
            AIMessage(content="Hangi formatta istersiniz?"),
        ],
        "user_query": "satis.csv grafiğini çiz",
        "required_outputs": CHART,
        "response": "Hangi formatta istersiniz?",
        "tool_rounds": 0, "tool_calls_attempted": 0,
        "repair_attempts_total": 0, "repair_reason": "",
        "args_repair_attempted": False,
    }
    state.update(overrides)
    return state


def _rows(path: Path, event_suffix: str) -> list[dict]:
    if not path.exists():
        return []
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    return [r for r in rows if r["event"] == f"output_contract_{event_suffix}"]


# ── `off` is a true no-op, down to the graph's shape ───────────────────────

def _graph_shape(mode: str, tmp_path):
    graph = build_graph(
        Settings(_env_file=None, required_outputs_mode=mode), tmp_path, MagicMock()
    ).get_graph()
    return (
        sorted(graph.nodes),
        sorted((e.source, e.target, str(e.data or "")) for e in graph.edges),
    )


def test_off_leaves_the_graph_bit_for_bit_unchanged(tmp_path, isolated_cwd):
    """T-E. A node that merely returns {} would still be a super-step, a
    changed checkpoint shape and extra latency -- so `off` does not add one.
    This compares the whole node set AND edge map, not just "is the node
    there": moving the END key's destination is the actual change."""
    off_nodes, off_edges = _graph_shape("off", tmp_path)
    on_nodes, on_edges = _graph_shape("shadow", tmp_path)

    assert "output_contract" not in off_nodes
    assert "output_contract" in on_nodes
    assert off_edges != on_edges, "the terminal edges must move when it is on"
    assert ("critic", "verify", "__end__") in off_edges
    assert ("critic", "output_contract", "__end__") in on_edges
    assert ("confirmation", "output_contract", "__end__") in on_edges
    assert ("output_contract", "agent", "repair") in on_edges
    assert ("verify", "__end__", "") in off_edges and ("verify", "__end__", "") in on_edges


@pytest.mark.asyncio
async def test_a_turn_without_a_requirement_reads_no_evidence(rollout_metrics_file):
    """A conversational turn must not open the working-set store, and must not
    write a row -- NOT_REQUIRED on every "merhaba" would bury the rows that
    matter."""
    store = _Store(boom=RuntimeError("must not be called"))

    out = await _node(store=store)(_state(required_outputs=[]), CONFIG)

    assert out == {"output_contract_action": "continue"}
    assert _rows(rollout_metrics_file, "decision") == []


# ── shadow: classify, record, mutate nothing ───────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("messages,artifacts,expected", [
    ([HumanMessage(content="çiz"), AIMessage(content="hangi format?")], (), "MISSING_NO_ATTEMPT"),
    (_drew(), (PNG,), "SATISFIED"),
    (_drew(), (), "EXECUTED_NO_OBJECT"),
    ([_ai(("plot_data", "c1")),
      ToolMessage(content="[ERROR] boom", tool_call_id="c1")], (), "MISSING_TOOL_FAILURE"),
])
async def test_shadow_observes_every_verdict_without_touching_the_turn(
    rollout_metrics_file, messages, artifacts, expected,
):
    """T-I, parametric on purpose: a mode that edits the answer for ONE
    verdict is not shadow, and a single happy-path assertion would not catch
    that. The only key shadow may return is the router's own signal."""
    out = await _node("shadow", _Store(artifacts))(_state(messages), CONFIG)

    assert out == {"output_contract_action": "continue"}
    rows = _rows(rollout_metrics_file, "decision")
    assert [r["status"] for r in rows] == [expected]
    assert rows[0]["action"] == "continue"


# ── enforce: who gets a repair, and who must never ─────────────────────────

@pytest.mark.asyncio
async def test_a_missing_attempt_is_sent_back_to_the_agent_once(rollout_metrics_file):
    out = await _node()(_state(), CONFIG)

    assert out["output_contract_action"] == "repair"
    assert route_from_output_contract({**_state(), **out}) == "repair"
    assert COMPLETION_REPAIR_MARKER in out["messages"][0].content
    assert out["repair_attempts_total"] == 1
    assert out["repair_reason"] == "missing_required_output"
    assert "response" not in out, "the repair pass must not write a final answer"


@pytest.mark.asyncio
async def test_the_pass_after_a_repair_cannot_re_enter_the_repair(rollout_metrics_file):
    """Test 18. repair_reason sticks for the whole turn by design, so a router
    keyed on it would read the same value it wrote and loop. The transient
    action is rewritten on every pass, which is what makes it safe."""
    repaired = _state(
        messages=_drew(), repair_attempts_total=1,
        repair_reason="missing_required_output", output_contract_action="repair",
    )

    out = await _node(store=_Store((PNG,)))(repaired, CONFIG)

    assert out["output_contract_action"] == "continue"
    assert route_from_output_contract({**repaired, **out}) == "continue"


@pytest.mark.asyncio
async def test_a_repair_that_produced_the_chart_is_recorded_as_a_success(rollout_metrics_file):
    repaired = _state(messages=_drew(), repair_attempts_total=1,
                      repair_reason="missing_required_output")

    await _node(store=_Store((PNG,)))(repaired, CONFIG)

    terminal = _rows(rollout_metrics_file, "terminal")[0]
    assert terminal["status"] == "SATISFIED"
    assert terminal["initial_status"] == "MISSING_NO_ATTEMPT"
    assert terminal["repair_success"] is True


@pytest.mark.asyncio
async def test_a_repair_that_still_produced_nothing_ends_honestly(rollout_metrics_file):
    """Test 3. One repair, then the truth -- never a second round."""
    spent = _state(repair_attempts_total=1, repair_reason="missing_required_output")

    out = await _node()(spent, CONFIG)

    assert out["output_contract_action"] == "continue"
    assert out["response_origin"] == "output_contract"
    assert "grafik" in out["response"].lower()
    terminal = _rows(rollout_metrics_file, "terminal")[0]
    assert terminal["repair_success"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("budget,reason", [
    ({"tool_rounds": 4}, "tool_round_budget_exhausted"),
    ({"tool_calls_attempted": 6}, "tool_call_budget_exhausted"),
    ({"args_repair_attempted": True}, "budget_spent"),
])
async def test_no_repair_is_promised_that_the_budgets_would_refuse(
    rollout_metrics_file, budget, reason,
):
    """Test 17. MISSING_NO_ATTEMPT means no CHART tool ran -- other tools may
    well have spent the round budget. Handing over the directive anyway costs
    a round, ends in a deterministic block, and tells the user about a retry
    that never happened."""
    out = await _node()(_state(**budget), CONFIG)

    assert out["output_contract_action"] == "continue"
    assert out["response_origin"] == "output_contract"
    assert _rows(rollout_metrics_file, "terminal")[0]["reason"].endswith(reason)


@pytest.mark.asyncio
async def test_a_postcondition_failure_is_audited_as_ours(rollout_metrics_file, monkeypatch):
    """Test 12. The chart drew, the object was not kept. Re-prompting the
    model would hide our own registration defect behind a retry."""
    audited: list = []
    monkeypatch.setattr("jarvis.audit_log.record", lambda e, **f: audited.append((e, f)))

    out = await _node(store=_Store())(_state(messages=_drew()), CONFIG)

    assert out["output_contract_action"] == "continue"
    assert any(e == "output_postcondition_failed" for e, _ in audited)


@pytest.mark.asyncio
async def test_unreadable_evidence_never_becomes_a_repair(rollout_metrics_file):
    """Tests 23/24. A broken SQLite read looks exactly like "no chart" if you
    squint -- and offering the model a repair for a chart it may already have
    drawn is the worst available reading of our own failure."""
    out = await _node(store=_Store(boom=RuntimeError("database is locked")))(_state(), CONFIG)

    assert out["output_contract_action"] == "continue"
    assert _rows(rollout_metrics_file, "terminal")[0]["status"] == "EVIDENCE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_a_missing_conversation_id_is_unreadable_evidence_not_a_missing_chart(
    rollout_metrics_file,
):
    out = await _node(store=_Store((PNG,)))(_state(), {"configurable": {}})

    assert _rows(rollout_metrics_file, "terminal")[0]["status"] == "EVIDENCE_UNAVAILABLE"
    assert out["output_contract_action"] == "continue"


# ── the answer this code wrote is not a model's claim ──────────────────────

class _ScriptedLLM:
    def __init__(self, log):
        self._log = log

    async def ainvoke(self, messages, config=None):
        self._log.append(config)
        return AIMessage(content="repaired")


@pytest.mark.asyncio
async def test_a_code_authored_answer_is_never_sent_to_the_claim_repair(
    monkeypatch, rollout_metrics_file,
):
    """T-K. The honest report for MISSING_TOOL_FAILURE names no file and
    claims nothing -- but it is prose about an output that does not exist,
    and the enforce-mode claim gate would happily spend a model round
    rewriting it. That would undo the contract's central rule (a tool that
    honestly failed is never retried) one layer further down."""
    log: list = []
    monkeypatch.setattr(providers, "get_llm", lambda *a, **k: _ScriptedLLM(log))
    verify = make_verification_node(
        Settings(_env_file=None, execution_contract_mode="enforce_reversible")
    )
    honest = "Grafiği oluşturamadım: çizim aracı bir hata bildirdi."

    out = await verify({
        "messages": [HumanMessage(content="çiz"), AIMessage(content=honest)],
        "response": honest, "response_origin": "output_contract",
        "use_pro_agent": False, "execution_envelopes": [],
    })

    assert log == [], "no model round may be spent on our own sentence"
    assert out == {}, "and the text must reach the user unchanged"


# ── a repair round's reach ─────────────────────────────────────────────────

def test_a_repair_round_keeps_its_reader_tools_and_loses_every_writer():
    """T-F. Not a deny list: the router picks up to eight tools and which
    eight depends on the query, so anticipating every write tool that could
    be routed alongside a chart request is exactly the kind of list that goes
    stale silently."""
    offered = ["plot_data", "csv_read", "file_list", "file_write", "shell_run",
               "gmail", "google_calendar", "chart_revise"]

    allowed = completion_repair_subset(offered, tools_producing("chart", "create"))

    assert "plot_data" in allowed
    assert {"csv_read", "file_list"} <= set(allowed), "reading is how it finds the columns"
    for name in allowed:
        spec = TOOL_SPECS[name]
        assert not spec.actions, f"{name}: a dispatch tool's actions do not share a class"
        assert (
            name in tools_producing("chart", "create")
            or spec.side_effect_type in ("none", "local_read")
        ), f"{name} can change something"


def test_an_unknown_tool_is_dropped_from_a_repair_round():
    """A dynamically registered MCP tool with no spec is not classified, and
    an unclassified tool in the round that follows a mis-plan is precisely
    what fail-closed means."""
    assert completion_repair_subset(["mystery_mcp_tool"], frozenset()) == []


# ── the budget's arithmetic across a whole turn ────────────────────────────

@pytest.mark.asyncio
async def test_a_completion_repair_leaves_nothing_for_a_later_args_repair(
    rollout_metrics_file, isolated_cwd,
):
    """Test 13. The repair round re-enters the agent, which then produces an
    invalid call. confirmation_node must NOT hand out a second corrected
    retry: "one bounded repair" is a property of the TURN, not of each
    mechanism that can ask for one."""
    from jarvis.graph.nodes import make_confirmation_node
    from jarvis.graph.repair_budget import corrective_repair_spent

    settings = Settings(_env_file=None, required_outputs_mode="enforce")
    after_repair = _state(
        repair_attempts_total=1, repair_reason="missing_required_output",
        invalid_args_calls=[{"tool_call_id": "x", "capability": "plot_data",
                             "errors": [{"loc": ["path"], "msg": "Field required"}]}],
    )

    assert corrective_repair_spent(after_repair, settings) is True
    # ...and the node that would grant the retry agrees, without a second flag
    # to keep in sync.
    assert make_confirmation_node(settings) is not None


@pytest.mark.asyncio
async def test_the_repaired_turn_records_exactly_one_row_of_each_kind(
    rollout_metrics_file, monkeypatch,
):
    """T-A. The node runs TWICE on a repaired turn. The claim gate and the
    per-operation verification rows must stay at one per turn -- those feed
    the enforce-promotion metric, and a repaired turn silently double-counting
    would inflate exactly the number the promotion is decided on. The
    contract's own rows are the opposite: one `decision` for the repair pass
    and one `terminal` at the end, or repair_triggered is unrecoverable."""
    node = _node()
    first = await node(_state(), CONFIG)
    assert first["output_contract_action"] == "repair"

    second_state = _state(messages=_drew(), **{
        k: v for k, v in first.items() if k != "messages"
    })
    await _node(store=_Store((PNG,)))(second_state, CONFIG)

    assert len(_rows(rollout_metrics_file, "decision")) == 1
    assert len(_rows(rollout_metrics_file, "terminal")) == 1


def test_the_repair_lap_is_paid_for_in_the_recursion_budget():
    """T-D, and it caught a real one. A repair sends a finished turn back to
    `agent`, which costs the repair lap's own five super-steps plus a second
    terminal chain. Sized against the pre-contract worst case that set
    graph_recursion_limit to 30, that did not fit -- and the failure mode is
    not a worse answer, it is GraphRecursionError on exactly the turns the
    repair exists to rescue.

    So the budget is raised while the contract is enforced, and left alone
    otherwise. The cap still stops the loop it was added for (F16's tool-call
    loop): what actually bounds a repair is the one-repair budget and the tool
    budgets, not this number.
    """
    from jarvis.agent import _COMPLETION_REPAIR_SUPERSTEPS

    # agent -> prepare_execution -> confirmation -> tools -> tool_result_accounting
    repair_lap = 5
    # compose -> critic -> output_contract -> verify
    second_terminal_chain = 4
    assert _COMPLETION_REPAIR_SUPERSTEPS == repair_lap + second_terminal_chain

    base = Settings(_env_file=None).graph_recursion_limit
    contracted = {"required_outputs": CHART}
    assert _limit_for("enforce", contracted) == base + _COMPLETION_REPAIR_SUPERSTEPS

    # ...and NOTHING else pays for it. A turn that cannot spend a completion
    # repair must not have its loop ceiling raised as a side effect of the
    # feature being switched on for other turns.
    assert _limit_for("enforce", {"required_outputs": []}) == base
    assert _limit_for("enforce", {}) == base
    assert _limit_for("shadow", contracted) == base
    assert _limit_for("off", contracted) == base


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
@pytest.mark.parametrize("required", [None, [], CHART])
def test_the_headroom_matches_the_budget_that_would_spend_it(mode, required):
    """The headroom and the shared repair budget must never disagree: room
    for a repair that cannot happen is dead ceiling, and a repair with no
    room is a GraphRecursionError. Both read the same predicate, and this
    pins that they keep agreeing across the whole grid."""
    from jarvis.graph.repair_budget import contract_scoped

    settings = Settings(_env_file=None, required_outputs_mode=mode)
    state = {"required_outputs": required}
    raised = _limit_for(mode, state) > settings.graph_recursion_limit
    assert raised is contract_scoped(state, settings)


def _limit_for(mode: str, state: dict) -> int:
    from jarvis.agent import JarvisAgent

    settings = Settings(_env_file=None, required_outputs_mode=mode)
    return JarvisAgent._recursion_limit_for(MagicMock(settings=settings), state)


# ── which entry points carry a contract at all ─────────────────────────────

def _contract_fields(mode: str, query: str) -> dict:
    from jarvis.agent import JarvisAgent

    agent = MagicMock(settings=Settings(_env_file=None, required_outputs_mode=mode))
    return JarvisAgent._output_contract_state(agent, query, {})


def test_off_writes_no_contract_fields_at_all():
    """T-E's other half. Zeroed fields still change a checkpoint diff and the
    TypedDict's populated shape -- `off` promises to be indistinguishable, so
    it writes nothing rather than writing falsy things."""
    assert _contract_fields("off", "satis.csv grafiğini çiz") == {}


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
def test_a_chart_request_carries_its_requirement_into_state(mode):
    fields = _contract_fields(mode, "satis.csv grafiğini çiz")
    assert fields["required_outputs"] == CHART
    assert fields["response_origin"] == "model"
    assert fields["repair_attempts_total"] == 0
    assert fields["invalid_args_history"] == [] and fields["preexecution_history"] == []


def test_an_analysis_request_carries_none():
    assert _contract_fields("enforce", "satis.csv'yi analiz et")["required_outputs"] == []


def test_a_proactive_turn_is_structurally_outside_the_contract():
    """Test 8a. proactive_turn builds its own partial state and never writes
    these fields, so a background self-check can never trigger a repair --
    structurally, not by a mode flag somebody could flip. background_turn is
    deliberately NOT in this class: that one is the USER's work (its own
    docstring says so), just running off-thread."""
    import inspect

    from jarvis.agent import JarvisAgent

    proactive = inspect.getsource(JarvisAgent.proactive_turn)
    assert "_output_contract_state" not in proactive
    background = inspect.getsource(JarvisAgent.background_turn)
    assert "_output_contract_state" in background, (
        "'run this in the background: draw the sales chart' is exactly the "
        "request the contract exists for"
    )


# ── the two turn-scoped histories actually accumulate ──────────────────────

def test_a_block_history_grows_across_rounds_instead_of_replacing():
    """T-G/T-J. JarvisState has no reducer outside `messages`, so a node that
    returned only this round's entries would REPLACE the list and silently
    erase round 1 -- taking with it the one record that distinguishes a
    blocked call from a tool that genuinely failed."""
    from jarvis.graph.nodes import make_confirmation_node

    assert make_confirmation_node(Settings(_env_file=None)) is not None
    # The read-extend-return shape, asserted where it is written.
    import inspect

    src = inspect.getsource(make_confirmation_node)
    assert 'list(state.get("preexecution_history") or [])' in src
    assert "history.extend(" in src

    from jarvis.graph.nodes import make_prepare_execution_node

    prep = inspect.getsource(make_prepare_execution_node)
    assert 'list(state.get("invalid_args_history") or [])' in prep
    assert "history.extend(" in prep


# ── the contract on the REAL compiled graph ────────────────────────────────

class _ScriptedLLM:
    """Answers each turn from a script; ignores tools. bind_tools/bind/
    with_fallbacks are the shape get_llm's callers expect."""

    def __init__(self, script):
        self._script = list(script)
        self.consumed = 0

    def bind_tools(self, *a, **k):
        return self

    def with_fallbacks(self, *a, **k):
        return self

    def bind(self, *a, **k):
        return self

    async def ainvoke(self, messages, **kwargs):
        item = self._script[min(self.consumed, len(self._script) - 1)]
        self.consumed += 1
        return item if isinstance(item, AIMessage) else AIMessage(content=str(item))


def _contract_state(query: str, required):
    from langchain_core.messages import SystemMessage

    return {
        "messages": [SystemMessage(content="test"), HumanMessage(content=query)],
        "user_query": query, "language": "tr", "memory_context": "",
        "needs_planning": False, "use_pro_agent": False, "plan": "", "response": "",
        "revise_count": 0, "critic_verdict": "", "critique": "", "transport": "cli-text",
        "tool_route": None, "tool_calls_attempted": 0, "tool_rounds": 0,
        "seen_tool_fingerprints": [], "completed_tool_fingerprints": [],
        "tool_execution_ledger": [], "args_repair_attempted": False,
        "required_outputs": required, "required_output_baseline": {},
        "output_contract_action": "", "response_origin": "model",
        "repair_attempts_total": 0, "repair_reason": "",
        "invalid_args_history": [], "preexecution_history": [],
    }


async def _run_real_graph(tmp_path, monkeypatch, *, mode, query, required, script):
    from jarvis.graph.graph import build_graph, make_checkpointer
    from jarvis.memory import Memory

    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    settings = Settings(_env_file=None, required_outputs_mode=mode)
    llm = _ScriptedLLM(script)
    monkeypatch.setattr("jarvis.providers.get_llm", lambda *a, **k: llm)
    monkeypatch.setattr("jarvis.graph.graph.get_llm", lambda *a, **k: llm)

    graph = build_graph(
        settings, workspace, Memory(settings),
        checkpointer=make_checkpointer(workspace / "cp" / "checkpoints.db"),
    )
    config = {
        "configurable": {"thread_id": f"contract-{mode}", "conversation_id": "conv-real"},
        "recursion_limit": 40,
    }
    return await graph.ainvoke(_contract_state(query, required), config), llm


@pytest.mark.asyncio
async def test_the_contract_actually_runs_on_the_compiled_graph(tmp_path, monkeypatch,
                                                                isolated_cwd,
                                                                rollout_metrics_file):
    """Every other test here calls the node directly. That proves the node
    works, not that a turn ever reaches it -- a guard with a green suite that
    nothing routes through is a failure mode this project has already had
    once, and the reason `verify` is a terminal node at all.

    A model that answers an explicit chart request with prose, twice: the
    contract must spend its one repair, then finish with a code-authored
    honest answer rather than looping or crashing.
    """
    result, llm = await _run_real_graph(
        tmp_path, monkeypatch, mode="enforce",
        query="satis.csv grafiğini çiz", required=CHART,
        script=["Hangi formatta istersiniz?", "Yine hangi formatta istersiniz?"],
    )

    assert result.get("response"), "the turn must end with a real answer"
    assert result["repair_attempts_total"] == 1, "exactly one repair, never a loop"
    assert result["repair_reason"] == "missing_required_output"
    assert result["response_origin"] == "output_contract"
    assert result["output_contract_action"] == "continue"
    assert llm.consumed >= 2, "the agent must have been re-entered once"


@pytest.mark.asyncio
async def test_a_turn_with_no_requirement_is_untouched_on_the_real_graph(
    tmp_path, monkeypatch, isolated_cwd, rollout_metrics_file,
):
    """The same graph, the same enforce mode, a query that carries no
    contract: the model's own answer survives verbatim and no budget moves."""
    result, _ = await _run_real_graph(
        tmp_path, monkeypatch, mode="enforce",
        query="satis.csv'yi analiz et", required=[],
        script=["Dosyada 12 satır var."],
    )

    assert result["response"] == "Dosyada 12 satır var."
    assert result["response_origin"] == "model"
    assert result["repair_attempts_total"] == 0
