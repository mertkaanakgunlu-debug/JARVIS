"""Faz 7.3 (P1) -- transport-agnostic workflow approval.

Three layers under test:
  1. jarvis.execution.workflow_approval -- the shared human-only service
     layer (exact decision allowlist, audit decision events, re-approval
     flow after a stale binding).
  2. WorkflowEngine.resolve_approval's re-mint-on-stale-signature behavior
     (simulated process restart: rotate approval's process-local HMAC key)
     -- the workflow must neither execute under the old yes NOR die; it
     must pause again with a freshly signed request.
  3. The API surface: /workflow list/show/resolve endpoints, and the
     structured confirmation_required SSE frame /chat/stream now emits
     instead of leaking chat_stream()'s internal __jarvis_confirm__ marker
     as response text.

Same fake-tool approach as test_workflow_crash_recovery.py (a real gmail
tool would hit the network); get_spec() still resolves the real registry.
"""
from __future__ import annotations

import json
import secrets

import pytest
from starlette.testclient import TestClient

import jarvis.api as api
from jarvis import audit_log
from jarvis.config import Settings
from jarvis.execution import approval, workflow_store
from jarvis.execution.contract import TaskContract
from jarvis.execution.workflow import WorkflowStep
from jarvis.execution.workflow_approval import resolve_workflow_approval
from jarvis.execution.workflow_engine import WorkflowEngine


class _FakeTool:
    def __init__(self, name: str, result: str = "ok -- done"):
        self.name = name
        self.result = result
        self.calls: list[dict] = []

    async def ainvoke(self, args: dict) -> str:
        self.calls.append(args)
        return self.result


def _contract() -> TaskContract:
    return TaskContract(task_id="t-appr", user_goal="approval service test")


def _gmail_args() -> dict:
    return {"action": "send", "to": "a@b.c", "subject": "s", "body": "b"}


async def _paused_gmail_plan(tools, settings, workspace):
    """Create + advance a one-step gmail plan to its approval pause."""
    engine = WorkflowEngine(tools, settings, workspace)
    plan = engine.create_plan(
        _contract(), [WorkflowStep(step_id="s1", capability="gmail", args=_gmail_args())]
    )
    plan = await engine.advance(plan)
    assert plan.status == "paused_for_approval" and plan.pending_approval_step_id == "s1"
    return plan


# ── Exact decision allowlist ─────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["yes", "", "invalid", "approve please", "ok", "APPROVED!"])
async def test_only_exact_decisions_are_accepted(isolated_cwd, tmp_path, bad):
    gmail = _FakeTool("gmail")
    settings = Settings(_env_file=None, confirmation_gate_enabled=True)
    plan = await _paused_gmail_plan([gmail], settings, tmp_path)

    outcome = await resolve_workflow_approval(
        plan.workflow_id, bad,
        tools=[gmail], settings=settings, workspace=tmp_path, transport="cli",
    )

    assert outcome.ok is False
    assert "invalid decision" in outcome.message
    assert gmail.calls == []  # and NOTHING executed
    # plan untouched -- still approvable afterward
    assert workflow_store.load(plan.workflow_id).status == "paused_for_approval"


@pytest.mark.asyncio
async def test_approve_dispatches_and_audits(isolated_cwd, tmp_path):
    gmail = _FakeTool("gmail", result="Email sent.")
    settings = Settings(_env_file=None, confirmation_gate_enabled=True)
    plan = await _paused_gmail_plan([gmail], settings, tmp_path)

    outcome = await resolve_workflow_approval(
        plan.workflow_id, "approve",
        tools=[gmail], settings=settings, workspace=tmp_path, transport="api",
    )

    assert outcome.ok is True and outcome.reapproval_required is False
    assert len(gmail.calls) == 1
    assert outcome.plan.status == "succeeded"
    mine = [e for e in audit_log.tail(20) if e.get("workflow_id") == plan.workflow_id]
    approved = [e for e in mine if e.get("outcome") == "user_approved"]
    assert approved, mine
    assert approved[-1]["transport"] == "api"
    assert approved[-1]["step_id"] == "s1"
    # Faz 7.3 P1 (audit core): the dispatch itself is audited too, same
    # execution_start/end pair the graph path's callback writes.
    assert any(e["event"] == "execution_start" and e["tool"] == "gmail" for e in mine)
    ends = [e for e in mine if e["event"] == "execution_end"]
    assert ends and ends[-1]["ok"] is True and ends[-1]["transport"] == "api"


