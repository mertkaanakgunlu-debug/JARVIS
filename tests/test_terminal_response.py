"""Post-MVP Faz 2.75, Paket A — what gets persisted is the terminal answer.

`chat_stream` wrote `"".join(chunks)` into history, memory, the run manifest
and therefore the next turn's context. That string is not the answer:

  * `graph_stream_to_text` yields every draft the answering node produces.
    When the critic asks for a revision, compose runs a second time and its
    tokens are streamed too, separated by a blank line -- so the persisted
    text was the REJECTED draft followed by its replacement.
  * A verification repair (Faz 1) is deliberately kept OUT of the stream, so
    the corrected answer never reached history at all.

The graph's terminal `response` is the one text that survived the critic and
the verification node. That is what gets stored now, and -- because tokens
cannot be unsent -- a `__jarvis_final__` marker tells consumers that can
redraw to replace what they showed.
"""
from __future__ import annotations

import json


from jarvis.voice.session import (
    parse_confirm_marker, parse_final_marker, parse_progress_marker,
)


# ── the marker ────────────────────────────────────────────────────────────────

def test_parses_the_final_marker():
    marker = json.dumps({"__jarvis_final__": True, "text": "düzeltilmiş cevap"})
    assert parse_final_marker(marker) == "düzeltilmiş cevap"


def test_ordinary_text_is_not_a_marker():
    for delta in ("merhaba", "", "{not json", '{"other": 1}', "  "):
        assert parse_final_marker(delta) is None


def test_the_two_markers_do_not_shadow_each_other():
    """Both are bare JSON objects on the wire. A consumer checks one then the
    other, so neither may match the other's payload."""
    confirm = json.dumps({"__jarvis_confirm__": True, "id": "c1", "payload": {}})
    final = json.dumps({"__jarvis_final__": True, "text": "x"})
    assert parse_final_marker(confirm) is None
    assert parse_confirm_marker(final) is None


# ── the progress marker (completion-contract TTFB) ────────────────────────────

def test_parses_the_progress_marker():
    marker = json.dumps({
        "__jarvis_progress__": True, "phase": "preparing_required_output", "kind": "chart",
    })
    parsed = parse_progress_marker(marker)
    assert parsed == {
        "__jarvis_progress__": True, "phase": "preparing_required_output", "kind": "chart",
    }


def test_progress_marker_without_a_kind_still_parses():
    marker = json.dumps({"__jarvis_progress__": True, "phase": "resuming_required_output"})
    parsed = parse_progress_marker(marker)
    assert parsed["phase"] == "resuming_required_output"
    assert "kind" not in parsed


def test_progress_marker_requires_a_phase():
    """A marker missing `phase` is not a recognizable progress frame -- fail
    closed rather than hand a consumer a phase-less control frame."""
    assert parse_progress_marker(json.dumps({"__jarvis_progress__": True})) is None


def test_ordinary_text_is_not_a_progress_marker():
    for delta in ("merhaba", "", "{not json", '{"other": 1}', "  "):
        assert parse_progress_marker(delta) is None


def test_none_of_the_three_markers_shadow_each_other():
    """Three bare JSON objects share the wire now. A consumer checks each in
    turn, so none may match either of the other two payloads."""
    confirm = json.dumps({"__jarvis_confirm__": True, "id": "c1", "payload": {}})
    final = json.dumps({"__jarvis_final__": True, "text": "x"})
    progress = json.dumps({"__jarvis_progress__": True, "phase": "preparing_required_output"})
    assert parse_progress_marker(confirm) is None
    assert parse_progress_marker(final) is None
    assert parse_confirm_marker(progress) is None
    assert parse_final_marker(progress) is None


def test_a_marker_survives_non_ascii():
    """Turkish is the default language here; ensure_ascii=False on the way out
    has to round-trip."""
    text = "Grafiği çizemedim efendim — şöyle yapabiliriz."
    assert parse_final_marker(json.dumps(
        {"__jarvis_final__": True, "text": text}, ensure_ascii=False)) == text


# ── the SSE wrapper reframes it instead of leaking it ─────────────────────────

