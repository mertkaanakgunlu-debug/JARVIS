"""Post-MVP Faz 1 -- declared_artifacts_exist, from the check to the verdict.

The point of this phase is that a tool claiming "written" and a file being
on disk are two different facts, and until now the second one could not be
established for any tool whose output path is not one of its arguments. The
critical assertion in this file is the middle branch: a DECLARED artifact
that is NOT on disk must turn the tool's self-reported success into
"reported_success_verification_failed", which is what summary.py surfaces to
the user. The honesty discipline (no declarations => "unverified", never a
free "verified") is asserted just as explicitly, because that is the branch
a future refactor is most likely to "simplify" into silence.
"""
from __future__ import annotations

import pytest

from jarvis.execution.artifacts import ArtifactRef
from jarvis.execution.envelope import build_shadow_envelope
from jarvis.execution.postcondition import PostconditionSpec
from jarvis.execution.postcondition_runner import run_postconditions
from jarvis.execution.summary import build_verified_summary

SPEC = PostconditionSpec(kind="declared_artifacts_exist", source="tool_contract")


def _run(artifacts, *, workspace=None):
    return run_postconditions(
        (SPEC,), workspace=workspace, args={}, tool_result_content="", artifacts=artifacts,
    )[0]


# ── the three branches ─────────────────────────────────────────────────────

def test_declared_and_present_is_verified(tmp_path):
    target = tmp_path / "chart.png"
    target.write_bytes(b"png")
    result = _run((ArtifactRef(path=str(target), kind="chart"),))
    assert result.status == "verified"
    assert str(target) in result.detail


def test_declared_but_absent_is_failed(tmp_path):
    missing = tmp_path / "never-written.png"
    result = _run((ArtifactRef(path=str(missing), kind="chart"),))
    assert result.status == "failed"
    assert "not on disk" in result.detail
    assert str(missing) in result.detail


def test_nothing_declared_is_unverified_never_verified():
    # A tool that declared nothing tells us nothing. Passing here would hand
    # every read-only action of a multi-action tool a badge it did not earn.
    result = _run(())
    assert result.status == "unverified"
    assert "declared no artifacts" in result.detail


def test_one_missing_among_several_fails_the_whole_check(tmp_path):
    present = tmp_path / "book.xlsx"
    present.write_bytes(b"xlsx")
    missing = tmp_path / "book.png"
    result = _run((
        ArtifactRef(path=str(present), kind="workbook"),
        ArtifactRef(path=str(missing), kind="chart"),
    ))
    assert result.status == "failed"
    assert str(missing) in result.detail
    assert "book.xlsx" not in result.detail.split("not on disk")[-1]


# ── path resolution ────────────────────────────────────────────────────────

def test_a_relative_path_resolves_against_the_workspace(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "r.tex").write_text("x", encoding="utf-8")
    result = _run((ArtifactRef(path="sub/r.tex", kind="report_source"),), workspace=tmp_path)
    assert result.status == "verified"


def test_a_relative_path_without_a_workspace_is_unverified_not_guessed():
    result = _run((ArtifactRef(path="sub/r.tex", kind="report_source"),), workspace=None)
    assert result.status == "unverified"
    assert "needs a workspace" in result.detail


def test_a_directory_is_not_a_file(tmp_path):
    d = tmp_path / "plots"
    d.mkdir()
    assert _run((ArtifactRef(path=str(d), kind="chart"),)).status == "failed"


def test_a_blank_declared_path_yields_unverified():
    class _Blank:
        path = ""
    assert _run((_Blank(),)).status == "unverified"


def test_the_runner_still_never_raises(tmp_path):
    class _Exploding:
        @property
        def path(self):
            raise RuntimeError("bad ref")

    result = _run((_Exploding(),))
    assert result.status == "unverified"
    assert "runner error" in result.detail


# ── through the envelope into the user-visible verdict ─────────────────────

def _envelope(ok: bool, artifacts: list[str], postconditions: list) -> dict:
    return build_shadow_envelope(
        tool_name="plot_data", args={}, ok=ok, content="C:/out/chart.png",
        retryable=False, error_code=None, execution_id="call-1",
        postconditions=postconditions, artifacts=artifacts,
    ).model_dump()


def test_a_missing_artifact_downgrades_a_reported_success(tmp_path):
    missing = tmp_path / "chart.png"
    checks = run_postconditions(
        (SPEC,), workspace=None, args={}, tool_result_content="",
        artifacts=(ArtifactRef(path=str(missing), kind="chart"),),
    )
    summary = build_verified_summary([_envelope(True, [str(missing)], checks)])
    op = summary.operations[0]
    assert op.tool_status == "success"          # the tool said it worked
    assert op.postcondition_verdict == "failed"  # the disk disagreed
    assert op.display_status == "reported_success_verification_failed"
    assert summary.any_failed is True           # this is what reaches the user


