"""jarvis/clock.py -- one source of "now".

Post-MVP Faz 2. Before this module, "what day is it" had two answers in one
process: jarvis/agent.py's _build_now_block() said Europe/Istanbul, and
jarvis/tools/calendar.py said UTC. For the three hours a day when those differ
by a date, a calendar event went to the wrong day. These tests pin the
properties that make that specific disagreement unrepresentable.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from jarvis import clock as clock_mod
from jarvis.clock import (
    DEFAULT_TZ, FrozenClock, SystemClock, UnknownTimezone, configure,
    get_clock, resolve_timezone, set_clock, use_clock,
)


class TestTimezoneResolution:
    def test_resolves_a_real_iana_zone(self):
        tz = resolve_timezone("Europe/Istanbul")
        assert datetime(2026, 7, 31, 12, tzinfo=tz).utcoffset() == timedelta(hours=3)

    def test_istanbul_has_no_dst_so_the_offset_is_the_same_in_january_and_july(self):
        """The fixed-offset fallback is only exact because of this. If Türkiye
        ever reintroduces DST, the fallback table in clock.py becomes wrong and
        this test is where that shows up."""
        tz = resolve_timezone("Europe/Istanbul")
        january = datetime(2026, 1, 15, 12, tzinfo=tz).utcoffset()
        july = datetime(2026, 7, 15, 12, tzinfo=tz).utcoffset()
        assert january == july == timedelta(hours=3)

    def test_unknown_zone_raises_instead_of_silently_becoming_utc(self):
        """Substituting UTC for an unresolvable zone would reintroduce this
        phase's whole bug class, quietly."""
        with pytest.raises(UnknownTimezone) as exc:
            resolve_timezone("Mars/Olympus_Mons")
        assert "Mars/Olympus_Mons" in str(exc.value)

    def test_fallback_is_used_when_zoneinfo_is_unavailable(self, monkeypatch):
        """tzdata is not guaranteed on Windows -- the reason the old
        _build_now_block carried its own try/except."""
        import builtins

        real_import = builtins.__import__

        def _no_zoneinfo(name, *a, **kw):
            if name == "zoneinfo":
                raise ImportError("no tzdata")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", _no_zoneinfo)
        tz = resolve_timezone("Europe/Istanbul")
        assert datetime(2026, 7, 31, 12, tzinfo=tz).utcoffset() == timedelta(hours=3)

    def test_a_dst_zone_has_no_fixed_offset_fallback(self):
        """A fixed offset would be wrong for half the year, so only zones where
        it is EXACT may appear in the table."""
        assert set(clock_mod._FIXED_OFFSET_FALLBACKS) == {"Europe/Istanbul", "UTC", "Etc/UTC"}


class TestSystemClock:
    def test_now_is_always_aware(self):
        assert SystemClock().now().tzinfo is not None

    def test_now_is_in_the_configured_zone(self):
        assert SystemClock("Europe/Istanbul").now().utcoffset() == timedelta(hours=3)

    def test_now_is_re_read_every_call_not_cached(self):
        """A cached instant would make an API server that stays up overnight
        confidently wrong about the date."""
        c = SystemClock()
        assert c.now() <= c.now()
        assert type(c).__dict__.get("now") is not None  # a method, not a stored value

    def test_today_matches_now(self):
        c = SystemClock()
        assert c.today() == c.now().date()


class TestFrozenClock:
    def test_naive_input_is_read_as_local_wall_clock(self):
        """The reading a human gives ("01:30 in Istanbul") is the input shape
        the calendar regression test needs."""
        c = FrozenClock.at("2026-08-01 01:30")
        assert c.now() == datetime(2026, 8, 1, 1, 30, tzinfo=resolve_timezone("Europe/Istanbul"))
        assert c.now().astimezone(timezone.utc).date() == date(2026, 7, 31)

    def test_aware_input_in_another_zone_is_converted_not_relabelled(self):
        c = FrozenClock(datetime(2026, 7, 31, 22, 30, tzinfo=timezone.utc))
        assert c.now().hour == 1 and c.now().day == 1  # 01:30 on the 1st, Istanbul

    def test_now_does_not_move(self):
        c = FrozenClock.at("2026-08-01 01:30")
        assert c.now() == c.now()

    def test_advance_returns_a_new_clock_and_leaves_the_original_alone(self):
        """Mutation would let one test leak a moved clock into another."""
        c = FrozenClock.at("2026-08-01 01:30")
        later = c.advance(hours=2)
        assert later.now().hour == 3
        assert c.now().hour == 1

    def test_today_crosses_at_local_midnight_not_utc_midnight(self):
        assert FrozenClock.at("2026-07-31 23:59").today() == date(2026, 7, 31)
        assert FrozenClock.at("2026-08-01 00:01").today() == date(2026, 8, 1)


