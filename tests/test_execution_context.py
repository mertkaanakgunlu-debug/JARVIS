"""Post-MVP Faz 2.75, Paket C — who is on the other end of a turn.

The transport string was answering two questions at once ("where did this come
from" and "can the user approve something right now"), and the conflation cost
a real unattended write: `not transport.startswith("monitor-")` counted the
background TaskExecutor as attended, so a background job's calendar create took
Faz 2's confidence downgrade and wrote an L3 external event with nobody
watching.

These lock the property that fixes the class rather than the instance: the
answer is computed once, at the entry point, from an allowlist, and anything
unrecognized lands in the safe corner.
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from jarvis.execution.context import ExecutionContext
from jarvis.graph.nodes import _context_of, _gate_inputs, make_confirmation_node
from jarvis.config import Settings

ATTENDED = ["cli", "cli-text", "api", "api-stream", "api-upload",
            "voice-cli", "voice-local", "voice-remote"]


# ── the lattice ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("transport", ATTENDED)
def test_an_attended_surface_may_act_without_asking(transport):
    ctx = ExecutionContext.for_transport(transport)
    assert ctx.origin == "user"
    assert ctx.human_present and ctx.can_confirm
    assert not ctx.unattended and not ctx.background
    assert ctx.may_act_without_asking is True


@pytest.mark.parametrize("transport,origin", [
    ("task-async", "task"),
    ("monitor-email", "monitor"),
    ("monitor-calendar", "monitor"),
])
def test_a_background_surface_may_not(transport, origin):
    ctx = ExecutionContext.for_transport(transport)
    assert ctx.origin == origin
    assert not ctx.human_present and not ctx.can_confirm
    assert ctx.unattended and ctx.background
    assert ctx.may_act_without_asking is False


@pytest.mark.parametrize("transport", ["unknown", "", None, "some-future-surface", "  "])
def test_anything_unrecognized_lands_in_the_safe_corner(transport):
    """The property a denylist cannot have. A transport added next month, by
    someone with no reason to open this file, is unattended until listed."""
    ctx = ExecutionContext.for_transport(transport)
    assert ctx.origin == "unknown"
    assert ctx.may_act_without_asking is False


def test_monitor_and_task_are_distinguished_even_though_both_are_unattended():
    """Origin is not a synonym for unattended, and confirmation_node depends on
    the difference: the read-only clamp is for turns JARVIS started itself. A
    background job the user explicitly asked for still has to be able to write
    the report it was asked for."""
    monitor = ExecutionContext.for_transport("monitor-email")
    task = ExecutionContext.for_transport("task-async")
    assert monitor.unattended and task.unattended
    assert monitor.origin != task.origin


# ── serialization ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("transport", ATTENDED + ["task-async", "monitor-x", "weird"])
def test_round_trips_through_graph_state(transport):
    """Checkpoints are msgpack-serialized, so this rides as a plain dict."""
    ctx = ExecutionContext.for_transport(transport)
    assert ExecutionContext.from_dict(ctx.to_dict()) == ctx


def test_absent_context_is_none_not_a_default():
    """None must mean "derive it", not "assume the default".

    The dataclass default is the SAFE corner, so silently applying it to a
    state that simply predates this field would turn a resumed foreground turn
    unattended and start demanding confirmations the user already gave.
    """
    assert ExecutionContext.from_dict(None) is None
    assert ExecutionContext.from_dict({}) is None
    assert ExecutionContext.from_dict("not a dict") is None


# ── how nodes read it ─────────────────────────────────────────────────────────

def test_state_context_wins_when_present():
    state = {"execution_context": ExecutionContext.for_transport("cli-text").to_dict(),
             "transport": "task-async"}
    assert _context_of(state).may_act_without_asking is True, (
        "the entry point's decision must beat a stale transport string"
    )


@pytest.mark.parametrize("transport,expected", [
    ("cli-text", True),
    ("task-async", False),
    ("monitor-email", False),
    ("unknown", False),
])
def test_an_old_checkpoint_falls_back_to_the_transport(transport, expected):
    """States that predate this field still get a correct answer, because
    for_transport fails closed on its own."""
    assert _context_of({"transport": transport}).may_act_without_asking is expected
    assert _gate_inputs({"transport": transport})[0] is expected


# ── the incident, end to end ──────────────────────────────────────────────────

CONFIDENT = {"action": "create", "title": "Baran ile toplantı",
             "date": "yarın", "time": "15:00"}
UTTERANCE = "Yarın saat 15:00'te Baran'la toplantı ekle"


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, confirmation_gate_enabled=True, **kw)


def _state(transport: str) -> dict:
    return {
        "messages": [AIMessage(content="", tool_calls=[
            {"name": "google_calendar", "args": CONFIDENT, "id": "c1", "type": "tool_call"}])],
        "transport": transport,
        "execution_context": ExecutionContext.for_transport(transport).to_dict(),
        "user_query": UTTERANCE,
    }


@pytest.mark.asyncio
async def test_a_background_calendar_create_still_reaches_the_gate(isolated_cwd, monkeypatch):
    """Paket C must not regress the allowlist fix it generalizes."""
    from langgraph.errors import GraphInterrupt

    reached = []
    monkeypatch.setattr(
        "langgraph.types.interrupt",
        lambda payload: (reached.append(payload), (_ for _ in ()).throw(GraphInterrupt()))[0],
    )
    node = make_confirmation_node(_settings())

    with pytest.raises(GraphInterrupt):
        await node(_state("task-async"))
    assert reached, "a background calendar create must not slip past the gate"


@pytest.mark.asyncio
async def test_the_same_call_from_the_cli_still_auto_approves(isolated_cwd):
    """The other direction: a fix that made everything unattended would pass
    every test above while deleting the feature Faz 2 shipped."""
    node = make_confirmation_node(_settings())
    result = await node(_state("cli-text"))
    assert result["confirmation_result"] == "approved"


# ── each condition carries its own weight ────────────────────────────────────
#
# for_transport() never produces a context where these disagree, so a mutation
# dropping either conjunct from may_act_without_asking survived the tests above.
# That is exactly the combination this type exists to make expressible: the
# fields are separate because they answer separate questions, and a claim that
# each one matters has to be testable before a future transport relies on it.

def test_present_but_unable_to_confirm_may_not_act():
    """A surface where the user is watching but has no way to answer -- a
    disconnected SSE client, a one-way notification channel."""
    ctx = ExecutionContext(
        transport="hypothetical", origin="user",
        human_present=True, can_confirm=False, unattended=False, background=False,
    )
    assert ctx.may_act_without_asking is False


def test_confirmable_later_is_not_the_same_as_present_now():
    """The case Faz 3 is about to create, not a hypothetical.

    A scheduled briefing delivered as a push notification with approve/deny
    buttons CAN be confirmed -- the channel exists and the answer will arrive.
    But nobody is at the surface at the moment the turn runs, so a
    confidence-based downgrade must not fire: the whole premise of the
    downgrade is that a human sees the result as it happens and can react.

    This is why `human_present` is a separate field from `can_confirm` rather
    than implied by it.
    """
    ctx = ExecutionContext(
        transport="push", origin="monitor",
        human_present=False, can_confirm=True, unattended=False, background=True,
    )
    assert ctx.may_act_without_asking is False


def test_present_and_able_but_unattended_may_not_act():
    """A turn running behind the user's back on a surface they are also using."""
    ctx = ExecutionContext(
        transport="hypothetical", origin="user",
        human_present=True, can_confirm=True, unattended=True, background=False,
    )
    assert ctx.may_act_without_asking is False


