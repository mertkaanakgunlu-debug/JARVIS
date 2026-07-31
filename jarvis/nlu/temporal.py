"""Turkish/English date + time expression resolver — code computes the timestamp.

Post-MVP Faz 2, plan item 2: *"model yalnız date_expression üretir, final
timestamp'i kod hesaplar, Clock'un timezone'unda."*

Two properties this module is built around, both of them load-bearing:

**1. The clock is injected, never read ambiently.** The bug this phase exists
to close was one call site resolving "yarın" against UTC while the rest of the
system spoke Europe/Istanbul (see jarvis/clock.py's docstring for the measured
3-of-24-hours failure). Everything here takes a Clock.

**2. Confidence is independent of the clock, by construction.** Confidence is
a property of the EXPRESSION ("is 'pazartesi' this Monday or next?"), not of
what day it happens to be. It is computed from the matched pattern alone, and
the clock is applied afterwards to turn that pattern into a value. This is not
a stylistic preference: jarvis/policy_guard.py must stay a pure function of
(tool, args, settings) — two independent evaluations of the same call, in two
different graph nodes, must never disagree about whether something needs the
user's OK. If confidence moved with the clock, a batch evaluated either side
of midnight could be gated one way and executed the other.
tests/test_temporal_resolver.py asserts the independence directly rather than
leaving it as an intention.

Scope note: this resolves the expressions a user actually says to a personal
assistant. It is deliberately NOT a general natural-language date library —
an unrecognized expression returns ok=False with an honest reason, which the
caller surfaces ("Efendim, 'gelecek ay ortası' tarihini çözemedim"), rather
than a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

from jarvis.clock import Clock, get_clock

# ── Confidence bands (shared vocabulary with jarvis/nlu/entities.py) ─────────
#
# The plan's bands, applied to time as well as to people:
#   >= 0.95  act on it
#   0.75..0.95  ask the user
#   < 0.75   do not act (and do not silently substitute a guess)
AUTO_THRESHOLD = 0.95
ASK_THRESHOLD = 0.75


def band(confidence: float) -> str:
    """"auto" | "ask" | "leave" — one place, so policy and NLU can never drift."""
    if confidence >= AUTO_THRESHOLD:
        return "auto"
    if confidence >= ASK_THRESHOLD:
        return "ask"
    return "leave"


# ── Turkish-safe folding ─────────────────────────────────────────────────────
#
# str.lower() is wrong for Turkish in exactly the place it matters here:
# "YARIN".lower() is "yarin" (dotted i) while the correctly-spelled word is
# "yarın" (dotless ı), so a naive lowercase makes the two spellings of the
# single most common expression in this user's requests compare unequal.
# Folding diacritics to ASCII sidesteps the İ/I/ı/i problem entirely and
# makes "Ağustos"/"agustos"/"AGUSTOS" one key.
_FOLD_MAP = str.maketrans({
    "ı": "i", "İ": "i", "I": "i", "i": "i",
    "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o",
    "ç": "c", "Ç": "c",
    "â": "a", "Â": "a", "î": "i", "Î": "i", "û": "u", "Û": "u",
})


def fold(text: str) -> str:
    """Turkish-safe casefold: diacritics → ASCII, then lowercase."""
    return text.translate(_FOLD_MAP).lower().strip()


# Turkish attaches case endings to the thing being named, and a model relays
# them verbatim: date="yarına", time="3'te", "saat 4'e". Stripping them is not
# optional — "3'te" parses as nothing at all, and "yarına" is not "yarın" to
# any exact match. Two different mechanisms, because one rule cannot serve
# both: numeric tokens get the ending removed (below), while word rules carry
# an OPTIONAL ending in their own pattern (_END), which keeps the stripping
# anchored to a word the resolver already knows instead of chopping letters
# off arbitrary text.
_END = r"(?:'|’)?(?:ndan|nden|dan|den|tan|ten|nda|nde|na|ne|da|de|ta|te|ya|ye|a|e|i|si|u)?"


def _strip_suffix(token: str) -> str:
    """Drop one Turkish case ending from a NUMERIC-tailed token ("3'te" → "3").

    Restricted to tokens that end in digits + ending on purpose: applied to
    words it would eat real letters ("cuma" → "cum"). Names get their own,
    much more careful treatment in jarvis/nlu/entities.py.
    """
    m = re.match(r"^(\d{1,2}(?:[:.]\d{2})?)(?:'|’)?(?:nde|nda|ne|na|de|da|te|ta|ye|ya|e|a)$", token)
    return m.group(1) if m else token


_WEEKDAYS: dict[str, int] = {  # folded name → Monday=0
    "pazartesi": 0, "sali": 1, "carsamba": 2, "persembe": 3,
    "cuma": 4, "cumartesi": 5, "pazar": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "pzt": 0, "sal": 1, "car": 2, "per": 3, "cum": 4, "cmt": 5, "paz": 6,
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}

_MONTHS: dict[str, int] = {
    "ocak": 1, "subat": 2, "mart": 3, "nisan": 4, "mayis": 5, "haziran": 6,
    "temmuz": 7, "agustos": 8, "eylul": 9, "ekim": 10, "kasim": 11, "aralik": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# "next"-style qualifiers that push a weekday into the FOLLOWING week rather
# than resolving to the nearest upcoming one.
_NEXT_WEEK_WORDS = ("gelecek", "onumuzdeki", "haftaya", "next")
_THIS_WEEK_WORDS = ("bu", "this")


@dataclass(frozen=True)
class DateResolution:
    ok: bool
    value: date | None
    confidence: float
    kind: str          # which rule matched — shows up in audit/debug output
    normalized: str    # "2026-08-01", or "" when unresolved
    reason: str        # plain language, safe to show the user

    @property
    def band(self) -> str:
        return band(self.confidence)


@dataclass(frozen=True)
class TimeResolution:
    ok: bool
    hour: int | None
    minute: int | None
    confidence: float
    kind: str
    normalized: str    # "15:00", or "" when unresolved
    reason: str

    @property
    def band(self) -> str:
        return band(self.confidence)


@dataclass(frozen=True)
class TemporalResolution:
    """A date expression and a time expression, resolved together."""

    ok: bool
    start: datetime | None   # timezone-aware, in the clock's zone
    all_day: bool
    confidence: float        # the WEAKEST link — see resolve()
    date_res: DateResolution
    time_res: TimeResolution
    reason: str

    @property
    def band(self) -> str:
        return band(self.confidence)


_EMPTY_TIME = TimeResolution(True, None, None, 1.0, "all_day", "", "no time given — all-day event")


# ── Date ─────────────────────────────────────────────────────────────────────
#
# Each rule is (regex, kind, confidence, resolver). Confidence lives in the
# TABLE, not in the resolver, which is what makes it structurally impossible
# for it to depend on the clock (see the module docstring).

def _offset(days: int) -> Callable[[re.Match, date], date]:
    return lambda _m, today: today + timedelta(days=days)


def _weekday_upcoming(target: int, today: date) -> date:
    """The next occurrence of `target`, today included."""
    return today + timedelta(days=(target - today.weekday()) % 7)


def _weekday_next_week(target: int, today: date) -> date:
    """The occurrence in the FOLLOWING calendar week (Monday-based)."""
    start_of_next_week = today + timedelta(days=7 - today.weekday())
    return start_of_next_week + timedelta(days=target)


def _resolve_bare_weekday(m: re.Match, today: date) -> date:
    return _weekday_upcoming(_WEEKDAYS[m.group("wd")], today)


def _resolve_qualified_weekday(m: re.Match, today: date) -> date:
    target = _WEEKDAYS[m.group("wd")]
    qualifier = m.group("q")
    if qualifier and any(qualifier.startswith(w) for w in _NEXT_WEEK_WORDS):
        return _weekday_next_week(target, today)
    return _weekday_upcoming(target, today)


def _resolve_iso(m: re.Match, _today: date) -> date:
    return date(int(m.group("y")), int(m.group("m")), int(m.group("d")))


def _resolve_dmy(m: re.Match, _today: date) -> date:
    year = int(m.group("y"))
    if year < 100:
        year += 2000
    return date(year, int(m.group("m")), int(m.group("d")))


def _next_occurrence_of(day: int, month: int, today: date) -> date:
    """A day+month with no year means the next time it comes round."""
    candidate = date(today.year, month, day)
    return candidate if candidate >= today else date(today.year + 1, month, day)


def _resolve_dm(m: re.Match, today: date) -> date:
    return _next_occurrence_of(int(m.group("d")), int(m.group("m")), today)


def _resolve_day_month_name(m: re.Match, today: date) -> date:
    day, month = int(m.group("d")), _MONTHS[m.group("mon")]
    year = m.group("y")
    return date(int(year), month, day) if year else _next_occurrence_of(day, month, today)


def _resolve_month_name_day(m: re.Match, today: date) -> date:
    day, month = int(m.group("d")), _MONTHS[m.group("mon")]
    year = m.group("y")
    return date(int(year), month, day) if year else _next_occurrence_of(day, month, today)


def _resolve_day_of_month(m: re.Match, today: date) -> date:
    """"ayın 15'i" — this month's 15th, or next month's if it has passed."""
    day = int(m.group("d"))
    try:
        candidate = date(today.year, today.month, day)
    except ValueError:  # e.g. "ayın 31'i" in a 30-day month
        candidate = today - timedelta(days=1)  # force the roll-forward below
    if candidate >= today:
        return candidate
    year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return date(year, month, day)


def _resolve_in_n_days(m: re.Match, today: date) -> date:
    return today + timedelta(days=int(m.group("n")))


def _resolve_in_n_weeks(m: re.Match, today: date) -> date:
    return today + timedelta(weeks=int(m.group("n")))


def _resolve_n_days_ago(m: re.Match, today: date) -> date:
    return today - timedelta(days=int(m.group("n")))


def _resolve_weekend(_m: re.Match, today: date) -> date:
    return _weekday_upcoming(5, today)  # Saturday


_WD = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))
_MON = "|".join(sorted(_MONTHS, key=len, reverse=True))

_DATE_RULES: list[tuple[re.Pattern, str, float, Callable[[re.Match, date], date], str]] = [
    # (pattern, kind, confidence, resolver, human reason)
    (re.compile(r"^(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})$"),
     "iso", 1.0, _resolve_iso, "explicit ISO date"),
    (re.compile(r"^(?P<d>\d{1,2})[./-](?P<m>\d{1,2})[./-](?P<y>\d{4})$"),
     "dmy", 1.0, _resolve_dmy, "explicit day/month/year"),
    (re.compile(r"^(?P<d>\d{1,2})[./-](?P<m>\d{1,2})[./-](?P<y>\d{2})$"),
     "dmy_short", 0.9, _resolve_dmy, "two-digit year assumed to be 20xx"),
    (re.compile(r"^(?P<d>\d{1,2})[./](?P<m>\d{1,2})$"),
     "dm", 0.9, _resolve_dm, "day/month with no year — took the next occurrence"),

    (re.compile(rf"^(bugun|today|simdi|now|su an){_END}$"),
     "today", 1.0, _offset(0), "today"),
    (re.compile(rf"^(yarin|tomorrow){_END}$"),
     "tomorrow", 1.0, _offset(1), "tomorrow"),
    (re.compile(rf"^(dun|yesterday){_END}$"),
     "yesterday", 1.0, _offset(-1), "yesterday"),
    (re.compile(rf"^(obur gun|oburgun|ertesi gun|day after tomorrow){_END}$"),
     "day_after_tomorrow", 0.97, _offset(2), "the day after tomorrow"),

    (re.compile(r"^(?P<n>\d{1,3})\s*(gun|day|days)\s*(sonra|icinde|later|from now)$"),
     "in_n_days", 1.0, _resolve_in_n_days, "counted forward from today"),
    (re.compile(r"^(?:in\s*)?(?P<n>\d{1,3})\s*(gun|day|days)$"),
     "in_n_days_bare", 0.9, _resolve_in_n_days, "counted forward from today"),
    (re.compile(r"^(?P<n>\d{1,3})\s*(hafta|week|weeks)\s*(sonra|icinde|later|from now)$"),
     "in_n_weeks", 1.0, _resolve_in_n_weeks, "counted forward from today"),
    (re.compile(r"^(?P<n>\d{1,3})\s*(gun|day|days)\s*(once|onceki|ago|earlier)$"),
     "n_days_ago", 1.0, _resolve_n_days_ago, "counted back from today"),

    (re.compile(rf"^(?P<q>{'|'.join(_NEXT_WEEK_WORDS + _THIS_WEEK_WORDS)})\s+(?P<wd>{_WD}){_END}$"),
     "qualified_weekday", 0.95, _resolve_qualified_weekday, "weekday with an explicit week"),
    # A bare weekday is genuinely ambiguous between this week and next, so it
    # lands in the ASK band rather than being resolved silently. This is the
    # single most common way a calendar entry goes to the wrong day.
    (re.compile(rf"^(?P<wd>{_WD}){_END}$"),
     "bare_weekday", 0.80, _resolve_bare_weekday,
     "a bare weekday could be this week or next — took the next one"),

    (re.compile(rf"^(?P<d>\d{{1,2}})\s+(?P<mon>{_MON})(?:\s+(?P<y>\d{{4}}))?{_END}$"),
     "day_month_name", 0.97, _resolve_day_month_name, "day and named month"),
    (re.compile(rf"^(?P<mon>{_MON})\s+(?P<d>\d{{1,2}})(?:,?\s+(?P<y>\d{{4}}))?$"),
     "month_name_day", 0.97, _resolve_month_name_day, "named month and day"),
    (re.compile(r"^ayin\s+(?P<d>\d{1,2})$"),
     "day_of_month", 0.85, _resolve_day_of_month,
     "'ayın N' with no month named — took this month, or next if it has passed"),

    (re.compile(rf"^(hafta sonu|weekend|this weekend){_END}$"),
     "weekend", 0.70, _resolve_weekend, "'hafta sonu' — assumed Saturday"),
]

# Recognized but deliberately NOT resolved: expressions that name a period
# rather than a day. Guessing a day out of these is exactly the silent-wrong
# behaviour this module exists to stop, so they return an honest failure with
# a reason the caller can read back to the user.
_UNDERSPECIFIED: dict[str, str] = {
    "haftaya": "'haftaya' names a week, not a day — which day?",
    "gelecek hafta": "'gelecek hafta' names a week, not a day — which day?",
    "onumuzdeki hafta": "'önümüzdeki hafta' names a week, not a day — which day?",
    "next week": "'next week' names a week, not a day — which day?",
    "gelecek ay": "'gelecek ay' names a month, not a day — which day?",
    "onumuzdeki ay": "'önümüzdeki ay' names a month, not a day — which day?",
    "next month": "'next month' names a month, not a day — which day?",
    "bu ay": "'bu ay' names a month, not a day — which day?",
    "bu hafta": "'bu hafta' names a week, not a day — which day?",
    "this week": "'this week' names a week, not a day — which day?",
    "yakinda": "'yakında' does not name a date",
    "soon": "'soon' does not name a date",
    "bir ara": "'bir ara' does not name a date",
}


def resolve_date(expression: str, *, clock: Clock | None = None) -> DateResolution:
    """Resolve a date expression in the clock's timezone.

    The timezone matters even though the result is a bare date: "today" is a
    different day in Istanbul than in UTC for three hours out of every
    twenty-four, which is precisely the bug this phase closes.
    """
    raw = (expression or "").strip()
    if not raw:
        return DateResolution(False, None, 0.0, "empty", "", "no date given")

    text = re.sub(r"\s+", " ", fold(raw))
    text = re.sub(r"(?:'|’)(?:nde|nda|ne|na|de|da|te|ta|ye|ya|i|si|u)$", "", text)

    if text in _UNDERSPECIFIED:
        return DateResolution(False, None, 0.40, "underspecified", "", _UNDERSPECIFIED[text])

    today = (clock or get_clock()).today()

    for pattern, kind, confidence, resolver, reason in _DATE_RULES:
        m = pattern.match(text)
        if not m:
            continue
        try:
            value = resolver(m, today)
        except ValueError as exc:  # e.g. "2026-02-31"
            return DateResolution(False, None, 0.0, kind, "", f"'{raw}' is not a real date ({exc})")
        return DateResolution(True, value, confidence, kind, value.isoformat(), reason)

    return DateResolution(
        False, None, 0.0, "unparsed", "",
        f"could not resolve the date '{raw}'",
    )


# ── Time ─────────────────────────────────────────────────────────────────────
#
# Turkish dayparts carry real information a bare number does not, and getting
# them wrong is a 12-hour error. "öğlen 3" is 15:00 — this is the exact
# expression in the owner's live calendar failure ("Yarın öğlen saat 3'e ...
# ekle"), and no amount of prompt engineering makes a model reliably know it.

_DAYPARTS = {
    "sabah": "morning", "sabahleyin": "morning", "morning": "morning",
    "oglen": "midday", "ogle": "midday", "ogleyin": "midday", "noon": "midday",
    "ogleden sonra": "afternoon", "afternoon": "afternoon",
    "aksam": "evening", "aksamleyin": "evening", "evening": "evening",
    "gece": "night", "geceleyin": "night", "night": "night",
}
_DAYPART_ALT = "|".join(sorted(_DAYPARTS, key=len, reverse=True))


def _apply_daypart(part: str, hour: int) -> tuple[int, float, str]:
    """(hour_24, confidence, note). A daypart plus an hour is usually exact;
    the low-confidence cases are the genuinely ambiguous ones (12 with an
    AM/PM-style qualifier), which the confidence band then routes to a
    question rather than a silent choice."""
    if hour > 12:
        # "akşam 20" — already 24-hour, the daypart is just emphasis.
        return hour, 0.97, "already a 24-hour value"
    if part == "morning":
        if hour == 12:
            return 0, 0.60, "'sabah 12' is ambiguous between 00:00 and 12:00"
        return hour, 0.97, "morning hour"
    if part in ("midday", "afternoon"):
        # "öğlen 12" is noon; "öğlen 1".."öğlen 11" are 13:00..23:00.
        return (12 if hour == 12 else hour + 12), 0.96, "afternoon hour"
    if part == "evening":
        if hour == 12:
            return 0, 0.60, "'akşam 12' is ambiguous between midnight and noon"
        return hour + 12, 0.96, "evening hour"
    if part == "night":
        if hour == 12:
            return 0, 0.70, "'gece 12' read as midnight"
        # "gece 3" is 03:00; "gece 11" is 23:00.
        return (hour if hour <= 5 else hour + 12), 0.92, "night hour"
    return hour, 0.60, "unrecognized daypart"


_DAYPART_ONLY = {
    "morning": (9, 0, 0.55, "'sabah' with no hour — 09:00 is a guess"),
    "midday": (12, 0, 0.90, "'öğlen' with no hour — noon"),
    "afternoon": (15, 0, 0.55, "'öğleden sonra' with no hour — 15:00 is a guess"),
    "evening": (19, 0, 0.55, "'akşam' with no hour — 19:00 is a guess"),
    "night": (21, 0, 0.50, "'gece' with no hour — 21:00 is a guess"),
}

_HHMM_RE = re.compile(r"^(?P<h>\d{1,2})[:.](?P<m>\d{2})$")
_BARE_HOUR_RE = re.compile(r"^(?P<h>\d{1,2})$")
_AMPM_RE = re.compile(r"^(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?\s*(?P<ap>am|pm|a\.m\.|p\.m\.)$")
_DAYPART_HOUR_RE = re.compile(
    rf"^(?P<part>{_DAYPART_ALT})\s*(?:saat\s*)?(?P<h>\d{{1,2}})(?:[:.](?P<m>\d{{2}}))?$"
)
_HOUR_DAYPART_RE = re.compile(
    rf"^(?:saat\s*)?(?P<h>\d{{1,2}})(?:[:.](?P<m>\d{{2}}))?\s*(?P<part>{_DAYPART_ALT})$"
)


def _valid(hour: int, minute: int) -> bool:
    return 0 <= hour <= 23 and 0 <= minute <= 59


def resolve_time(expression: str, *, clock: Clock | None = None) -> TimeResolution:
    """Resolve a time-of-day expression. `clock` is accepted for symmetry and
    for the relative forms; the absolute ones do not consult it."""
    raw = (expression or "").strip()
    if not raw:
        return _EMPTY_TIME

    text = re.sub(r"\s+", " ", fold(raw))
    text = re.sub(r"^saat\s+", "", text)
    text = " ".join(_strip_suffix(tok) for tok in text.split(" "))
    text = text.replace("gece yarisi", "midnight").replace("oglen vakti", "oglen")

    if text in ("midnight", "gece yarisi"):
        return TimeResolution(True, 0, 0, 0.95, "midnight", "00:00", "midnight")
    if text in ("noon", "oglen vakti"):
        return TimeResolution(True, 12, 0, 0.95, "noon", "12:00", "noon")

    m = _AMPM_RE.match(text)
    if m:
        hour, minute = int(m.group("h")), int(m.group("m") or 0)
        if 1 <= hour <= 12 and 0 <= minute <= 59:
            pm = m.group("ap").startswith("p")
            hour = (hour % 12) + (12 if pm else 0)
            return TimeResolution(True, hour, minute, 0.99, "am_pm", f"{hour:02d}:{minute:02d}",
                                  "explicit am/pm")

    m = _HHMM_RE.match(text)
    if m:
        hour, minute = int(m.group("h")), int(m.group("m"))
        if _valid(hour, minute):
            return TimeResolution(True, hour, minute, 1.0, "hhmm", f"{hour:02d}:{minute:02d}",
                                  "explicit 24-hour time")
        return TimeResolution(False, None, None, 0.0, "hhmm", "", f"'{raw}' is not a valid time")

    m = _DAYPART_HOUR_RE.match(text) or _HOUR_DAYPART_RE.match(text)
    if m:
        part = _DAYPARTS[m.group("part")]
        hour, minute = int(m.group("h")), int(m.group("m") or 0)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            hour24, confidence, note = _apply_daypart(part, hour)
            return TimeResolution(True, hour24, minute, confidence, f"daypart_{part}",
                                  f"{hour24:02d}:{minute:02d}", note)

    if text in _DAYPARTS:
        hour, minute, confidence, note = _DAYPART_ONLY[_DAYPARTS[text]]
        return TimeResolution(True, hour, minute, confidence, "daypart_only",
                              f"{hour:02d}:{minute:02d}", note)

    m = _BARE_HOUR_RE.match(text)
    if m:
        hour = int(m.group("h"))
        if not 0 <= hour <= 23:
            return TimeResolution(False, None, None, 0.0, "bare_hour", "",
                                  f"'{raw}' is not a valid hour")
        if hour >= 13:
            # Unambiguous: nobody means 1am by "17".
            return TimeResolution(True, hour, 0, 1.0, "bare_hour", f"{hour:02d}:00",
                                  "24-hour value, unambiguous")
        # 1..12 with no daypart really could be either half of the day. The
        # ASK band exists for exactly this.
        return TimeResolution(True, hour, 0, 0.78, "bare_hour_ambiguous", f"{hour:02d}:00",
                              f"'{raw}' could be {hour:02d}:00 or {hour + 12:02d}:00 — took the morning")

    return TimeResolution(False, None, None, 0.0, "unparsed", "",
                          f"could not resolve the time '{raw}'")


# ── Combined ─────────────────────────────────────────────────────────────────

# When a model puts the whole phrase in one field (date="yarın öğlen 3"),
# splitting it is better than failing — but only when the split is
# unambiguous, i.e. exactly one side parses as a date and the other as a time.
_SPLIT_HINTS = re.compile(rf"\b({_DAYPART_ALT}|saat|\d{{1,2}}[:.]\d{{2}})\b")


def _split_combined(expression: str, clock: Clock) -> tuple[str, str] | None:
    """Try to split "yarın öğlen 3" into ("yarın", "öğlen 3"). None if unsure."""
    text = re.sub(r"\s+", " ", fold(expression)).strip()
    words = text.split(" ")
    if len(words) < 2:
        return None
    for cut in range(1, len(words)):
        head, tail = " ".join(words[:cut]), " ".join(words[cut:])
        if not _SPLIT_HINTS.search(tail):
            continue
        d, t = resolve_date(head, clock=clock), resolve_time(tail, clock=clock)
        if d.ok and t.ok and t.hour is not None:
            return head, tail
    return None


def resolve(
    date_expression: str,
    time_expression: str = "",
    *,
    clock: Clock | None = None,
) -> TemporalResolution:
    """Resolve a (date, time) pair into one timezone-aware instant.

    Confidence is the MINIMUM of the two parts, not the average: a perfectly
    understood date paired with an ambiguous time still produces an event at
    the wrong hour, and averaging would hide that behind the confident half.
    """
    clk = clock or get_clock()
    date_expr, time_expr = (date_expression or "").strip(), (time_expression or "").strip()

    d = resolve_date(date_expr, clock=clk)
    if not d.ok and not time_expr:
        split = _split_combined(date_expr, clk)
        if split:
            date_expr, time_expr = split
            d = resolve_date(date_expr, clock=clk)

    t = resolve_time(time_expr, clock=clk)

    if not d.ok:
        return TemporalResolution(False, None, False, d.confidence, d, t, d.reason)
    if not t.ok:
        return TemporalResolution(False, None, False, min(d.confidence, t.confidence), d, t, t.reason)

    all_day = t.hour is None
    hour, minute = (0, 0) if all_day else (t.hour, t.minute or 0)
    start = datetime(d.value.year, d.value.month, d.value.day, hour, minute, tzinfo=clk.tz)

    confidence = min(d.confidence, t.confidence)
    reasons = [r for r in (d.reason, t.reason) if r]
    return TemporalResolution(True, start, all_day, confidence, d, t, "; ".join(reasons))


# ── Confidence without resolution ────────────────────────────────────────────
#
# jarvis/policy_guard.py needs to know HOW SURE an expression is without
# needing to know what it resolves to. These two functions give it that by
# matching the rule table and stopping — they never call a resolver, never
# touch a clock, and so cannot depend on one. That is a structural guarantee
# rather than a convention: policy_guard must stay a pure function of
# (tool, args, settings) so its two independent evaluations, in two different
# graph nodes, can never disagree about whether a call needs the user's OK.
#
# (resolve_date's confidence is ALSO clock-independent in all but a handful of
# cases, enumerated exhaustively rather than assumed: sweeping every day of a
# year against "ayın 28".."ayın 31" produces exactly three divergences —
# 2026-01-30/"ayın 29", 2026-01-31/"ayın 29", 2026-01-31/"ayın 30" — where the
# roll-forward lands in February and no valid date exists in either month, so
# resolution fails and reports 0.0 instead of 0.85. That moves toward "ask",
# the safe direction, but "safe direction" is not "cannot happen", so the gate
# uses these functions instead.)

def date_expression_confidence(expression: str) -> tuple[float, str]:
    """(confidence, reason) for a date expression, without resolving it."""
    raw = (expression or "").strip()
    if not raw:
        return 0.0, "no date given"

    text = re.sub(r"\s+", " ", fold(raw))
    text = re.sub(r"(?:'|’)(?:nde|nda|ne|na|de|da|te|ta|ye|ya|i|si|u)$", "", text)

    if text in _UNDERSPECIFIED:
        return 0.40, _UNDERSPECIFIED[text]

    for pattern, _kind, confidence, _resolver, reason in _DATE_RULES:
        if pattern.match(text):
            return confidence, reason
    return 0.0, f"could not resolve the date '{raw}'"


def time_expression_confidence(expression: str) -> tuple[float, str]:
    """(confidence, reason) for a time expression. An EMPTY time is a
    deliberate all-day event, not missing information, so it scores 1.0 —
    the same thing resolve_time() reports."""
    r = resolve_time(expression, clock=_CONFIDENCE_CLOCK)
    return r.confidence, r.reason


# ── What the USER said, as opposed to what the model passed ─────────────────
#
# Live measurement (real qwen3:8b, 2026-07-31) found the hole these close.
# Asked *"Pazartesi saat 4'te spor salonu diye takvime bir şey ekle"*, the
# model did NOT pass date="pazartesi" through as the tool description asks. It
# resolved the weekday itself — to a Saturday — and passed an ISO date. The
# args-only confidence check then scored that ISO date 1.0 and auto-approved an
# event on the wrong day.
#
# So scoring the arguments is not enough: a model that does the resolution
# itself launders an ambiguous request into a confident-looking one. These two
# functions read the user's own words instead, and both are clock-free so the
# gate stays pure.

# Two details that were both wrong in the first draft and both matter:
#
#   * LENGTH-SORTED alternation. Unsorted, "cuma" precedes "cumartesi" in the
#     dict, regex alternation is first-match-wins, and "cumartesi" (Saturday)
#     would be read as "cuma" (Friday) — a one-day error inside the check that
#     exists to catch one-day errors.
#   * A BOUNDED ending (_END), not `\w*`. With `\w*` the name "Salih" matches
#     "sali" + "h" and a person becomes a Tuesday.
_WEEKDAY_WORD_RE = re.compile(
    r"\b(?P<wd>{})".format(
        "|".join(sorted((w for w in _WEEKDAYS if len(w) > 3), key=len, reverse=True))
    )
    + _END
    + r"\b"
)
_BARE_HOUR_IN_TEXT = re.compile(r"\bsaat\s*(?P<h>\d{1,2})(?![:.\d])")
_DAYPART_IN_TEXT = re.compile(rf"\b({_DAYPART_ALT})\b")
_ABSOLUTE_IN_TEXT = re.compile(
    r"\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{1,2}[./-]\d{1,2}([./-]\d{2,4})?\b"
    rf"|\b(bugun|yarin|dun|obur gun|today|tomorrow|yesterday)\b"
    rf"|\b\d{{1,2}}\s+({_MON})\b"
)


def weekdays_named(text: str) -> set[int]:
    """Weekday indices (Mon=0) named anywhere in a sentence, tolerant of the
    case endings Turkish adds ("pazartesiye", "cumaya")."""
    return {_WEEKDAYS[m.group("wd")] for m in _WEEKDAY_WORD_RE.finditer(fold(text or ""))}


def absolute_date(expression: str) -> date | None:
    """The date an expression denotes WITHOUT consulting a clock, or None if it
    needs one. Only the fully-specified forms qualify."""
    text = re.sub(r"\s+", " ", fold(expression or ""))
    for pattern, kind, _c, resolver, _r in _DATE_RULES:
        if kind not in ("iso", "dmy", "dmy_short"):
            continue
        m = pattern.match(text)
        if m:
            try:
                return resolver(m, date(2000, 1, 1))
            except ValueError:
                return None
    return None


def utterance_ambiguity(text: str) -> tuple[float, str]:
    """The confidence ceiling the user's OWN wording implies.

    1.0 when nothing in the sentence is ambiguous. Otherwise the score of the
    ambiguous thing — so a request that was vague stays vague no matter how
    precise the model's arguments look.
    """
    raw = (text or "").strip()
    if not raw:
        return 1.0, ""
    folded = fold(raw)

    named = weekdays_named(raw)
    qualified = any(w in folded for w in _NEXT_WEEK_WORDS + _THIS_WEEK_WORDS)
    if named and not qualified and not _ABSOLUTE_IN_TEXT.search(folded):
        return 0.80, "the request names a weekday without saying which week"

    hour = _BARE_HOUR_IN_TEXT.search(folded)
    if hour and not _DAYPART_IN_TEXT.search(folded):
        h = int(hour.group("h"))
        if 1 <= h <= 12:
            return 0.78, f"the request says 'saat {h}' with no morning/afternoon — could be {h:02d}:00 or {h + 12:02d}:00"

    return 1.0, ""


def weekday_conflict(text: str, resolved: date | None) -> str | None:
    """A reason string when the user named a weekday that `resolved` is not.

    Skipped entirely when the sentence ALSO carries its own date expression:
    in "Cuma raporu için yarın toplantı", "Cuma" names the report, not the day,
    and "yarın" is the real date. Without that guard this would fire on every
    event whose title happens to contain a weekday.
    """
    if resolved is None:
        return None
    folded = fold(text or "")
    if _ABSOLUTE_IN_TEXT.search(folded):
        return None
    named = weekdays_named(text)
    if not named or resolved.weekday() in named:
        return None
    day_names = ["pazartesi", "salı", "çarşamba", "perşembe", "cuma", "cumartesi", "pazar"]
    wanted = ", ".join(sorted(day_names[i] for i in named))
    return (
        f"the request says '{wanted}' but the date given is "
        f"{resolved.isoformat()}, a {day_names[resolved.weekday()]}"
    )


class _ConfidenceClock:
    """Never actually read — resolve_time consults no clock. Passed explicitly
    so this path can never fall through to the process clock by accident."""

    tz_name = "UTC"
    locale = "en_US"

    @property
    def tz(self):
        from datetime import timezone as _tz

        return _tz.utc

    def now(self) -> datetime:
        from datetime import timezone as _tz

        return datetime(2000, 1, 1, tzinfo=_tz.utc)

    def today(self) -> date:
        return date(2000, 1, 1)


_CONFIDENCE_CLOCK = _ConfidenceClock()


def describe(resolution: TemporalResolution) -> str:
    """One line, safe to read aloud or print in a confirmation prompt.

    The point is that the user confirms the RESOLVED value, not the raw
    expression — "yarın" in a prompt tells them nothing about whether the
    resolution was right, which is how a one-day-early event gets approved.
    """
    if not resolution.ok or resolution.start is None:
        return resolution.reason or "unresolved date/time"
    if resolution.all_day:
        return f"{resolution.start:%Y-%m-%d} (all day)"
    return f"{resolution.start:%Y-%m-%d %H:%M}"
