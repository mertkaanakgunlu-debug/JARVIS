"""jarvis/cli.py -- _handle_confirmation_cli must not leak a second
same-turn confirmation marker as plain text (review remediation).

Before this fix, resume_and_stream() yielding a fresh __jarvis_confirm__
marker (a second confirmable tool call in the resumed turn) was printed
verbatim as JARVIS's reply via _print_jarvis(), leaving the graph
permanently interrupted with no CLI path left to resume it.
"""
from __future__ import annotations

import json

import pytest

from jarvis import cli


class _FakeAgent:
    def __init__(self, streams):
        self._streams = list(streams)
        self.calls: list[tuple[str, str]] = []
        self.current_model_label = "test-model"

    async def resume_and_stream(self, conf_id, decision):
        self.calls.append((conf_id, decision))
        for token in self._streams.pop(0):
            yield token


def _marker(conf_id: str, tool: str) -> str:
    return json.dumps({
        "__jarvis_confirm__": True, "id": conf_id,
        "payload": {"tools": [{"name": tool, "description": tool}]},
    })


@pytest.mark.asyncio
async def test_second_interrupt_reprompts_instead_of_printing_raw_marker(monkeypatch):
    agent = _FakeAgent([
        [_marker("conf-2", "google_calendar")],  # first resume hits a SECOND confirmable action
        ["Done."],  # second resume completes normally
    ])
    prompts = iter(["y", "y"])
    monkeypatch.setattr(cli.Prompt, "ask", lambda *a, **k: next(prompts))
    printed: list[str] = []
    monkeypatch.setattr(cli, "_print_jarvis", lambda text, label: printed.append(text))

    await cli._handle_confirmation_cli(
        agent, "conf-1", {"tools": [{"name": "gmail", "description": "send"}]}
    )

    assert agent.calls == [("conf-1", "approve"), ("conf-2", "approve")]
    assert printed == ["Done."]
    assert not any("__jarvis_confirm__" in p for p in printed)


@pytest.mark.asyncio
async def test_single_interrupt_still_prints_response_normally(monkeypatch):
    """No regression for the common case: no second interrupt at all."""
    agent = _FakeAgent([["Sent."]])
    monkeypatch.setattr(cli.Prompt, "ask", lambda *a, **k: "y")
    printed: list[str] = []
    monkeypatch.setattr(cli, "_print_jarvis", lambda text, label: printed.append(text))

    await cli._handle_confirmation_cli(
        agent, "conf-1", {"tools": [{"name": "gmail", "description": "send"}]}
    )

    assert agent.calls == [("conf-1", "approve")]
    assert printed == ["Sent."]


@pytest.mark.asyncio
async def test_deny_reason_is_forwarded_as_decision(monkeypatch):
    agent = _FakeAgent([["Acknowledged."]])
    monkeypatch.setattr(cli.Prompt, "ask", lambda *a, **k: "not now")
    monkeypatch.setattr(cli, "_print_jarvis", lambda text, label: None)

    await cli._handle_confirmation_cli(
        agent, "conf-1", {"tools": [{"name": "gmail", "description": "send"}]}
    )

    assert agent.calls == [("conf-1", "deny:not now")]
