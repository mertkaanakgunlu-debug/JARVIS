"""jarvis/nlu/temporal.py -- code computes the timestamp, not the model.

Post-MVP Faz 2, plan item 2. Two families of test here, and they are testing
different things:

  * The resolution table -- does "öğlen 3" mean 15:00. These are ordinary
    correctness tests over the expressions this user actually says.

  * The two structural invariants the rest of the system leans on:
    confidence is independent of the clock, and an unrecognised expression
    fails honestly instead of guessing. jarvis/policy_guard.py is only safe to
    build on this module because of the first one.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from jarvis.clock import FrozenClock
from jarvis.nlu import temporal
from jarvis.nlu.temporal import (
    ASK_THRESHOLD, AUTO_THRESHOLD, band, date_expression_confidence, fold,
    resolve, resolve_date, resolve_time, time_expression_confidence,
)

# Friday 2026-07-31, 01:30 local -- deliberately inside the window where UTC is
# still on the previous date, so every relative date here is also a check that
# the resolver is reading local time.
FRIDAY_0130 = FrozenClock.at("2026-07-31 01:30")


class TestTurkishFolding:
    def test_uppercase_dotless_i_folds_to_the_same_key_as_lowercase(self):
        """str.lower() is wrong here: "YARIN".lower() is "yarin" with a DOTTED
        i, while the correctly spelled word has a dotless one. The single most
        common expression in this user's requests turns on this."""
        assert fold("YARIN") == fold("yarın") == fold("Yarın") == "yarin"

    def test_diacritics_fold_to_ascii(self):
        assert fold("Ağustos") == "agustos"
        assert fold("Perşembe") == "persembe"
        assert fold("öğlen") == "oglen"


class TestDateResolution:
    @pytest.mark.parametrize("expression,expected", [
        ("bugün", "2026-07-31"), ("bugun", "2026-07-31"), ("today", "2026-07-31"),
        ("yarın", "2026-08-01"), ("yarin", "2026-08-01"), ("YARIN", "2026-08-01"),
        ("Yarına", "2026-08-01"), ("tomorrow", "2026-08-01"),
        ("dün", "2026-07-30"), ("öbür gün", "2026-08-02"),
        ("2026-08-15", "2026-08-15"), ("15.08.2026", "2026-08-15"),
        ("15/08/2026", "2026-08-15"), ("15-08-2026", "2026-08-15"),
        ("3 gün sonra", "2026-08-03"), ("2 hafta sonra", "2026-08-14"),
        ("5 gün önce", "2026-07-26"),
        ("15 Ağustos", "2026-08-15"), ("15 agustos 2027", "2027-08-15"),
        ("August 15", "2026-08-15"),
        ("gelecek pazartesi", "2026-08-03"), ("haftaya salı", "2026-08-04"),
        ("next monday", "2026-08-03"),
        ("ayın 15", "2026-08-15"),
    ])
    def test_resolves(self, expression, expected):
        assert resolve_date(expression, clock=FRIDAY_0130).normalized == expected

    def test_a_day_month_with_no_year_rolls_forward_rather_than_landing_in_the_past(self):
        """"3 Ocak" in July means next January, not seven months ago."""
        assert resolve_date("3 Ocak", clock=FRIDAY_0130).normalized == "2027-01-03"

    def test_bare_weekday_takes_the_next_occurrence_including_today(self):
        # Clock is a Friday.
        assert resolve_date("cuma", clock=FRIDAY_0130).normalized == "2026-07-31"
        assert resolve_date("cumartesi", clock=FRIDAY_0130).normalized == "2026-08-01"

    def test_qualified_weekday_moves_into_the_following_week(self):
        assert resolve_date("önümüzdeki cuma", clock=FRIDAY_0130).normalized == "2026-08-07"

    def test_relative_dates_track_the_clock(self):
        for offset in range(0, 40, 7):
            clk = FrozenClock.at("2026-07-31 09:00").advance(days=offset)
            expected = date(2026, 7, 31) + timedelta(days=offset + 1)
            assert resolve_date("yarın", clock=clk).value == expected