async def test_sse_reframes_the_final_marker():
    from jarvis.api import _sse_frames

    async def stream():
        yield "ilk taslak"
        yield json.dumps({"__jarvis_final__": True, "text": "gerçek cevap"},
                         ensure_ascii=False)

    frames = [f async for f in _sse_frames(stream())]
    assert frames[0] == "data: ilk taslak\n\n"
    payload = json.loads(frames[1].removeprefix("data: ").strip())
    assert payload == {"type": "final_answer", "text": "gerçek cevap"}


async def test_sse_still_reframes_a_confirmation():
    """The new branch must not shadow the existing one."""
    from jarvis.api import _sse_frames

    async def stream():
        yield json.dumps({"__jarvis_confirm__": True, "id": "c1", "payload": {"tools": []}})

    frames = [f async for f in _sse_frames(stream())]
    payload = json.loads(frames[0].removeprefix("data: ").strip())
    assert payload["type"] == "confirmation_required"


# ── the SSE wrapper reframes the progress marker too ───────────────────────────

async def test_sse_reframes_the_progress_marker():
    from jarvis.api import _sse_frames

    async def stream():
        yield json.dumps({
            "__jarvis_progress__": True, "phase": "preparing_required_output", "kind": "chart",
        })
        yield "İşte grafiğiniz."

    frames = [f async for f in _sse_frames(stream())]
    payload = json.loads(frames[0].removeprefix("data: ").strip())
    assert payload == {"type": "progress", "phase": "preparing_required_output", "kind": "chart"}
    assert frames[1] == "data: İşte grafiğiniz.\n\n"


async def test_sse_progress_frame_omits_kind_when_the_marker_has_none():
    from jarvis.api import _sse_frames

    async def stream():
        yield json.dumps({"__jarvis_progress__": True, "phase": "resuming_required_output"})

    frames = [f async for f in _sse_frames(stream())]
    payload = json.loads(frames[0].removeprefix("data: ").strip())
    assert payload == {"type": "progress", "phase": "resuming_required_output"}
    assert "kind" not in payload


async def test_sse_progress_does_not_leak_as_a_raw_token():
    """The exact regression this whole feature must not introduce: the
    internal marker reaching a client as literal text instead of a
    structured frame."""
    from jarvis.api import _sse_frames

    async def stream():
        yield json.dumps({"__jarvis_progress__": True, "phase": "preparing_required_output"})

    frames = [f async for f in _sse_frames(stream())]
    assert not any("__jarvis_progress__" in f for f in frames)


# ── chat_stream persists the terminal response ────────────────────────────────

def test_the_streamed_text_and_the_terminal_answer_can_differ():
    """The premise, stated so the tests below are not vacuous.

    graph_stream_to_text separates a critic revision from the draft it
    replaces with a blank line -- it does not drop the draft. So a revised
    turn's stream really is both texts.
    """
    import inspect

    from jarvis.graph import streaming

    src = inspect.getsource(streaming.graph_stream_to_text)
    assert 'yield "\\n\\n"' in src, (
        "if the stream stopped emitting superseded drafts, this whole package "
        "needs revisiting -- the marker would no longer be necessary"
    )


def test_chat_stream_persists_the_checkpoint_response_not_the_chunks():
    """Source-level, and deliberately so.

    Driving chat_stream end to end needs a real graph, a context builder, a
    system prompt and MCP wiring -- a fake big enough to run it would be a
    reimplementation, and a test that re-derives the rule it is checking
    proves only that the test agrees with itself. (That trap is not
    hypothetical here: test_calendar_autonomy's "both nodes agree" test
    re-implemented the interactivity rule inline and stayed green straight
    through a live unattended-write bug.)

    So this pins the two facts that can be read directly: the persisted text
    comes from the checkpoint's `response`, and the streamed accumulation is
    only its fallback.
    """
    import inspect

    from jarvis.agent import JarvisAgent

    src = inspect.getsource(JarvisAgent.chat_stream)
    assert 'terminal_response = str(values.get("response") or "")' in src
    # The precedence, not one spelling of it: Post-MVP Faz 6 wrapped this
    # expression in finalize_terminal_response() and the old assertion went
    # red although the rule it guards never moved. A source-level test should
    # fail when the RULE changes.
    assert "terminal_response or streamed_response" in src
    assert "finalize_terminal_response(" in src, (
        "the persisted/emitted text must go through the one canonical "
        "sanitiser -- chat() cleaned its markers and this path did not, which "
        "is how the same model output ended up scrubbed on one transport and "
        "raw on another"
    )
    assert '"".join(chunks)' in src and "streamed_response" in src, (
        "the accumulated stream must still exist as the fallback -- an "
        "interrupted or checkpoint-less run should persist what the user saw "
        "rather than nothing"
    )
    # and history/memory must be built from full_response, never from the
    # accumulation directly
    assert "_compact_completed_turn_for_history(\n                human_msg, full_response" in src


