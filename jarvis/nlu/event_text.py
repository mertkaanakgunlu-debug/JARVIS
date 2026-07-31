"""Event title/description discipline — the record, not the request.

Post-MVP Faz 2, plan item 4: *"kullanıcının cümlesi başlık olarak kopyalanmaz
(olay kaydedilir: `Baran ile toplantı`); söylenmemiş açıklama üretilmez."*

The observed shape of the problem: told *"yarın öğlen 3'te Baran'la toplantı
ayarla"*, a model creates an event **titled** "yarın öğlen 3'te Baran'la
toplantı ayarla". Every word of that is redundant with fields the event
already has — the date is in `start`, and "ayarla" is an instruction to
JARVIS, not part of the meeting. A calendar is a record of what is happening,
so the title should read "Baran'la toplantı".

Two functions, and they are deliberately different in kind:

  * `clean_title()` REMOVES spans it can identify exactly — a leading date/time
    expression this project's own resolver can parse, or a trailing
    calendar-command verb. It never rewrites, never reorders, never invents,
    and never returns empty: if cleaning would leave nothing, the original
    stands. A title that survives untouched is the normal case.

  * `title_quality()` SCORES, and is consumed by jarvis/policy_guard.py. A
    title that still reads like a raw instruction after cleaning is evidence
    the model did not understand the request, and that is a reason to ask the
    user rather than to act — which is why this is a confidence signal and not
    just a cosmetic fix.

`description_is_restatement()` is the description half, and it is narrow on
purpose. Whether a description was actually said is not knowable from the tool
arguments — only the model knows what it heard. What IS checkable is that the
description adds nothing: it repeats the title, or it repeats the instruction.
That case is caught here; the general "don't invent one" is a prompt rule, and
saying otherwise would overstate what this code does.
"""

from __future__ import annotations

import re

from jarvis.nlu import temporal
from jarvis.nlu.temporal import fold
from jarvis.nlu.temporal import fold_indexable as _foldable

__all__ = [
    "clean_title", "title_quality", "description_is_restatement", "TitleCleaning",
]

# Verbs that address JARVIS rather than describe the event. Folded forms.
_COMMAND_VERBS = (
    "ekle", "ekler misin", "ekleyiver", "olustur", "olusturur musun", "kaydet",
    "koy", "gir", "ayarla", "ayarlar misin", "planla", "yaz", "kur", "at",
    "randevu al", "not al",
    "add", "create", "schedule", "put", "set", "book", "make",
)
# Objects those verbs take, which are equally not part of the event.
_COMMAND_OBJECTS = (
    "takvime", "takvimime", "ajandaya", "ajandama", "takvimde", "takvim",
    "to my calendar", "to the calendar", "on my calendar", "in my calendar",
)

_VERB_ALT = "|".join(re.escape(v) for v in sorted(_COMMAND_VERBS, key=len, reverse=True))
_OBJ_ALT = "|".join(re.escape(o) for o in sorted(_COMMAND_OBJECTS, key=len, reverse=True))

# Turkish puts the verb last ("... takvime ekle"); English puts it first and
# the object last ("Add ... to my calendar"). Both shapes, both ends.
#
# \b on every side is not decoration. Without it the verb "at" matched inside
# "kat", so "Dr. Yılmaz, 2. kat" was classified as a bare instruction and a
# real description was discarded — found by tests/test_event_text.py, and it
# would have truncated titles the same way ("Konser sanat" -> "Konser san").
_COMMAND_TAIL = re.compile(rf"(?:\b(?:{_OBJ_ALT})\s+)?\b(?:{_VERB_ALT})\b\s*[.!]?$")
_OBJECT_TAIL = re.compile(rf"\b(?:{_OBJ_ALT})\b\s*[.!]?$")
_COMMAND_HEAD = re.compile(rf"^(?:{_VERB_ALT})\b\s+")
_OBJECT_HEAD = re.compile(rf"^(?:{_OBJ_ALT})\b\s+")

# ── Which leading date/time spans may be removed ─────────────────────────────
#
# Not "any span that parses as a date" — that rule ate the "Cuma" in "Cuma
# raporu" (a Friday report, where Friday is the report's NAME, not when it
# happens). Turkish marks a time adverbial with a case ending, so the safe
# subset is: spans that are inherently adverbial, spans whose last token
# carries a locative/dative ending, and bare clock times. A bare weekday or a
# bare "15 Ağustos" is left alone — a slightly redundant title costs the user
# nothing, and a title with a word eaten out of it is not recoverable.
_ALWAYS_ADVERBIAL_SPANS = frozenset({
    "bugun", "yarin", "dun", "obur gun", "oburgun", "ertesi gun", "simdi",
    "today", "tomorrow", "yesterday", "tonight",
    "bu sabah", "bu aksam", "bu gece", "bu ogleden sonra",
    "this morning", "this evening", "this afternoon", "this weekend",
})
_CASE_MARKED_TAIL = re.compile(r"(?:'|’)(?:nde|nda|te|ta|de|da|ye|ya|e|a)$|(?:\d)(?:te|ta|de|da|ye|ya|e|a)$")
_CLOCK_SPAN = re.compile(r"^\d{1,2}[:.]\d{2}$|^\d{1,2}\s*(am|pm)$")

# Turkish yes/no question particle, and its written-together forms.
_QUESTION = re.compile(r"(\?|\b(mi|mı|mu|mü|misin|mısın|musun|müsün)\b)", re.IGNORECASE)

_MAX_REASONABLE_TITLE = 80


class TitleCleaning:
    """(title, changed, reason) with a bit of structure so callers can log why."""

    __slots__ = ("title", "changed", "reason")

    def __init__(self, title: str, changed: bool, reason: str) -> None:
        self.title, self.changed, self.reason = title, changed, reason

    def __iter__(self):
        return iter((self.title, self.changed, self.reason))

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return f"TitleCleaning({self.title!r}, changed={self.changed}, reason={self.reason!r})"