class TestDatesThatMustNotResolve:
    @pytest.mark.parametrize("expression", [
        "haftaya", "gelecek hafta", "next week", "gelecek ay", "bu ay",
        "yakında", "bir ara", "soon",
    ])
    def test_a_period_is_not_a_day(self, expression):
        """Guessing a day out of "haftaya" is exactly the silently-wrong
        behaviour this module exists to remove. It must fail, and say why."""
        r = resolve_date(expression, clock=FRIDAY_0130)
        assert r.ok is False
        assert r.value is None
        assert r.reason, "a failure with no reason cannot be read back to the user"

    @pytest.mark.parametrize("expression", ["zzzz", "", "the vibes", "2026-02-31"])
    def test_unparseable_input_fails_rather_than_defaulting_to_today(self, expression):
        r = resolve_date(expression, clock=FRIDAY_0130)
        assert r.ok is False and r.value is None

    def test_an_impossible_calendar_date_is_named_as_such(self):
        assert "not a real date" in resolve_date("2026-02-31", clock=FRIDAY_0130).reason


class TestTimeResolution:
    @pytest.mark.parametrize("expression,expected", [
        ("15:00", "15:00"), ("14:30", "14:30"), ("9:05", "09:05"), ("14.30", "14:30"),
        ("17", "17:00"), ("20", "20:00"),
        ("3pm", "15:00"), ("3:30 pm", "15:30"), ("9am", "09:00"),
        ("noon", "12:00"), ("midnight", "00:00"), ("gece yarısı", "00:00"),
    ])
    def test_absolute_forms(self, expression, expected):
        assert resolve_time(expression).normalized == expected

    @pytest.mark.parametrize("expression,expected", [
        ("öğlen 3", "15:00"),        # the owner's live calendar failure
        ("öğlen saat 3", "15:00"),
        ("öğlen 4", "16:00"),        # the plan's own example
        ("öğlen 12", "12:00"),
        ("öğlen 1", "13:00"),
        ("öğleden sonra 2", "14:00"),
        ("sabah 9", "09:00"),
        ("akşam 8", "20:00"),
        ("gece 11", "23:00"),
        ("gece 3", "03:00"),
        ("akşam 20", "20:00"),       # already 24h; the daypart is emphasis
    ])
    def test_turkish_dayparts_are_a_12_hour_difference_the_model_should_not_own(self, expression, expected):
        assert resolve_time(expression).normalized == expected

    @pytest.mark.parametrize("expression,expected", [
        ("saat 3'te", "03:00"), ("3'e", "03:00"), ("16:00'da", "16:00"),
        ("sabah 7'de", "07:00"), ("akşam 8'de", "20:00"),
    ])
    def test_turkish_case_endings_are_stripped(self, expression, expected):
        """A model relays "3'te" verbatim, and "3'te" parses as nothing."""
        assert resolve_time(expression).normalized == expected

    def test_an_empty_time_is_an_all_day_event_not_missing_information(self):
        r = resolve_time("")
        assert r.ok is True and r.hour is None and r.confidence == 1.0

    @pytest.mark.parametrize("expression", ["25:00", "12:99", "gibi", "sonra"])
    def test_invalid_times_fail(self, expression):
        assert resolve_time(expression).ok is False


class TestConfidenceBands:
    def test_band_boundaries_match_the_plan(self):
        assert band(AUTO_THRESHOLD) == "auto"
        assert band(AUTO_THRESHOLD - 0.001) == "ask"
        assert band(ASK_THRESHOLD) == "ask"
        assert band(ASK_THRESHOLD - 0.001) == "leave"

    @pytest.mark.parametrize("expression", ["yarın", "2026-08-15", "3 gün sonra", "15 Ağustos"])
    def test_unambiguous_dates_reach_the_auto_band(self, expression):
        assert resolve_date(expression, clock=FRIDAY_0130).band == "auto"

    def test_a_bare_weekday_asks_rather_than_choosing_silently(self):
        """"pazartesi" is genuinely ambiguous between this week and next, and
        that ambiguity is the most common way an event lands on the wrong day."""
        r = resolve_date("pazartesi", clock=FRIDAY_0130)
        assert r.ok is True and r.band == "ask"

    def test_a_bare_small_hour_asks_because_it_could_be_either_half_of_the_day(self):
        r = resolve_time("4")
        assert r.ok is True and r.band == "ask"
        assert "16:00" in r.reason  # the alternative is named

    def test_a_bare_large_hour_is_unambiguous(self):
        assert resolve_time("16").band == "auto"


