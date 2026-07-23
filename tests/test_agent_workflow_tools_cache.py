"""jarvis/agent.py's JarvisAgent.get_workflow_tools() -- review remediation.

Before this, the API's POST /workflow/{id}/resolve and the CLI's
/workflow approve|deny command each called
make_tools(workspace, settings, memory) fresh on every single approval
request, rebuilding all ~38 tool closures (and each one's inferred
pydantic args schema) synchronously, just to dispatch the plan's one or
two remaining steps. Safe to cache for the agent's whole lifetime:
workspace/settings/memory are each assigned exactly once in __init__ and
never reassigned afterward.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from jarvis.agent import JarvisAgent
from jarvis.config import Settings


def _bare_agent(tmp_path):
    agent = JarvisAgent.__new__(JarvisAgent)
    agent.workspace = tmp_path
    agent.settings = Settings(_env_file=None)
    agent.memory = MagicMock()
    agent._workflow_tools_cache = None
    return agent


def test_get_workflow_tools_builds_once_and_reuses_the_cache(tmp_path):
    agent = _bare_agent(tmp_path)

    with patch("jarvis.graph.tools.make_tools") as fake_make_tools:
        fake_make_tools.return_value = ["tool-a", "tool-b"]
        first = agent.get_workflow_tools()
        second = agent.get_workflow_tools()
        third = agent.get_workflow_tools()

    assert first is second is third  # same cached list object, never rebuilt
    assert first == ["tool-a", "tool-b"]
    fake_make_tools.assert_called_once_with(agent.workspace, agent.settings, agent.memory)


def test_get_workflow_tools_returns_real_tools(tmp_path):
    """No regression: a fresh agent with no cache primed still gets the
    real make_tools() output, not an empty/stub list."""
    agent = _bare_agent(tmp_path)

    tools = agent.get_workflow_tools()

    assert tools
    assert any(t.name == "workflow_start" for t in tools)
