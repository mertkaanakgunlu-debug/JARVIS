"""Agent Runtime rev.2, Faz 5 -- removing JarvisAgent's silent session
auto-resume.

_resolve_initial_session() is the decision agent.py's __init__ used to make
inline by unconditionally calling session_store.latest_session() and
guessing "the most recently active session belongs to whoever is
constructing this agent" -- a real reproducibility hazard (a server restart
silently inherited a prior smoke test's session) and a real privacy leak (a
brand-new client would transparently see someone else's history). It is
factored out here specifically so it's directly testable without
constructing a full JarvisAgent (heavy -- see test_reset_lifecycle.py's own
docstring for this suite's established reason to avoid that).

cli.py's own _read_last_session_id()/_write_last_session_id() (the CLI's
explicit, JARVIS_HOME-aware replacement for the old guess, preserving its
"continue where I left off" UX) are covered separately below.
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from jarvis.agent import _resolve_initial_session
from jarvis.session_store import SessionStore


def _store(tmp_path) -> SessionStore:
    return SessionStore(tmp_path / "sessions.db")


# ── _resolve_initial_session ─────────────────────────────────────────────────

def test_no_resume_id_always_creates_a_fresh_session(tmp_path):
    """The core behavior change: even with a perfectly good PRIOR session
    sitting in the store, no resume_session_id means a fresh one -- the
    guess is gone, not just relocated."""
    store = _store(tmp_path)
    prior = store.new_session()
    store.save_turn(prior, [HumanMessage(content="hi")], turn_idx=1)

    sid, history, turn = _resolve_initial_session(store, None)

    assert sid != prior
    assert history == []
    assert turn == 0
    assert store.session_exists(sid)


def test_valid_resume_id_restores_history_and_turn_counter(tmp_path):
    store = _store(tmp_path)
    prior = store.new_session()
    msgs = [HumanMessage(content="hi"), AIMessage(content="hello")]
    store.save_turn(prior, msgs, turn_idx=3)

    sid, history, turn = _resolve_initial_session(store, prior)

    assert sid == prior
    assert [m.content for m in history] == ["hi", "hello"]
    assert turn == 3  # BUG-11: must resume the real counter, not restart at 0


def test_unknown_resume_id_falls_back_to_a_fresh_session(tmp_path):
    """A stale/foreign id (deleted session, different JARVIS_HOME, a typo in
    a hand-edited state file) must not be trusted blindly -- falling back to
    fresh is the safe default, not a crash or a silently-wrong session_id
    with no row behind it."""
    store = _store(tmp_path)

    sid, history, turn = _resolve_initial_session(store, "does-not-exist")

    assert sid != "does-not-exist"
    assert store.session_exists(sid)
    assert history == []
    assert turn == 0


def test_empty_string_resume_id_is_treated_as_no_id(tmp_path):
    store = _store(tmp_path)
    sid, history, turn = _resolve_initial_session(store, "")
    assert store.session_exists(sid)
    assert history == []


def test_archived_resume_id_still_resumes(tmp_path):
    """An explicit resume request succeeds even for an archived session --
    only the removed *guessing* behavior was 'active' sessions only."""
    store = _store(tmp_path)
    prior = store.new_session()
    store.save_turn(prior, [HumanMessage(content="hi")], turn_idx=1)
    store.archive_session(prior)

    sid, history, turn = _resolve_initial_session(store, prior)
    assert sid == prior
    assert history


# ── cli.py's explicit continuity mechanism ──────────────────────────────────

def test_cli_read_last_session_returns_none_when_file_missing(jarvis_home):
    from jarvis import cli

    assert cli._read_last_session_id() is None


def test_cli_write_then_read_round_trips(jarvis_home):
    from jarvis import cli

    cli._write_last_session_id("20260722-abcd")
    assert cli._read_last_session_id() == "20260722-abcd"


def test_cli_last_session_path_follows_jarvis_home(jarvis_home):
    from jarvis import cli

    assert cli._cli_last_session_path() == jarvis_home / "data" / "cli_last_session.txt"


def test_cli_read_last_session_tolerates_blank_file(jarvis_home):
    from jarvis import cli

    path = cli._cli_last_session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("   \n", encoding="utf-8")
    assert cli._read_last_session_id() is None