class TestConfidenceIsIndependentOfTheClock:
    """The invariant jarvis/policy_guard.py is built on.

    policy_guard must be a pure function of (tool, args, settings): two
    independent evaluations of the same call, in two different graph nodes,
    must never disagree about whether it needs the user's OK. If confidence
    moved with the clock, a batch evaluated either side of midnight could be
    gated one way and executed the other.
    """

    EXPRESSIONS = [
        "yarın", "bugün", "pazartesi", "gelecek pazartesi", "2026-08-15",
        "15 Ağustos", "ayın 15", "hafta sonu", "haftaya", "zzzz", "",
        "3 gün sonra", "15.08", "öbür gün",
    ]

    @pytest.mark.parametrize("expression", EXPRESSIONS)
    def test_date_confidence_is_the_same_under_many_different_clocks(self, expression):
        clocks = [
            FrozenClock.at("2026-07-31 01:30").advance(days=d, hours=h)
            for d in range(0, 400, 37) for h in (0, 11, 23)
        ]
        scores = {resolve_date(expression, clock=c).confidence for c in clocks}
        assert len(scores) == 1, f"{expression!r} scored {scores} on different days"

    @pytest.mark.parametrize("expression", EXPRESSIONS)
    def test_the_gate_helper_agrees_with_the_resolver(self, expression):
        """date_expression_confidence() is what policy_guard calls -- it must
        report the same number the resolver would, or the gate would be judging
        something other than what runs."""
        assert date_expression_confidence(expression)[0] == pytest.approx(
            resolve_date(expression, clock=FRIDAY_0130).confidence
        )

    def test_the_gate_helper_never_touches_a_clock(self):
        """Structural, not incidental.

        The resolver's own confidence is clock-independent in all but a
        handful of cases, and those cases were enumerated exhaustively rather
        than guessed at: sweeping every day of 2026 x "ayın 28..31" produces
        exactly three divergences, all of them the end of January rolling into
        February, where no valid date exists in either the current or the next
        month. Direction is safe (0.85 -> 0.0 moves toward "ask") but "safe
        direction" is not "cannot happen", so the gate gets its own entry
        point that structurally cannot consult a clock.
        """
        from jarvis import clock as clock_mod

        # The PROCESS clock is what a clock-reading implementation would fall
        # back to, so it has to be the thing that moves. Moving only an
        # injected clock proved nothing -- the first version of this test did
        # exactly that and a "gate confidence reads the clock again" mutation
        # survived it.
        with clock_mod.use_clock(FrozenClock.at("2026-01-31 10:00")):
            assert resolve_date("ayın 30").ok is False           # the resolver cannot
            assert date_expression_confidence("ayın 30")[0] == pytest.approx(0.85)  # the gate is unmoved

    @pytest.mark.parametrize("expression", EXPRESSIONS)
    def test_gate_confidence_is_identical_across_a_full_year_of_process_clocks(self, expression):
        """The property the previous test's three exceptions make necessary."""
        from jarvis import clock as clock_mod

        scores = set()
        for offset in (0, 1, 29, 30, 31, 59, 180, 364):
            with clock_mod.use_clock(FrozenClock.at("2026-01-01 10:00").advance(days=offset)):
                scores.add(date_expression_confidence(expression)[0])
        assert len(scores) == 1, f"{expression!r} scored {scores} on different days"

    @pytest.mark.parametrize("expression", ["ayın 28", "ayın 29", "ayın 30", "ayın 31"])
    def test_the_known_divergent_family_is_stable_at_the_gate(self, expression):
        """These are the only expressions where the resolver itself moves with
        the clock, so they are the ones worth sweeping hardest."""
        from datetime import date as _date

        from jarvis import clock as clock_mod

        scores = set()
        day = _date(2026, 1, 25)
        while day < _date(2026, 3, 5):
            with clock_mod.use_clock(FrozenClock.at(f"{day.isoformat()} 10:00")):
                scores.add(date_expression_confidence(expression)[0])
            day += timedelta(days=1)
        assert scores == {0.85}

    @pytest.mark.parametrize("expression", ["15:00", "öğlen 3", "4", "", "gibi"])
    def test_time_confidence_matches_the_resolver(self, expression):
        assert time_expression_confidence(expression)[0] == pytest.approx(
            resolve_time(expression).confidence
        )


