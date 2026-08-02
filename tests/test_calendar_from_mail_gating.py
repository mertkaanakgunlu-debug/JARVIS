"""Is the gate actually on the mail→calendar path? (Post-MVP Faz 5)

`test_calendar_from_mail.py` proves the service does the right thing. That
proves nothing about whether the SYSTEM lets it be reached the right way, and
MEMORY.md's verify-the-guard-is-on-the-path lesson exists because a guard with
100% green tests was never on the real edge map.

The classification this file pins:

    propose  → L1, external_read, no confirmation   (showing a proposal
                                                     must not interrupt)
    create   → L3, external_write, CONFIRMED        (it writes a real event)
    ignore   → L3 by inheritance
    <novel>  → L3 by inheritance                    (fail-safe: an action
                                                     added later without a
                                                     table entry is treated as
                                                     the dangerous one)

The last row is the one worth having. `_TOOL_ACTIONS` lists only the safe
actions precisely so that forgetting an entry fails closed, and that promise is
worth a test rather than a comment.
"""
from __future__ import annotations

import pytest

from jarvis import policy_guard
from jarvis.config import Settings
from jarvis.graph.tool_router import classify_query, select_tool_names, with_active_object
from jarvis.tool_registry import get_spec
from jarvis.working_set import KIND_CALENDAR_CANDIDATE


@pytest.fixture
def settings():
    return Settings(_env_file=None)


# ── risk classification ──────────────────────────────────────────────────────

def test_propose_does_not_interrupt(settings):
    d = policy_guard.evaluate(
        "calendar_from_mail", {"action": "propose", "message_id": "m1"}, settings
    )
    assert d.risk_level == 1
    assert not d.requires_confirmation
    assert d.side_effect_type == "external_read"
    assert d.allowed


def test_create_requires_confirmation(settings):
    d = policy_guard.evaluate("calendar_from_mail", {"action": "create"}, settings)
    assert d.risk_level == 3
    assert d.requires_confirmation
    assert d.side_effect_type == "external_write"


@pytest.mark.parametrize("action", ["ignore", "", "delete_everything", "CREATE"])
def test_every_unlisted_action_inherits_the_dangerous_classification(settings, action):
    """The fail-closed promise `_TOOL_ACTIONS` is built on."""
    d = policy_guard.evaluate("calendar_from_mail", {"action": action}, settings)
    assert d.risk_level == 3, f"{action!r} escaped the L3 default"
    assert d.requires_confirmation


def test_the_tool_is_classified_like_the_calendar_tool_it_writes_through():
    """It is a calendar write wearing a mail-shaped input; the spec must say so
    even though the model reaches it from a mail."""
    mine, theirs = get_spec("calendar_from_mail"), get_spec("google_calendar")
    assert (mine.risk_level, mine.side_effect_type) == (
        theirs.risk_level, theirs.side_effect_type
    )
    assert mine.requires_confirmation


# ── reachability: the model can find it, and only where it should ────────────

def test_the_tool_exists_and_is_bound(isolated_cwd, tmp_path):
    from unittest.mock import MagicMock

    from jarvis.graph.tools import make_tools

    names = {t.name for t in make_tools(tmp_path, Settings(), MagicMock())}
    assert "calendar_from_mail" in names


@pytest.mark.parametrize("query", [
    "bu maili takvime ekle",
    "gelen mailden takvim etkinliği oluştur",
])
def test_a_mail_to_calendar_request_routes_to_it(isolated_cwd, tmp_path, query):
    from unittest.mock import MagicMock

    from jarvis.graph.tools import make_tools

    avail = [t.name for t in make_tools(tmp_path, Settings(), MagicMock())]
    route = classify_query(query)
    assert "calendar_from_mail" in select_tool_names(route, avail, query)


def test_a_bare_approval_reaches_the_tool_via_the_active_candidate(
    isolated_cwd, tmp_path
):
    """"evet, ekle" names no capability. Without the working-set route it
    classifies as `conversation`, the model gets zero tools, and it answers
    "tamam, ekledim" — the Faz 3 weather failure with a worse consequence,
    because the user then believes the event exists."""
    from unittest.mock import MagicMock

    from jarvis.graph.tools import make_tools

    avail = [t.name for t in make_tools(tmp_path, Settings(), MagicMock())]
    query = "evet, ekle"
    bare = classify_query(query)
    assert bare.primary_domain == "conversation"
    assert select_tool_names(bare, avail, query) == []

    routed = with_active_object(bare, KIND_CALENDAR_CANDIDATE)
    assert routed.primary_domain == "calendar"
    assert "calendar_from_mail" in select_tool_names(routed, avail, query)


# ── the background path stays read-only ──────────────────────────────────────

def test_a_proactive_turn_cannot_create_the_event(settings):
    """The proactive clamp blocks risk >= 2 that is not a gated interrupt. An
    L3 create becomes a needs_confirmation notification, never an event."""
    d = policy_guard.evaluate("calendar_from_mail", {"action": "create"}, settings)
    unsupervised = d.risk_level >= 2 and not (
        d.requires_confirmation and settings.confirmation_gate_enabled
    )
    assert not unsupervised, "create would auto-execute on a background turn"
    assert d.requires_confirmation, "and it must be a real interrupt, not a silent pass"


def test_monitor_ingest_is_off_by_default(settings):
    assert settings.calendar_from_mail_enabled is False


def test_monitor_ingest_runs_only_when_enabled(monkeypatch, tmp_path):
    from jarvis.monitor import JarvisMonitor

    calls: list[str] = []

    class _Svc:
        def __init__(self, *a, **kw): pass
        @property
        def ledger(self):
            return type("L", (), {"should_process": staticmethod(lambda mid: True)})()
        def propose(self, mid, **kw):
            calls.append(mid)
            return type("O", (), {"state": "extracted"})()

    monkeypatch.setattr("jarvis.calendar_from_mail.CalendarFromMailService", _Svc)

    off = Settings(_env_file=None)
    JarvisMonitor(off)._ingest_calendar_candidate("m1")
    assert calls == []

    on = Settings(_env_file=None)
    on.calendar_from_mail_enabled = True
    JarvisMonitor(on)._ingest_calendar_candidate("m1")
    assert calls == ["m1"]


def test_ingest_never_raises(monkeypatch):
    """It runs on the monitor's polling thread; an exception there is a dead
    monitor, not a failed mail."""
    from jarvis.monitor import JarvisMonitor

    def _boom(*a, **kw):
        raise RuntimeError("gmail exploded")

    monkeypatch.setattr("jarvis.calendar_from_mail.CalendarFromMailService", _boom)
    s = Settings(_env_file=None)
    s.calendar_from_mail_enabled = True

    JarvisMonitor(s)._ingest_calendar_candidate("m1")   # must not raise