def test_the_correction_marker_is_conditional_in_the_source():
    """The marker fires only on a real difference: on every turn it would make
    consumers redraw for nothing, on no turn it would leave the user reading a
    rejected draft."""
    import inspect

    from jarvis.agent import JarvisAgent

    src = inspect.getsource(JarvisAgent.chat_stream)
    assert "if terminal_response and terminal_response != streamed_response:" in src
    assert "__jarvis_final__" in src


# ── completion-contract TTFB: chat_stream's progress marker, source-level ─────
#
# chat_stream() cannot be driven end-to-end with a fake agent the way
# resume_and_stream() can (see test_output_contract_streaming.py for THAT
# behavioral coverage) -- it builds a system prompt, calls the context
# builder and needs a real compiled graph. Same reasoning as the two tests
# just above: pin the two facts that can be read directly from the source.

def test_chat_stream_emits_progress_before_the_expensive_graph_call():
    """The marker must be yielded BEFORE graph_stream_to_text() starts, and
    from inside the same try/except that already handles
    (asyncio.CancelledError, GeneratorExit) -- a cancellation landing exactly
    at that yield must not skip the existing interrupted=True bookkeeping."""
    import inspect

    from jarvis.agent import JarvisAgent

    src = inspect.getsource(JarvisAgent.chat_stream)
    assert "_progress_marker_json(" in src
    assert '"preparing_required_output"' in src
    progress_idx = src.index("_progress_marker_json(")
    graph_call_idx = src.index("async for delta in graph_stream_to_text(")
    assert progress_idx < graph_call_idx, (
        "the progress marker must be yielded before the expensive graph call, "
        "not after"
    )
    # And covered by the same cancellation handler as the graph loop itself.
    try_idx = src.rindex("try:", 0, progress_idx)
    cancel_idx = src.index("except (asyncio.CancelledError, GeneratorExit):", graph_call_idx)
    assert try_idx < progress_idx < graph_call_idx < cancel_idx


def test_chat_stream_progress_is_gated_by_buffered():
    """Unconditional would mean every ordinary turn -- off/shadow included --
    grew a new leading frame; this pins that the yield is inside `if buffered:`.

    chat_stream() has TWO `if buffered:` blocks (this one, guarding the
    leading progress marker; a second, later one guards yielding the
    verified full_response once the graph is done) -- this test is about
    the FIRST one specifically, so it takes the earliest occurrence rather
    than assuming there is only one.
    """
    import inspect

    from jarvis.agent import JarvisAgent

    src = inspect.getsource(JarvisAgent.chat_stream)
    assert src.count("if buffered:\n") == 2
    if_idx = src.index("if buffered:\n")  # the first occurrence
    progress_idx = src.index("_progress_marker_json(")
    graph_call_idx = src.index("async for delta in graph_stream_to_text(")
    assert if_idx < progress_idx < graph_call_idx


def test_chat_stream_progress_marker_never_joins_chunks():
    """chunks/streamed_response feed history, memory and the run manifest --
    the marker must never be appended to that list."""
    import inspect

    from jarvis.agent import JarvisAgent

    src = inspect.getsource(JarvisAgent.chat_stream)
    # Structural check: the progress yield line itself must not be followed
    # by a chunks.append call before the next yield/loop boundary.
    progress_stmt_end = src.index("\n", src.index("preparing_required_output"))
    next_chunk_append = src.index("chunks.append(delta)")
    async_for_idx = src.index("async for delta in graph_stream_to_text(")
    assert progress_stmt_end < async_for_idx < next_chunk_append, (
        "chunks.append(delta) must belong to the graph loop, not run for "
        "the progress marker itself"
    )