@pytest.mark.asyncio
async def test_deny_with_reason_fails_step_and_audits(isolated_cwd, tmp_path):
    gmail = _FakeTool("gmail")
    settings = Settings(_env_file=None, confirmation_gate_enabled=True)
    plan = await _paused_gmail_plan([gmail], settings, tmp_path)

    outcome = await resolve_workflow_approval(
        plan.workflow_id, "deny:not today",
        tools=[gmail], settings=settings, workspace=tmp_path, transport="cli",
    )

    assert outcome.ok is True
    assert gmail.calls == []
    assert outcome.plan.step("s1").status == "failed"
    assert "not today" in outcome.plan.step("s1").error
    mine = [e for e in audit_log.tail(20) if e.get("workflow_id") == plan.workflow_id]
    denied = [e for e in mine if e.get("outcome") == "user_denied"]
    assert denied and denied[-1]["reason"] == "not today"
    assert denied[-1]["transport"] == "cli"


# ── Restart-invalidated signature: re-mint, never execute, never die ─────────

@pytest.mark.asyncio
async def test_stale_signature_reissues_approval_instead_of_failing(
    isolated_cwd, tmp_path, monkeypatch
):
    gmail = _FakeTool("gmail", result="Email sent.")
    settings = Settings(_env_file=None, confirmation_gate_enabled=True)
    plan = await _paused_gmail_plan([gmail], settings, tmp_path)
    old_signature = plan.step("s1").approval_signature

    # Simulated process restart: approval.py's key is process-local, so a
    # new process = a new key = every persisted signature stops verifying.
    monkeypatch.setattr(approval, "_PROCESS_KEY", secrets.token_bytes(32))

    outcome = await resolve_workflow_approval(
        plan.workflow_id, "approve",
        tools=[gmail], settings=settings, workspace=tmp_path, transport="cli",
    )

    assert outcome.ok is False and outcome.reapproval_required is True
    assert gmail.calls == []  # the old yes NEVER executes
    reloaded = workflow_store.load(plan.workflow_id)
    assert reloaded.status == "paused_for_approval"  # not dead -- asking again
    assert reloaded.step("s1").status == "needs_approval"
    assert reloaded.step("s1").approval_signature != old_signature  # freshly signed
    assert "re-approve" in outcome.message
    mine = [e for e in audit_log.tail(20) if e.get("workflow_id") == plan.workflow_id]
    assert any(e.get("outcome") == "blocked_stale_approval" for e in mine)

    # The human answers the fresh request -- now it executes.
    outcome2 = await resolve_workflow_approval(
        plan.workflow_id, "approve",
        tools=[gmail], settings=settings, workspace=tmp_path, transport="cli",
    )
    assert outcome2.ok is True
    assert len(gmail.calls) == 1
    assert outcome2.plan.status == "succeeded"


