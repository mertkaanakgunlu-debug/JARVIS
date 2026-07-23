"""jarvis/memory.py -- Memory.close() (external-review finding, 2026-07-23).

Memory had no lifecycle at all: chromadb.PersistentClient(path=X) registers
its System in a process-GLOBAL, refcounted cache keyed on that exact path
(chromadb.api.shared_system_client.SharedSystemClient) and never releases it
without an explicit Client.close() call. Confirmed live: two Memory()
instances constructed back to back in tests/test_shadow_replay_equivalence.py
(off arm + shadow arm) resolved to the IDENTICAL default chroma_dir (both
cwd-relative, same isolated_cwd test) and so silently shared one chromadb
System for the whole pytest process's lifetime -- a plausible contributor to
this suite's intermittent chromadb corruption ("no such table:
acquire_write", "Failed to get segments"). Fixed at the call site (that test
now gives each arm its own workspace-scoped chroma_dir) and generically here
(Memory.close() actually releases the reference).
"""
from __future__ import annotations

import chromadb.api.shared_system_client as ssc

from jarvis.config import Settings
from jarvis.memory import Memory


def _refcount(identifier: str) -> int:
    return ssc.SharedSystemClient._identifier_to_refcount.get(identifier, 0)


def test_close_releases_this_instances_reference(isolated_cwd, tmp_path):
    chroma_dir = tmp_path / "chroma"
    settings = Settings(_env_file=None, chroma_dir=chroma_dir, vault_dir=tmp_path / "vault")
    memory = Memory(settings)
    identifier = str(chroma_dir)

    # +2, not +1: chromadb's Client constructs an internal AdminClient
    # (chromadb/api/client.py's Client.__init__) that independently
    # registers against the SAME identifier -- Client.close() releases
    # both references together, which is exactly what this test pins.
    assert _refcount(identifier) == 2
    memory.close()
    assert _refcount(identifier) == 0


def test_two_instances_on_the_same_path_share_one_system_until_both_close(isolated_cwd, tmp_path):
    """Documents the actual chromadb mechanism the bug relied on: this is not
    a jarvis-side cache, it is chromadb's own SharedSystemClient, refcounted
    per identical persist_directory string. Sharing a path is fine WHEN both
    holders eventually close(); the bug was never closing at all."""
    chroma_dir = tmp_path / "chroma"
    settings = Settings(_env_file=None, chroma_dir=chroma_dir, vault_dir=tmp_path / "vault")
    identifier = str(chroma_dir)

    m1 = Memory(settings)
    m2 = Memory(settings)
    assert _refcount(identifier) == 4  # 2 Memory()s x (client + its admin client)

    m1.close()
    assert _refcount(identifier) == 2, "one close() must not tear down the still-referenced System"
    m2.close()
    assert _refcount(identifier) == 0


def test_close_is_safe_to_call_more_than_once(isolated_cwd, tmp_path):
    settings = Settings(_env_file=None, chroma_dir=tmp_path / "chroma", vault_dir=tmp_path / "vault")
    memory = Memory(settings)
    memory.close()
    memory.close()  # must not raise


def test_close_failure_is_logged_not_silently_swallowed(isolated_cwd, tmp_path, monkeypatch, caplog):
    """Review remediation: close() previously did `except Exception: pass`
    with zero logging -- a genuine close failure (e.g. a locked SQLite file)
    would be indistinguishable from a clean close, hiding exactly the kind
    of evidence that would explain a leaked System / this suite's chromadb
    flakiness. Must still never raise -- callers use this best-effort in
    finally/teardown paths."""
    import logging as _logging

    settings = Settings(_env_file=None, chroma_dir=tmp_path / "chroma", vault_dir=tmp_path / "vault")
    memory = Memory(settings)

    def _raise():
        raise RuntimeError("simulated locked chromadb client")
    monkeypatch.setattr(memory._client, "close", _raise)

    with caplog.at_level(_logging.WARNING, logger="jarvis.memory"):
        memory.close()  # must not raise

    assert any("close" in r.message.lower() for r in caplog.records)


def test_distinct_chroma_dirs_get_independent_systems(isolated_cwd, tmp_path):
    """The fix's actual guarantee for test_shadow_replay_equivalence.py: two
    Memory instances on DIFFERENT paths never share a System in the first
    place, regardless of close() -- the workspace-scoped chroma_dir is what
    stops the off/shadow arms from colliding, close() just stops the leak."""
    s_off = Settings(_env_file=None, chroma_dir=tmp_path / "ws-off" / "chroma",
                      vault_dir=tmp_path / "ws-off" / "vault")
    s_shadow = Settings(_env_file=None, chroma_dir=tmp_path / "ws-shadow" / "chroma",
                        vault_dir=tmp_path / "ws-shadow" / "vault")
    m_off = Memory(s_off)
    m_shadow = Memory(s_shadow)
    try:
        assert m_off._client is not m_shadow._client
        assert _refcount(str(tmp_path / "ws-off" / "chroma")) == 2
        assert _refcount(str(tmp_path / "ws-shadow" / "chroma")) == 2
    finally:
        m_off.close()
        m_shadow.close()
