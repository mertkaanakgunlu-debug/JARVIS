"""What happened to each mail we tried to turn into an event (Post-MVP Faz 5).

The monitor already had a set of "IDs I have notified about", and the review
that opened this phase was explicit that it must not become the record of "IDs
I have PROCESSED". Two reasons, both live defects if they are merged:

  * The notification set is added to whether or not the inner work succeeded.
    A transient Gmail/body/extraction failure would therefore mark the mail
    handled forever, and it would never be retried.
  * The monitor marks every message already unread at startup as seen, on
    purpose, so a restart does not toast a hundred old mails. Correct for
    notification, useless as a processing record -- it would mean a restart
    silently skips every pending mail.

So processing state lives here, keyed by the one identifier Gmail guarantees:
`message_id`. That primary key is also the idempotency guarantee -- the same
mail cannot produce two calendar events, whatever order the monitor, a user
request and a restart happen to arrive in.

The states, and what each one means about the outside world:

    new        row exists, nothing read yet
    extracted  a date/time/title came out of the mail with usable confidence
    ambiguous  the mail mentions a meeting but not unambiguously WHEN
    proposed   a calendar_candidate working object exists; the user can see it
    confirmed  the user approved it; the calendar call has not returned yet
    created    a real Google Calendar event exists -- `calendar_event_id` is set
    ignored    the user (or a rule) said no; never retried
    error      an attempt failed; `attempts`/`last_error` say how often and why

Only `created` and `ignored` are terminal. `error` is deliberately NOT: the
whole point of a durable ledger is that a mail which failed for a bad reason
gets another chance, which is the behaviour the old notification set removed.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis.clock import local_naive_now

STATE_NEW = "new"
STATE_EXTRACTED = "extracted"
STATE_AMBIGUOUS = "ambiguous"
STATE_PROPOSED = "proposed"
STATE_CONFIRMED = "confirmed"
STATE_CREATED = "created"
STATE_IGNORED = "ignored"
STATE_ERROR = "error"

VALID_STATES = frozenset({
    STATE_NEW, STATE_EXTRACTED, STATE_AMBIGUOUS, STATE_PROPOSED,
    STATE_CONFIRMED, STATE_CREATED, STATE_IGNORED, STATE_ERROR,
})

# Reached the end of the road: never re-ingested, never retried.
TERMINAL_STATES = frozenset({STATE_CREATED, STATE_IGNORED})

# How many times a failing message is retried before it stops being picked up
# by the background sweep. A user asking for it by name always bypasses this --
# a person retrying deliberately is not the runaway loop this guards against.
MAX_ATTEMPTS = 3

_TABLE = """
CREATE TABLE IF NOT EXISTS mail_event_candidates (
    message_id        TEXT PRIMARY KEY,
    thread_id         TEXT,
    state             TEXT NOT NULL,
    attempts          INTEGER NOT NULL DEFAULT 0,
    last_error        TEXT,
    candidate_json    TEXT,
    calendar_event_id TEXT,
    object_id         TEXT,
    conversation_id   TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mail_state  ON mail_event_candidates(state);
CREATE INDEX IF NOT EXISTS idx_mail_object ON mail_event_candidates(object_id);
"""


def _now() -> str:
    return local_naive_now().isoformat(timespec="seconds")


@dataclass(frozen=True)
class LedgerEntry:
    message_id: str
    state: str
    thread_id: str = ""
    attempts: int = 0
    last_error: str = ""
    candidate: dict[str, Any] = field(default_factory=dict)
    calendar_event_id: str = ""
    object_id: str = ""
    conversation_id: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def retryable(self) -> bool:
        """Worth another automatic attempt?

        `created`/`ignored` are done. Anything else is retryable until the
        attempt budget runs out -- including `error`, which is the entire
        reason this table exists.
        """
        if self.is_terminal:
            return False
        return self.attempts < MAX_ATTEMPTS


class MailEventLedger:
    """Thread-safe SQLite store. Same shape as TodoStore/WorkingSetStore."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_TABLE)

    # ── Reads ───────────────────────────────────────────────────────────────

    @staticmethod
    def _row(row: sqlite3.Row) -> LedgerEntry:
        return LedgerEntry(
            message_id=row["message_id"],
            state=row["state"],
            thread_id=row["thread_id"] or "",
            attempts=int(row["attempts"] or 0),
            last_error=row["last_error"] or "",
            candidate=json.loads(row["candidate_json"] or "{}"),
            calendar_event_id=row["calendar_event_id"] or "",
            object_id=row["object_id"] or "",
            conversation_id=row["conversation_id"] or "",
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )

    def get(self, message_id: str) -> LedgerEntry | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM mail_event_candidates WHERE message_id=?", (message_id,)
            ).fetchone()
        return self._row(row) if row else None

    def by_object(self, object_id: str) -> LedgerEntry | None:
        """The ledger row a working-set object came from.

        This is the link that makes "bunu takvime ekle" idempotent: the user
        points at a working object, and the create path can still find the
        message_id that is the real dedup key.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM mail_event_candidates WHERE object_id=?", (object_id,)
            ).fetchone()
        return self._row(row) if row else None

    def list_by_state(self, *states: str, limit: int = 50) -> list[LedgerEntry]:
        if not states:
            return []
        placeholders = ",".join("?" for _ in states)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM mail_event_candidates WHERE state IN ({placeholders}) "
                "ORDER BY updated_at DESC LIMIT ?",
                (*states, limit),
            ).fetchall()
        return [self._row(r) for r in rows]

    def should_process(self, message_id: str) -> bool:
        """Is this mail worth (re-)ingesting right now?

        Unknown → yes. Terminal → no. Failed but under budget → yes, which is
        precisely what the notification set could not express.
        """
        entry = self.get(message_id)
        return True if entry is None else entry.retryable

    # ── Writes ──────────────────────────────────────────────────────────────

    def ensure(self, message_id: str, thread_id: str = "") -> LedgerEntry:
        """Insert the row if it is new; return whatever is there now.

        `INSERT OR IGNORE` rather than a read-then-write: two monitor sweeps,
        or a sweep racing a user request, must not create two rows for one
        message -- and the PRIMARY KEY is the thing that makes that impossible
        rather than unlikely.
        """
        now = _now()
        with self._lock:
            self._conn.execute(
                """INSERT OR IGNORE INTO mail_event_candidates
                   (message_id, thread_id, state, attempts, created_at, updated_at)
                   VALUES (?,?,?,0,?,?)""",
                (message_id, thread_id, STATE_NEW, now, now),
            )
        entry = self.get(message_id)
        assert entry is not None  # just inserted or already there
        return entry

    def mark(
        self,
        message_id: str,
        state: str,
        *,
        candidate: dict[str, Any] | None = None,
        calendar_event_id: str | None = None,
        object_id: str | None = None,
        conversation_id: str | None = None,
        last_error: str | None = None,
        bump_attempts: bool = False,
    ) -> LedgerEntry | None:
        """Move a row to `state`, writing only the fields named.

        A `None` argument means "leave this column alone", NOT "clear it" --
        the opposite of WorkingSetStore.patch's convention, and deliberately
        so: there is no user-facing "unset the calendar event id" operation
        here, while "advance the state without touching the extraction" is the
        common case.
        """
        if state not in VALID_STATES:
            raise ValueError(f"unknown ledger state: {state!r}")
        self.ensure(message_id)

        sets = ["state=?", "updated_at=?"]
        params: list[Any] = [state, _now()]
        if candidate is not None:
            sets.append("candidate_json=?")
            params.append(json.dumps(candidate, ensure_ascii=False))
        if calendar_event_id is not None:
            sets.append("calendar_event_id=?")
            params.append(calendar_event_id)
        if object_id is not None:
            sets.append("object_id=?")
            params.append(object_id)
        if conversation_id is not None:
            sets.append("conversation_id=?")
            params.append(conversation_id)
        if last_error is not None:
            sets.append("last_error=?")
            params.append(last_error)
        if bump_attempts:
            sets.append("attempts=attempts+1")

        params.append(message_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE mail_event_candidates SET {', '.join(sets)} WHERE message_id=?",
                params,
            )
        return self.get(message_id)

    def record_error(self, message_id: str, error: str) -> LedgerEntry | None:
        """A failed attempt: counted, kept, and still retryable under budget."""
        return self.mark(
            message_id, STATE_ERROR, last_error=str(error)[:500], bump_attempts=True
        )

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001 -- close is best-effort everywhere in this repo
            pass


_default_ledger: MailEventLedger | None = None
_default_ledger_path: Path | None = None
_default_ledger_lock = threading.Lock()


def default_ledger() -> MailEventLedger:
    """The process ledger, alongside sessions/todos/working set.

    Memoized on the resolved path, for the reason working_set.default_store()
    learned the hard way: a per-call constructor leaks a SQLite connection per
    tool invocation, and a cache that ignores the path hands a test the
    previous test's database when JARVIS_HOME moves.
    """
    global _default_ledger, _default_ledger_path
    from jarvis import paths

    path = paths.data_dir() / "sessions.db"
    with _default_ledger_lock:
        if _default_ledger is None or _default_ledger_path != path:
            if _default_ledger is not None:
                _default_ledger.close()
            _default_ledger = MailEventLedger(path)
            _default_ledger_path = path
        return _default_ledger


def reset_default_ledger() -> None:
    """Drop the memoized ledger (tests, and JARVIS_HOME switches)."""
    global _default_ledger, _default_ledger_path
    with _default_ledger_lock:
        if _default_ledger is not None:
            _default_ledger.close()
        _default_ledger = None
        _default_ledger_path = None
