"""Post-MVP Faz 2.75, Paket E — a tool's actions are not all alike.

`policy_guard` owned a private `_READ_ACTIONS` table covering four external_api
tools. Everything else inherited its ToolSpec's risk uniformly, so `todo`,
`schedule` and `finance` -- all L2/local_write at the tool level -- had their
pure reads classified as writes. The proactive clamp fires on
``risk_level >= 2 and not requires_confirmation``, which every one of those
reads matched:

    todo.list          risk=2 -> BLOCKED on a proactive turn
    schedule.list      risk=2 -> BLOCKED
    finance.summary    risk=2 -> BLOCKED
    google_calendar.list  risk=1 -> passed
    gmail.list_unread     risk=1 -> passed

Verified live 2026-08-01. A Daily Briefing on that path could read the calendar
and the mailbox and not the to-do list, which is why this is a Faz 3 blocker
rather than tidying.
"""
from __future__ import annotations

import pytest

from jarvis import policy_guard
from jarvis.config import Settings
from jarvis.tool_registry import TOOL_SPECS, _TOOL_ACTIONS, get_action_spec


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, confirmation_gate_enabled=True, **kw)


def _blocked_on_a_proactive_turn(tool: str, action: str) -> bool:
    """The exact condition confirmation_node applies to a self-initiated turn."""
    s = _settings()
    d = policy_guard.evaluate(tool, {"action": action}, s, interactive=False)
    return d.risk_level >= 2 and not (d.requires_confirmation and s.confirmation_gate_enabled)


# ── the blocker, closed ───────────────────────────────────────────────────────

BRIEFING_READS = [
    ("todo", "list"), ("todo", "today"),
    ("schedule", "list"), ("schedule", "done"),
    ("finance", "summary"), ("finance", "budget_status"),
    ("finance", "recent"), ("finance", "top_categories"),
    ("google_calendar", "list"), ("google_calendar", "search"),
    ("gmail", "list_unread"), ("gmail", "search"), ("gmail", "read"),
]


@pytest.mark.parametrize("tool,action", BRIEFING_READS)
def test_a_briefing_read_is_not_blocked_on_a_proactive_turn(tool, action, isolated_cwd):
    assert not _blocked_on_a_proactive_turn(tool, action), (
        f"{tool}.{action} is a read; a self-initiated briefing must be able to run it"
    )


@pytest.mark.parametrize("tool,action", BRIEFING_READS)
def test_a_read_is_classified_as_a_read(tool, action, isolated_cwd):
    d = policy_guard.evaluate(tool, {"action": action}, _settings(), interactive=False)
    assert d.risk_level == 1
    assert d.side_effect_type.endswith("_read")
    assert d.requires_confirmation is False


# ── and the writes are still writes ───────────────────────────────────────────

WRITES = [
    ("todo", "add"), ("todo", "delete"), ("todo", "done"), ("todo", "edit"),
    ("todo", "analyze"),
    ("schedule", "add"), ("schedule", "delete"),
    ("schedule", "pause"), ("schedule", "resume"),
    ("finance", "sync"), ("finance", "import_statement"),
    ("finance", "set_budget"), ("finance", "export"), ("finance", "chart"),
]


@pytest.mark.parametrize("tool,action", WRITES)
def test_a_write_keeps_its_tool_level_risk(tool, action, isolated_cwd):
    """The regression guard in the other direction. A table that downgraded
    generously would pass every read test above and quietly let a proactive
    turn delete a to-do."""
    d = policy_guard.evaluate(tool, {"action": action}, _settings(), interactive=False)
    assert d.risk_level >= 2, f"{tool}.{action} must not be classified as a read"
    assert _blocked_on_a_proactive_turn(tool, action)


def test_done_means_opposite_things_on_todo_and_schedule(isolated_cwd):
    """The reason this table is per-tool and not a global set of read-ish verbs.

    todo("done") MARKS a task complete -- a write. schedule("done") LISTS
    completed tasks -- a read. One shared "done is a read" rule would silently
    let an unattended turn close the user's to-dos.
    """
    todo_done = policy_guard.evaluate("todo", {"action": "done"}, _settings())
    schedule_done = policy_guard.evaluate("schedule", {"action": "done"}, _settings())
    assert todo_done.risk_level >= 2
    assert schedule_done.risk_level == 1


