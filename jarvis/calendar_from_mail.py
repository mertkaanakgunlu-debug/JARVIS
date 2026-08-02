"""Mail → a calendar event the user approves (Post-MVP Faz 5).

The shape of this phase was set by the review that gated it, and the first
decision is what NOT to build:

    yeni mail geldi → model → Google Calendar create        ← not this

An unattended model call that writes to a real calendar has no one watching it
choose the wrong day. So the flow is staged, and the stage the user sees is a
PROPOSAL:

    message_id → fetch → extract → validate → dedup
               → calendar_candidate working object → USER APPROVAL → create

Three properties fall out of that ordering, and each one answers a specific
failure this codebase has already had:

**The tool takes a message_id and nothing else.** Not sender, not subject, not
date. A model asked to relay those re-types them, and a re-typed date is how
Faz 4's chart lost its colour and how "Tarih" became a column that did not
exist. The service reads the message itself, by canonical id.

**Extraction is deterministic.** Faz 3 measured what happens when a model is
asked for facts it thinks it already knows: `weather` fabricated a temperature
in 4 of 5 runs. Dates in a mail are exactly that kind of fact. So the date, the
time and the title come out of `jarvis/nlu/temporal.py` and
`jarvis/nlu/event_text.py` -- the same resolvers the calendar tool itself uses,
with the same confidence bands -- and the model's job is to relay, not to
compute. Nothing here calls an LLM.

**Confidence is recorded, never spent.** Even a 0.99 extraction produces a
candidate, not an event. The trusted-sender auto-create the review sketches as
a later option is the only thing confidence would ever unlock, and it is
deliberately not built: it needs measurement first, and the measurement needs
this ledger to exist.

The candidate is also the Working Set's **second real consumer** -- Faz 4 built
a kind-agnostic store and shipped only `chart`, so "the same primitive later
holds a mail draft, a report, a table" was a claim with one example. A
`calendar_candidate` is a different kind, with a different renderer, and it
goes through the same `create`/`activate`/prompt-injection path unchanged.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from jarvis.clock import Clock, get_clock
from jarvis.mail_ledger import (
    MailEventLedger,
    STATE_AMBIGUOUS,
    STATE_CREATED,
    STATE_EXTRACTED,
    STATE_IGNORED,
    STATE_PROPOSED,
    default_ledger,
)
from jarvis.nlu import event_text, temporal
from jarvis.working_set import KIND_CALENDAR_CANDIDATE, WorkingObject, WorkingSetStore

if TYPE_CHECKING:
    from jarvis.config import Settings

logger = logging.getLogger(__name__)

# Words that make a mail *about* an appointment. Without one of these, a date
# in the text is just a date -- an invoice due date, a newsletter's "since
# 2019", a delivery estimate -- and turning those into calendar events would
# make the feature noise the user has to clean up. Cheap to widen later; the
# ledger records what was skipped, so the cost of being wrong here is visible.
_EVENT_HINTS = (
    "toplanti", "toplantı", "gorusme", "görüşme", "randevu", "bulusma", "buluşma",
    "seminer", "sunum", "mulakat", "mülakat", "ders", "sinav", "sınav", "davet",
    "etkinlik", "konferans", "workshop", "calistay", "çalıştay", "vize", "final",
    "meeting", "appointment", "interview", "call", "demo", "webinar", "session",
    "invitation", "invite", "conference", "schedule", "scheduled",
)

# Bounds on how much of a mail is scanned. A marketing mail can be enormous and
# the interesting line is near the top in practice; an unbounded scan would put
# an O(words x 6) window search on the monitor's path.
MAX_BODY_CHARS = 4000
MAX_LINES = 60
MAX_WORDS_PER_LINE = 40
MAX_WINDOW_WORDS = 6

# The default meeting length when a mail says when but not how long.
DEFAULT_DURATION_MINUTES = 60


@dataclass(frozen=True)
class EventCandidate:
    """A proposed event, plus everything needed to judge it without the mail."""

    message_id: str
    ok: bool
    title: str = ""
    start: str = ""              # ISO 8601, timezone-aware, in the user's zone
    end: str = ""
    all_day: bool = False
    timezone: str = ""
    location: str = ""
    confidence: float = 0.0
    band: str = "leave"          # auto | ask | leave -- temporal.band()
    sender: str = ""
    subject: str = ""
    evidence: tuple[str, ...] = ()   # the exact lines the date/time came from
    reason: str = ""
    date_expression: str = ""    # what was matched, for the audit trail
    time_expression: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_spec(self) -> dict[str, Any]:
        """The working object's spec.

        Leading-underscore keys are METADATA in `WorkingObject.render()` -- they
        get their own line instead of joining the "these are the settings" list.
        Evidence belongs there: it is why the system believes this, not a knob.
        """
        spec = {
            "title": self.title,
            "start": self.start,
            "end": self.end,
            "location": self.location,
            "timezone": self.timezone,
            "source_message_id": self.message_id,
            "_sender": self.sender,
            "_subject": self.subject,
            "_confidence": f"{self.confidence:.2f} ({self.band})",
            "_evidence": " | ".join(self.evidence),
        }
        if self.all_day:
            spec["all_day"] = "evet"
        return {k: v for k, v in spec.items() if v not in (None, "", ())}


# ── Deterministic extraction ────────────────────────────────────────────────

def _mentions_event(text: str) -> bool:
    folded = temporal.fold(text)
    return any(hint in folded for hint in _EVENT_HINTS)


def _windows(line: str) -> list[str]:
    """Consecutive word spans, longest first.

    `temporal.resolve_date` anchors with `.match()`, so it answers "is this
    whole string a date", not "does this string contain one". Free-form mail
    text therefore has to be offered candidate spans -- the same technique
    `event_text._strip_leading_temporal` uses to decide whether a title starts
    with a date. Longest first so "5 Ağustos Çarşamba" wins over "5".
    """
    words = line.split()[:MAX_WORDS_PER_LINE]
    spans: list[str] = []
    for size in range(min(MAX_WINDOW_WORDS, len(words)), 0, -1):
        for start in range(len(words) - size + 1):
            spans.append(" ".join(words[start:start + size]))
    return spans


def _best_in_line(line: str, clock: Clock) -> tuple[str, float, str, float]:
    """(date_expression, date_conf, time_expression, time_conf) for one line."""
    best_date, best_date_conf = "", 0.0
    best_time, best_time_conf = "", 0.0
    for span in _windows(line):
        if not best_date or best_date_conf < 1.0:
            d = temporal.resolve_date(span, clock=clock)
            if d.ok and d.confidence > best_date_conf:
                best_date, best_date_conf = span, d.confidence
        if not best_time or best_time_conf < 1.0:
            t = temporal.resolve_time(span, clock=clock)
            if t.ok and t.hour is not None and t.confidence > best_time_conf:
                best_time, best_time_conf = span, t.confidence
    return best_date, best_date_conf, best_time, best_time_conf


def _scan_temporal(text: str, clock: Clock) -> tuple[str, str, list[str]]:
    """Find the best (date expression, time expression, evidence lines).

    A line carrying BOTH beats two lines carrying one each: "Toplantı 5 Ağustos
    Çarşamba saat 14.00'te" is one statement, and pairing a date from the
    signature block with a time from the body would silently invent a third.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:MAX_LINES]
    best: tuple[float, str, str, str] | None = None
    fallback_date: tuple[float, str, str] | None = None
    fallback_time: tuple[float, str, str] | None = None

    for line in lines:
        d_expr, d_conf, t_expr, t_conf = _best_in_line(line, clock)
        if d_expr and t_expr:
            score = min(d_conf, t_conf)
            if best is None or score > best[0]:
                best = (score, d_expr, t_expr, line)
        if d_expr and (fallback_date is None or d_conf > fallback_date[0]):
            fallback_date = (d_conf, d_expr, line)
        if t_expr and (fallback_time is None or t_conf > fallback_time[0]):
            fallback_time = (t_conf, t_expr, line)

    if best is not None:
        return best[1], best[2], [best[3]]
    if fallback_date is not None:
        evidence = [fallback_date[2]]
        time_expr = ""
        if fallback_time is not None:
            time_expr = fallback_time[1]
            if fallback_time[2] != fallback_date[2]:
                evidence.append(fallback_time[2])
        return fallback_date[1], time_expr, evidence
    return "", "", []