class TestReadingTheUsersOwnSentence:
    """Added after live measurement, not from reading the code.

    Real qwen3:8b, given "Pazartesi saat 4'te ... ekle", resolved the weekday
    itself (to a Saturday) and passed an ISO date. Scoring the arguments alone
    calls that a 1.00. These helpers let jarvis/policy_guard.py score the
    request instead of the model's interpretation of it. All clock-free.
    """

    def test_weekdays_are_found_through_turkish_case_endings(self):
        assert temporal.weekdays_named("Pazartesi saat 4'te spor") == {0}
        assert temporal.weekdays_named("Cumaya toplantı ekle") == {4}
        assert temporal.weekdays_named("pazartesiye erteleyelim") == {0}
        assert temporal.weekdays_named("Salı veya perşembe olur") == {1, 3}

    def test_a_sentence_with_no_weekday_names_none(self):
        assert temporal.weekdays_named("Yarın öğlen toplantı") == set()
        assert temporal.weekdays_named("") == set()

    def test_cumartesi_is_saturday_and_not_a_prefix_match_on_cuma(self):
        """Regression: alternation is first-match-wins, so an unsorted pattern
        read "cumartesi" as "cuma" + "rtesi" — a one-day error inside the check
        that exists to catch one-day errors."""
        assert temporal.weekdays_named("Cumartesi piknik") == {5}
        assert temporal.weekdays_named("Cumartesiye piknik ekle") == {5}
        assert temporal.weekdays_named("Pazartesi toplantı") == {0}
        assert temporal.weekdays_named("Pazar günü") == {6}

    @pytest.mark.parametrize("sentence", [
        "Salih ile toplantı ekle",      # "sali" + "h" -- a person, not a Tuesday
        "Cumhuriyet Bayramı etkinliği",  # "cum..." is not "cuma"
        "Pazarlama toplantısı ekle",     # "pazar" + "lama"
        "Persembeler dergisi",
    ])
    def test_a_word_that_merely_starts_like_a_weekday_is_not_one(self, sentence):
        """The ending must be a real Turkish case ending, not any word
        characters -- otherwise names and nouns become days."""
        assert temporal.weekdays_named(sentence) == set(), sentence

    @pytest.mark.parametrize("expression,expected", [
        ("2026-08-01", date(2026, 8, 1)),
        ("01.08.2026", date(2026, 8, 1)),
        ("1/8/2026", date(2026, 8, 1)),
    ])
    def test_absolute_dates_resolve_without_a_clock(self, expression, expected):
        assert temporal.absolute_date(expression) == expected

    @pytest.mark.parametrize("expression", ["yarın", "pazartesi", "15 Ağustos", "ayın 15", "", "zzz"])
    def test_anything_needing_a_clock_returns_none(self, expression):
        """The weekday cross-check only runs on dates it can pin without a
        clock — which is exactly the pre-resolved case it exists to catch."""
        assert temporal.absolute_date(expression) is None

    def test_a_bare_weekday_in_the_request_caps_confidence(self):
        score, reason = temporal.utterance_ambiguity("Pazartesi spor salonu ekle")
        assert score == pytest.approx(0.80)
        assert "which week" in reason

    def test_a_qualified_weekday_in_the_request_does_not(self):
        assert temporal.utterance_ambiguity("Gelecek pazartesi spor salonu ekle")[0] == 1.0

    def test_a_bare_hour_in_the_request_caps_confidence(self):
        score, reason = temporal.utterance_ambiguity("Yarın saat 4'te toplantı")
        assert score == pytest.approx(0.78)
        assert "16:00" in reason

    def test_a_daypart_removes_the_bare_hour_ambiguity(self):
        assert temporal.utterance_ambiguity("Yarın öğlen saat 3'e toplantı")[0] == 1.0

    def test_an_explicit_clock_time_is_not_a_bare_hour(self):
        assert temporal.utterance_ambiguity("Yarın saat 15:00'te toplantı")[0] == 1.0

    def test_an_empty_utterance_carries_no_signal(self):
        assert temporal.utterance_ambiguity("")[0] == 1.0
        assert temporal.utterance_ambiguity(None)[0] == 1.0

    def test_a_weekday_the_date_contradicts_is_reported(self):
        reason = temporal.weekday_conflict("Pazartesi saat 4'te spor", date(2026, 8, 1))
        assert reason and "pazartesi" in reason and "cumartesi" in reason

    def test_a_matching_weekday_is_no_conflict(self):
        assert temporal.weekday_conflict("Pazartesi spor", date(2026, 8, 3)) is None

    def test_any_of_several_named_weekdays_matching_is_enough(self):
        assert temporal.weekday_conflict("Salı veya perşembe", date(2026, 8, 4)) is None

    def test_a_sentence_carrying_its_own_date_suppresses_the_check(self):
        """"Cuma raporu için yarın toplantı" — Friday names the REPORT. Without
        this guard the check would fire on every event whose title contains a
        weekday."""
        assert temporal.weekday_conflict("Cuma raporu için yarın toplantı", date(2026, 8, 2)) is None
        assert temporal.weekday_conflict("Cuma raporu 2026-08-02'de", date(2026, 8, 2)) is None

    def test_no_resolvable_date_means_no_conflict_to_report(self):
        assert temporal.weekday_conflict("Pazartesi spor", None) is None


