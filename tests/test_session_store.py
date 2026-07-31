"""session_store.py — SQLite conversation history, including two Faz 0
stabilization fixes this suite locks in as permanent regressions:

  - History duplication: save_turn() used to write a FULL cumulative snapshot
    per turn_idx bucket (not a delta), but the readers concatenated across
    *all* buckets -- duplicating every message that appeared in more than one.
  - BUG-11: switch_session()/restart resetting the turn counter to 0 caused
    thread_id collisions against old LangGraph checkpoints. last_turn_idx()
    is the fix's foundation.

Faz 0 verified these live (see ROADMAP.md) but never left a persisted test --
this file is that test.
"""
from __future__ import annotations

import sqlite3
import threading

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from jarvis.session_store import SessionStore


class _FlakyConnProxy:
    """Wraps a real sqlite3.Connection, raising on the Nth .execute() call.

    sqlite3.Connection is a C type -- its methods can't be monkeypatched on
    an instance directly (attribute is read-only), so this wraps the whole
    object instead and forwards everything else unchanged via __getattr__.
    """

    def __init__(self, real_conn: sqlite3.Connection, fail_on_call_number: int):
        self._real = real_conn
        self._fail_on = fail_on_call_number
        self._call_count = 0

    def execute(self, sql, *args):
        self._call_count += 1
        if self._call_count == self._fail_on:
            raise sqlite3.OperationalError("simulated failure")
        return self._real.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _store(tmp_path) -> SessionStore:
    return SessionStore(tmp_path / "sessions.db")


def test_new_session_is_latest_session(tmp_path):
    store = _store(tmp_path)
    sid = store.new_session("test topic")
    assert store.latest_session() == sid


# ── id collision (found live 2026-08-01 by the Faz 2.5 A/B harness) ─────────

def test_new_session_survives_an_id_collision(tmp_path, monkeypatch):
    """A repeated id used to raise sqlite3.IntegrityError out of the turn.

    The id is YYYYMMDD + random hex, so the space resets daily and the draws
    are a birthday problem, not a sequence: at 4 hex digits (65 536/day) a
    measurement run of ~150 sessions had roughly a 1-in-6 chance of colliding,
    and one did -- 75 turns into a 100-turn run.

    Forcing the FIRST draw to repeat an existing id is the whole point: with 8
    hex digits a natural collision will never be observed again, so only a
    forced one can prove the retry is there. Without it this test passes on
    the broken code too.
    """
    store = _store(tmp_path)
    first = store.new_session()

    draws = iter([first.split("-", 1)[1], "beefcafe", "beefcafe"])

    class _FixedUUID:
        hex = property(lambda self: next(draws).ljust(32, "0"))

    monkeypatch.setattr("jarvis.session_store.uuid.uuid4", lambda: _FixedUUID())

    second = store.new_session()
    assert second != first
    assert store.session_exists(second)


def test_new_session_id_keeps_its_entropy_budget(tmp_path):
    """Pins the width, because no behavioural test can.

    Narrowing the random part back to 4 hex digits does not break any
    behaviour -- the retry above absorbs it -- so a mutation that reverts it
    survives every other test in this file while quietly restoring 65 536
    ids/day and the collision rate that caused the incident. The retry makes
    a repeat survivable; the width is what keeps it from happening.
    """
    suffix = _store(tmp_path).new_session().split("-", 1)[1]
    assert len(suffix) >= 8, "session id entropy was reduced -- see the retry docstring"


def test_new_session_gives_up_rather_than_spinning(tmp_path, monkeypatch):
    """If every draw collides, something other than chance is wrong -- say so
    instead of looping forever."""
    store = _store(tmp_path)
    first = store.new_session()
    suffix = first.split("-", 1)[1]

    class _AlwaysSame:
        hex = suffix.ljust(32, "0")

    monkeypatch.setattr("jarvis.session_store.uuid.uuid4", lambda: _AlwaysSame())

    with pytest.raises(RuntimeError, match="unique session id"):
        store.new_session()


# ── Agent Runtime rev.2, Faz 5: session_exists() ────────────────────────────

def test_session_exists_true_for_a_real_session(tmp_path):
    store = _store(tmp_path)
    sid = store.new_session()
    assert store.session_exists(sid) is True


def test_session_exists_false_for_an_unknown_id(tmp_path):
    store = _store(tmp_path)
    assert store.session_exists("never-created") is False


def test_session_exists_true_even_when_archived(tmp_path):
    """Unlike latest_session() (which only ever guesses among 'active'
    sessions), an EXPLICIT resume request should still succeed against an
    archived one -- archiving isn't deletion."""
    store = _store(tmp_path)
    sid = store.new_session()
    store.archive_session(sid)
    assert store.session_exists(sid) is True


# ── Agent Runtime rev.2, Faz 5 follow-up: ensure_session() ──────────────────

def test_ensure_session_creates_a_row_for_a_brand_new_id(tmp_path):
    store = _store(tmp_path)
    created = store.ensure_session("client-chosen-id")
    assert created is True
    assert store.session_exists("client-chosen-id") is True
    assert any(s["id"] == "client-chosen-id" for s in store.list_sessions(50))


def test_ensure_session_is_idempotent_for_an_existing_id(tmp_path):
    store = _store(tmp_path)
    store.new_session()
    sid = store.list_sessions(1)[0]["id"]

    created_again = store.ensure_session(sid)

    assert created_again is False
    assert store.total_sessions() == 1  # no duplicate row


