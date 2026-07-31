"""jarvis/nlu/event_text.py -- a calendar holds a record, not a request.

Post-MVP Faz 2, plan item 4. Observed shape: told "yarın öğlen 3'te Baran'la
toplantı ayarla", a model creates an event TITLED "yarın öğlen 3'te Baran'la
toplantı ayarla" -- with the date duplicated into the title and an instruction
to JARVIS ("ayarla") preserved as if it were part of the meeting.

The tests that matter most here are the ones asserting what is NOT removed.
An over-eager cleaner is worse than none: a redundant title costs the user
nothing, while a title with a meaningful word eaten out of it is not something
they can recover. "Cuma raporu" is the canonical case -- Friday is the report's
NAME, and an early version of this module ate it.
"""
from __future__ import annotations

import pytest

from jarvis.nlu.event_text import (
    clean_title, description_is_restatement, title_quality,
)


class TestCleaningRemovesTheRequest:
    @pytest.mark.parametrize("raw,expected", [
        ("yarın öğlen 3'te Baran'la toplantı ayarla", "Baran'la toplantı"),
        ("yarın Baran ile toplantı", "Baran ile toplantı"),
        ("takvime diş hekimi randevusu ekle", "diş hekimi randevusu"),
        ("Diş hekimi randevusu ekle", "Diş hekimi randevusu"),
        ("Add dentist appointment to my calendar", "dentist appointment"),
        ("15 Ağustos'ta piknik", "piknik"),
        ("Cuma'da Baran ile toplantı", "Baran ile toplantı"),
        ("bugün sprint planlama", "sprint planlama"),
    ])
    def test_instructions_and_adverbial_dates_come_off(self, raw, expected):
        assert clean_title(raw).title == expected

    def test_cleaning_is_reported_so_the_change_is_visible(self):
        cleaning = clean_title("yarın Baran ile toplantı")
        assert cleaning.changed is True
        assert "yarın" in cleaning.reason

    def test_cleaning_is_idempotent(self):
        once = clean_title("yarın öğlen 3'te Baran'la toplantı ayarla").title
        assert clean_title(once).title == once


class TestCleaningLeavesMeaningAlone:
    @pytest.mark.parametrize("title", [
        "Cuma raporu",            # Friday is the report's NAME
        "Pazartesi",              # cleaning to empty -> keep the original
        "15 Ağustos piknik",      # bare date, could be the event's name
        "Baran'la toplantı",
        "Sprint planlama toplantısı",
        "Q3 bütçe gözden geçirme",
        "Diş hekimi",
        "Monday standup",
    ])
    def test_a_legitimate_title_survives_untouched(self, title):
        cleaning = clean_title(title)
        assert cleaning.title == title
        assert cleaning.changed is False

    @pytest.mark.parametrize("raw", [
        "takvime ekle", "ekle", "yarın", "Pazartesi", "add to my calendar",
        # Punctuation-only input is the case that actually reaches the final
        # guard -- the earlier inputs are all rescued by the per-step checks,
        # which is why an "may return an empty title" mutation survived the
        # first round.
        "---", "...", " - ", ",,,",
    ])
    def test_the_result_is_never_empty(self, raw):
        """If cleaning would consume everything, the original stands -- an
        untitled event is worse than a redundant one."""
        assert clean_title(raw).title, raw

    def test_empty_in_empty_out(self):
        assert clean_title("").title == ""
        assert clean_title(None).title == ""

    @pytest.mark.parametrize("title", [
        "Konser sanat",       # ends in "at", the command verb
        "Toplantı 2. kat",    # ditto
        "Sunum eskiz",        # ends in "kiz", near "koy"/"gir" territory
        "Randevu iptal",      # ends in "tal"
        "Yemek at Ali",       # "at" mid-string, not a trailing instruction
    ])
    def test_a_command_verb_hiding_inside_a_word_is_not_a_command(self, title):
        """Regression: the tail patterns had no word boundaries, so the verb
        "at" matched inside "kat". "Dr. Yılmaz, 2. kat" was classified as a
        bare instruction and a real description was discarded; the same
        pattern would have truncated titles."""
        assert clean_title(title).title == title

    def test_folding_used_for_offsets_preserves_length(self):
        """clean_title slices the ORIGINAL string at offsets found in the
        folded one, which is only sound while folding is 1:1. "İ".lower() is
        two code points in Python."""
        from jarvis.nlu.event_text import _foldable

        for sample in ["İstanbul toplantı", "ŞİRKET GEZİSİ", "Ağustos pikniği", "ẞ test"]:
            assert len(_foldable(sample)) == len(sample), sample


