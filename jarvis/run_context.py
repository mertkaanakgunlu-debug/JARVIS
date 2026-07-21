"""RunContext -- Agent Runtime rev.2, Faz 5 (isolation & reproducibility).

Reviewer #8's scope separation: user · conversation · workflow · run/turn ·
execution · artifact. user/conversation/workflow already exist under other
names in this codebase (JARVIS_HOME, session_id, ToolRoute's domain); this
module gives the two that didn't -- run/turn and artifact -- a real,
testable identity instead of ad hoc string-building at each call site.

Two constructors for the two shapes this codebase actually has today:

  for_turn()      -- ties a run to a specific (session_id, turn). The
                     natural identity for anything computed at the agent/
                     graph level, where session_id/turn are already in
                     scope (e.g. the run manifest written from
                     JarvisAgent.chat()). Deliberately reuses the EXACT
                     string LangGraph's own thread_id already uses
                     ("{session_id}-t{turn}", see agent.py) rather than a
                     parallel ID scheme, so a run_id and its graph
                     checkpoint are always trivially correlatable.

  for_execution()  -- a fresh, unique run for a single call that has no
                     access to per-turn state. Native @tool closures (see
                     jarvis/graph/tools.py) are bound ONCE per process by
                     make_tools()/build_graph(), not rebuilt per turn --
                     giving them genuine per-TURN identity would need
                     LangGraph's InjectedState/InjectedToolCallId
                     machinery, not introduced anywhere in this codebase
                     yet and out of scope for this phase (a real
                     architecture change, not a narrow fix). A fresh
                     per-CALL run_id is the honest, low-risk alternative:
                     it still gives every artifact-producing call its own
                     collision-proof home under data/runs/, which is the
                     concrete bug this phase's own motivating example
                     names ("plot.png collides") -- it just does not (yet)
                     group multiple calls from the same turn under one
                     shared run_id. plot_data (jarvis/graph/tools.py) is
                     the first, and today the only, consumer.

Neither constructor creates artifact_dir -- callers create it lazily on
first write, the same convention every other output directory in this
codebase already follows.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RunContext:
    run_id: str
    workspace: Path

    @property
    def artifact_dir(self) -> Path:
        return self.workspace / "data" / "runs" / self.run_id

    @classmethod
    def for_turn(cls, workspace: Path, session_id: str, turn: int) -> "RunContext":
        return cls(run_id=f"{session_id}-t{turn}", workspace=workspace)

    @classmethod
    def for_execution(cls, workspace: Path) -> "RunContext":
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return cls(run_id=f"exec-{stamp}-{uuid.uuid4().hex[:8]}", workspace=workspace)


def write_run_manifest(ctx: RunContext, **fields: Any) -> Path:
    """Write run_manifest.json under ctx.artifact_dir for later replay.

    Plan's own field list: model+version, prompt hash, registry version,
    temperature, tool subset, input digest, envelope list. Intentionally
    open-ended (**fields) rather than a fixed schema -- no single call site
    in this codebase holds all of those at once yet (see agent.py's own
    call: prompt hash and a registry version concept do not exist anywhere
    in this codebase today and are honestly NOT included -- documented as
    deferred, not silently omitted).

    Never raises -- same "an auxiliary record can't take down the turn it's
    describing" discipline as audit_log.record()/tool_trace.record(); a
    write failure (disk full, permissions) is swallowed, not propagated.
    """
    import json

    path = ctx.artifact_dir / "run_manifest.json"
    try:
        ctx.artifact_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": ctx.run_id,
            "written_at": datetime.now(timezone.utc).isoformat(),
            **fields,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, default=str, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass
    return path
