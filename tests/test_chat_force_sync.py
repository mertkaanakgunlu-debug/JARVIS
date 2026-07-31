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

These tests pin _should_offload's precedence. The keyword list itself was a
product decision left open when this file was written; the owner made it on
2026-07-31 (narrow the list so ordinary interactive requests stop diverting),
so the keyword-dependent expectations below now assert the NARROWED behavior
rather than the original bug.
"""
from __future__ import annotations

from types import SimpleNamespace

from jarvis.api import ChatRequest, _should_offload
from jarvis.task_executor import _should_async

MVP_PROMPT = (
    "Maillerimi kontrol et, hesabimdaki para akisini analiz et, "
    "bir excel tablosuna donustur ve grafikle"
)

# A request that genuinely IS long-running, used to exercise the branch where
# the heuristic (not a flag) makes the call. Kept separate from MVP_PROMPT so
# narrowing the list again cannot silently turn every test here into a no-op.
LONG_RUNNING_PROMPT = "bu konuyu derinlemesine arastir ve bir rapor hazirla"


class _Executor:
    """Stands in for TaskExecutor: only should_async is consulted here."""

    def should_async(self, message, force=False):
        return _should_async(message, force)


def test_the_mvp_prompt_no_longer_trips_the_heuristic():
    """The narrowing, pinned. "grafik" was a bare substring in the hint list, so
    the owner's own MVP sentence diverted to the background executor and /chat
    answered {"async": true} in ~0s. Charting is fast and interactive; it must
    not divert on the keyword alone."""
    assert _should_async(MVP_PROMPT) is False


def test_ordinary_requests_that_used_to_divert_stay_interactive():
    """Each of these tripped a bare-substring hint ("grafik", "rapor",
    "finansal", "arastir") while being an ordinary interactive request."""
    for prompt in (
        "grafigi cizgi grafik yap",
        "grafiği çizgi grafik yap",
        "raporu goster",
        "finansal durumum nedir",
        "su siteyi arastir bakalim",
    ):
        assert _should_async(prompt) is False, prompt


def test_genuinely_long_running_requests_still_divert():
    """The narrowing must not disable the feature. Both diacritic spellings, so
    the ASCII-folded matching stays covered."""
    for prompt in (
        LONG_RUNNING_PROMPT,
        "bu konuyu derinlemesine araştır",
        "bana bir rapor hazirla",
        "dalga simulasyonu calistir",
        "latex compile et",
    ):
        assert _should_async(prompt) is True, prompt


def test_hints_are_stem_anchored_not_bare_substrings():
    """Hints must start at a word boundary. Unanchored matching is what let the
    old two-character "3d" hint fire from inside an unrelated word."""
    assert _should_async("x3design dosyasini ac") is False
    assert _should_async("3 boyutlu gorsellestirme yap") is True


def test_force_sync_keeps_the_mvp_prompt_interactive():
    body = ChatRequest(message=MVP_PROMPT, force_sync=True)

    assert _should_offload(_Executor(), body) is False


def test_without_any_flag_the_heuristic_decides():
    """The default path still consults the heuristic — force_sync short-circuits
    it, it does not replace it."""
    assert _should_offload(_Executor(), ChatRequest(message=LONG_RUNNING_PROMPT)) is True
    assert _should_offload(_Executor(), ChatRequest(message=MVP_PROMPT)) is False


def test_force_sync_overrides_a_genuinely_long_running_request():
    """The HUD sends force_sync on everything typed at its command bar: the user
    is sitting there waiting, which beats any keyword guess."""
    body = ChatRequest(message=LONG_RUNNING_PROMPT, force_sync=True)

    assert _should_offload(_Executor(), body) is False


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