def extract_candidate(
    fields: dict[str, Any], *, clock: Clock | None = None
) -> EventCandidate:
    """A Gmail `message_fields()` dict → a proposed event, or an honest refusal.

    Pure and deterministic: same mail, same clock, same answer. That is what
    makes it testable against fixtures and what keeps a model from being asked
    to remember a date it never read.
    """
    clk = clock or get_clock()
    message_id = str(fields.get("id") or "")
    subject = str(fields.get("subject") or "")
    sender = str(fields.get("from") or "")
    body = str(fields.get("body") or "")[:MAX_BODY_CHARS]
    haystack = f"{subject}\n{body}"

    base = dict(
        message_id=message_id, sender=sender, subject=subject,
        timezone=clk.tz_name,
    )

    if not _mentions_event(haystack):
        return EventCandidate(
            ok=False, band="leave",
            reason="mail bir toplantı/randevudan söz etmiyor",
            **base,
        )

    date_expr, time_expr, evidence = _scan_temporal(haystack, clk)
    if not date_expr:
        return EventCandidate(
            ok=False, band="leave",
            reason="mailde bir tarih bulunamadı — hangi gün olduğu belirsiz",
            **base,
        )

    resolution = temporal.resolve(date_expr, time_expr, clock=clk)
    if not resolution.ok or resolution.start is None:
        return EventCandidate(
            ok=False, band=temporal.band(resolution.confidence),
            confidence=resolution.confidence,
            reason=resolution.reason,
            date_expression=date_expr, time_expression=time_expr,
            evidence=tuple(evidence), **base,
        )

    title = event_text.clean_title(subject).title or subject.strip() or "Toplantı"
    start = resolution.start
    end = start + timedelta(minutes=DEFAULT_DURATION_MINUTES)

    return EventCandidate(
        ok=True,
        title=title,
        start=start.isoformat(),
        end=end.isoformat(),
        all_day=resolution.all_day,
        confidence=resolution.confidence,
        band=temporal.band(resolution.confidence),
        evidence=tuple(evidence),
        reason=resolution.reason,
        date_expression=date_expr,
        time_expression=time_expr,
        **base,
    )


