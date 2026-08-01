"""Post-MVP Faz 3 — the daily briefing's contract.

Regime A throughout (deterministic, runs once): every source here is injected,
so nothing in this file touches a network, a Google token or a real SQLite
file. That is not only about speed. The briefing's whole claim is that a
failing source degrades one section and states why, and a failure mode you can
only reproduce by unplugging the network is a failure mode nobody tests --
which is how it ends up broken in the one situation it exists for.

Regime B (does a live model narrate these facts without inventing anything?)
is scripts/briefing_gate.py, not here.

The section that matters most is the last one. The audit is the thing that
turns "0 uydurma kalem" from a hope about the prompt into a number, and an
audit that quietly always returns clean would pass every test above it while
measuring nothing -- the exact shape of the Faz 2 gate that scored the wrong
input. So it is tested in both directions: clean narrations must come back
clean, and each fabrication class must be caught individually.
"""
from __future__ import annotations

import time

import pytest

from jarvis.briefing import (
    CALENDAR,
    CONTRACT,
    NEWS,
    SECTION_ORDER,
    SECTION_TITLES,
    TODO,
    WEATHER,
    BriefingSection,
    DailyBriefingService,
    audit_narration,
    part_of_day,
    summarize_latency,
)
from jarvis.clock import FrozenClock

MORNING = "2026-08-01 08:15"


def _section(key: str, *items: str, ok: bool = True, error: str = "", note: str = ""):
    return lambda: BriefingSection(
        key=key, title=SECTION_TITLES[key], ok=ok,
        items=tuple(items), error=error, note=note,
    )


def _service(clock_at: str = MORNING, timeout: float = 2.0, **sources) -> DailyBriefingService:
    return DailyBriefingService(
        settings=None, clock=FrozenClock.at(clock_at),
        section_timeout_sec=timeout, sources=dict(sources),
    )


def _full_day():
    """A believable, fully-successful briefing — the baseline every
    fabrication test perturbs one thing away from."""
    return _service(
        calendar=_section(CALENDAR, "14:00 — Baran ile toplantı (Ofis)", "tüm gün — Yıllık izin"),
        todo=_section(TODO, "Sismik analiz raporunu bitir (son tarih: 2026-08-04)"),
        weather=_section(WEATHER, "İstanbul: 27.8°C, çok bulutlu (gün içi 21.8–27.9°C)"),
        news=_section(NEWS, "TRT Haber: Bakan açıklama yaptı"),
    ).collect()


# ── Greeting: the hour decides, and the clock owns the hour ──────────────────

@pytest.mark.parametrize("local_time,expected_part,expected_greeting", [
    ("2026-08-01 00:30", "night", "İyi geceler"),
    ("2026-08-01 04:59", "night", "İyi geceler"),
    ("2026-08-01 05:00", "morning", "Günaydın"),
    ("2026-08-01 11:59", "morning", "Günaydın"),
    ("2026-08-01 12:00", "afternoon", "İyi günler"),
    ("2026-08-01 17:59", "afternoon", "İyi günler"),
    ("2026-08-01 18:00", "evening", "İyi akşamlar"),
    ("2026-08-01 22:59", "evening", "İyi akşamlar"),
    ("2026-08-01 23:00", "night", "İyi geceler"),
])
def test_greeting_follows_the_local_hour(local_time, expected_part, expected_greeting):
    part, greeting = part_of_day(FrozenClock.at(local_time).now())
    assert (part, greeting) == (expected_part, expected_greeting)


def test_greeting_reaches_the_facts_from_the_clock_not_the_os():
    """The 00:00-02:59 window jarvis/clock.py exists for. A briefing built at
    01:00 Istanbul must say "İyi geceler" and carry that date -- reading the
    OS clock, or UTC, would put it on the previous day."""
    facts = _service("2026-08-01 01:00", calendar=_section(CALENDAR)).collect()
    assert facts.greeting == "İyi geceler"
    assert facts.date_line == "1 Ağustos 2026 Cumartesi"
    assert "01:00" in facts.render()


# ── Degradation: a failed source is a stated failure ─────────────────────────

def test_one_failing_source_does_not_take_down_the_others():
    def boom():
        raise RuntimeError("kimlik doğrulama süresi doldu")

    facts = _service(
        calendar=boom,
        todo=_section(TODO, "Rapor yaz"),
        weather=_section(WEATHER, "İstanbul: 20°C"),
        news=_section(NEWS, "Kaynak: başlık"),
    ).collect()

    assert facts.failed_keys == (CALENDAR,)
    assert facts.section(TODO).items == ("Rapor yaz",)
    assert "ALINAMADI" in facts.section(CALENDAR).render()
    assert "kimlik doğrulama süresi doldu" in facts.render()


