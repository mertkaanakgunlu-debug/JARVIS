"""Faz 1.4 — history-echo / claim-on-failure guard.

The bare composer (make_compose_node) can echo a PRIOR turn's "dosyayı
oluşturdum" answer — it survives in the compacted history — without any tool
running this turn, or gloss a tool that FAILED this turn as success (the B6
hallucination). D13b showed this live: the kill-switch turn reused the previous
answer instead of re-issuing the shell call.

Guard: a tool-routed turn with no successful completion this turn
(completed_tool_fingerprints is reset per turn) gets a node-local SystemMessage
forbidding any completion claim. It must never leak back into graph state.
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import jarvis.providers as providers
from jarvis.graph.nodes import make_compose_node
from jarvis.graph.tool_router import ToolRoute

GUARD_MARK = "No tool completed successfully in THIS turn"


class _FakeLLM:
    def __init__(self, captured: dict):
        self._captured = captured

    async def ainvoke(self, messages):
        self._captured["messages"] = list(messages)
        return AIMessage(content="ok")


def _patch_llm(monkeypatch) -> dict:
    captured: dict = {}
    monkeypatch.setattr(providers, "get_llm", lambda role, settings=None, **k: _FakeLLM(captured))
    return captured


def _state(route: ToolRoute | None, completed: list):
    return {
        "messages": [HumanMessage(content="aynı komutu tekrar çalıştır")],
        "tool_route": route.to_dict() if route else None,
        "completed_tool_fingerprints": completed,
        "use_pro_agent": False,
    }


def _has_guard(msgs) -> bool:
    return any(isinstance(m, SystemMessage) and GUARD_MARK in m.content for m in msgs)


async def test_guard_fires_when_tool_routed_and_nothing_succeeded(monkeypatch):
    captured = _patch_llm(monkeypatch)
    node = make_compose_node(settings=None)
    await node(_state(ToolRoute("system", ["system"], 1.0, False), []))
    assert _has_guard(captured["messages"])


async def test_guard_absent_when_a_tool_succeeded(monkeypatch):
    captured = _patch_llm(monkeypatch)
    node = make_compose_node(settings=None)
    await node(_state(ToolRoute("system", ["system"], 1.0, False), ["fp-abc"]))
    assert not _has_guard(captured["messages"])


async def test_guard_absent_for_conversation_route(monkeypatch):
    captured = _patch_llm(monkeypatch)
    node = make_compose_node(settings=None)
    await node(_state(ToolRoute("conversation", ["conversation"], 1.0, False), []))
    assert not _has_guard(captured["messages"])


async def test_guard_absent_when_route_missing(monkeypatch):
    # background/proactive paths or old checkpoints without a route ⇒ no guard
    captured = _patch_llm(monkeypatch)
    node = make_compose_node(settings=None)
    await node(_state(None, []))
    assert not _has_guard(captured["messages"])


async def test_guard_stays_node_local(monkeypatch):
    captured = _patch_llm(monkeypatch)
    node = make_compose_node(settings=None)
    out = await node(_state(ToolRoute("system", ["system"], 1.0, False), []))
    assert all(
        not (isinstance(m, SystemMessage) and GUARD_MARK in m.content)
        for m in out["messages"]
    )