# ── The staged flow ─────────────────────────────────────────────────────────

@dataclass
class ProposalOutcome:
    """What one ingest attempt did, in terms the caller can act on."""

    state: str
    candidate: EventCandidate | None = None
    object_id: str = ""
    message: str = ""
    created_event_id: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def proposed(self) -> bool:
        return self.state == STATE_PROPOSED


class CalendarFromMailService:
    """Mail id in, approvable proposal out. No LLM, no calendar write."""

    def __init__(
        self,
        settings: "Settings",
        *,
        ledger: MailEventLedger | None = None,
        working_set: WorkingSetStore | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.settings = settings
        self._ledger = ledger
        self._working_set = working_set
        self._clock = clock

    # Resolved lazily so constructing the service never opens a database --
    # the monitor builds one per sweep.
    @property
    def ledger(self) -> MailEventLedger:
        if self._ledger is None:
            self._ledger = default_ledger()
        return self._ledger

    @property
    def working_set(self) -> WorkingSetStore:
        if self._working_set is None:
            from jarvis.working_set import default_store

            self._working_set = default_store()
        return self._working_set

    def _fetch(self, message_id: str) -> dict[str, Any]:
        from jarvis.tools import gmail

        service = gmail._get_service(self.settings)
        msg = (
            service.users().messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        return gmail.message_fields(msg)

    def propose(
        self,
        message_id: str,
        *,
        conversation_id: str = "",
        force: bool = False,
    ) -> ProposalOutcome:
        """Fetch, extract, validate, dedup, and stage a candidate.

        `force` is the user asking for this mail by name: it bypasses the
        attempt budget and a previous `ignored`, because a person retrying
        deliberately is not the runaway loop the budget guards against. It does
        NOT bypass `created` -- that would be the duplicate event the whole
        ledger exists to prevent.
        """
        if not message_id:
            return ProposalOutcome(STATE_AMBIGUOUS, message="message_id gerekli.")

        entry = self.ledger.ensure(message_id)

        if entry.state == STATE_CREATED:
            return ProposalOutcome(
                STATE_CREATED,
                created_event_id=entry.calendar_event_id,
                message=(
                    "Bu mail için zaten bir takvim etkinliği oluşturulmuş "
                    f"(id: {entry.calendar_event_id[:16]})."
                ),
            )
        if not force and not entry.retryable:
            return ProposalOutcome(
                entry.state,
                message=(
                    f"Bu mail {entry.attempts} denemede işlenemedi ve otomatik olarak "
                    f"tekrar denenmiyor. Son hata: {entry.last_error or '(kaydedilmedi)'}"
                ),
            )

        try:
            fields = self._fetch(message_id)
        except Exception as exc:  # noqa: BLE001 -- recorded, retried later
            self.ledger.record_error(message_id, f"mail okunamadı: {exc}")
            logger.info("calendar_from_mail: fetch failed for %s: %s", message_id, exc)
            return ProposalOutcome(
                "error", message=f"[ERROR] Mail okunamadı: {exc}"
            )

        candidate = extract_candidate(fields, clock=self._clock)

        if not candidate.ok:
            # An honest "I could not tell WHEN" is a terminal-ish state, not an
            # error: nothing failed, the mail simply does not say. Attempts are
            # NOT bumped -- re-reading the same text would give the same answer,
            # and burning the retry budget on it would block a real transient
            # failure later.
            self.ledger.mark(
                message_id, STATE_AMBIGUOUS,
                candidate=candidate.to_dict(),
                conversation_id=conversation_id or None,
            )
            return ProposalOutcome(
                STATE_AMBIGUOUS, candidate=candidate,
                message=f"Bu mailden etkinlik çıkarılamadı: {candidate.reason}",
            )

        self.ledger.mark(
            message_id, STATE_EXTRACTED, candidate=candidate.to_dict()
        )

        if not conversation_id:
            # Nobody's conversation to hang it on -- the monitor's ingest path.
            # The extraction is kept (that is the expensive part and it is now
            # durable); the working object waits until a conversation asks.
            return ProposalOutcome(
                STATE_EXTRACTED, candidate=candidate,
                message="Etkinlik çıkarıldı; bir konuşmaya bağlanmadı.",
            )

        obj = self._stage(candidate, conversation_id)
        self.ledger.mark(
            message_id, STATE_PROPOSED,
            object_id=obj.id, conversation_id=conversation_id,
        )
        return ProposalOutcome(
            STATE_PROPOSED, candidate=candidate, object_id=obj.id,
            message=render_candidate(obj, candidate),
        )

    def _stage(self, candidate: EventCandidate, conversation_id: str) -> WorkingObject:
        """Put the candidate in the working set, replacing this mail's previous
        one rather than stacking a second object for the same message."""
        existing = self.ledger.get(candidate.message_id)
        if existing and existing.object_id:
            obj = self.working_set.get(existing.object_id)
            if obj is not None and obj.conversation_id == conversation_id:
                patched = self.working_set.patch(obj.id, candidate.to_spec())
                self.working_set.activate(conversation_id, obj.id)
                return patched or obj
        return self.working_set.create(
            conversation_id,
            KIND_CALENDAR_CANDIDATE,
            candidate.to_spec(),
            title=candidate.title,
        )

    def confirm(
        self,
        *,
        conversation_id: str,
        object_id: str = "",
        duration_minutes: int = DEFAULT_DURATION_MINUTES,
    ) -> ProposalOutcome:
        """The approved candidate becomes a real event. THE L3 step.

        Reached only after a person said yes, and still gated: the tool that
        calls this is registered L3/external_write, so policy_guard, the kill
        switch and the audit log all treat it exactly like `google_calendar`'s
        own create.
        """
        obj = (
            self.working_set.get(object_id) if object_id
            else self.working_set.active(conversation_id, KIND_CALENDAR_CANDIDATE)
        )
        if obj is None:
            return ProposalOutcome(
                "error",
                message=(
                    "[ERROR] Takvime eklenecek bir etkinlik önerisi yok. "
                    "Önce calendar_from_mail(message_id=...) çalıştırın."
                ),
            )
        if obj.conversation_id != conversation_id:
            return ProposalOutcome("error", message="[ERROR] Bu öneri bu konuşmaya ait değil.")
        if obj.kind != KIND_CALENDAR_CANDIDATE:
            return ProposalOutcome(
                "error",
                message=f"[ERROR] {obj.ref} bir takvim önerisi değil ({obj.kind}).",
            )

        message_id = str(obj.spec.get("source_message_id") or "")
        entry = self.ledger.get(message_id) if message_id else None
        if entry is not None and entry.state == STATE_CREATED:
            return ProposalOutcome(
                STATE_CREATED, object_id=obj.id,
                created_event_id=entry.calendar_event_id,
                message=(
                    "Bu öneri zaten takvime eklenmiş "
                    f"(id: {entry.calendar_event_id[:16]}). Yenisi oluşturulmadı."
                ),
            )

        start = str(obj.spec.get("start") or "")
        if not start:
            return ProposalOutcome("error", message="[ERROR] Önerinin başlangıç zamanı yok.")
        date_part, _, time_part = start.partition("T")
        if obj.spec.get("all_day"):
            time_part = ""
        else:
            time_part = time_part[:5]

        from jarvis.tools.calendar import create_event

        result = create_event(
            title=str(obj.spec.get("title") or obj.title),
            date=date_part,
            time=time_part,
            duration_minutes=duration_minutes,
            location=str(obj.spec.get("location") or ""),
            description=f"Kaynak mail: {obj.spec.get('_subject') or ''}".strip(),
            settings=self.settings,
        )
        if not result.ok:
            if message_id:
                self.ledger.record_error(message_id, result.error)
            return ProposalOutcome("error", message=f"[ERROR] {result.error}")

        if message_id:
            self.ledger.mark(
                message_id, STATE_CREATED,
                calendar_event_id=result.event_id,
                conversation_id=conversation_id,
            )
        note = (
            " (bu saatte aynı adlı bir etkinlik zaten vardı; yenisi oluşturulmadı)"
            if result.duplicate_of else ""
        )
        return ProposalOutcome(
            STATE_CREATED, object_id=obj.id, created_event_id=result.event_id,
            message=(
                f"[Calendar] '{result.title}' takvime eklendi{note}.\n"
                f"  Tarih: {result.when} ({result.tz_name})\n"
                f"  id: {result.event_id}"
            ),
        )

    def ignore(self, *, conversation_id: str, object_id: str = "") -> ProposalOutcome:
        """The user said no. Terminal, so no sweep re-proposes it."""
        obj = (
            self.working_set.get(object_id) if object_id
            else self.working_set.active(conversation_id, KIND_CALENDAR_CANDIDATE)
        )
        if obj is None or obj.conversation_id != conversation_id:
            return ProposalOutcome("error", message="[ERROR] Reddedilecek bir öneri yok.")
        message_id = str(obj.spec.get("source_message_id") or "")
        if message_id:
            self.ledger.mark(message_id, STATE_IGNORED, conversation_id=conversation_id)
        return ProposalOutcome(
            STATE_IGNORED, object_id=obj.id,
            message=f"[Calendar] {obj.ref} önerisi yok sayıldı; takvime eklenmedi.",
        )


def render_candidate(obj: WorkingObject, candidate: EventCandidate) -> str:
    """What the user reads when a proposal is staged.

    States the evidence and the confidence, because the decision being asked
    for is "is this right?" and the honest answer depends on a line of the mail
    the user has not necessarily read.
    """
    lines = [
        f"[Takvim önerisi] {obj.ref} — \"{candidate.title}\"",
        f"  Ne zaman: {candidate.start}" + (" (tüm gün)" if candidate.all_day else ""),
    ]
    if candidate.location:
        lines.append(f"  Yer: {candidate.location}")
    lines.append(f"  Güven: {candidate.confidence:.2f} ({candidate.band})")
    if candidate.sender:
        lines.append(f"  Kaynak: {candidate.sender} — {candidate.subject}")
    if candidate.evidence:
        lines.append(f"  Dayanak: {candidate.evidence[0][:160]}")
    lines.append(
        "  Onaylamak için: \"takvime ekle\" — henüz HİÇBİR ŞEY oluşturulmadı."
    )
    return "\n".join(lines)
