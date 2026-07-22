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
    events = audit_log.tail(10)
    mine = [e for e in events if e.get("workflow_id") == plan.workflow_id]
    assert mine and mine[-1]["outcome"] == "user_approved"
    assert mine[-1]["transport"] == "api"
    assert mine[-1]["step_id"] == "s1"


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
    mine = [e for e in audit_log.tail(10) if e.get("workflow_id") == plan.workflow_id]
    assert mine and mine[-1]["outcome"] == "user_denied"
    assert mine[-1]["reason"] == "not today"


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
    mine = [e for e in audit_log.tail(10) if e.get("workflow_id") == plan.workflow_id]
    assert mine and mine[-1]["outcome"] == "blocked_stale_approval"

    # The human answers the fresh request -- now it executes.
    outcome2 = await resolve_workflow_approval(
        plan.workflow_id, "approve",
        tools=[gmail], settings=settings, workspace=tmp_path, transport="cli",
    )
    assert outcome2.ok is True
    assert len(gmail.calls) == 1
    assert outcome2.plan.status == "succeeded"


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

    assert client.get("/workflow/wf-does-not-exist").status_code == 404


def test_workflow_resolve_endpoint_rejects_unknown_id_and_bad_decision(
    isolated_cwd, monkeypatch
):
    class _AgentStub:
        workspace = None
        settings = Settings(_env_file=None)
        memory = object()

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