def test_ensure_session_never_overwrites_an_existing_row(tmp_path):
    """INSERT OR IGNORE must not clobber a real session's topic_hint/status
    with ensure_session()'s own defaults on a second call."""
    store = _store(tmp_path)
    sid = store.new_session("original topic")
    store.archive_session(sid)

    store.ensure_session(sid, topic_hint="should not apply")

    row = next(s for s in store.list_sessions(50) if s["id"] == sid)
    assert row["topic_hint"] == "original topic"
    assert row["status"] == "archived"


def test_save_turn_then_load_history_round_trips(tmp_path):
    store = _store(tmp_path)
    sid = store.new_session()
    msgs = [HumanMessage(content="hi"), AIMessage(content="hello")]
    store.save_turn(sid, msgs, turn_idx=1)

    loaded = store.load_history(sid)
    assert [m.content for m in loaded] == ["hi", "hello"]


def test_no_duplication_across_turn_buckets(tmp_path):
    """The core Faz 0 bonus fix: each save_turn() call persists a FULL
    cumulative snapshot (not a delta) -- readers must see it exactly once,
    not once per turn_idx bucket it was ever written under."""
    store = _store(tmp_path)
    sid = store.new_session()

    turn1 = [HumanMessage(content="hi"), AIMessage(content="hello")]
    store.save_turn(sid, turn1, turn_idx=1)

    turn2 = turn1 + [HumanMessage(content="how are you"), AIMessage(content="good")]
    store.save_turn(sid, turn2, turn_idx=2)

    loaded = store.load_history(sid)
    assert len(loaded) == 4, f"expected 4 messages, got {len(loaded)} (duplication bug regressed)"
    assert [m.content for m in loaded] == ["hi", "hello", "how are you", "good"]


def test_save_turn_collapses_old_buckets(tmp_path):
    """save_turn deletes turn_idx<=current for the session -- verifies via the
    raw table, not just load_history's MAX(turn_idx) read, that old buckets
    are actually gone rather than merely shadowed."""
    store = _store(tmp_path)
    sid = store.new_session()
    store.save_turn(sid, [HumanMessage(content="a")], turn_idx=1)
    store.save_turn(sid, [HumanMessage(content="a"), HumanMessage(content="b")], turn_idx=2)

    with store._lock:
        rows = store._conn.execute(
            "SELECT DISTINCT turn_idx FROM messages WHERE session_id=?", (sid,)
        ).fetchall()
    assert [r["turn_idx"] for r in rows] == [2]


def test_last_turn_idx_zero_for_new_session(tmp_path):
    store = _store(tmp_path)
    sid = store.new_session()
    assert store.last_turn_idx(sid) == 0


def test_last_turn_idx_resumes_correctly(tmp_path):
    """BUG-11: resuming a session must continue the turn counter above any
    prior thread_id, not reset to 0 and collide with an old checkpoint."""
    store = _store(tmp_path)
    sid = store.new_session()
    store.save_turn(sid, [HumanMessage(content="a")], turn_idx=1)
    store.save_turn(sid, [HumanMessage(content="a")], turn_idx=5)  # e.g. after a gap

    assert store.last_turn_idx(sid) == 5


def test_save_turn_rolls_back_on_failure(tmp_path):
    """Multi-statement writes run inside an explicit BEGIN/COMMIT so a crash
    mid-write can't leave a session's messages half-deleted -- verified here
    by forcing the first message INSERT to fail (call #3: BEGIN, DELETE,
    INSERT) and confirming the DB ends up exactly as it was before the failed
    save_turn call, not half-applied."""
    store = _store(tmp_path)
    sid = store.new_session()
    store.save_turn(sid, [HumanMessage(content="original")], turn_idx=1)
    before = store.load_history(sid)
    before_turn_idx = store.last_turn_idx(sid)

    real_conn = store._conn
    store._conn = _FlakyConnProxy(real_conn, fail_on_call_number=3)
    try:
        with pytest.raises(sqlite3.OperationalError):
            store.save_turn(
                sid, [HumanMessage(content="new1"), HumanMessage(content="new2")], turn_idx=2
            )
    finally:
        store._conn = real_conn

    after = store.load_history(sid)
    assert [m.content for m in after] == [m.content for m in before], (
        "a failed save_turn must roll back, not leave a half-written state"
    )
    assert store.last_turn_idx(sid) == before_turn_idx, (
        "the DELETE FROM messages WHERE turn_idx<=2 must have rolled back too"
    )


def test_concurrent_writers_and_readers_do_not_raise(tmp_path):
    """Mirrors Faz 0's own live verification methodology (3 writers x 3
    readers). threading.Lock() guards every method -- this proves it actually
    serializes access rather than merely existing."""
    store = _store(tmp_path)
    sid = store.new_session()
    errors: list[Exception] = []

    def writer(n: int):
        try:
            for i in range(20):
                store.save_turn(sid, [HumanMessage(content=f"w{n}-{i}")], turn_idx=i + 1)
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    def reader():
        try:
            for _ in range(20):
                store.load_history(sid)
                store.last_turn_idx(sid)
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(3)]
    threads += [threading.Thread(target=reader) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"concurrent access raised: {errors}"


def test_entity_upsert_increments_mention_count(tmp_path):
    store = _store(tmp_path)
    sid = store.new_session()
    store.upsert_entity("Mert", "person", "the user", sid)
    store.upsert_entity("Mert", "person", "the user, updated", sid)

    top = store.top_entities()
    assert len(top) == 1
    assert top[0]["mention_count"] == 2
    assert top[0]["description"] == "the user, updated"