def _is_adverbial(span: str) -> bool:
    """Is this span functioning as "when", rather than naming something?"""
    folded = fold(span)
    if folded in _ALWAYS_ADVERBIAL_SPANS:
        return True
    if _CLOCK_SPAN.match(folded):
        return True
    last = folded.split()[-1] if folded.split() else ""
    return bool(_CASE_MARKED_TAIL.search(last))


def _strip_leading_temporal(text: str) -> tuple[str, str]:
    """Drop a leading date/time span, longest first. ("", reason) if none.

    Two conditions, both required: the span must PARSE as a date/time through
    this project's own resolver, and it must READ as a time adverbial
    (_is_adverbial). The second condition is what protects "Cuma raporu" —
    "Cuma" parses as a date perfectly well, which is exactly why parsing alone
    was not a safe test.
    """
    words = text.split()
    for cut in range(min(len(words) - 1, 6), 0, -1):
        span = " ".join(words[:cut])
        rest = " ".join(words[cut:]).strip(" ,;:-–—")
        if not rest or not _is_adverbial(span):
            continue
        d = temporal.resolve_date(span, clock=_STATIC_CLOCK)
        t = temporal.resolve_time(span, clock=_STATIC_CLOCK)
        combined = temporal.resolve(span, clock=_STATIC_CLOCK)
        if d.ok or (t.ok and t.hour is not None) or combined.ok:
            return rest, f"removed the leading date/time span '{span}'"
    return "", ""


class _StaticClock:
    """A fixed instant, used ONLY to ask "is this text a date expression?".

    Cleaning must not depend on today's date — the same title has to clean the
    same way on every day of the year, or a cached prompt and a live turn would
    disagree. The resolved VALUE is discarded here; only ok/not-ok is read.
    """

    tz_name = "UTC"
    locale = "en_US"

    @property
    def tz(self):
        from datetime import timezone

        return timezone.utc

    def now(self):
        from datetime import datetime, timezone

        return datetime(2000, 1, 1, tzinfo=timezone.utc)

    def today(self):
        from datetime import date

        return date(2000, 1, 1)


_STATIC_CLOCK = _StaticClock()


def clean_title(title: str) -> TitleCleaning:
    """Turn an instruction into a record. Conservative and idempotent."""
    raw = (title or "").strip()
    if not raw:
        return TitleCleaning("", False, "")

    reasons: list[str] = []
    current = raw

    # Applied to a fixed point: "takvime diş hekimi randevusu ekle" needs the
    # trailing verb gone before the leading object is even at the edge. Bounded
    # so a pathological input cannot loop.
    for _ in range(4):
        before = current

        for pattern, label in ((_COMMAND_TAIL, "trailing instruction"), (_OBJECT_TAIL, "trailing instruction")):
            m = pattern.search(_foldable(current))
            if m and m.start() > 0:
                candidate = current[: m.start()].strip(" ,;:-–—")
                if candidate:
                    reasons.append(f"removed the {label} '{current[m.start():].strip()}'")
                    current = candidate
                    break

        for pattern in (_OBJECT_HEAD, _COMMAND_HEAD):
            m = pattern.match(_foldable(current))
            if m and m.end() < len(current):
                candidate = current[m.end():].strip(" ,;:-–—")
                if candidate:
                    reasons.append(f"removed the leading instruction '{current[: m.end()].strip()}'")
                    current = candidate
                    break

        stripped, why = _strip_leading_temporal(current)
        if stripped:
            reasons.append(why)
            current = stripped

        if current == before:
            break

    current = current.strip(" ,;:-–—")
    if not current:
        # Cleaning consumed everything — the original is the only honest answer.
        return TitleCleaning(raw, False, "")
    return TitleCleaning(current, current != raw, "; ".join(reasons))


def title_quality(title: str) -> tuple[float, str]:
    """(score, reason) for how much this reads like an event rather than a request.

    Scored on the CLEANED title, because the cleaned title is what actually
    gets written — penalising a title that clean_title() already fixed would
    make the gate ask about something that is no longer wrong.
    """
    cleaned = clean_title(title).title.strip()
    if not cleaned:
        return 0.0, "no title given"

    folded = fold(cleaned)

    if _QUESTION.search(cleaned):
        return 0.55, f"the title is phrased as a question ('{cleaned}')"

    if _COMMAND_TAIL.search(folded):
        # Survived cleaning, i.e. the title is ONLY a command ("takvime ekle").
        return 0.50, f"the title is an instruction, not an event ('{cleaned}')"

    if len(cleaned) > _MAX_REASONABLE_TITLE:
        return 0.70, f"the title is {len(cleaned)} characters — that reads like a sentence, not a title"

    combined = temporal.resolve(cleaned, clock=_STATIC_CLOCK)
    if combined.ok:
        return 0.60, f"the title is just a date/time ('{cleaned}')"

    return 1.0, ""


def description_is_restatement(title: str, description: str) -> bool:
    """True when the description carries nothing the event does not already say.

    Narrow by design: equal-after-folding to the title, contained in it (or
    containing it with nothing added), or a bare command. Anything else is
    left alone — a description that genuinely was not said is not detectable
    from here, and pretending otherwise would be the same overclaiming this
    phase is trying to remove.
    """
    t, d = fold(title or ""), fold(description or "")
    if not d:
        return False
    if not t:
        return bool(_COMMAND_TAIL.search(d))
    if d == t:
        return True
    if d in t or (t in d and len(d) - len(t) <= 3):
        return True
    return bool(_COMMAND_TAIL.search(d)) and len(d) <= len(t) + 20