@pytest.mark.asyncio
async def test_stale_approval_writes_policy_decision_trace(isolated_cwd, tmp_path, monkeypatch):
    """Review remediation: a stale-approval block never dispatches the tool,
    so on_tool_start/end (and tool_trace's own execution rows) never fire --
    before this fix, the engine wrote only the audit_log "decision" row,
    never the tool_trace "policy_decision" row nodes.py's identical
    graph-path stale-approval block writes."""
    from jarvis import tool_trace

    monkeypatch.setenv("JARVIS_TOOL_TRACE", "1")
    gmail = _FakeTool("gmail", result="Email sent.")
    settings = Settings(_env_file=None, confirmation_gate_enabled=True)
    plan = await _paused_gmail_plan([gmail], settings, tmp_path)
    monkeypatch.setattr(approval, "_PROCESS_KEY", secrets.token_bytes(32))

    outcome = await resolve_workflow_approval(
        plan.workflow_id, "approve",
        tools=[gmail], settings=settings, workspace=tmp_path, transport="cli",
    )
    assert outcome.reapproval_required is True

    rows = [r for r in tool_trace.load() if r.get("event") == "policy_decision"]
    assert rows, "expected a policy_decision trace row for the stale-approval block"
    assert rows[-1]["outcome"] == "blocked_stale_approval"
    assert rows[-1]["ok"] is False
    assert rows[-1]["workflow_id"] == plan.workflow_id


# ── API endpoints ────────────────────────────────────────────────────────────

def _client(monkeypatch, agent=None) -> TestClient:
    # Same no-lifespan TestClient pattern as test_api_upload.py: _agent/
    # _settings monkeypatched directly, auth disabled via _settings=None.
    monkeypatch.setattr(api, "_agent", agent if agent is not None else object())
    monkeypatch.setattr(api, "_settings", None)
    return TestClient(api.app)


def test_workflow_list_and_show_endpoints(isolated_cwd, tmp_path, monkeypatch):
    async def _seed():
        gmail = _FakeTool("gmail")
        settings = Settings(_env_file=None, confirmation_gate_enabled=True)
        return await _paused_gmail_plan([gmail], settings, tmp_path)

    import asyncio
    plan = asyncio.run(_seed())
    client = _client(monkeypatch)

    listed = client.get("/workflow")
    assert listed.status_code == 200
    assert any(w["workflow_id"] == plan.workflow_id for w in listed.json()["workflows"])

    shown = client.get(f"/workflow/{plan.workflow_id}")
    assert shown.status_code == 200
    body = shown.json()
    assert body["status"] == "paused_for_approval"
    assert body["pending_approval_step_id"] == "s1"
    assert "Awaiting approval" in body["report"]

    # B1.2a: structured parallel to the report text, same source (plan.steps)
    # -- lets a driver/oracle assert per-step status without regex-parsing
    # the pipe-delimited report table.
    steps = {s["step_id"]: s for s in body["steps"]}
    assert steps["s1"]["capability"] == "gmail"
    assert steps["s1"]["status"] == "needs_approval"

    assert client.get("/workflow/wf-does-not-exist").status_code == 404


def test_workflow_resolve_endpoint_rejects_unknown_id_and_bad_decision(
    isolated_cwd, monkeypatch
):
    class _AgentStub:
        workspace = None
        settings = Settings(_env_file=None)
        memory = object()

        def get_workflow_tools(self):
            return []

    from pathlib import Path
    _AgentStub.workspace = Path(".")
    client = _client(monkeypatch, agent=_AgentStub())

    resp = client.post("/workflow/wf-nope/resolve", json={"decision": "approve"})
    assert resp.status_code == 404

    # bad decision on a real paused workflow would need a full plan; the
    # allowlist itself is covered above -- here just pin the 400 mapping.
    resp2 = client.post("/workflow/wf-nope/resolve", json={"decision": "yes"})
    assert resp2.status_code == 400
    assert "invalid decision" in resp2.json()["detail"]