# ── origin, not "unattended", decides the proactive read-only clamp ──────────

L2_WRITE = {"name": "todo", "args": {"action": "add", "text": "süt al"},
            "id": "t1", "type": "tool_call"}


def _write_state(transport: str) -> dict:
    return {
        "messages": [AIMessage(content="", tool_calls=[L2_WRITE])],
        "transport": transport,
        "execution_context": ExecutionContext.for_transport(transport).to_dict(),
        "user_query": "yapılacaklara süt almayı ekle",
    }


@pytest.mark.asyncio
async def test_a_self_initiated_turn_is_clamped_to_read_only(isolated_cwd):
    """A monitor poll nobody asked for must not write, even locally: the L2
    class is ungated by design because a human is normally there to notice."""
    node = make_confirmation_node(_settings())
    result = await node(_write_state("monitor-email"))
    assert result["confirmation_result"] == "denied"


@pytest.mark.asyncio
async def test_a_user_requested_background_job_may_still_write(isolated_cwd):
    """The distinction that justifies carrying `origin` at all.

    A TaskExecutor job is unattended too -- but the user asked for it, and
    clamping its local writes would break the reports, files and todos such
    jobs exist to produce. Keying the clamp on `unattended` instead of
    `origin` would silently gut background work; this is the test that says so.
    """
    node = make_confirmation_node(_settings())
    result = await node(_write_state("task-async"))
    assert result["confirmation_result"] == "approved"