# ── the table itself ──────────────────────────────────────────────────────────

def test_an_unlisted_action_falls_back_to_the_tool(isolated_cwd):
    """Not exhaustive, on purpose and in the safe direction: an action nobody
    listed -- including one added next year -- keeps the tool's higher risk."""
    assert get_action_spec("todo", "some_action_added_later") is None
    d = policy_guard.evaluate("todo", {"action": "some_action_added_later"}, _settings())
    assert d.risk_level == TOOL_SPECS["todo"].risk_level


def test_every_declared_action_belongs_to_a_real_tool():
    assert set(_TOOL_ACTIONS) <= set(TOOL_SPECS)


def test_only_reads_are_declared():
    """The table's invariant. Declaring a WRITE here would be a downgrade with
    no confirmation attached to it -- if that is ever wanted it needs its own
    review, not a quiet row."""
    for tool, actions in _TOOL_ACTIONS.items():
        for action, spec in actions.items():
            assert spec.risk_level == 1, f"{tool}.{action}"
            assert spec.side_effect_type.endswith("_read"), f"{tool}.{action}"
            assert spec.requires_confirmation is False, f"{tool}.{action}"


def test_action_lookup_is_case_and_whitespace_insensitive(isolated_cwd):
    """The model supplies this string; it arrives however it arrives."""
    for raw in ("LIST", " list ", "List"):
        d = policy_guard.evaluate("todo", {"action": raw}, _settings())
        assert d.risk_level == 1, raw


@pytest.mark.parametrize("raw", ["LIST", " list ", "List", "list"])
def test_get_action_spec_normalizes_on_its_own(raw):
    """Asserted on the accessor, not only through policy_guard.

    policy_guard lowercases before it asks, so a mutation removing the
    normalization here survives every test that goes through the gate. But the
    whole point of moving this table onto the registry is that the audit log,
    the workflow engine and the confirmation UI read the same source directly
    -- and they have no reason to know policy_guard did the cleaning.
    """
    assert get_action_spec("todo", raw) is not None


# ── local vs external is not cosmetic ─────────────────────────────────────────

@pytest.mark.parametrize("tool,action", [
    ("todo", "list"), ("todo", "today"),
    ("schedule", "list"), ("schedule", "done"),
    ("finance", "summary"), ("finance", "recent"),
    ("finance", "top_categories"), ("finance", "budget_status"),
])
def test_a_local_read_says_local(tool, action, isolated_cwd):
    """side_effect_type is not a label, it is what the EXTERNAL_WRITES_ENABLED
    gate and the audit classification key on. Calling a local SQLite read
    "external_read" would put it in the wrong bucket in both -- and
    `endswith("_read")` alone cannot tell the difference."""
    d = policy_guard.evaluate(tool, {"action": action}, _settings(), interactive=False)
    assert d.side_effect_type == "local_read"


def test_the_external_read_downgrade_is_unchanged(isolated_cwd):
    """Migrated from policy_guard._READ_ACTIONS, so the four external tools
    must behave exactly as before -- including keeping external_read rather
    than local_read, which is what the EXTERNAL_WRITES_ENABLED gate reads."""
    for tool in ("google_calendar", "gmail", "itu_mail", "google_drive"):
        for action in _TOOL_ACTIONS[tool]:
            d = policy_guard.evaluate(tool, {"action": action}, _settings())
            assert d.side_effect_type == "external_read", f"{tool}.{action}"


def test_a_gated_external_write_still_asks(isolated_cwd):
    """The four migrated tools kept their write side."""
    for tool, action in (("gmail", "send"), ("google_calendar", "delete"),
                         ("google_drive", "upload")):
        d = policy_guard.evaluate(tool, {"action": action}, _settings(), interactive=False)
        assert d.requires_confirmation is True, f"{tool}.{action}"
        assert d.risk_level >= 3, f"{tool}.{action}"