class TestTheOtherConsumersReadTheSameClock:
    """The plan's requirement is that the now-block, the calendar resolver, the
    scheduler and the todo store share ONE clock. Scheduler and todo were
    calling datetime.now() — the OPERATING SYSTEM's timezone, not
    settings.calendar_timezone. On this machine the two agree, so nothing was
    observably wrong; "correct because two independent settings happen to
    match" is the shape of the bug this module exists to remove.
    """

    def test_local_naive_now_follows_the_clock_and_drops_tzinfo(self):
        from jarvis.clock import local_naive_now

        frozen = FrozenClock.at("2027-03-15 09:00")
        assert local_naive_now(frozen) == datetime(2027, 3, 15, 9, 0)
        assert local_naive_now(frozen).tzinfo is None

    def test_the_scheduler_computes_next_run_from_the_configured_clock(self):
        """Frozen a year out, so a result derived from the OS clock cannot
        coincidentally match."""
        from jarvis import clock as clock_mod
        from jarvis.scheduler import calc_next_run

        with clock_mod.use_clock(FrozenClock.at("2027-03-15 09:00")):
            assert calc_next_run("daily", "10:00") == "2027-03-15T10:00:00"
            assert calc_next_run("daily", "08:00") == "2027-03-16T08:00:00"

    def test_the_scheduler_follows_a_non_default_timezone(self):
        from jarvis import clock as clock_mod
        from jarvis.scheduler import calc_next_run

        # 2027-03-15 23:30 in Istanbul is still 20:30 UTC the same day, so a
        # UTC-reading scheduler and an Istanbul-reading one disagree about
        # whether a 22:00 reminder has already passed.
        with clock_mod.use_clock(FrozenClock.at("2027-03-15 23:30", tz_name="Europe/Istanbul")):
            assert calc_next_run("daily", "22:00") == "2027-03-16T22:00:00"
        with clock_mod.use_clock(FrozenClock.at("2027-03-15 20:30", tz_name="UTC")):
            assert calc_next_run("daily", "22:00") == "2027-03-15T22:00:00"

    def test_the_todo_store_timestamps_from_the_configured_clock(self):
        from jarvis import clock as clock_mod
        from jarvis.todo_store import _now_iso

        with clock_mod.use_clock(FrozenClock.at("2027-03-15 09:00")):
            assert _now_iso() == "2027-03-15T09:00:00"


class TestProcessClock:
    def test_default_process_clock_is_istanbul(self):
        assert get_clock().tz_name == DEFAULT_TZ

    def test_use_clock_restores_the_previous_clock(self):
        before = get_clock()
        with use_clock(FrozenClock.at("2020-01-01 00:00")):
            assert get_clock().now().year == 2020
        assert get_clock() is before

    def test_use_clock_restores_even_when_the_body_raises(self):
        before = get_clock()
        with pytest.raises(RuntimeError):
            with use_clock(FrozenClock.at("2020-01-01 00:00")):
                raise RuntimeError("boom")
        assert get_clock() is before

    def test_set_clock_returns_the_previous_one(self):
        original = get_clock()
        try:
            previous = set_clock(FrozenClock.at("2020-01-01 00:00"))
            assert previous is original
        finally:
            set_clock(original)

    def test_configure_points_the_process_clock_at_a_zone(self):
        original = get_clock()
        try:
            configure("UTC")
            assert get_clock().tz_name == "UTC"
            assert get_clock().now().utcoffset() == timedelta(0)
        finally:
            set_clock(original)

    def test_configure_fails_fast_on_a_bad_zone_and_leaves_the_clock_intact(self):
        """Finding out at startup beats finding out when an event lands in the
        wrong timezone."""
        original = get_clock()
        with pytest.raises(UnknownTimezone):
            configure("Nowhere/Nothing")
        assert get_clock() is original
