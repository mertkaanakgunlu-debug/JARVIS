"""Injectable clock — one source of "what time is it" for the whole system.

Post-MVP Faz 2. Before this module there was no such source: every consumer
called `datetime.now()` on its own and picked its own timezone, and the two
that mattered picked DIFFERENT ones. `_build_now_block()` (jarvis/agent.py)
told the model the date in Europe/Istanbul; `calendar.py:_parse_date()`
resolved "yarın" off `datetime.now(timezone.utc)` and then handed the naive
wall-clock result to Google with `timeZone: Europe/Istanbul`.

Measured consequence (verified against the real `_parse_date`, all 24 local
hours, before any fix): during Istanbul 00:00–02:59 — the hours when UTC is
still on the previous date — "yarın" resolved one day early. 3 of 24 hours
wrong, silently, with the event landing on a real (wrong) day rather than
failing. Note this corrects an instant recorded in HANDOFF.md: 23:30
Europe/Istanbul does NOT reproduce it (UTC is 20:30 the same day); the
window is 00:00–02:59 local.

Two things make that class of bug hard to reach again:

  * Every consumer takes a Clock, so "which timezone is this?" is answered
    once, at construction, instead of per call site.
  * A Clock can be frozen, so the 3-hour window is a test rather than a
    thing you have to be awake at 01:00 to observe.

Deliberately dependency-free (stdlib only, no Settings import) — the same
reason jarvis/policy_guard.py is: so anything can consume it without
dragging in config or the graph.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Iterator, Protocol, runtime_checkable

DEFAULT_TZ = "Europe/Istanbul"
DEFAULT_LOCALE = "tr_TR"

# Fallback offsets for when tzdata is unavailable (a real possibility on
# Windows, where zoneinfo has no system database to read — the same guard
# jarvis/agent.py's _build_now_block() already carried). Only zones this
# project actually commits to, and only where a FIXED offset is exact rather
# than approximate: Türkiye has been on permanent UTC+3 with no DST since
# 2016, so +03:00 is not a simplification. A zone with DST must never be
# added here — a fixed offset would be wrong for half the year, which is
# worse than the loud failure of not having it.
_FIXED_OFFSET_FALLBACKS: dict[str, int] = {
    "Europe/Istanbul": 3,
    "UTC": 0,
    "Etc/UTC": 0,
}


class UnknownTimezone(ValueError):
    """Raised when a timezone name can be resolved neither by zoneinfo nor by
    the exact-offset fallback table above."""


def resolve_timezone(name: str) -> tzinfo:
    """IANA name → tzinfo, falling back to an exact fixed offset when tzdata
    is missing. Raises UnknownTimezone rather than silently substituting UTC:
    a calendar event written into the wrong zone is a wrong event, and the
    quiet version of that failure is the whole reason this module exists."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 — missing tzdata, bad name, or no key
        offset = _FIXED_OFFSET_FALLBACKS.get(name)
        if offset is None:
            raise UnknownTimezone(
                f"timezone '{name}' could not be resolved: zoneinfo has no such key "
                "(tzdata may not be installed) and it has no exact fixed-offset "
                "fallback. Install tzdata (`pip install tzdata`) or configure a "
                f"timezone that does: {sorted(_FIXED_OFFSET_FALLBACKS)}"
            )
        return timezone(timedelta(hours=offset), name)


@runtime_checkable
class Clock(Protocol):
    """What a consumer is allowed to depend on. Kept deliberately small: a
    consumer that needs more than "now, and in which zone" is usually about to
    reimplement date arithmetic that jarvis/nlu/temporal.py already owns."""

    @property
    def tz(self) -> tzinfo: ...

    @property
    def tz_name(self) -> str: ...

    @property
    def locale(self) -> str: ...

    def now(self) -> datetime:
        """Timezone-aware, in self.tz. Never naive."""
        ...

    def today(self) -> date: ...


@dataclass(frozen=True)
class SystemClock:
    """The real clock. Reads the OS time on every call — never caches.

    Caching would make an API server that stays up overnight confidently
    wrong about the date, which is worse than being slow; that reasoning is
    already recorded on JarvisAgent._env_block and applies identically here.
    """

    tz_name: str = DEFAULT_TZ
    locale: str = DEFAULT_LOCALE

    @property
    def tz(self) -> tzinfo:
        return resolve_timezone(self.tz_name)

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def today(self) -> date:
        return self.now().date()


