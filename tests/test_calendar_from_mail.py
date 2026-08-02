"""Mail → calendar, the staged flow (Post-MVP Faz 5).

Regime A (deterministic). That this phase CAN be tested this way is the point:
extraction calls no model, so "does this mail produce the right event" is a
fixture question rather than a live-run question. Faz 3 measured what the
alternative costs -- asked for facts it thought it knew, the model fabricated a
temperature in 4 of 5 runs, and a date in a mail is exactly that kind of fact.

What the tests are organised around, in the order the review demanded them:

  * **Nothing reaches the calendar without a human.** `propose` must create a
    working-set candidate and make ZERO calendar calls. This is the property
    the whole staged design exists for.
  * **The same mail cannot become two events.** message_id is the ledger's
    primary key, so a replay -- a retry, a restart, a second user request --
    returns the event that already exists.
  * **A transient failure is retried; an honest "I can't tell" is not.** The
    old notification set could express neither, which is why it must not be
    reused as processing state.
  * **The candidate is the Working Set's second kind.** Faz 4 shipped a
    kind-agnostic store with one consumer, so "this primitive generalises" was
    a claim with a single example.

Live Gmail/Calendar are faked at the service boundary (the `.execute()`
objects), not at the extraction layer -- the parsing under test runs unmodified,
which is the same rule `gmail._fixture_service` follows.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.calendar_from_mail import (
    CalendarFromMailService,
    extract_candidate,
)
from jarvis.clock import SystemClock
from jarvis.config import Settings
from jarvis.mail_ledger import (
    MailEventLedger,
    MAX_ATTEMPTS,
    STATE_AMBIGUOUS,
    STATE_CREATED,
    STATE_IGNORED,
    STATE_PROPOSED,
)
from jarvis.working_set import KIND_CALENDAR_CANDIDATE, WorkingSetStore, render_block

CONVERSATION = "c-mail"
CLOCK = SystemClock("Europe/Istanbul")

MEETING = {
    "id": "m-meeting",
    "subject": "Proje toplantısı",
    "from": "ayse@firma.com",
    "body": "Merhaba,\nToplantı 5 Ağustos Çarşamba saat 14.00'te yapılacaktır.\nİyi çalışmalar.",
}
MARKETING = {
    "id": "m-spam",
    "subject": "Kampanya! %50 indirim",
    "from": "kampanya@magaza.com",
    "body": "2019'dan beri hizmetinizdeyiz. 31 Aralık'a kadar geçerli!",
}
NO_DATE = {
    "id": "m-vague",
    "subject": "Toplantı hakkında",
    "from": "veli@firma.com",
    "body": "Toplantıyı ileri bir tarihe alalım mı?",
}


# ── Fakes ────────────────────────────────────────────────────────────────────

class _FakeCalendar:
    """Counts what actually reached Google Calendar."""

    def __init__(self):
        self.inserted: list[dict] = []
        self.existing: list[dict] = []

    def events(self):
        return self

    def list(self, **kwargs):
        return SimpleNamespace(execute=lambda: {"items": list(self.existing)})

    def insert(self, calendarId=None, body=None):
        eid = f"evt{len(self.inserted) + 1}"
        self.inserted.append(body)
        return SimpleNamespace(
            execute=lambda: {"id": eid, "htmlLink": f"https://cal/{eid}"}
        )


class _FakeGmail:
    """Serves message dicts by id; can be told to fail a fixed number of times."""

    def __init__(self, messages, fail_times: int = 0):
        self._messages = {m["id"]: m for m in messages}
        self.fail_times = fail_times
        self.fetches = 0

    def _envelope(self, mid):
        m = self._messages[mid]
        return {
            "id": m["id"],
            "threadId": m.get("threadId", m["id"]),
            "snippet": m["body"][:80],
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "Subject", "value": m["subject"]},
                    {"name": "From", "value": m["from"]},
                ],
                "body": {"data": _b64(m["body"])},
            },
        }

    def users(self):
        return self

    def messages(self):
        return self

    def get(self, userId=None, id=None, format=None, **kw):
        def _execute():
            self.fetches += 1
            if self.fetches <= self.fail_times:
                raise RuntimeError("Gmail 503 -- transient")
            return self._envelope(id)
        return SimpleNamespace(execute=_execute)


def _b64(text: str) -> str:
    import base64
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


@pytest.fixture
def ledger(tmp_path):
    lg = MailEventLedger(tmp_path / "ledger.db")
    yield lg
    lg.close()


@pytest.fixture
def store(tmp_path):
    s = WorkingSetStore(tmp_path / "ws.db")
    yield s
    s.close()


@pytest.fixture
def calendar(monkeypatch):
    cal = _FakeCalendar()
    monkeypatch.setattr("jarvis.tools.calendar._get_service", lambda settings: cal)
    return cal


def _service(ledger, store, monkeypatch, messages=(MEETING,), fail_times=0):
    gmail = _FakeGmail(list(messages), fail_times=fail_times)
    monkeypatch.setattr("jarvis.tools.gmail._get_service", lambda settings: gmail)
    svc = CalendarFromMailService(
        Settings(_env_file=None), ledger=ledger, working_set=store, clock=CLOCK,
    )
    return svc, gmail


# ── Extraction ───────────────────────────────────────────────────────────────

def test_a_real_meeting_extracts_with_evidence():
    c = extract_candidate(MEETING, clock=CLOCK)
    assert c.ok
    assert c.start.startswith("2026-08-05T14:00")
    assert c.title == "Proje toplantısı"
    assert c.band == "auto"
    assert c.evidence and "14.00" in c.evidence[0]


def test_marketing_mail_with_dates_in_it_is_not_an_event():
    """A date in the text is not an appointment. Without this the feature is
    a machine for filling the calendar with newsletters."""
    c = extract_candidate(MARKETING, clock=CLOCK)
    assert not c.ok
    assert "toplantı" in c.reason or "randevu" in c.reason


def test_a_meeting_with_no_date_is_refused_not_guessed():
    c = extract_candidate(NO_DATE, clock=CLOCK)
    assert not c.ok
    assert not c.start
    assert "tarih" in c.reason


def test_extraction_is_deterministic():
    a = extract_candidate(MEETING, clock=CLOCK)
    b = extract_candidate(MEETING, clock=CLOCK)
    assert a == b


# ── propose: a proposal, never an event ──────────────────────────────────────

def test_propose_creates_a_candidate_and_touches_no_calendar(
    ledger, store, calendar, monkeypatch
):
    """THE core property of the phase."""
    svc, _ = _service(ledger, store, monkeypatch)

    outcome = svc.propose(MEETING["id"], conversation_id=CONVERSATION)

    assert outcome.proposed
    assert calendar.inserted == [], "propose must not write to the calendar"
    obj = store.active(CONVERSATION, KIND_CALENDAR_CANDIDATE)
    assert obj is not None
    assert obj.kind == KIND_CALENDAR_CANDIDATE
    assert obj.spec["source_message_id"] == MEETING["id"]
    assert ledger.get(MEETING["id"]).state == STATE_PROPOSED


def test_the_proposal_text_says_nothing_was_created(ledger, store, calendar, monkeypatch):
    svc, _ = _service(ledger, store, monkeypatch)
    outcome = svc.propose(MEETING["id"], conversation_id=CONVERSATION)
    assert "HİÇBİR ŞEY oluşturulmadı" in outcome.message
    assert "2026-08-05" in outcome.message


def test_an_ambiguous_mail_is_recorded_without_a_working_object(
    ledger, store, calendar, monkeypatch
):
    svc, _ = _service(ledger, store, monkeypatch, messages=(NO_DATE,))

    outcome = svc.propose(NO_DATE["id"], conversation_id=CONVERSATION)

    assert outcome.state == STATE_AMBIGUOUS
    assert store.list(CONVERSATION) == [], "an unusable extraction must not be staged"
    assert ledger.get(NO_DATE["id"]).state == STATE_AMBIGUOUS


def test_ambiguous_does_not_burn_the_retry_budget(ledger, store, calendar, monkeypatch):
    """Re-reading the same text gives the same answer, so it is not a failure.
    Spending an attempt on it would block a real transient failure later."""
    svc, _ = _service(ledger, store, monkeypatch, messages=(NO_DATE,))
    svc.propose(NO_DATE["id"], conversation_id=CONVERSATION)
    assert ledger.get(NO_DATE["id"]).attempts == 0


def test_reproposing_replaces_rather_than_stacking(ledger, store, calendar, monkeypatch):
    svc, _ = _service(ledger, store, monkeypatch)
    first = svc.propose(MEETING["id"], conversation_id=CONVERSATION)
    second = svc.propose(MEETING["id"], conversation_id=CONVERSATION)

    assert first.object_id == second.object_id
    assert len(store.list(CONVERSATION, KIND_CALENDAR_CANDIDATE)) == 1


# ── create: the L3 step, and it happens once ─────────────────────────────────

def test_create_writes_the_event_and_closes_the_ledger_row(
    ledger, store, calendar, monkeypatch
):
    svc, _ = _service(ledger, store, monkeypatch)
    svc.propose(MEETING["id"], conversation_id=CONVERSATION)

    outcome = svc.confirm(conversation_id=CONVERSATION)

    assert outcome.state == STATE_CREATED
    assert len(calendar.inserted) == 1
    assert calendar.inserted[0]["summary"] == "Proje toplantısı"
    entry = ledger.get(MEETING["id"])
    assert entry.state == STATE_CREATED
    assert entry.calendar_event_id == outcome.created_event_id == "evt1"


def test_the_same_mail_cannot_become_two_events(ledger, store, calendar, monkeypatch):
    """Idempotency, and the reason message_id is the primary key."""
    svc, _ = _service(ledger, store, monkeypatch)
    svc.propose(MEETING["id"], conversation_id=CONVERSATION)
    first = svc.confirm(conversation_id=CONVERSATION)

    second = svc.confirm(conversation_id=CONVERSATION)

    assert len(calendar.inserted) == 1, "a replayed confirm created a second event"
    assert second.created_event_id == first.created_event_id
    assert "zaten" in second.message


def test_a_created_mail_is_never_reproposed(ledger, store, calendar, monkeypatch):
    """Even `force` must not reopen it -- that is the duplicate this exists to
    prevent, and a user asking twice is the likeliest way to reach it."""
    svc, _ = _service(ledger, store, monkeypatch)
    svc.propose(MEETING["id"], conversation_id=CONVERSATION)
    svc.confirm(conversation_id=CONVERSATION)

    again = svc.propose(MEETING["id"], conversation_id=CONVERSATION, force=True)

    assert again.state == STATE_CREATED
    assert len(calendar.inserted) == 1


def test_confirm_without_a_proposal_is_an_honest_error(ledger, store, calendar, monkeypatch):
    svc, _ = _service(ledger, store, monkeypatch)
    outcome = svc.confirm(conversation_id=CONVERSATION)
    assert outcome.message.startswith("[ERROR]")
    assert calendar.inserted == []


def test_a_proposal_from_another_conversation_is_refused(
    ledger, store, calendar, monkeypatch
):
    svc, _ = _service(ledger, store, monkeypatch)
    svc.propose(MEETING["id"], conversation_id="conversation-A")

    outcome = svc.confirm(conversation_id="conversation-B")

    assert outcome.message.startswith("[ERROR]")
    assert calendar.inserted == []


def test_ignore_is_terminal(ledger, store, calendar, monkeypatch):
    svc, _ = _service(ledger, store, monkeypatch)
    svc.propose(MEETING["id"], conversation_id=CONVERSATION)

    outcome = svc.ignore(conversation_id=CONVERSATION)

    assert outcome.state == STATE_IGNORED
    assert calendar.inserted == []
    assert not ledger.get(MEETING["id"]).retryable


# ── failure, retry, restart ──────────────────────────────────────────────────

def test_a_transient_fetch_failure_is_retryable(ledger, store, calendar, monkeypatch):
    """THE reason the ledger is not the notification set: that set marked a
    mail handled whether or not the work succeeded, so a 503 lost it forever."""
    svc, gmail = _service(ledger, store, monkeypatch, fail_times=1)

    first = svc.propose(MEETING["id"], conversation_id=CONVERSATION)
    assert first.message.startswith("[ERROR]")
    entry = ledger.get(MEETING["id"])
    assert entry.state == "error" and entry.attempts == 1 and entry.retryable

    second = svc.propose(MEETING["id"], conversation_id=CONVERSATION)
    assert second.proposed, "the retry must succeed once Gmail recovers"


def test_the_attempt_budget_stops_automatic_retries_but_not_the_user(
    ledger, store, calendar, monkeypatch
):
    svc, _ = _service(ledger, store, monkeypatch, fail_times=MAX_ATTEMPTS + 5)
    for _ in range(MAX_ATTEMPTS):
        svc.propose(MEETING["id"])

    entry = ledger.get(MEETING["id"])
    assert not entry.retryable
    assert not ledger.should_process(MEETING["id"]), "the sweep must stop picking it up"

    # A person asking by name is not the runaway loop the budget guards against.
    forced = svc.propose(MEETING["id"], conversation_id=CONVERSATION, force=True)
    assert forced.message.startswith("[ERROR]")   # still failing, but it TRIED
    assert ledger.get(MEETING["id"]).attempts == MAX_ATTEMPTS + 1


def test_the_ledger_survives_a_restart(tmp_path, store, calendar, monkeypatch):
    """A restart is exactly when the monitor's in-memory set forgets
    everything and re-notifies -- processing state must not."""
    path = tmp_path / "restart.db"
    first = MailEventLedger(path)
    svc, _ = _service(first, store, monkeypatch)
    svc.propose(MEETING["id"], conversation_id=CONVERSATION)
    svc.confirm(conversation_id=CONVERSATION)
    first.close()

    reopened = MailEventLedger(path)
    try:
        entry = reopened.get(MEETING["id"])
        assert entry is not None
        assert entry.state == STATE_CREATED
        assert entry.calendar_event_id == "evt1"
        assert not reopened.should_process(MEETING["id"])
    finally:
        reopened.close()


def test_an_existing_calendar_event_is_not_duplicated(
    ledger, store, calendar, monkeypatch
):
    """The calendar-side guard, independent of the ledger: the event exists
    because it was made some other way."""
    calendar.existing = [{"id": "already-there", "summary": "Proje toplantısı"}]
    svc, _ = _service(ledger, store, monkeypatch)
    svc.propose(MEETING["id"], conversation_id=CONVERSATION)

    outcome = svc.confirm(conversation_id=CONVERSATION)

    assert calendar.inserted == []
    assert outcome.created_event_id == "already-there"
    assert ledger.get(MEETING["id"]).state == STATE_CREATED


# ── the Working Set's second kind ────────────────────────────────────────────

def test_the_candidate_reaches_the_prompt_like_any_object(
    ledger, store, calendar, monkeypatch
):
    """Faz 4 built a kind-agnostic store and shipped one kind. This is the
    second, going through the same injection path with no new machinery."""
    svc, _ = _service(ledger, store, monkeypatch)
    svc.propose(MEETING["id"], conversation_id=CONVERSATION)

    block = render_block(store.list(CONVERSATION))

    assert "calendar_candidate:" in block
    assert "Proje toplantısı" in block
    assert "2026-08-05" in block


def test_the_evidence_renders_as_metadata_not_as_a_setting(
    ledger, store, calendar, monkeypatch
):
    svc, _ = _service(ledger, store, monkeypatch)
    svc.propose(MEETING["id"], conversation_id=CONVERSATION)
    obj = store.active(CONVERSATION, KIND_CALENDAR_CANDIDATE)

    rendered = obj.render()

    assert "evidence:" in rendered      # leading underscore stripped, own line
    assert "_evidence=" not in rendered
