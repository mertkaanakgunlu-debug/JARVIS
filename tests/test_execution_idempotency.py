"""Agent Runtime rev.2, Faz 2 -- jarvis.execution.idempotency's durable journal.

Uses isolated_cwd (this module resolves paths.data_dir(), same rule as
kill_switch/audit_log -- see MEMORY.md's isolate-test-data-paths lesson).
"""
from __future__ import annotations

from jarvis.execution import idempotency


def test_uncommitted_execution_id_is_not_committed(isolated_cwd):
    assert idempotency.is_committed("never-seen") is False


def test_commit_then_is_committed(isolated_cwd):
    idempotency.commit("exec-1", "file_write", "digest-a")
    assert idempotency.is_committed("exec-1") is True


def test_first_commit_returns_true(isolated_cwd):
    assert idempotency.commit("exec-2", "file_write", "digest-a") is True


def test_duplicate_commit_returns_false_and_does_not_raise(isolated_cwd):
    idempotency.commit("exec-3", "file_write", "digest-a")
    assert idempotency.commit("exec-3", "file_write", "digest-a") is False


def test_different_execution_ids_do_not_collide(isolated_cwd):
    idempotency.commit("exec-4a", "file_write", "digest-a")
    assert idempotency.is_committed("exec-4b") is False


def test_journal_persists_across_a_fresh_connection(isolated_cwd):
    """Every call opens its own connection (see the module docstring) --
    this proves that isn't silently losing state between calls."""
    idempotency.commit("exec-5", "gmail", "digest-x")
    for _ in range(3):
        assert idempotency.is_committed("exec-5") is True
