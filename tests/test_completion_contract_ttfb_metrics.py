"""scripts/completion_contract_ab.py's TTFB-diagnostic fields, source-level.

`scripts/completion_contract_ab.py` is deliberately NOT imported here --
tests/test_completion_contract_harness.py's own docstring explains why: it
sets JARVIS_HOME, chdir()s into a scratch home and imports the whole agent
at module scope, so importing it inside the normal pytest process would pay
for (and risk) all of that for every future test run. Reading its source as
plain text proves the same mechanism without any of the side effects.

What these tests pin: `time_to_first_visible_s` keeps its ORIGINAL,
pre-registered meaning (literally whichever delta arrives first, unchanged);
`first_visible_kind`/`time_to_first_answer_token_s` are new, additive fields
that specifically exclude progress/confirmation control frames; and the
pre-registered gate clause in `_gate_verdict()` was not touched.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "scripts" / "completion_contract_ab.py"


def _source() -> str:
    return HARNESS.read_text(encoding="utf-8")


def _function_body(src: str, def_line: str) -> str:
    start = src.index(def_line)
    nxt = src.index("\ndef ", start + 1)
    return src[start:nxt]


def test_classify_delta_reuses_the_shared_marker_parsers():
    """Must not re-derive the marker shape -- reuse the SAME parsers every
    real consumer (API/Electron/mobile/voice) uses, so the harness can never
    classify a delta differently than a real client would."""
    src = _source()
    assert "from jarvis.voice.session import parse_confirm_marker, parse_progress_marker" in src
    body = _function_body(src, "def _classify_delta(delta: str) -> str:")
    progress_idx = body.index("parse_progress_marker(delta)")
    confirm_idx = body.index("parse_confirm_marker(delta)")
    answer_idx = body.index('return "answer"')
    assert progress_idx < confirm_idx < answer_idx, (
        "progress must be checked before confirmation, and the answer "
        "fallback must come last"
    )


def test_first_visible_and_its_kind_are_set_together_on_the_first_delta():
    """first_visible_kind must describe WHATEVER set first_visible, not be
    computed independently -- item 20 of the task's own test matrix."""
    src = _source()
    idx = src.index("if first_visible is None:")
    window = src[idx: idx + 150]
    assert "first_visible = now" in window
    assert "first_visible_kind = kind" in window


def test_first_answer_token_is_gated_on_answer_kind_specifically():
    """A progress (or confirmation) delta must NOT start the answer-token
    clock -- item 21 of the task's own test matrix."""
    src = _source()
    assert 'if first_answer_token is None and kind == "answer":' in src
    idx = src.index('if first_answer_token is None and kind == "answer":')
    window = src[idx: idx + 100]
    assert "first_answer_token = now" in window


def test_only_answer_shaped_deltas_accumulate_into_the_streamed_answer():
    """A control frame is not streamed answer text -- letting it into
    `chunks` would inflate/skew streamed_len against every historical run,
    none of which ever had a progress marker at all."""
    src = _source()
    idx = src.index('if kind == "answer":')
    window = src[idx: idx + 120]
    assert "chunks.append(delta)" in window


def test_new_fields_are_additive_in_the_row():
    src = _source()
    assert (
        '"time_to_first_visible_s": round(first_visible, 3) if first_visible else None,'
        in src
    ), "the historical field's own computation must be untouched"
    assert '"first_visible_kind": first_visible_kind,' in src
    assert '"time_to_first_answer_token_s": (' in src


def test_the_pre_registered_gate_clause_still_reads_the_untouched_field():
    """The follow-up work must not redefine what the pre-registered
    `treatment first-visible p90 <= 60s (ABSOLUTE)` clause measures, and must
    not touch its threshold. _gate_verdict() itself was not edited by this
    change -- this pins that the field/threshold pair it reads is exactly
    what it always was."""
    src = _source()
    assert '"treatment first-visible p90 <= 60s (ABSOLUTE)"' in src
    gate_idx = src.index("def _gate_verdict(")
    gate_body = src[gate_idx:]
    assert 't_first <= 60.0 if t_first else None' in gate_body
    assert 'time_to_first_answer_token_s' not in gate_body, (
        "this is not a new acceptance gate -- the new field must stay "
        "reporting-only, never folded into the pre-registered clause list"
    )


def test_report_prints_the_new_metric_without_replacing_the_old_one():
    src = _source()
    report_idx = src.index("def report(")
    gate_idx = src.index("def _gate_verdict(")
    report_body = src[report_idx:gate_idx]
    assert "first-visible p50/p90" in report_body
    assert "first-answer-token p50/p90" in report_body


# ── phase-split timing instrumentation (2026-08-07 follow-up round 2) ──────
#
# A live run hit a trial that resolved at elapsed_s=4224.3s against a
# nominal 300s asyncio.wait_for timeout, and the pre-round-2 harness had no
# way to attribute that gap to the foreground call's own slow cancellation
# vs. an unbounded background-task drain. These tests pin the fix: bounded
# drain, phase timestamps, and honest (never silently merged, never
# silently dropped) reporting of timed-out trials.