def test_a_present_artifact_confirms_the_operation(tmp_path):
    present = tmp_path / "chart.png"
    present.write_bytes(b"png")
    checks = run_postconditions(
        (SPEC,), workspace=None, args={}, tool_result_content="",
        artifacts=(ArtifactRef(path=str(present), kind="chart"),),
    )
    summary = build_verified_summary([_envelope(True, [str(present)], checks)])
    assert summary.operations[0].display_status == "confirmed"
    assert summary.any_failed is False


def test_no_declaration_leaves_the_operation_merely_unverified():
    checks = run_postconditions(
        (SPEC,), workspace=None, args={}, tool_result_content="", artifacts=(),
    )
    summary = build_verified_summary([_envelope(True, [], checks)])
    # Honest middle ground: not confirmed, but not a failure either -- and
    # display-identical to the "not_applicable" this tool reported before
    # the phase, so no read-only action regressed.
    assert summary.operations[0].display_status == "reported_success_unverified"
    assert summary.any_failed is False


def test_the_envelope_carries_the_declared_paths():
    env = _envelope(True, ["C:/out/chart.png", "C:/out/book.xlsx"], [])
    assert env["artifacts"] == ["C:/out/chart.png", "C:/out/book.xlsx"]


# ── registry wiring ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "tool", ["plot_data", "report_write", "report_compile", "report_compose", "finance"]
)
def test_every_artifact_tool_declares_the_postcondition(tool):
    from jarvis.tool_registry import get_spec

    kinds = [p.kind for p in get_spec(tool).postconditions]
    assert "declared_artifacts_exist" in kinds


def test_file_write_keeps_its_own_arg_based_postconditions():
    """file_write's path IS an argument, so it stays on the stronger,
    args-resolved checks rather than being folded into the new kind."""
    from jarvis.tool_registry import get_spec

    kinds = [p.kind for p in get_spec("file_write").postconditions]
    assert kinds == ["file_exists", "path_within_workspace"]


def test_the_shared_spec_is_required_severity():
    """A declared-but-absent artifact must be able to contradict the tool.
    At "warning" severity it could not -- _postcondition_verdict ignores
    non-required results by design."""
    from jarvis.tool_registry import get_spec

    for spec in get_spec("plot_data").postconditions:
        if spec.kind == "declared_artifacts_exist":
            assert spec.severity == "required"


# ── tool_result_accounting reads ToolMessage.artifact ──────────────────────

async def test_accounting_reads_the_declarations_off_the_tool_message(tmp_path):
    from langchain_core.messages import AIMessage, ToolMessage

    from jarvis.config import Settings
    from jarvis.graph.tool_accounting import make_tool_result_accounting_node

    written = tmp_path / "chart.png"
    written.write_bytes(b"png")
    ai = AIMessage(content="", tool_calls=[{"name": "plot_data", "args": {}, "id": "c1"}])
    tm = ToolMessage(
        content=str(written), tool_call_id="c1", name="plot_data",
        artifact=[{"path": str(written), "kind": "chart", "produced_by": "plot_data"}],
    )
    node = make_tool_result_accounting_node(
        settings=Settings(_env_file=None, execution_contract_mode="shadow"),
        workspace=tmp_path,
    )
    out = await node({"messages": [ai, tm], "execution_envelopes": []})
    env = out["execution_envelopes"][0]
    assert env["artifacts"] == [str(written)]
    assert [p["status"] for p in env["postconditions"]] == ["verified"]


async def test_accounting_reports_failure_for_a_declared_but_absent_file(tmp_path):
    from langchain_core.messages import AIMessage, ToolMessage

    from jarvis.config import Settings
    from jarvis.graph.tool_accounting import make_tool_result_accounting_node

    missing = tmp_path / "never.png"
    ai = AIMessage(content="", tool_calls=[{"name": "plot_data", "args": {}, "id": "c1"}])
    tm = ToolMessage(
        content=str(missing), tool_call_id="c1", name="plot_data",
        artifact=[{"path": str(missing), "kind": "chart"}],
    )
    node = make_tool_result_accounting_node(
        settings=Settings(_env_file=None, execution_contract_mode="shadow"),
        workspace=tmp_path,
    )
    out = await node({"messages": [ai, tm], "execution_envelopes": []})
    env = out["execution_envelopes"][0]
    summary = build_verified_summary([env])
    assert summary.operations[0].display_status == "reported_success_verification_failed"


async def test_off_mode_still_builds_no_envelopes_at_all(tmp_path):
    from langchain_core.messages import AIMessage, ToolMessage

    from jarvis.config import Settings
    from jarvis.graph.tool_accounting import make_tool_result_accounting_node

    ai = AIMessage(content="", tool_calls=[{"name": "plot_data", "args": {}, "id": "c1"}])
    tm = ToolMessage(content="x", tool_call_id="c1", name="plot_data",
                     artifact=[{"path": "x.png", "kind": "chart"}])
    node = make_tool_result_accounting_node(
        settings=Settings(_env_file=None, execution_contract_mode="off"), workspace=tmp_path,
    )
    out = await node({"messages": [ai, tm], "execution_envelopes": []})
    assert "execution_envelopes" not in out
