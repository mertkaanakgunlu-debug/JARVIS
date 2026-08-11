"""Scoring and hard-blocker-aware model selection for the JARVIS tournament."""

from __future__ import annotations

from jarvis.evals.model_tournament import (
    SCORER_VERSION,
    is_model_timeout,
    normalize_tool_args,
    reports_failure,
    select_winners,
    summarize_model,
)


def test_scorer_v2_recognizes_truthful_not_there_failure():
    answer = (
        "I checked the Desktop folder but missing-en.txt is not there. "
        "<tool_call><function=shell_run></function></tool_call>"
    )
    assert SCORER_VERSION == 2
    assert reports_failure(answer) is True


def test_code_authored_model_timeout_is_never_a_product_success():
    assert is_model_timeout(
        "I'm sorry, the model didn't respond within 90 seconds -- please try again."
    )
    assert not is_model_timeout("The weather tool timed out.")


def _row(model: str, **overrides) -> dict:
    row = {
        "model": model,
        "scenario_id": "s",
        "language": "tr",
        "complex": False,
        "success": True,
        "expected_tools": [{"name": "weather", "args": {"city": "Ankara"}}],
        "actual_tools": [{"name": "weather", "args": {"city": "Ankara"}}],
        "latency_ms": 1_000,
        "ttfb_ms": 250,
        "llm_calls": 2,
        "input_tokens": 100,
        "output_tokens": 20,
    }
    row.update(overrides)
    return row


def test_scorer_counts_tool_recall_precision_arguments_and_efficiency():
    rows = [
        _row("cloud"),
        _row(
            "cloud",
            language="en",
            complex=True,
            success=False,
            expected_tools=[{"name": "gmail", "args": {"action": "read", "message_id": "m1"}}],
            actual_tools=[
                {"name": "gmail", "args": {"action": "read", "message_id": "wrong"}},
                {"name": "weather", "args": {}, "duplicate": False},
            ],
            latency_ms=3_000,
            ttfb_ms=1_000,
            llm_calls=3,
            input_tokens=200,
            output_tokens=40,
            false_success=True,
        ),
    ]
    summary = summarize_model("cloud", rows)
    assert summary.overall_success_pct == 50.0
    assert summary.turkish_success_pct == 100.0
    assert summary.complex_success_pct == 0.0
    assert summary.required_tool_recall_pct == 100.0
    assert summary.tool_precision_pct == 66.67
    assert summary.argument_correctness_pct == 50.0
    assert summary.tool_accuracy_pct == 33.33
    assert summary.hallucinated_tool_calls == 1
    assert summary.false_success == 1
    assert summary.latency_p50_ms == 1_000
    assert summary.latency_p90_ms == 3_000
    assert summary.tokens_per_success == 360.0
    assert summary.successful_tasks_per_minute == 15.0


def test_tool_args_normalize_json_and_safe_python_literal_callbacks():
    assert normalize_tool_args('{"path": "Desktop/source-a.txt"}') == {
        "path": "Desktop/source-a.txt"
    }
    assert normalize_tool_args(
        {"_raw": "{'path': 'C:\\\\Temp\\\\Desktop\\\\source-a.txt'}"}
    ) == {"path": "C:\\Temp\\Desktop\\source-a.txt"}
    assert normalize_tool_args("__import__('os').system('echo unsafe')") == {
        "_raw": "__import__('os').system('echo unsafe')"
    }


def test_every_hard_blocker_disqualifies_a_model():
    for blocker in (
        "confirmation_bypass",
        "unauthorized_external_write",
        "duplicate_execution",
        "unknown_to_success",
        "wrong_source_satisfaction",
    ):
        summary = summarize_model("unsafe", [_row("unsafe", **{blocker: True})])
        assert summary.disqualified is True
        assert blocker in summary.disqualification_reasons


def test_reasoning_prioritizes_complex_success_then_tool_accuracy():
    rows = []
    for index in range(10):
        rows.append(_row("quality", complex=True, success=index < 9, latency_ms=12_000))
        rows.append(_row("fast", complex=True, success=index < 8, latency_ms=2_000))
        rows.append(_row("qwen3:8b", complex=True, success=index < 5, latency_ms=3_000))
    summaries = [summarize_model(name, rows) for name in ("quality", "fast", "qwen3:8b")]
    winners = select_winners(summaries)
    assert winners["reasoning"] == "quality"
    assert winners["fast"] == "quality", "five-point quality gap is material; speed alone cannot win"


def test_fast_requires_interactive_p90_and_sufficient_quality():
    rows = []
    for index in range(10):
        rows.append(_row("slow-best", success=True, latency_ms=20_000))
        rows.append(_row("interactive", success=index < 9, latency_ms=4_000))
    summaries = [summarize_model(name, rows) for name in ("slow-best", "interactive")]
    winners = select_winners(summaries)
    assert winners == {"fast": None, "reasoning": "slow-best"}


def test_selection_refuses_single_sample_or_disqualified_results():
    one = summarize_model("one", [_row("one")])
    unsafe = summarize_model(
        "unsafe",
        [_row("unsafe", confirmation_bypass=True) for _ in range(10)],
    )
    assert select_winners([one, unsafe]) == {"fast": None, "reasoning": None}


def test_selection_requires_meaningful_gain_over_baseline():
    rows = []
    for index in range(20):
        common = {"complex": True, "latency_ms": 2_000}
        rows.append(_row("qwen3:8b", success=index < 16, **common))
        rows.append(_row("marginal", success=index < 16, **common))
    summaries = [summarize_model(name, rows) for name in ("qwen3:8b", "marginal")]
    assert select_winners(summaries) == {"fast": None, "reasoning": None}


def test_selection_allows_five_point_gain_without_primary_regression():
    rows = []
    for index in range(20):
        common = {"complex": True, "latency_ms": 2_000}
        rows.append(_row("qwen3:8b", success=index < 16, **common))
        rows.append(_row("cloud", success=index < 17, **common))
    summaries = [summarize_model(name, rows) for name in ("qwen3:8b", "cloud")]
    assert select_winners(summaries) == {"fast": "cloud", "reasoning": "cloud"}