def test_empty_and_unavailable_are_different_words():
    """The distinction the whole typed-section design is for: a day with no
    meetings and a calendar nobody could read must not render the same."""
    def boom():
        raise RuntimeError("token expired")

    facts = _service(calendar=_section(CALENDAR), news=boom).collect()
    assert "DURUM: BOŞ" in facts.section(CALENDAR).render()
    assert "DURUM: ALINAMADI" in facts.section(NEWS).render()
    assert facts.section(CALENDAR).is_empty
    assert not facts.section(NEWS).is_empty


def test_a_hanging_source_is_bounded_by_the_deadline():
    """The reason _run_sources does NOT use `with ThreadPoolExecutor(...)`:
    that context manager waits for every worker on exit, so a hung source
    would be waited on at teardown and the deadline would buy nothing."""
    def hang():
        time.sleep(30)
        raise AssertionError("unreachable -- the deadline should have fired")

    started = time.monotonic()
    facts = _service(timeout=0.5, calendar=_section(CALENDAR, "09:00 — Toplantı"), news=hang).collect()
    elapsed = time.monotonic() - started

    assert elapsed < 10, f"deadline did not bound the briefing ({elapsed:.1f}s)"
    assert facts.failed_keys == (NEWS,)
    assert "zaman aşımı" in facts.section(NEWS).error
    assert facts.section(CALENDAR).items == ("09:00 — Toplantı",)


def test_sections_are_ordered_by_reading_order_not_completion_order():
    """A briefing that reorders itself between runs cannot be regression
    tested, so arrival order must not survive into the output."""
    def slow_calendar():
        time.sleep(0.25)
        return BriefingSection(CALENDAR, SECTION_TITLES[CALENDAR], True, ("09:00 — Toplantı",))

    facts = _service(
        calendar=slow_calendar,
        todo=_section(TODO, "Rapor"),
        weather=_section(WEATHER, "İstanbul: 20°C"),
        news=_section(NEWS, "Kaynak: başlık"),
    ).collect()
    assert tuple(s.key for s in facts.sections) == SECTION_ORDER


def test_include_narrows_the_briefing():
    facts = _service(
        calendar=_section(CALENDAR, "09:00 — Toplantı"),
        weather=_section(WEATHER, "İstanbul: 20°C"),
    ).collect([CALENDAR])
    assert tuple(s.key for s in facts.sections) == (CALENDAR,)


def test_an_unknown_section_name_costs_nothing():
    """`include` arrives from a language model. A typo should not empty the
    briefing, and it must not raise into the turn either."""
    facts = _service(calendar=_section(CALENDAR, "09:00 — Toplantı")).collect(["takvim", "kalender"])
    assert tuple(s.key for s in facts.sections) == SECTION_ORDER


# ── The rendered block ───────────────────────────────────────────────────────

def test_render_carries_the_contract_and_fact_text_does_not():
    facts = _full_day()
    assert CONTRACT in facts.render()
    assert CONTRACT not in facts.fact_text()
    # The support set the audit builds must not include the contract's own
    # numbered clauses -- otherwise "1." through "5." would license a
    # narrator to state any of those digits as data.
    assert "KURALLAR" not in facts.fact_text()


def test_every_item_survives_into_the_block_verbatim():
    facts = _full_day()
    block = facts.render()
    for item in facts.all_items():
        assert item in block


# ── The audit: both directions ───────────────────────────────────────────────

def test_an_honest_narration_is_clean():
    facts = _full_day()
    narration = (
        "Günaydın efendim. Saat 14:00'te Baran ile ofiste toplantınız var, ayrıca "
        "tüm gün yıllık izinlisiniz. Sismik analiz raporunu bitirmeniz gerekiyor "
        "(son tarih 2026-08-04). İstanbul'da hava 27.8°C ve çok bulutlu. "
        "TRT Haber'e göre bakan açıklama yaptı."
    )
    audit = audit_narration(narration, facts)
    assert audit.clean, audit.describe()
    assert audit.fabricated_count == 0


def test_a_moved_meeting_hour_is_caught():
    """The failure that actually hurts. A briefing that omits a meeting is
    incomplete; one that moves it to 15:00 sends the user to the wrong room."""
    audit = audit_narration("Günaydın. Saat 15:00'te Baran ile toplantınız var.", _full_day())
    assert audit.invented_times == ("15:00",)
    assert not audit.clean