def test_background_drain_is_bounded_and_reports_telemetry():
    src = _source()
    body = _function_body(src, "async def drain_background_tasks(")
    assert "timeout_s" in body, "the drain must take an explicit ceiling"
    assert "asyncio.wait(pending, timeout=timeout_s)" in body, (
        "must be a BOUNDED wait -- asyncio.gather(*pending) with no timeout "
        "was the pre-fix bug"
    )
    for key in ('"count"', '"drained"', '"timed_out"', '"elapsed_s"'):
        assert key in body, f"drain telemetry must report {key}"
    assert "t.cancel()" in body, "a task that outlives the ceiling must be cancelled, not abandoned unobserved"


def test_background_drain_default_is_a_module_constant_not_unbounded():
    src = _source()
    assert "BACKGROUND_DRAIN_TIMEOUT_S" in src
    assert "timeout_s: float = BACKGROUND_DRAIN_TIMEOUT_S" in src


def test_background_drain_does_not_import_or_patch_jarvis_agent():
    """Eval-only instrumentation: this bounds what THE SCRIPT waits for, not
    jarvis/agent.py's own (still correctly unbounded, for production)
    _bg_tasks handling. It must only read the same `agent._bg_tasks`
    attribute every caller already had access to -- no new coupling to
    JarvisAgent internals beyond that."""
    src = _source()
    body = _function_body(src, "async def drain_background_tasks(")
    assert "import" not in body
    assert 'getattr(agent, "_bg_tasks", ()' in body


def test_foreground_elapsed_is_captured_before_the_background_drain():
    """The whole point of the split: foreground_elapsed_s must be measured
    BEFORE drain_background_tasks() runs, not after -- otherwise it is just
    `elapsed_s` under a new name and the two phases stay conflated."""
    src = _source()
    finally_idx = src.index("foreground_elapsed = time.perf_counter() - started")
    drain_call_idx = src.index("drain_info = await drain_background_tasks(agent)")
    assert finally_idx < drain_call_idx


def test_cancellation_cleanup_is_the_excess_over_the_requested_timeout():
    src = _source()
    assert "round(foreground_elapsed - timeout_s, 3) if timed_out else None" in src


def test_elapsed_s_formula_is_unchanged_by_the_phase_split():
    """The pre-registered gate reads elapsed_s -- this pins that its
    computation (time.perf_counter() - started, same call site relative to
    the finally block) was not altered while adding the new phase fields."""
    src = _source()
    assert 'elapsed = time.perf_counter() - started' in src
    # Exactly one such line -- if the phase-split work had introduced a
    # second, divergent computation of "elapsed", this would catch it.
    assert src.count("time.perf_counter() - started") >= 2  # foreground_elapsed AND elapsed both derive from `started`


def test_new_phase_fields_are_additive_in_the_row():
    src = _source()
    for field in (
        '"foreground_elapsed_s": round(foreground_elapsed, 3),',
        '"timeout_requested_s": timeout_s,',
        '"cancellation_cleanup_s": cancellation_cleanup,',
        '"background_drain_s": drain_info["elapsed_s"],',
        '"background_drain_task_count": drain_info["count"],',
        '"background_drain_timed_out": drain_info["timed_out"],',
    ):
        assert field in src, f"missing additive row field: {field}"


def test_report_never_silently_merges_or_drops_timed_out_trials():
    src = _source()
    report_idx = src.index("def report(")
    gate_idx = src.index("def _gate_verdict(")
    report_body = src[report_idx:gate_idx]
    assert "[completed only]" in report_body
    assert "[timed-out]" in report_body
    assert "[all, incl. timeouts]" in report_body
    # The completed-only latency view must actually EXCLUDE timed-out rows,
    # not just be a relabeling of the same list.
    completed_idx = report_body.index(
        "completed = [r for r in arm_rows if not r.get(\"timed_out\") and not r.get(\"error\")]"
    )
    assert completed_idx >= 0


def test_gate_verdict_is_still_completely_untouched_by_round_2():
    """Re-asserted after the phase-split round: _gate_verdict() must not
    have grown any reference to the new phase-timing fields either."""
    src = _source()
    gate_idx = src.index("def _gate_verdict(")
    gate_body = src[gate_idx:]
    for new_field in (
        "foreground_elapsed_s", "cancellation_cleanup_s",
        "background_drain_s", "timeout_requested_s",
    ):
        assert new_field not in gate_body, (
            f"{new_field} leaked into the pre-registered gate computation -- "
            "this follow-up must stay reporting-only"
        )


def test_finding_2_diagnostic_does_not_rewrite_the_historical_gate():
    src = _source()
    report_idx = src.index("def report(")
    gate_idx = src.index("def _gate_verdict(")
    assert "honest_failure_retried_diagnostic" in src[report_idx:gate_idx]
    assert "honest_failure_retried_diagnostic" not in src[gate_idx:]
    assert (
        'eligible = [r for r in c_rows if r.get("chart_attempted") '
        'and not r.get("chart_executed")]'
    ) in src[gate_idx:]
