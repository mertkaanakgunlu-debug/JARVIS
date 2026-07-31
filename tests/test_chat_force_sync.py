"""The interactive /chat path must be explicitly reachable (force_sync).

Found while building the MVP gate (2026-07-30): the async heuristic
(jarvis/task_executor._should_async) matches BARE SUBSTRINGS against short
everyday Turkish words. "grafik" is in the list, so the owner's own MVP sentence

    "Maillerimi kontrol et, hesabimdaki para akisini analiz et,
     bir excel tablosuna donustur ve grafikle"

was handed to the background TaskExecutor and /chat answered
{"async": true, "task_id": ...} in ~0 seconds. On the gate's first run that
looked exactly like a model that did nothing at all -- five silent step
failures.

Two facts worth keeping straight, because they are easy to conflate:
  * cli.py NEVER consults this heuristic, so the same sentence is interactive in
    the CLI and asynchronous over HTTP. The API/mobile client is the affected
    surface, not the terminal.
  * background_turn() (what the executor runs) is a DIFFERENT entry point from
    chat(), with different confirmation semantics -- see MEMORY.md on
    TaskExecutor and ConfirmationRequired. Measuring one and claiming the other
    would be dishonest, which is why the gate needs this flag.

These tests pin _should_offload's precedence. They deliberately do NOT assert
anything about which keywords the heuristic contains: narrowing that list is a
product decision about phone UX, left to the owner.
"""
from __future__ import annotations

from types import SimpleNamespace

from jarvis.api import ChatRequest, _should_offload
from jarvis.task_executor import _should_async

MVP_PROMPT = (
    "Maillerimi kontrol et, hesabimdaki para akisini analiz et, "
    "bir excel tablosuna donustur ve grafikle"
)


class _Executor:
    """Stands in for TaskExecutor: only should_async is consulted here."""

    def should_async(self, message, force=False):
        return _should_async(message, force)


def test_the_mvp_prompt_really_does_trip_the_heuristic():
    """Guards the premise. If this ever goes False the bug was fixed elsewhere
    and the rest of this file is testing a hypothetical."""
    assert _should_async(MVP_PROMPT) is True


def test_force_sync_keeps_the_mvp_prompt_interactive():
    body = ChatRequest(message=MVP_PROMPT, force_sync=True)

    assert _should_offload(_Executor(), body) is False


def test_without_force_sync_the_mvp_prompt_is_offloaded():
    body = ChatRequest(message=MVP_PROMPT)

    assert _should_offload(_Executor(), body) is True


def test_force_async_wins_over_force_sync():
    """An explicit request for background execution is more specific than a
    request to skip the guess."""
    body = ChatRequest(message="merhaba", force_async=True, force_sync=True)

    assert _should_offload(_Executor(), body) is True


def test_force_async_still_offloads_a_short_query():
    body = ChatRequest(message="merhaba", force_async=True)

    assert _should_offload(_Executor(), body) is True


def test_plain_short_query_stays_interactive():
    body = ChatRequest(message="merhaba")

    assert _should_offload(_Executor(), body) is False


def test_no_executor_never_offloads():
    """A build with no TaskExecutor must not crash or claim to offload."""
    body = ChatRequest(message=MVP_PROMPT, force_async=True)

    assert _should_offload(None, body) is False


def test_defaults_preserve_pre_existing_client_behavior():
    """Every client predating force_sync sends neither flag -- their behavior
    must be byte-identical to before the field existed."""
    body = ChatRequest(message=MVP_PROMPT)

    assert body.force_sync is False
    assert body.force_async is False
    assert _should_offload(_Executor(), body) is _should_async(MVP_PROMPT)


def test_getattr_shaped_executor_is_accepted():
    """api.py reaches the executor via getattr(agent, "_task_executor", None);
    a SimpleNamespace-shaped stub must work the same as the real class."""
    body = ChatRequest(message=MVP_PROMPT, force_sync=True)
    stub = SimpleNamespace(should_async=lambda m, force=False: True)

    assert _should_offload(stub, body) is False