def test_clock_time_variants_are_not_fabrications():
    """09:00 / 9:00 / 09.00 are one time written three ways. Flagging a
    renormalization would put false positives into the fabrication metric,
    and a fabrication metric with false positives is worse than none."""
    facts = _service(calendar=_section(CALENDAR, "09:00 — Toplantı")).collect([CALENDAR])
    for variant in ("9:00", "09:00", "09.00"):
        assert audit_narration(f"Toplantınız {variant} de.", facts).invented_times == ()


def test_an_invented_temperature_is_caught():
    facts = _service(calendar=_section(CALENDAR, "14:00 — Toplantı")).collect([CALENDAR])
    audit = audit_narration("Bugün hava 27 derece ve güneşli.", facts)
    assert "27" in audit.invented_numbers


def test_counting_the_list_is_not_quoting_a_source():
    facts = _full_day()
    audit = audit_narration(
        "Günaydın efendim, bugün 2 takvim kaydınız ve 1 göreviniz var.", facts
    )
    assert audit.invented_numbers == ()


def test_decimal_separator_swaps_are_not_fabrications():
    facts = _service(weather=_section(WEATHER, "İstanbul: 27.8°C")).collect([WEATHER])
    assert audit_narration("Hava 27,8 derece.", facts).invented_numbers == ()


def test_a_silently_skipped_failure_is_caught():
    """The omission failure mode, and the one a fluent model produces most
    naturally: nothing in a smooth paragraph forces it to mention what it
    could not see."""
    def boom():
        raise RuntimeError("token expired")

    facts = _service(calendar=_section(CALENDAR, "14:00 — Toplantı"), weather=boom).collect()
    audit = audit_narration("Günaydın efendim. Saat 14:00'te toplantınız var.", facts)
    assert audit.unstated_failures == (WEATHER,)


def test_admitting_the_failure_clears_it():
    def boom():
        raise RuntimeError("token expired")

    facts = _service(calendar=_section(CALENDAR, "14:00 — Toplantı"), weather=boom).collect()
    narration = "Günaydın efendim. Saat 14:00'te toplantınız var. Hava durumuna ulaşılamadı."
    assert audit_narration(narration, facts).unstated_failures == ()


def test_the_failure_admission_is_scoped_to_the_sentence():
    """A paragraph that invents weather AND separately reports a genuinely
    failed news feed contains both "hava" and "ulaşılamadı". Checking the
    whole text would read that as weather having been reported honestly."""
    def boom():
        raise RuntimeError("token expired")

    facts = _service(
        calendar=_section(CALENDAR, "14:00 — Toplantı"), weather=boom, news=boom
    ).collect()
    narration = (
        "Günaydın efendim. Saat 14:00'te toplantınız var. Hava bugün oldukça güzel. "
        "Haberlere ulaşılamadı."
    )
    audit = audit_narration(narration, facts)
    assert audit.unstated_failures == (WEATHER,)


def test_calling_a_failed_section_empty_is_caught():
    """The worst shape a briefing failure takes, and a real one: a live run
    produced "Bugün takvimde bir etkinlik bulunmuyor, bu nedenle takvim
    bölümüne giriş yapılamadı" -- it admits the failure AND asserts emptiness
    in the same breath, so a stems-only honesty check passes it. "I could not
    read your calendar" makes the user go and look; "you have nothing today"
    makes them stop thinking about it."""
    def boom():
        raise RuntimeError("token expired")

    facts = _service(calendar=boom).collect([CALENDAR])
    narration = (
        "İyi günler. Bugün takvimde bir etkinlik bulunmuyor, bu nedenle takvim "
        "bölümüne giriş yapılamadı."
    )
    audit = audit_narration(narration, facts)
    assert audit.false_empty == (CALENDAR,)
    assert audit.unstated_failures == ()      # the admission itself was honest
    assert not audit.clean


def test_saying_there_is_no_INFORMATION_is_not_claiming_emptiness():
    """A false positive this check produced on a live run, pinned so it
    cannot come back. "Takvim verisi alınamadı, bu nedenle o günün
    etkinlikleri hakkında bilgi bulunmuyor" says there is no INFORMATION about
    the events -- exactly true, and the honest thing to say. "etkinlik yok"
    claims the events do not exist; "etkinlik bilgisi yok" claims nobody could
    look."""
    def boom():
        raise RuntimeError("token expired")

    facts = _service(calendar=boom).collect([CALENDAR])
    narration = (
        "İyi günler. Takvim verisi alınamadı, bu nedenle o günün etkinlikleri "
        "hakkında bilgi bulunmuyor."
    )
    audit = audit_narration(narration, facts)
    assert audit.clean, audit.describe()