@dataclass(frozen=True)
class FrozenClock:
    """A clock stopped at one instant, for tests.

    Construct from a LOCAL wall-clock reading (the way a human states a time)
    or from an aware datetime in any zone — both end up as the same instant.
    `advance()` returns a new FrozenClock rather than mutating, so a test can
    never leak a moved clock into another test.
    """

    instant: datetime  # always tz-aware; normalized in __post_init__
    tz_name: str = DEFAULT_TZ
    locale: str = DEFAULT_LOCALE

    def __post_init__(self) -> None:
        tz = resolve_timezone(self.tz_name)
        inst = self.instant
        if inst.tzinfo is None:
            # Naive input means "wall clock in tz_name" — the reading a human
            # would give ("01:30 in Istanbul"), which is exactly the input
            # shape the calendar bug's regression test needs.
            inst = inst.replace(tzinfo=tz)
        object.__setattr__(self, "instant", inst.astimezone(tz))

    @classmethod
    def at(cls, local_iso: str, tz_name: str = DEFAULT_TZ, locale: str = DEFAULT_LOCALE) -> "FrozenClock":
        """FrozenClock.at("2026-08-01 01:30") — local wall clock in tz_name."""
        return cls(datetime.fromisoformat(local_iso), tz_name, locale)

    @property
    def tz(self) -> tzinfo:
        return resolve_timezone(self.tz_name)

    def now(self) -> datetime:
        return self.instant

    def today(self) -> date:
        return self.instant.date()

    def advance(self, **timedelta_kwargs) -> "FrozenClock":
        return FrozenClock(self.instant + timedelta(**timedelta_kwargs), self.tz_name, self.locale)


# ── Process clock ────────────────────────────────────────────────────────────
#
# Every public function in this project takes `clock: Clock | None = None` and
# falls back to get_clock(). Explicit injection is the primary path (it is
# what makes the resolvers testable at all); this global exists so the dozens
# of call sites that genuinely have no clock to pass — a tool function called
# by LangChain, a prompt builder — still share ONE timezone rather than each
# re-deciding. Guarded by a lock because JARVIS runs the graph from several
# independent event loops (see BUG-8's _state_lock for the same reasoning).

_lock = threading.Lock()
_clock: Clock = SystemClock()


def get_clock() -> Clock:
    with _lock:
        return _clock


def set_clock(clock: Clock) -> Clock:
    """Install the process clock; returns the previous one so a caller can
    restore it. Prefer use_clock() — it restores on the exception path too."""
    global _clock
    with _lock:
        previous, _clock = _clock, clock
    return previous


def configure(tz_name: str, locale: str = DEFAULT_LOCALE) -> Clock:
    """Point the process clock at a configured timezone (settings.calendar_timezone).

    Raises UnknownTimezone for an unresolvable zone rather than falling back to
    UTC — startup is the right place to find out that the configured zone is
    unusable, not the moment an event is written to the wrong day.
    """
    resolve_timezone(tz_name)  # fail fast, here, not at first use
    return set_clock(SystemClock(tz_name=tz_name, locale=locale))


def local_naive_now(clock: Clock | None = None) -> datetime:
    """Wall-clock "now" in the configured zone, with the tzinfo stripped.

    For the stores that persist naive ISO strings and compare them directly
    (jarvis/scheduler.py, jarvis/todo_store.py). Those were calling
    `datetime.now()`, i.e. reading the OPERATING SYSTEM's timezone while the
    rest of the system read settings.calendar_timezone. On this machine the
    two agree, so nothing was observably wrong — but "correct because two
    independent settings happen to match" is exactly the shape of the bug this
    module exists to remove, and it becomes visible the moment the owner
    travels or the OS zone changes.

    Returning naive keeps those stores' existing comparisons intact; making
    them timezone-aware would break comparisons against rows already on disk.
    """
    return (clock or get_clock()).now().replace(tzinfo=None)


@contextmanager
def use_clock(clock: Clock) -> Iterator[Clock]:
    previous = set_clock(clock)
    try:
        yield clock
    finally:
        set_clock(previous)
