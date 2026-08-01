"""Daily briefing — the facts are gathered in code, the model only narrates.

Post-MVP Faz 3, the plan's first acceptance milestone: *"JARVIS bugün neler
var"* answered with the calendar, the to-do list, the weather and the day's
headlines, hour-aware, **with nothing invented**.

The load-bearing decision is that a briefing is NOT an LLM workflow::

    DailyBriefingService
       ├─ calendar   ├─ todo
       ├─ weather    └─ news
                  ↓
            BriefingFacts      ← deterministic, this module
                  ↓
                 LLM           ← narration only

The model does not gather the data, does not resolve "today", and does not
decide which event exists. It receives a closed list of already-final strings
and turns them into a sentence. Everything below exists to make that division
real rather than a wish written in a system prompt.

Three properties follow from it, and each is measured rather than asserted:

**Fabrication is bounded by construction.** Every fact the model can state is
a string in ``BriefingFacts``. ``audit_narration()`` re-checks the model's
output against exactly those strings, so "0 uydurma kalem" is a number this
repo can produce, not a claim about how good the prompt is.

**A failed source is a stated failure, not an omission.** Each section carries
``ok`` and, when false, why. A briefing that silently skips the calendar looks
identical to a briefing on a genuinely empty day — and the second one is true
while the first is a lie by omission. That distinction is the entire reason
sections are typed instead of being concatenated text.

**Latency is a section-local property.** The gate is p50 < 5 s / p95 < 10 s
across four sources, three of which are network calls. They run concurrently
with a per-section deadline: a hanging feed costs its own section and nothing
else. Honest caveat on that deadline, and it is the same one
``ToolSpec.timeout_class="soft_thread_timeout"`` already names elsewhere in
this codebase: it bounds *how long the briefing waits*, not how long the
underlying HTTP call runs. The worker thread is not killed. Nothing here
writes anything, so a straggler that finishes after its deadline has no effect
beyond its own wasted socket.
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable, Iterable, Sequence

from jarvis.clock import Clock, format_long_date, get_clock

if TYPE_CHECKING:
    from jarvis.config import Settings

logger = logging.getLogger(__name__)

# Section keys, in the order a briefing reads them. Calendar first because it
# is the only section with a hard consequence attached (miss it and you miss a
# meeting); news last because it is the only one the user can skip without
# losing anything.
CALENDAR = "calendar"
TODO = "todo"
WEATHER = "weather"
NEWS = "news"
SECTION_ORDER: tuple[str, ...] = (CALENDAR, TODO, WEATHER, NEWS)

SECTION_TITLES: dict[str, str] = {
    CALENDAR: "Takvim (bugün)",
    TODO: "Yapılacaklar",
    WEATHER: "Hava durumu",
    NEWS: "Öne çıkan haberler",
}

EMPTY_NOTES: dict[str, str] = {
    CALENDAR: "Bugün için planlanmış etkinlik yok.",
    TODO: "Açık görev yok.",
    WEATHER: "Hava durumu verisi yok.",
    NEWS: "Başlık alınamadı.",
}

DEFAULT_SECTION_TIMEOUT_SEC = 8.0
DEFAULT_TODO_LIMIT = 5
DEFAULT_CALENDAR_LIMIT = 10

# Hour boundaries for the greeting. Turkish daily rhythm, not a translation of
# the English one: "İyi günler" covers the whole working afternoon and "İyi
# akşamlar" starts at 18:00, not 17:00.
_PARTS_OF_DAY: tuple[tuple[int, str, str], ...] = (
    (5, "morning", "Günaydın"),
    (12, "afternoon", "İyi günler"),
    (18, "evening", "İyi akşamlar"),
    (23, "night", "İyi geceler"),
)


def part_of_day(moment: datetime) -> tuple[str, str]:
    """(part, greeting) for a local wall-clock hour.

    00:00–04:59 wraps to "night" rather than falling off the table's start:
    the boundaries are a cycle, and 03:00 is the same part of the day as 23:30.
    """
    hour = moment.hour
    part, greeting = _PARTS_OF_DAY[-1][1], _PARTS_OF_DAY[-1][2]
    for start, name, word in _PARTS_OF_DAY:
        if hour >= start:
            part, greeting = name, word
    return part, greeting


@dataclass(frozen=True)
class BriefingSection:
    """One source's contribution, with its own success or failure.

    `items` are FINAL user-facing strings. Nothing downstream reformats them,
    and the audit compares the narration against them verbatim — so a section
    that returns half-rendered data (a raw ISO timestamp, a Google event dict
    repr) has already lost the property this type exists to give.
    """

    key: str
    title: str
    ok: bool
    items: tuple[str, ...] = ()
    error: str = ""
    note: str = ""          # honest qualifier on a PARTIAL success
    elapsed_sec: float = 0.0

    @property
    def is_empty(self) -> bool:
        return self.ok and not self.items

    def render(self) -> str:
        head = f"### {self.title}"
        if not self.ok:
            return f"{head}\nDURUM: ALINAMADI — {self.error or 'bilinmeyen hata'}"
        if not self.items:
            return f"{head}\nDURUM: BOŞ — {EMPTY_NOTES.get(self.key, 'Kayıt yok.')}"
        lines = [head, f"DURUM: {len(self.items)} kayıt"]
        lines.extend(f"- {item}" for item in self.items)
        if self.note:
            lines.append(f"NOT: {self.note}")
        return "\n".join(lines)


def failed_section(key: str, error: str, elapsed: float = 0.0) -> BriefingSection:
    return BriefingSection(
        key=key, title=SECTION_TITLES.get(key, key), ok=False,
        error=error, elapsed_sec=elapsed,
    )


@dataclass(frozen=True)
class BriefingFacts:
    """Everything the narrator is allowed to say, and nothing else."""

    generated_at: datetime
    tz_name: str
    part_of_day: str
    greeting: str
    user_name: str
    date_line: str
    sections: tuple[BriefingSection, ...] = ()
    elapsed_sec: float = 0.0

    def section(self, key: str) -> BriefingSection | None:
        return next((s for s in self.sections if s.key == key), None)

    @property
    def failed_keys(self) -> tuple[str, ...]:
        return tuple(s.key for s in self.sections if not s.ok)

    def all_items(self) -> tuple[str, ...]:
        return tuple(item for section in self.sections for item in section.items)

    def fact_text(self) -> str:
        """Everything that is DATA — the header and the sections, no contract.

        Kept separate from render() because the audit's support set is built
        from this. CONTRACT is a numbered list of instructions; counting its
        "1." through "5." as evidence would license a narrator to state any of
        those digits as a fact and pass the check.
        """
        header = (
            "[Briefing] GÜNLÜK BRİFİNG VERİSİ — aşağıdaki kayıtlar sistem "
            "tarafından toplandı, doğrulanmıştır.\n"
            f"Hitap: {self.greeting} {self.user_name}\n"
            f"Tarih: {self.date_line}\n"
            f"Saat: {self.generated_at:%H:%M} ({self.tz_name}), günün dilimi: {self.part_of_day}"
        )
        # A failed source is announced in the HEADER, not only inside its own
        # section. Measured, n=10: with the failure stated only where it
        # happened, 3 of 10 live runs walked past it and presented the failed
        # calendar as an empty day. The section is several hundred tokens
        # down a structured block and reads like one row among four; the
        # header is the first thing after the greeting.
        #
        # Note this is a code-authored sentence, not a plea to the model. It
        # names the exact sections and the exact wrong thing to say, because
        # the observed failure was not "forgot to mention" -- it was
        # substituting a plausible fact for a missing one.
        if self.failed_keys:
            names = ", ".join(SECTION_TITLES.get(k, k) for k in self.failed_keys)
            header += (
                f"\n⚠ VERİSİ ALINAMAYAN BÖLÜM: {names}. Bunu kullanıcıya AÇIKÇA "
                "söylemek zorundasın. Bu bölüm için 'kayıt yok' / 'etkinlik "
                "bulunmuyor' / 'boş' DEME — boş olduğunu bilmiyorsun, veriye "
                "ulaşılamadı."
            )
        body = "\n\n".join(section.render() for section in self.sections)
        return f"{header}\n\n{body}"

    def render(self) -> str:
        """The fact block handed to the model.

        Deliberately not prose. It is a labelled, closed record with an
        explicit contract at the end, because the one thing measured to change
        this model's behaviour more than code is what the tool output looks
        like (the plan's risk table: a single added sentence in a docstring
        took a gate from 10/10 to 0/10). A block that reads like a report
        invites the model to extend the report; a block that reads like data
        invites it to describe the data.
        """
        return f"{self.fact_text()}\n\n{CONTRACT}"


# The narration contract. Every clause here corresponds to a way the gate can
# fail, in the order the gate checks them — this is not general advice about
# tone, it is the list of things that would make the briefing wrong.
CONTRACT = (
    "KURALLAR (bu brifingi anlatırken):\n"
    "1. Yalnızca yukarıdaki kayıtları aktar. Listede olmayan bir etkinlik, görev, "
    "sıcaklık veya haber EKLEME — tek bir tane bile.\n"
    "2. Hiçbir sayıyı, saati veya tarihi değiştirme; yukarıda yazdığı gibi kullan.\n"
    "3. DURUM: ALINAMADI olan bölüm için o bölümün verisine ULAŞILAMADIĞINI açıkça "
    "söyle. Bu bölüm için 'kayıt yok', 'etkinlik bulunmuyor', 'boş' DEME — veri "
    "alınamadı, boş olduğunu bilmiyorsun. O bölüm hakkında tahmin yürütme.\n"
    "4. DURUM: BOŞ olan bölüm gerçekten boştur — 'veri alınamadı' deme, 'kayıt yok' de.\n"
    "5. Hitabı ve günün dilimini kullanarak kısa, doğal bir özet yaz. Araç çağırma; "
    "gereken her şey yukarıda."
)


# ── Sources ──────────────────────────────────────────────────────────────────
#
# Each source is a zero-argument callable returning a BriefingSection, so a
# test can inject a fake without a network, an OAuth token or a database. That
# is not a convenience: the sections' failure behaviour IS the feature here,
# and a failure mode you can only reproduce by unplugging the network is a
# failure mode nobody tests.

SectionSource = Callable[[], BriefingSection]


def _fmt_event(event: dict, clock: Clock) -> str:
    """One Google Calendar event as a final briefing line.

    Times are rendered in the CLOCK's zone, not the zone Google happened to
    return, so a briefing never states an hour the user's other tools would
    disagree with (jarvis/clock.py's whole reason for existing).
    """
    summary = str(event.get("summary") or "(başlıksız)")
    start = event.get("start") or {}
    raw = start.get("dateTime") or start.get("date") or ""
    label = ""
    if "T" in raw:
        try:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if moment.tzinfo is not None:
                moment = moment.astimezone(clock.tz)
            label = f"{moment:%H:%M}"
        except ValueError:
            label = raw
    elif raw:
        label = "tüm gün"

    location = str(event.get("location") or "").strip()
    line = f"{label} — {summary}" if label else summary
    return f"{line} ({location})" if location else line


def calendar_source(settings: "Settings", clock: Clock, limit: int = DEFAULT_CALENDAR_LIMIT) -> SectionSource:
    """Today's remaining events: now → local midnight.

    Not "the next 24 hours". Asked *"bugün neler var"* at 18:00, a 24-hour
    window answers with tomorrow morning's 09:00 stand-up, which is a true
    statement about a different question. The end of the local day is what
    "bugün" means, and the local day is the clock's, not the OS's.
    """
    def run() -> BriefingSection:
        from jarvis.tools import calendar as calendar_tool

        now = clock.now()
        end_of_day = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        events = calendar_tool.fetch_events(
            settings, time_min=now, time_max=end_of_day, max_results=limit,
        )
        return BriefingSection(
            key=CALENDAR, title=SECTION_TITLES[CALENDAR], ok=True,
            items=tuple(_fmt_event(event, clock) for event in events),
        )

    return run


def todo_source(settings: "Settings", clock: Clock, limit: int = DEFAULT_TODO_LIMIT) -> SectionSource:
    """The highest-priority open to-dos, with their due dates.

    `top_open` (priority DESC, due ASC) rather than everything: a briefing is
    a summary, and a 40-item list read aloud is not one. The count of what was
    left out is reported as a note so the omission is stated, never silent.
    """
    def run() -> BriefingSection:
        from jarvis import paths
        from jarvis.todo_store import TodoStore

        store = TodoStore(paths.data_dir() / "sessions.db")
        try:
            rows = store.top_open(limit)
            total = store.count_open()
        finally:
            store.close()

        items = []
        for row in rows:
            title = str(row.get("title") or "(başlıksız)")
            due = str(row.get("due_date") or "").strip()
            items.append(f"{title} (son tarih: {due})" if due else title)

        note = f"{total - len(rows)} görev daha açık, listelenmedi." if total > len(rows) else ""
        return BriefingSection(
            key=TODO, title=SECTION_TITLES[TODO], ok=True,
            items=tuple(items), note=note,
        )

    return run


def weather_source(settings: "Settings", clock: Clock) -> SectionSource:
    def run() -> BriefingSection:
        from jarvis.tools import weather as weather_tool

        report = weather_tool.fetch_for_settings(settings)
        return BriefingSection(
            key=WEATHER, title=SECTION_TITLES[WEATHER], ok=True,
            items=(report.as_line(),),
        )

    return run


BRIEFING_NEWS_LIMIT = 3


def news_source(settings: "Settings", clock: Clock) -> SectionSource:
    """Headlines WITHOUT their URLs, and fewer of them than the `news` tool.

    Both differences are for the narration, measured rather than assumed. A
    briefing is read or spoken; a BBC article URL is ~80 characters of query
    string that nobody hears, and every one of them is input the model pays
    for and output it can garble. The standalone `news` tool keeps the URLs,
    because there the user can actually click them.
    """
    def run() -> BriefingSection:
        from jarvis.tools import news as news_tool

        items, failures = news_tool.fetch_for_settings(settings, limit=BRIEFING_NEWS_LIMIT)
        if not items and failures:
            # Every configured feed failed. That is a failed SECTION, not an
            # empty one — "no headlines today" would be false.
            return failed_section(
                NEWS, "; ".join(failure.as_line() for failure in failures)
            )
        note = ""
        if failures:
            note = "Ulaşılamayan kaynak: " + "; ".join(f.as_line() for f in failures)
        return BriefingSection(
            key=NEWS, title=SECTION_TITLES[NEWS], ok=True,
            items=tuple(f"{item.source}: {item.title}" for item in items), note=note,
        )

    return run


# ── Service ──────────────────────────────────────────────────────────────────


@dataclass
class DailyBriefingService:
    """Collects BriefingFacts. Deterministic given its sources."""

    settings: "Settings | None" = None
    clock: Clock | None = None
    sources: dict[str, SectionSource] = field(default_factory=dict)
    section_timeout_sec: float = 0.0

    def __post_init__(self) -> None:
        if self.clock is None:
            self.clock = get_clock()
        if not self.section_timeout_sec:
            self.section_timeout_sec = float(
                getattr(self.settings, "briefing_section_timeout_sec", None)
                or DEFAULT_SECTION_TIMEOUT_SEC
            )

    def _default_sources(self) -> dict[str, SectionSource]:
        return {
            CALENDAR: calendar_source(self.settings, self.clock),
            TODO: todo_source(self.settings, self.clock),
            WEATHER: weather_source(self.settings, self.clock),
            NEWS: news_source(self.settings, self.clock),
        }

    def collect(self, include: Sequence[str] | None = None) -> BriefingFacts:
        """Run every requested source and freeze the result.

        `include` narrows the briefing (the tool exposes it, so "sadece
        takvim" costs one API call instead of four). Unknown keys are ignored
        rather than raising: this argument reaches here from a language model,
        and a typo should cost that section, not the briefing.
        """
        started = time.monotonic()
        available = {**self._default_sources(), **self.sources}
        keys = [k for k in (include or SECTION_ORDER) if k in available]
        if not keys:
            keys = list(SECTION_ORDER)

        sections = self._run_sources(keys, available)
        now = self.clock.now()
        part, greeting = part_of_day(now)
        return BriefingFacts(
            generated_at=now,
            tz_name=self.clock.tz_name,
            part_of_day=part,
            greeting=greeting,
            user_name=str(getattr(self.settings, "user_name", None) or "efendim"),
            date_line=format_long_date(now),
            sections=tuple(sections),
            elapsed_sec=round(time.monotonic() - started, 3),
        )

    def _run_sources(
        self, keys: list[str], available: dict[str, SectionSource]
    ) -> list[BriefingSection]:
        """Concurrent, deadline-bounded, and returned in SECTION_ORDER.

        Order is restored deliberately: the briefing must read the same way
        every morning regardless of which server answered first, or no two runs
        are comparable and the gate measures noise.
        """
        results: dict[str, BriefingSection] = {}
        started: dict[str, float] = {}
        deadline = time.monotonic() + self.section_timeout_sec

        # NOT `with ThreadPoolExecutor(...)`. The context manager's __exit__
        # calls shutdown(wait=True), which blocks until every worker finishes
        # -- so a hanging source would be waited for at pool teardown and the
        # per-section deadline above would buy nothing at all. Shutting down
        # with wait=False is what makes the timeout real.
        #
        # A straggler thread still runs to completion in the background and is
        # joined by the interpreter's own atexit hook. Bounded in practice
        # because every network source here carries its own HTTP timeout
        # (weather/news default to 8 s, Google's client to its own), and
        # harmless because no briefing source writes anything.
        pool = ThreadPoolExecutor(max_workers=max(1, len(keys)))
        try:
            futures = {}
            for key in keys:
                started[key] = time.monotonic()
                futures[pool.submit(available[key])] = key

            for future, key in futures.items():
                remaining = max(0.0, deadline - time.monotonic())
                try:
                    section = future.result(timeout=remaining)
                    results[key] = BriefingSection(
                        key=section.key, title=section.title, ok=section.ok,
                        items=section.items, error=section.error, note=section.note,
                        elapsed_sec=round(time.monotonic() - started[key], 3),
                    )
                except TimeoutError:
                    # concurrent.futures raises the builtin TimeoutError on
                    # 3.11+.
                    results[key] = failed_section(
                        key,
                        f"zaman aşımı ({self.section_timeout_sec:g} sn)",
                        round(time.monotonic() - started[key], 3),
                    )
                    logger.info("briefing: section %s timed out", key)
                except Exception as exc:  # noqa: BLE001 -- one bad source, not a bad briefing
                    results[key] = failed_section(
                        key,
                        f"{type(exc).__name__}: {exc}",
                        round(time.monotonic() - started[key], 3),
                    )
                    logger.info("briefing: section %s failed -- %s", key, exc)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        ordered = [k for k in SECTION_ORDER if k in results]
        ordered += [k for k in results if k not in SECTION_ORDER]
        return [results[k] for k in ordered]


def build_briefing(
    settings: "Settings | None" = None,
    include: Sequence[str] | None = None,
    clock: Clock | None = None,
) -> str:
    """Model-facing string for the `daily_briefing` tool."""
    facts = DailyBriefingService(settings=settings, clock=clock).collect(include)
    return facts.render()


# ── The no-fabrication audit ─────────────────────────────────────────────────
#
# What makes this checkable at all is that the facts are a closed set of
# strings. The audit is deliberately NARROW: it checks the classes of
# fabrication that can be decided by comparison, and stays quiet about the
# ones that cannot. A checker that guessed at semantic invention would produce
# false positives, and a fabrication metric with false positives is worse than
# none — it would be the "gate that scored the wrong input" incident again.

_TIME = re.compile(r"\b(\d{1,2})[:.](\d{2})\b")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")

# Numerals a narrator legitimately produces that are not claims FROM the facts:
# list ordinals ("1.", "2."), and counts of items the reader can verify by
# counting the list itself. Bounded at the number of items in the briefing —
# beyond that a bare integer is a claim, not enumeration.
#
# This exemption is why clock times are extracted FIRST and checked separately.
# Left in the general number pool, "15:00" decomposes into 15 and 00, both
# under the ceiling — so the single most damaging fabrication a briefing can
# commit, moving a meeting by an hour, would have been the one thing the audit
# could not see.
_ENUMERATION_CEILING = 20

# What a narrator calls each section. Title words alone are not enough: nobody
# says "Öne çıkan haberler", they say "haberler" or "gündem". Stems, because
# Turkish suffixes attach at the end -- the same rule the tool router's pattern
# table is built on -- and matched against fold()ed text so a transcript that
# dropped its diacritics still matches.
_SECTION_MENTIONS: dict[str, tuple[str, ...]] = {
    CALENDAR: ("takvim", "toplanti", "etkinlik", "randevu", "ajanda", "program"),
    TODO: ("yapilacak", "gorev", "todo", "is listesi"),
    WEATHER: ("hava",),
    NEWS: ("haber", "gundem", "manset"),
}

# Turkish stems for "could not be reached / unavailable / error". Stems for the
# same reason as above; written against fold()ed text, so no diacritics appear
# in the pattern itself.
#
# `yapilamad` ("giriş yapılamadı") was added after a live run: the model
# admitted the failure in wording none of the other stems covered, and the
# audit would have scored an honest sentence as a silent omission. A checker
# that flags honest output is worse than one that misses -- it puts false
# positives into the fabrication metric.
#
# `bilgi yok` is deliberately ABSENT, and it is the interesting omission. It
# is genuinely ambiguous: "takvim bilgisi yok" can mean "the calendar could
# not be read" or "the calendar is empty", and those are opposite claims about
# a failed section. Left out, an honest use of it costs one false flag; left
# in, the dishonest use passes silently. The first error is the recoverable one.
_ADMITS_FAILURE = re.compile(
    r"ulasilam|ulasamad|alinam|alamad|erisilem|erisemed|baglanam|yapilamad"
    r"|hata|basarisiz|veri gelmed|cekilem|cekemed|goruntuleyemed|okuyamad"
    r"|unavailable|could not|couldn'?t|unable|failed|error"
)

# Asserting that a section is EMPTY. Checked only against sections that FAILED,
# where the assertion is false by construction: a source that did not answer
# cannot be known to hold nothing.
#
# This check exists because a live run produced exactly it -- "Bugün takvimde
# bir etkinlik bulunmuyor, bu nedenle takvim bölümüne giriş yapılamadı" -- and
# it is the most damaging shape a briefing failure can take. "I could not read
# your calendar" makes the user check it; "you have nothing today" makes them
# stop thinking about it. The narration even admitted the failure in the same
# breath, so a stems-only honesty check passes it.
#
# Tightly anchored to an ABSENCE-OF-ITEMS claim (a thing, then a negation), not
# to bare "yok" -- "haber yok" and "bilgi yok" are different sentences and only
# one of them is a lie.
#
# The gap between the noun and the negation is TEMPERED against "bilgi"/"veri"
# after this check produced its own false positive on a live run: *"Takvim
# verisi alınamadı, bu nedenle o günün etkinlikleri hakkında bilgi bulunmuyor"*
# says there is no INFORMATION about the events, which is exactly true and the
# honest thing to say. "etkinlik yok" claims the events do not exist;
# "etkinlik bilgisi yok" claims nobody could look. Flagging the second would
# have put a false positive into the fabrication metric -- and a fabrication
# metric with false positives is worse than none.
# "bulunamadı" is here rather than in _ADMITS_FAILURE, and the distinction is
# the whole point: applied to a SOURCE it would be an admission, but applied to
# an ITEM -- "takvimde kayıtlı bir etkinlik bulunamadı" -- it asserts the
# calendar was read and found empty. Three live runs produced exactly that
# sentence about a calendar that never answered.
_CLAIMS_EMPTY = re.compile(
    r"(etkinlik|toplanti|randevu|gorev|kayit|madde|baslik|haber|bir sey|hicbir sey)"
    r"(?:(?!bilgi|veri)[^.!?]){0,24}?(yok|bulunmuyor|bulunmamakta|bulunamad|mevcut degil)"
    r"|(bos|bombos)\b|\bnothing (scheduled|planned)\b|\bno (events?|tasks?|items?)\b"
)

_SENTENCE_SPLIT = re.compile(r"[.!?\n]+")
_HEADING = re.compile(r"^\s*(?:#{1,6}\s*|\*\*)\s*(.+?)\s*(?:\*\*)?\s*:?\s*$")


@dataclass(frozen=True)
class BriefingAudit:
    """What the narration said that the facts do not support."""

    invented_times: tuple[str, ...] = ()
    invented_numbers: tuple[str, ...] = ()
    unstated_failures: tuple[str, ...] = ()
    false_empty: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return not (
            self.invented_times or self.invented_numbers
            or self.unstated_failures or self.false_empty
        )

    @property
    def fabricated_count(self) -> int:
        return (
            len(self.invented_times) + len(self.invented_numbers)
            + len(self.unstated_failures) + len(self.false_empty)
        )

    def describe(self) -> str:
        if self.clean:
            return "temiz"
        parts = []
        if self.invented_times:
            parts.append("kayıtta olmayan saat: " + ", ".join(self.invented_times))
        if self.invented_numbers:
            parts.append("kayıtta olmayan sayı: " + ", ".join(self.invented_numbers))
        if self.false_empty:
            parts.append(
                "alınamayan bölüm 'boş' diye sunuldu: " + ", ".join(self.false_empty)
            )
        if self.unstated_failures:
            parts.append(
                "başarısız bölüm sessizce atlandı: " + ", ".join(self.unstated_failures)
            )
        return "; ".join(parts)


def _times_in(text: str) -> set[str]:
    """Clock times, zero-padded to HH:MM.

    `9:30` and `09:30` are the same time; `14.00` is the same time written
    with the separator Turkish also uses. A narrator that renormalizes has not
    invented anything, so all three collapse to one key.
    """
    found = set()
    for match in _TIME.finditer(text):
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour <= 23 and minute <= 59:
            found.add(f"{hour:02d}:{minute:02d}")
    return found


def _numbers_in(text: str) -> set[str]:
    """Numerals, with `,`/`.` normalized to one form, EXCLUDING clock times.

    "21,8" and "21.8" are the same temperature written by two locales, and a
    narrator that switches decimal separators has not invented anything.
    """
    without_times = _TIME.sub(" ", text)
    return {match.group(0).replace(",", ".") for match in _NUMBER.finditer(without_times)}


def _fold(text: str) -> str:
    """Turkish-safe casefold. Imported lazily and wrapped here so this module
    keeps its single dependency on jarvis.nlu in one place."""
    from jarvis.nlu.temporal import fold

    return fold(text)


def _scoped_sentences(narration: str) -> list[str]:
    """Fold the narration into the units the section checks are scoped to.

    Sentences, except that a **markdown heading carries down onto the lines
    below it**. That is what a heading means, and ignoring it cost this audit
    three false negatives in one live round. The model answered:

        ### Takvim (bugün)
        DURUM: ALINAMADI — Google Calendar yetkilendirme geçersiz.

    which is the most honest output it could possibly produce -- it echoed the
    status verbatim. Split on newlines, the first segment names the section
    with no admission and the second admits with no section named, so NEITHER
    segment satisfied "names the section AND admits the failure" and the audit
    reported a silent omission about a narration that hid nothing.

    Prose is untouched (no headings ⇒ no merging), so the per-sentence scoping
    that the false-positive case needs is fully intact.
    """
    segments: list[str] = []
    heading = ""
    for line in (narration or "").splitlines():
        if not line.strip():
            heading = ""
            continue
        match = _HEADING.match(line)
        if match and len(line.strip()) < 60:
            heading = match.group(1)
            segments.append(_fold(heading))
            continue
        for sentence in _SENTENCE_SPLIT.split(line):
            if sentence.strip():
                segments.append(_fold(f"{heading} {sentence}" if heading else sentence))
    return segments


def audit_narration(narration: str, facts: BriefingFacts) -> BriefingAudit:
    """Compare what the model SAID against what the facts SUPPORT.

    Four checks, all decidable by comparison:

    **Invented times.** Every HH:MM in the narration must appear in the facts.
    This is the check that catches the failure that actually hurts: a briefing
    that moves a 14:00 meeting to 15:00 is worse than one that omits it.

    **Invented numbers.** Every remaining numeral — temperature, percentage,
    year, day-of-month — must appear too. Small bare integers are exempt as
    enumeration: "3 toplantınız var" is arithmetic over a list the reader can
    count, not a claim quoted from a source.

    **Unstated failures.** If a section failed, some sentence must both name
    that section and admit it could not be read. Silence about a source that
    did not answer is the omission failure mode, and the one a fluent model
    produces most naturally — nothing in a smooth paragraph forces it to
    mention what it could not see.

    **False-empty claims.** A failed section must not be described as empty.
    This is the worst shape a briefing failure takes and it is not the same as
    silence: "I could not read your calendar" makes the user go and look,
    "you have nothing today" makes them stop thinking about it. Reported
    separately from the check above because a live run produced a sentence
    that failed this one while PASSING that one — it admitted the failure and
    asserted emptiness in the same breath.

    The last two are scoped per sentence rather than per narration on purpose:
    a paragraph that reports invented weather and, separately, a genuinely
    failed news feed contains the words "hava" and "ulaşılamadı", and a
    whole-text check would read that as weather having been reported honestly.
    A markdown heading carries down onto the lines under it — see
    `_scoped_sentences` for the live round that cost.

    Every check is conservative in the same direction — anything ambiguous is
    NOT reported. A non-zero count is evidence; a zero is the absence of proof,
    not proof of absence.
    """
    # The FACTS, not the fact block: CONTRACT is a numbered instruction list,
    # and letting its "1." through "5." count as support would license a
    # narrator to state any of those digits as data.
    reference = facts.fact_text()
    supported_times = _times_in(reference)
    supported_numbers = _numbers_in(reference)
    enumeration_ceiling = max(_ENUMERATION_CEILING, len(facts.all_items()))

    text = narration or ""
    invented_times = tuple(sorted(_times_in(text) - supported_times))

    invented_numbers: list[str] = []
    for number in sorted(_numbers_in(text)):
        if number in supported_numbers:
            continue
        if "." not in number and number.isdigit() and int(number) <= enumeration_ceiling:
            continue
        invented_numbers.append(number)

    sentences = _scoped_sentences(text)
    unstated: list[str] = []
    false_empty: list[str] = []
    for section in facts.sections:
        if section.ok:
            continue
        stems = _SECTION_MENTIONS.get(section.key) or (_fold(section.title),)
        about = [s for s in sentences if any(stem in s for stem in stems)]
        if not any(_ADMITS_FAILURE.search(s) for s in about):
            unstated.append(section.key)
        # Independent of the above, and reported separately: a narration can
        # admit the failure AND assert emptiness in the same sentence. A live
        # run did exactly that, and the second half is the part the user acts on.
        if any(_CLAIMS_EMPTY.search(s) for s in about):
            false_empty.append(section.key)

    return BriefingAudit(
        invented_times, tuple(invented_numbers), tuple(unstated), tuple(false_empty)
    )


def summarize_latency(samples: Iterable[float]) -> dict[str, float]:
    """p50/p95 over a sample list — the two axes Faz 3's gate is stated in.

    Nearest-rank percentile, no interpolation: with n=10 runs an interpolated
    p95 invents a value between two real measurements, and this project's own
    rule is that a measurement is a thing that happened.
    """
    values = sorted(float(v) for v in samples)
    if not values:
        return {"n": 0, "p50": 0.0, "p95": 0.0, "max": 0.0}

    def rank(pct: float) -> float:
        index = max(0, min(len(values) - 1, int(-(-len(values) * pct // 100)) - 1))
        return values[index]

    return {
        "n": len(values),
        "p50": round(rank(50), 3),
        "p95": round(rank(95), 3),
        "max": round(values[-1], 3),
    }
