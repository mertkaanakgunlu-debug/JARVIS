"""TaskContract -- Agent Runtime rev.2, Faz 1.

A lightweight alternative to a full Action IR (see the plan's section B for
why a full IR was rejected: 36 tools would each need a second schema layer
to maintain, plus an extra LLM step). TaskContract exists to catch the class
of bug a plain args<->output postcondition cannot: B6 (jarvis/graph/tools.py's
plot_data) ran successfully with arguments that were internally consistent
with its own output, but did not match what the user actually asked for --
"correct capability, schema-valid, semantically wrong argument."

Two sources are meant to populate TaskContract.expected_outcomes (see the
plan's "B6'nin gercek sinifi" section):
  - a deterministic extractor over the user's message (catches
    *misunderstanding* -- a contract built from the wrong literals would
    conflict with itself before the tool ever runs)
  - a model-declared contract (catches *binding errors* -- correct
    understanding, wrong parameter slot; B6's actual failure class)

Neither extractor is built yet -- this module only defines the shapes.
Faz 2's prepare_execution node is where a TaskContract first gets attached
to an ExecutionRequest and actually checked against anything.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class ExpectedOutcome(BaseModel):
    outcome_type: Literal[
        "file", "chart", "record", "message", "computation", "listing"
    ]
    params: dict[str, Any] = {}
    source: Literal["user_literal", "model_declared", "tool_contract"]


class TaskContract(BaseModel):
    task_id: str
    user_goal: str
    required_capabilities: list[str] = []
    constraints: dict[str, Any] = {}
    expected_outcomes: list[ExpectedOutcome] = []
    approval_scope: str | None = None
