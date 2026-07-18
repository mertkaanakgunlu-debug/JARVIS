"""jarvis/tools/calendar.py -- Google Calendar dedup guard.

Covers BUG-21: the create-event dedup window was built by string-appending a
hardcoded "+03:00" to a naive datetime, ignoring settings.calendar_timezone
entirely. Correct only by coincidence for the default Europe/Istanbul (which
has been on permanent UTC+3 since 2016) -- silently wrong the moment the
setting is anything else.

_get_service() requires real Google OAuth credentials, so these tests
monkeypatch it with a fake service that records the timeMin/timeMax it was
called with, rather than hitting the network.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from jarvis.tools import calendar as cal


class _FakeEventsList:
    def __init__(self, capture: dict, items: list):
        self._capture = capture
        self._items = items

    def list(self, **kwargs):
        self._capture.update(kwargs)
        return SimpleNamespace(execute=lambda: {"items": self._items})

    def insert(self, calendarId, body):
        self._capture["insert_body"] = body
        return SimpleNamespace(execute=lambda: {"id": "evt123", "htmlLink": "https://example.com"})


class _FakeService:
    def __init__(self, capture: dict, items: list):
        self._events = _FakeEventsList(capture, items)

    def events(self):
        return self._events


def _settings(tz: str):
    return SimpleNamespace(calendar_timezone=tz, google_calendar_creds_file="unused")


def test_dedup_window_uses_configured_timezone_not_hardcoded_offset(monkeypatch):
    capture: dict = {}
    fake_service = _FakeService(capture, items=[])
    monkeypatch.setattr(cal, "_get_service", lambda settings: fake_service)

    cal.calendar_control(
        action="create", title="Team sync", date="2026-07-20", time="14:00",
        settings=_settings("Europe/Berlin"),  # NOT Europe/Istanbul -- +02:00 in July (DST)
    )

    assert capture["timeMin"].endswith("+02:00"), capture.get("timeMin")
    assert capture["timeMax"].endswith("+02:00"), capture.get("timeMax")


def test_dedup_window_matches_istanbul_offset_by_default(monkeypatch):
    capture: dict = {}
    fake_service = _FakeService(capture, items=[])
    monkeypatch.setattr(cal, "_get_service", lambda settings: fake_service)

    cal.calendar_control(
        action="create", title="Team sync", date="2026-07-20", time="14:00",
        settings=_settings("Europe/Istanbul"),
    )

    assert capture["timeMin"].endswith("+03:00")


def test_dedup_window_is_five_minutes_wide_around_start(monkeypatch):
    capture: dict = {}
    fake_service = _FakeService(capture, items=[])
    monkeypatch.setattr(cal, "_get_service", lambda settings: fake_service)

    cal.calendar_control(
        action="create", title="X", date="2026-07-20", time="14:00",
        settings=_settings("Europe/Istanbul"),
    )

    tz = ZoneInfo("Europe/Istanbul")
    win_min = datetime.fromisoformat(capture["timeMin"])
    win_max = datetime.fromisoformat(capture["timeMax"])
    assert win_min.astimezone(tz).strftime("%H:%M") == "13:55"
    assert win_max.astimezone(tz).strftime("%H:%M") == "14:05"


def test_matching_existing_event_is_skipped_as_duplicate(monkeypatch):
    existing = [{"id": "evt-existing-1234567890", "summary": "Team sync"}]
    capture: dict = {}
    fake_service = _FakeService(capture, items=existing)
    monkeypatch.setattr(cal, "_get_service", lambda settings: fake_service)

    result = cal.calendar_control(
        action="create", title="Team sync", date="2026-07-20", time="14:00",
        settings=_settings("Europe/Istanbul"),
    )

    assert "SKIPPED" in result
    assert "insert_body" not in capture  # never actually created a duplicate


def test_non_matching_title_is_not_treated_as_duplicate(monkeypatch):
    existing = [{"id": "evt-other", "summary": "Unrelated meeting"}]
    capture: dict = {}
    fake_service = _FakeService(capture, items=existing)
    monkeypatch.setattr(cal, "_get_service", lambda settings: fake_service)

    result = cal.calendar_control(
        action="create", title="Team sync", date="2026-07-20", time="14:00",
        settings=_settings("Europe/Istanbul"),
    )

    assert "SKIPPED" not in result
    assert capture.get("insert_body", {}).get("summary") == "Team sync"


# ── audit-outcome regression (live-found 2026-07-18) ─────────────────────────
#
# calendar_control used [Calendar] to prefix BOTH success ("Event created")
# and failure (missing credentials, validation errors) -- content_is_failure()
# can't add "[Calendar]" to _FAILURE_PREFIXES without misjudging real
# successes too, so these calls were silently logged ok:true in the audit,
# even after the Faz 1.1 fix (which correctly reads .content, but still needs
# a real failure-shaped prefix to recognize). Errors now use [ERROR].

def test_missing_credentials_returns_error_prefix(monkeypatch):
    def _raise(settings):
        raise RuntimeError("Google Calendar credentials not found at 'x'.")
    monkeypatch.setattr(cal, "_get_service", _raise)

    result = cal.calendar_control(action="list", settings=_settings("Europe/Istanbul"))

    assert result.startswith("[ERROR]")
    from jarvis.graph.tool_accounting import content_is_failure
    assert content_is_failure(result) is True


def test_validation_error_returns_error_prefix(monkeypatch):
    fake_service = _FakeService({}, items=[])
    monkeypatch.setattr(cal, "_get_service", lambda settings: fake_service)

    result = cal.calendar_control(action="create", settings=_settings("Europe/Istanbul"))  # no title/date

    assert result.startswith("[ERROR]")


def test_successful_create_still_uses_calendar_prefix_not_error(monkeypatch):
    """The fix must not turn genuine successes into failures."""
    capture: dict = {}
    fake_service = _FakeService(capture, items=[])
    monkeypatch.setattr(cal, "_get_service", lambda settings: fake_service)

    result = cal.calendar_control(
        action="create", title="Team sync", date="2026-07-20", time="14:00",
        settings=_settings("Europe/Istanbul"),
    )

    assert result.startswith("[Calendar] Event created")
    from jarvis.graph.tool_accounting import content_is_failure
    assert content_is_failure(result) is False