def test_workflow_resolve_endpoint_returns_400_when_not_awaiting_approval(
    isolated_cwd, tmp_path, monkeypatch
):
    """Review remediation: a workflow that isn't paused for approval (e.g.
    already succeeded) must return a 4xx, not a 200 with ok:false silently
    buried in the body. outcome.plan IS populated for this outcome (unlike
    the not-found/invalid-decision cases above) -- gating the error branch
    on `outcome.plan is None` let this specific failure fall through as an
    ordinary 200, mirroring the identical bug this fix closes in cli.py's
    /workflow approve|deny command."""
    async def _seed():
        writer = _FakeTool("file_write", result="written")
        settings = Settings(_env_file=None, confirmation_gate_enabled=False)
        engine = WorkflowEngine([writer], settings, tmp_path)
        plan = engine.create_plan(
            _contract(),
            [WorkflowStep(step_id="s1", capability="file_write", args={"path": "a.txt", "content": "x"})],
        )
        plan = await engine.advance(plan)
        assert plan.status == "succeeded"
        return plan

    import asyncio
    plan = asyncio.run(_seed())

    class _AgentStub:
        workspace = tmp_path
        settings = Settings(_env_file=None)
        memory = object()

        def get_workflow_tools(self):
            return []

    client = _client(monkeypatch, agent=_AgentStub())
    resp = client.post(f"/workflow/{plan.workflow_id}/resolve", json={"decision": "approve"})

    assert resp.status_code == 400
    assert "is not awaiting approval" in resp.json()["detail"]


# ── Structured SSE confirmation frame ────────────────────────────────────────

def test_chat_stream_emits_structured_confirmation_frame(isolated_cwd, monkeypatch):
    marker = json.dumps({
        "__jarvis_confirm__": True, "id": "conf-123",
        "payload": {"tools": [{"name": "gmail", "description": "send an email"}]},
    })

    class _ConfirmingAgent:
        session_id = "s"

        async def chat_stream(self, *a, **k):
            yield "Let me check that. "
            yield marker

    client = _client(monkeypatch, agent=_ConfirmingAgent())
    resp = client.post("/chat/stream", json={"message": "send the mail"})

    assert resp.status_code == 200
    body = resp.text
    assert "__jarvis_confirm__" not in body  # internal marker never leaks
    frame_lines = [
        line for line in body.splitlines()
        if line.startswith("data: {") and "confirmation_required" in line
    ]
    assert frame_lines, body
    frame = json.loads(frame_lines[0][len("data: "):])
    assert frame == {
        "type": "confirmation_required",
        "id": "conf-123",
        "payload": {"tools": [{"name": "gmail", "description": "send an email"}]},
    }
    assert "data: [DONE]" in body


def test_chat_confirm_emits_structured_confirmation_frame_for_a_second_interrupt(
    isolated_cwd, monkeypatch,
):
    """Review remediation: /chat/confirm's resume_and_stream() consumer was
    the one SSE endpoint this diff's marker-reframing fix missed -- a second,
    different confirmable action in the same resumed turn used to leak the
    raw __jarvis_confirm__ JSON as response text. Same fixture shape as
    test_chat_stream_emits_structured_confirmation_frame above, but for the
    resume endpoint."""
    marker = json.dumps({
        "__jarvis_confirm__": True, "id": "conf-456",
        "conversation_id": "conv-mobile", "expires_in_seconds": 300,
        "payload": {"tools": [{"name": "google_calendar", "description": "delete an event"}]},
    })

    resume_calls = []

    class _ConfirmingAgent:
        session_id = "s"

        async def resume_and_stream(self, conf_id, decision, *,
                                    pre_claimed=None, conversation_id=""):
            resume_calls.append((conf_id, decision, conversation_id))
            yield "Okay, one more thing. "
            yield marker

    client = _client(monkeypatch, agent=_ConfirmingAgent())
    resp = client.post(
        "/chat/confirm/conf-123",
        json={"decision": "approve", "conversation_id": "conv-mobile"},
    )

    assert resp.status_code == 200
    body = resp.text
    assert "__jarvis_confirm__" not in body  # internal marker never leaks
    frame_lines = [
        line for line in body.splitlines()
        if line.startswith("data: {") and "confirmation_required" in line
    ]
    assert frame_lines, body
    frame = json.loads(frame_lines[0][len("data: "):])
    assert frame == {
        "type": "confirmation_required",
        "id": "conf-456",
        "conversation_id": "conv-mobile",
        "expires_in_seconds": 300,
        "payload": {"tools": [{"name": "google_calendar", "description": "delete an event"}]},
    }
    assert resume_calls == [("conf-123", "approve", "conv-mobile")]
    assert "data: [DONE]" in body
