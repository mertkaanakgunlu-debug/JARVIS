"""jarvis/agent.py's _HudEventCallback -- Faz 7 of the GPT-5.6 review
remediation plan (verdict #18, CONFIRMED): on_llm_end used to label the raw
output-token *count* as "tok/s" with no duration measured anywhere --
`spd = f" ; {to_:,} tok/s"` where to_ is just usage_metadata's output_tokens.
Now on_llm_start timestamps the run_id and on_llm_end divides by actual
elapsed wall-clock time.
"""
from __future__ import annotations

from types import SimpleNamespace

import jarvis.agent as agent_mod
from jarvis.agent import _HudEventCallback


class _FakeEventBus:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def tool_call(self, body: str, kind: str = "tool") -> None:
        self.calls.append((body, kind))


def _fake_response(output_tokens: int, input_tokens: int = 5, model_name: str = "gemini-2.5-flash"):
    message = SimpleNamespace(
        usage_metadata={"input_tokens": input_tokens, "output_tokens": output_tokens},
        response_metadata={"model_name": model_name},
    )
    generation = SimpleNamespace(message=message)
    return SimpleNamespace(generations=[[generation]])


def test_tok_per_sec_divides_by_actual_elapsed_time(monkeypatch):
    fake_bus = _FakeEventBus()
    monkeypatch.setattr(agent_mod, "event_bus", fake_bus)

    clock = iter([100.0, 102.0])  # on_llm_start, then on_llm_end -- 2s elapsed
    monkeypatch.setattr("time.monotonic", lambda: next(clock))

    cb = _HudEventCallback("cli-text")
    cb.on_llm_start({"kwargs": {"model": "gemini-2.5-flash"}}, ["prompt"], run_id="run-1")
    cb.on_llm_end(_fake_response(output_tokens=20), run_id="run-1")

    body, kind = fake_bus.calls[-1]
    assert "10.0 tok/s" in body, body
    # The old bug: the raw output token count alone rendered as "20 tok/s".
    assert "20 tok/s" not in body


def test_tok_per_sec_omitted_when_start_time_unknown(monkeypatch):
    """on_llm_end fired without a matching on_llm_start (e.g. a run_id that
    was never tracked) must not crash or fabricate a rate -- just drop the
    tok/s suffix."""
    fake_bus = _FakeEventBus()
    monkeypatch.setattr(agent_mod, "event_bus", fake_bus)

    cb = _HudEventCallback("cli-text")
    cb.on_llm_end(_fake_response(output_tokens=20), run_id="never-started")

    body, kind = fake_bus.calls[-1]
    assert "tok/s" not in body
    assert "20 out" in body


def test_concurrent_runs_track_independent_start_times(monkeypatch):
    """Two overlapping LLM calls (fast + reasoning role) must each get their
    own elapsed time, not share one global timestamp."""
    fake_bus = _FakeEventBus()
    monkeypatch.setattr(agent_mod, "event_bus", fake_bus)

    times = {"run-a": [10.0, 15.0], "run-b": [11.0, 13.0]}
    calls_per_run = {"run-a": 0, "run-b": 0}

    def fake_monotonic():
        # Not directly keyed by run_id (monotonic takes no args) -- instead
        # drive from a flat sequence matching call order below.
        return sequence.pop(0)

    sequence = [10.0, 11.0, 15.0, 13.0]  # start-a, start-b, end-a, end-b
    monkeypatch.setattr("time.monotonic", fake_monotonic)

    cb = _HudEventCallback("cli-text")
    cb.on_llm_start({}, [], run_id="run-a")
    cb.on_llm_start({}, [], run_id="run-b")
    cb.on_llm_end(_fake_response(output_tokens=50), run_id="run-a")   # 5s -> 10.0 tok/s
    cb.on_llm_end(_fake_response(output_tokens=20), run_id="run-b")   # 2s -> 10.0 tok/s

    bodies = [c[0] for c in fake_bus.calls]
    assert "10.0 tok/s" in bodies[-2]  # run-a: 50 tokens / 5s
    assert "10.0 tok/s" in bodies[-1]  # run-b: 20 tokens / 2s
