"""The Post-MVP Faz 2 headline bug: "yarın" landing one day early.

Mechanism, verified against the pre-fix code before anything was changed:
`_parse_date` resolved relative dates off `datetime.now(timezone.utc)`, and the
resulting wall-clock numbers were then handed to Google as a naive string with
`timeZone: Europe/Istanbul`. During the hours when UTC is still on the previous
date, that is a whole day of error. A sweep of all 24 local hours against the
original `_parse_date` put the failure at exactly 3 of 24 -- Istanbul 00:00,
01:00 and 02:00.

**This corrects an instant recorded in HANDOFF.md**, which named
`FrozenClock(2026-07-31 23:30 Europe/Istanbul)` as the regression case. That
instant does NOT reproduce the bug: 23:30 in Istanbul is 20:30 UTC on the same
date. The window is 00:00-02:59 local, i.e. 21:00-23:59 UTC the day before.

These tests drive the real `calendar_control` against a fake Google service and
assert on the request body that would actually be sent, because the bug lived
in the seam between resolution and the request body -- an assertion on an
internal helper would not have covered it.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from jarvis.clock import FrozenClock
from jarvis.tools import calendar as cal

ISTANBUL = ZoneInfo("Europe/Istanbul")


class _FakeEvents:
    def __init__(self, capture: dict, items: list):
        self._capture, self._items = capture, items

    def list(self, **kwargs):
        self._capture.update(kwargs)
        return SimpleNamespace(execute=lambda: {"items": self._items})

    def insert(self, calendarId, body):
        self._capture["insert_body"] = body
        return SimpleNamespace(execute=lambda: {"id": "evt1", "htmlLink": "https://example.com"})

    def get(self, calendarId, eventId):
        return SimpleNamespace(execute=lambda: {"summary": "Old title", "id": eventId})

    def update(self, calendarId, eventId, body):
        self._capture["update_body"] = body
        return SimpleNamespace(execute=lambda: {"summary": body.get("summary", ""), "id": eventId})


class _FakeService:
    def __init__(self, capture: dict, items: list):
        self._events = _FakeEvents(capture, items)

    def events(self):
        return self._events


@pytest.fixture
def calendar_at(monkeypatch):
    """Drive calendar_control at a chosen local instant; return the captured request."""

    def _run(local_iso: str, *, tz: str = "Europe/Istanbul", items: list | None = None, **kwargs):
        capture: dict = {}
        monkeypatch.setattr(cal, "_get_service", lambda settings: _FakeService(capture, items or []))
        monkeypatch.setattr(cal, "_clock_for", lambda settings: FrozenClock.at(local_iso, tz_name=tz))
        output = cal.calendar_control(
            settings=SimpleNamespace(calendar_timezone=tz, google_calendar_creds_file="unused"),
            **kwargs,
        )
        return capture, output

    return _run


class TestTheDateBugItself:
    @pytest.mark.parametrize("hour", range(24))
    def test_tomorrow_is_tomorrow_at_every_hour_of_the_day(self, calendar_at, hour):
        """The whole sweep, not just the three broken hours -- a fix that moved
        the error to a different hour would pass a narrower test."""
        local = f"2026-07-31 {hour:02d}:30:00"
        capture, _ = calendar_at(local, action="create", title="Toplantı", date="yarın", time="15:00")

        expected = (datetime.fromisoformat(local).replace(tzinfo=ISTANBUL) + timedelta(days=1)).date()
        sent = capture["insert_body"]["start"]["dateTime"]
        assert sent == f"{expected.isoformat()}T15:00:00", (
            f"at {local} Istanbul (UTC {datetime.fromisoformat(local).replace(tzinfo=ISTANBUL).astimezone().date()}), "
            f"'yarın' produced {sent}"
        )

    @pytest.mark.parametrize("hour", [0, 1, 2])
    def test_the_three_hours_that_used_to_be_wrong(self, calendar_at, hour):
        """Named separately because these are the ONLY hours the original code
        got wrong, so a regression would show here first."""
        capture, _ = calendar_at(f"2026-08-01 {hour:02d}:30:00",
                                 action="create", title="X", date="yarın", time="09:00")
        assert capture["insert_body"]["start"]["dateTime"].startswith("2026-08-02")

    def test_the_owners_actual_failing_utterance(self, calendar_at):
        """"Yarın öğlen saat 3'e ... ekle", said at 01:30 -- the exact shape of
        the live failure, at an instant inside the exact broken window."""
        capture, _ = calendar_at("2026-08-01 01:30:00", action="create",
                                 title="Baran'la toplantı", date="yarın", time="öğlen saat 3")

        start = capture["insert_body"]["start"]
        assert start == {"dateTime": "2026-08-02T15:00:00", "timeZone": "Europe/Istanbul"}
        assert capture["insert_body"]["end"]["dateTime"] == "2026-08-02T16:00:00"

    def test_today_is_also_read_in_local_time(self, calendar_at):
        capture, _ = calendar_at("2026-08-01 01:30:00", action="create",
                                 title="X", date="bugün", time="09:00")
        assert capture["insert_body"]["start"]["dateTime"].startswith("2026-08-01")

    def test_an_update_that_moves_a_date_uses_the_same_resolver(self, calendar_at):
        """An update that moved an event to the wrong day would be the identical
        bug wearing a different action name."""
        capture, _ = calendar_at("2026-08-01 01:30:00", action="update", event_id="e1",
                                 date="yarın", time="10:00")
        assert capture["update_body"]["start"]["dateTime"] == "2026-08-02T10:00:00"


class TestTheToolBuildsItsOwnClockFromSettings:
    """The fixture above replaces `_clock_for` wholesale, which is what lets it
    freeze time — but it also means every test using it would pass even if
    `_clock_for` itself went back to UTC. A mutation round caught exactly that
    (the "calendar resolves against UTC again" mutant survived), so the real
    function is tested here, unpatched."""

    def test_it_reads_the_configured_calendar_timezone(self):
        from types import SimpleNamespace

        clock = cal._clock_for(SimpleNamespace(calendar_timezone="Europe/Berlin"))
        assert clock.tz_name == "Europe/Berlin"

    def test_it_defaults_to_istanbul_and_specifically_not_to_utc(self):
        from datetime import timedelta

        from jarvis.clock import DEFAULT_TZ

        for settings in (None, SimpleNamespace(calendar_timezone=None), SimpleNamespace()):
            clock = cal._clock_for(settings)
            assert clock.tz_name == DEFAULT_TZ
            assert clock.now().utcoffset() == timedelta(hours=3), (
                "resolving relative dates against UTC is the bug this phase closed"
            )

    def test_an_unpatched_create_uses_the_configured_zone(self, monkeypatch):
        """One end-to-end pass with the REAL clock, so the wiring is covered
        even though the instant cannot be pinned."""
        capture: dict = {}
        monkeypatch.setattr(cal, "_get_service", lambda settings: _FakeService(capture, []))
        cal.calendar_control(
            action="create", title="X", date="2026-08-15", time="14:00",
            settings=SimpleNamespace(calendar_timezone="Europe/Berlin",
                                     google_calendar_creds_file="unused"),
        )
        assert capture["insert_body"]["start"]["timeZone"] == "Europe/Berlin"
        assert capture["timeMin"].endswith("+02:00")


class TestTitleDisciplineIsActuallyApplied:
    """jarvis/nlu/event_text.py is unit-tested on its own; these assert the
    tool actually calls it. A mutation that deleted the call from
    calendar_control survived the first round."""

    def test_the_created_event_carries_the_cleaned_title(self, calendar_at):
        capture, output = calendar_at(
            "2026-07-31 10:00:00", action="create",
            title="yarın öğlen 3'te Baran'la toplantı ayarla", date="yarın", time="15:00",
        )
        assert capture["insert_body"]["summary"] == "Baran'la toplantı"
        assert "Title normalised" in output

    def test_a_description_that_only_restates_the_title_is_dropped(self, calendar_at):
        capture, _ = calendar_at(
            "2026-07-31 10:00:00", action="create", title="Diş hekimi randevusu ekle",
            description="Diş hekimi randevusu ekle", date="yarın", time="15:00",
        )
        body = capture["insert_body"]
        assert body["summary"] == "Diş hekimi randevusu"
        assert body["description"] == ""

    def test_a_real_description_survives(self, calendar_at):
        capture, _ = calendar_at(
            "2026-07-31 10:00:00", action="create", title="Baran'la toplantı",
            description="Q3 bütçesini gözden geçireceğiz", date="yarın", time="15:00",
        )
        assert capture["insert_body"]["description"] == "Q3 bütçesini gözden geçireceğiz"

    def test_a_good_title_is_created_verbatim(self, calendar_at):
        capture, output = calendar_at("2026-07-31 10:00:00", action="create",
                                      title="Sprint planlama", date="yarın", time="15:00")
        assert capture["insert_body"]["summary"] == "Sprint planlama"
        assert "Title normalised" not in output

    def test_an_update_cleans_the_title_too(self, calendar_at):
        capture, _ = calendar_at("2026-07-31 10:00:00", action="update", event_id="e1",
                                 title="takvime diş hekimi randevusu ekle")
        assert capture["update_body"]["summary"] == "diş hekimi randevusu"


class TestTimezoneIsConfiguredNotAssumed:
    def test_a_non_default_zone_is_honoured_end_to_end(self, calendar_at):
        capture, _ = calendar_at("2026-07-31 23:30:00", tz="Europe/Berlin",
                                 action="create", title="X", date="yarın", time="09:00")
        assert capture["insert_body"]["start"] == {
            "dateTime": "2026-08-01T09:00:00", "timeZone": "Europe/Berlin",
        }

    def test_the_dedup_window_carries_the_configured_offset(self, calendar_at):
        """BUG-21's original property, re-asserted through the new code path:
        the window must not be a hardcoded +03:00."""
        capture, _ = calendar_at("2026-07-20 09:00:00", tz="Europe/Berlin",
                                 action="create", title="X", date="2026-07-20", time="14:00")
        assert capture["timeMin"].endswith("+02:00")
        assert capture["timeMax"].endswith("+02:00")


class TestHonestFailureInsteadOfAGuess:
    @pytest.mark.parametrize("expression", ["haftaya", "gelecek ay", "bir ara", "zzz"])
    def test_an_unresolvable_date_is_refused_with_a_reason(self, calendar_at, expression):
        """Owner's stated requirement for the whole hallucination class:
        "Efendim, istediğiniz ... bulamadım" rather than a plausible guess."""
        capture, output = calendar_at("2026-07-31 10:00:00", action="create",
                                      title="X", date=expression, time="15:00")
        assert output.startswith("[ERROR]")
        assert "insert_body" not in capture, "nothing may be created from an unresolved date"

    def test_an_unresolvable_time_does_not_silently_become_midnight(self, calendar_at):
        capture, output = calendar_at("2026-07-31 10:00:00", action="create",
                                      title="X", date="yarın", time="belki")
        assert output.startswith("[ERROR]")
        assert "insert_body" not in capture

    def test_the_error_is_shaped_so_the_audit_sees_a_failure(self, calendar_at):
        """[Calendar] prefixes successes too, so a failure must use [ERROR] --
        the 2026-07-18 incident where a failed call was logged ok:true."""
        from jarvis.graph.tool_accounting import content_is_failure

        _, output = calendar_at("2026-07-31 10:00:00", action="create",
                                title="X", date="haftaya", time="15:00")
        assert content_is_failure(output) is True


class TestWhatTheToolReportsBack:
    def test_the_success_line_names_the_resolved_day_not_the_expression(self, calendar_at):
        """This string is what the model relays to the user. Echoing "yarın"
        back tells them nothing about which day was written -- and the Faz 1
        honesty kernel can only be as truthful as the tool output it checks."""
        _, output = calendar_at("2026-08-01 01:30:00", action="create",
                                title="Toplantı", date="yarın", time="15:00")
        assert "2026-08-02 15:00" in output
        assert "Europe/Istanbul" in output

    def test_an_all_day_event_ends_on_the_following_date(self, calendar_at):
        """Google treats end.date as EXCLUSIVE. The pre-Faz-2 code sent
        start + duration_minutes, which for an all-day event is the same
        calendar date -- an empty range."""
        capture, _ = calendar_at("2026-07-31 10:00:00", action="create", title="X", date="yarın")
        body = capture["insert_body"]
        assert body["start"] == {"date": "2026-08-01"}
        assert body["end"] == {"date": "2026-08-02"}

    def test_a_multi_day_all_day_event_spans_the_right_number_of_days(self, calendar_at):
        capture, _ = calendar_at("2026-07-31 10:00:00", action="create", title="X",
                                 date="yarın", duration_minutes=1440 * 3)
        assert capture["insert_body"]["end"] == {"date": "2026-08-04"}