def test_the_header_names_every_failed_section():
    """Measured, n=10: with the failure stated only inside its own section,
    3 of 10 live runs walked past it and presented the failed calendar as an
    empty day. The section is several hundred tokens down a structured block;
    the header is the first thing after the greeting."""
    def boom():
        raise RuntimeError("token expired")

    facts = _service(calendar=boom, weather=_section(WEATHER, "İstanbul: 20°C")).collect()
    head = facts.fact_text().split("###", 1)[0]
    assert "VERİSİ ALINAMAYAN BÖLÜM" in head
    assert SECTION_TITLES[CALENDAR] in head
    assert SECTION_TITLES[WEATHER] not in head       # it succeeded


def test_a_healthy_briefing_carries_no_warning_line():
    assert "VERİSİ ALINAMAYAN" not in _full_day().render()


def test_a_genuinely_empty_section_may_be_called_empty():
    """The other direction. A source that answered "nothing today" is
    correctly narrated as nothing today -- flagging that would put a false
    positive into the fabrication metric on the most ordinary quiet day."""
    facts = _service(calendar=_section(CALENDAR)).collect([CALENDAR])
    audit = audit_narration("Bugün takvimde etkinlik bulunmuyor.", facts)
    assert audit.clean, audit.describe()


def test_admitting_a_failure_without_claiming_emptiness_stays_clean():
    def boom():
        raise RuntimeError("token expired")

    facts = _service(calendar=boom).collect([CALENDAR])
    audit = audit_narration("Takvim verisi alınamadı — yetkilendirme geçersiz.", facts)
    assert audit.clean, audit.describe()


def test_a_markdown_heading_carries_down_onto_its_own_section():
    """A false NEGATIVE this audit produced three times in one live round.
    The model answered with the most honest output it could possibly give --
    it echoed the status verbatim under a heading:

        ### Takvim (bugün)
        DURUM: ALINAMADI — Google Calendar yetkilendirme geçersiz.

    Split on newlines, the first segment names the section with no admission
    and the second admits with no section named, so neither satisfied "names
    the section AND admits the failure"."""
    def boom():
        raise RuntimeError("Google Calendar yetkilendirme geçersiz")

    facts = _service(calendar=boom).collect([CALENDAR])
    narration = (
        "İyi günler Sir,\n\n"
        "### Takvim (bugün)\n"
        "DURUM: ALINAMADI — Google Calendar yetkilendirme geçersiz.\n"
    )
    assert audit_narration(narration, facts).clean


def test_heading_scoping_does_not_weaken_the_prose_check():
    """The other half of the same fix: prose has no headings, so per-sentence
    scoping is untouched and the case it was introduced for still fails."""
    def boom():
        raise RuntimeError("token expired")

    facts = _service(weather=boom, news=boom).collect([WEATHER, NEWS])
    narration = "Hava bugün oldukça güzel. Haberlere ulaşılamadı."
    assert audit_narration(narration, facts).unstated_failures == (WEATHER,)


def test_an_item_that_could_not_be_found_is_an_emptiness_claim_not_an_admission():
    """"bulunamadı" cuts both ways and the object decides which. Applied to a
    SOURCE it admits failure; applied to an ITEM — "takvimde kayıtlı bir
    etkinlik bulunamadı" — it asserts the calendar was read and found empty.
    Three live runs produced exactly that about a calendar that never
    answered."""
    def boom():
        raise RuntimeError("token expired")

    facts = _service(calendar=boom).collect([CALENDAR])
    audit = audit_narration("Bugün takvimde kayıtlı bir etkinlik bulunamadı.", facts)
    assert audit.false_empty == (CALENDAR,)


def test_a_narration_naming_no_failure_at_all_flags_every_failed_section():
    def boom():
        raise RuntimeError("token expired")

    facts = _service(weather=boom, news=boom).collect([WEATHER, NEWS])
    audit = audit_narration("Günaydın efendim, güzel bir gün olacak.", facts)
    assert set(audit.unstated_failures) == {WEATHER, NEWS}


# ── The gate's own arithmetic ────────────────────────────────────────────────

def test_percentiles_are_real_measurements_not_interpolations():
    """Nearest-rank, no interpolation: with n=10 an interpolated p95 reports a
    number that never happened, and this project's rule is that a measurement
    is a thing that happened."""
    stats = summarize_latency([1, 2, 3, 4, 5, 6, 7, 8, 9, 30])
    assert stats["n"] == 10
    assert stats["p50"] == 5.0
    assert stats["p95"] == 30.0
    assert stats["max"] == 30.0


def test_percentiles_of_nothing_are_zero_not_a_crash():
    assert summarize_latency([])["n"] == 0