class TestCombinedResolution:
    def test_date_and_time_combine_into_one_aware_instant(self):
        r = resolve("yarın", "öğlen saat 3", clock=FRIDAY_0130)
        assert r.ok is True
        assert r.start.isoformat() == "2026-08-01T15:00:00+03:00"

    def test_a_phrase_in_the_date_field_alone_is_still_split(self):
        """Models put the whole phrase in one field. Splitting beats failing --
        but only when exactly one side parses as a date and the other as a time."""
        r = resolve("yarın öğlen 3", "", clock=FRIDAY_0130)
        assert r.ok is True and r.start.strftime("%Y-%m-%d %H:%M") == "2026-08-01 15:00"

    def test_no_time_means_an_all_day_event(self):
        r = resolve("yarın", "", clock=FRIDAY_0130)
        assert r.ok is True and r.all_day is True

    def test_confidence_is_the_minimum_not_the_average(self):
        """A perfect date with an ambiguous time still puts the event at the
        wrong hour; averaging would hide that behind the confident half."""
        r = resolve("2026-08-15", "4", clock=FRIDAY_0130)
        assert r.confidence == pytest.approx(resolve_time("4").confidence)
        assert r.band == "ask"

    def test_an_unresolvable_date_fails_the_whole_resolution(self):
        r = resolve("haftaya", "15:00", clock=FRIDAY_0130)
        assert r.ok is False and r.start is None
        assert "haftaya" in r.reason

    def test_describe_names_the_resolved_instant_not_the_expression(self):
        """What a confirmation prompt shows. "yarın" tells the user nothing
        about whether the resolution was right."""
        assert temporal.describe(resolve("yarın", "15:00", clock=FRIDAY_0130)) == "2026-08-01 15:00"
        assert temporal.describe(resolve("yarın", "", clock=FRIDAY_0130)) == "2026-08-01 (all day)"