class TestTitleQuality:
    @pytest.mark.parametrize("title", [
        "Baran'la toplantı", "Sprint planlama toplantısı", "Diş hekimi", "Cuma raporu",
    ])
    def test_a_real_event_title_scores_full_marks(self, title):
        assert title_quality(title)[0] == 1.0

    def test_a_title_that_CLEANING_fixes_scores_on_the_cleaned_version(self):
        """The cleaned title is what actually gets written, so penalising a
        title clean_title() already fixed would make the gate ask about
        something that is no longer wrong."""
        assert title_quality("yarın öğlen 3'te Baran'la toplantı ayarla")[0] == 1.0

    @pytest.mark.parametrize("title,ceiling", [
        ("", 0.0),                       # nothing to create
        ("takvime ekle", 0.6),           # pure instruction, survives cleaning
        ("Yarın müsait miyim?", 0.6),    # a question, not an event
        ("Pazartesi", 0.7),              # only a date
        ("çok uzun bir başlık " * 6, 0.75),
    ])
    def test_a_title_that_still_reads_like_a_request_scores_below_auto(self, title, ceiling):
        score, reason = title_quality(title)
        assert score <= ceiling
        assert reason, "a low score must say why -- it becomes a question to the user"

    def test_scores_never_depend_on_todays_date(self):
        """Cleaning consults a fixed instant on purpose: the same title has to
        clean the same way on every day of the year."""
        from jarvis import clock
        from jarvis.clock import FrozenClock

        titles = ["Cuma raporu", "Pazartesi", "yarın Baran ile toplantı", "Baran'la toplantı"]
        baseline = {t: title_quality(t) for t in titles}
        for offset in (0, 90, 180, 300):
            with clock.use_clock(FrozenClock.at("2026-01-01 12:00").advance(days=offset)):
                assert {t: title_quality(t) for t in titles} == baseline


class TestDescriptionDiscipline:
    @pytest.mark.parametrize("title,description", [
        ("Baran'la toplantı", "Baran'la toplantı"),
        ("Baran'la toplantı", "baran'la toplanti"),      # same after folding
        ("Baran'la toplantı", "Baran'la toplantı ayarla"),
        ("Diş hekimi", "takvime ekle"),
    ])
    def test_a_description_that_adds_nothing_is_recognised(self, title, description):
        assert description_is_restatement(title, description) is True

    @pytest.mark.parametrize("title,description", [
        ("Baran'la toplantı", "Q3 bütçesini gözden geçireceğiz"),
        ("Diş hekimi", "Dr. Yılmaz, 2. kat"),
        ("Baran'la toplantı", ""),
        ("Toplantı", "Zoom: https://example.com/j/123"),
    ])
    def test_a_description_that_carries_real_information_is_kept(self, title, description):
        assert description_is_restatement(title, description) is False

    def test_this_does_not_claim_to_detect_invention(self):
        """Whether a description was actually SAID is not knowable from the
        tool arguments -- only the model knows what it heard. This function
        catches "adds nothing"; the rest is a prompt rule, and pretending
        otherwise would be the overclaiming this phase exists to remove."""
        invented_but_plausible = "Yıllık performans değerlendirmesi öncesi hazırlık"
        assert description_is_restatement("Baran'la toplantı", invented_but_plausible) is False
