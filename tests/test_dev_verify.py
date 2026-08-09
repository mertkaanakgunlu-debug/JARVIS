"""`scripts/dev_verify.py` — the targeted development verification selector.

The selector's only real failure mode is *silently running less than it should*,
so most of what is pinned here is the conservative direction: an unmappable
Python change falls back to the whole suite, a shared test asset falls back, a
git error refuses to plan at all, and no mode of the script can ever select a
live model workload.

Two mechanics worth knowing before editing this file:

* Classification tests run against the REAL repository root on purpose — the
  mapping's value is that it matches *this* repo's test layout, and asserting it
  against a fixture would only prove the code agrees with itself. All of it is
  read-only.
* `tests_referencing()` scans the text of every `tests/test_*.py`, including
  this one. A dotted module path written here verbatim would make this file
  match it, so the negative cases below use paths whose dotted form never
  appears literally anywhere in the suite.

Change-collection tests build a throwaway git repository under `tmp_path`.
Nothing here writes to the owner's real `data/`, `.claude/session-recovery/`, or
git state.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import dev_verify as dv

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "dev_verify.py"


# ── fixture repositories ────────────────────────────────────────────────────

def _isolated_env(**overrides: str) -> dict:
    """`os.environ` with this machine's global/system git config removed.

    This repo's owner has a global ignore rule for `.claude/`, and
    `core.autocrlf`/`init.defaultBranch` differ per machine; pointing these at
    non-existent paths gives every fixture the same empty configuration.
    """
    env = dict(os.environ)
    env.update({
        "GIT_CONFIG_GLOBAL": str(Path("/no-such-global-gitconfig")),
        "GIT_CONFIG_SYSTEM": str(Path("/no-such-system-gitconfig")),
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    })
    env.update(overrides)
    return env


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ("git", *args), cwd=repo, capture_output=True, text=True,
        env=_isolated_env(),
    )
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "fixture-repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "work")
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(root, "add", "seed.txt")
    _git(root, "commit", "-qm", "seed")
    return root


def _write(root: Path, rel: str, text: str = "x\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ── classification ──────────────────────────────────────────────────────────

def test_docs_only_change_selects_no_python_tests():
    """The whole point of the docs case: a README edit must not cost 10 minutes."""
    plan = dv.build_plan(["README.md", "docs/ARCHITECTURE.md", "ROADMAP.md"])

    assert plan.selected_tests == []
    assert plan.fallbacks == []
    assert not plan.full_python_fallback
    assert not plan.py_sources_changed
    assert [s.source for s in plan.ignored] == [
        "README.md", "ROADMAP.md", "docs/ARCHITECTURE.md",
    ]
    assert not any("pytest" in c.argv for c in plan.commands())


def test_direct_module_mapping_selects_the_matching_test():
    plan = dv.build_plan(["jarvis/context_builder.py"])

    assert "tests/test_context_builder.py" in plan.selected_tests
    assert not plan.full_python_fallback
    assert plan.py_sources_changed
    reasons = dv._reasons_for(plan.tests, "tests/test_context_builder.py")
    assert any("direct module coverage" in r for r in reasons)


def test_a_changed_test_file_selects_itself():
    plan = dv.build_plan(["tests/test_clock.py"])

    assert plan.selected_tests == ["tests/test_clock.py"]
    assert dv._reasons_for(plan.tests, "tests/test_clock.py") == [
        "changed test file <- tests/test_clock.py",
    ]


def test_cross_cutting_graph_change_selects_the_whole_area():
    """A graph file's own filename test is not enough: the graph is imported by
    dozens of test modules named after behaviour, not after the file."""
    plan = dv.build_plan(["jarvis/graph/tool_router.py"])

    selected = plan.selected_tests
    assert "tests/test_tool_router.py" in selected
    # behaviour-named modules the filename mapping alone would have missed
    assert "tests/test_confirmation_node.py" in selected
    assert "tests/test_graph_separation.py" in selected
    assert len(selected) > 20, "the cross-cutting area collapsed to a subset"
    assert any(
        "cross-cutting area: graph orchestration" in r
        for r in dv._reasons_for(plan.tests, "tests/test_graph_separation.py")
    )


def test_cross_cutting_execution_change_selects_the_execution_area():
    plan = dv.build_plan(["jarvis/execution/output_contract.py"])

    selected = plan.selected_tests
    assert "tests/test_output_contract.py" in selected
    assert "tests/test_execution_idempotency.py" in selected
    assert len(selected) > 20


def test_unknown_python_impact_falls_back_to_the_full_suite():
    """No filename match and no test references it — the selector must not
    invent a subset.

    The path is assembled from fragments so its dotted form never appears
    verbatim in this file; `tests_referencing()` scans this file too, and a
    literal would make the module look referenced by its own negative test.
    """
    rel = "jarvis/" + "unmapped" + "_sentinel_mod.py"
    plan = dv.build_plan([rel])

    assert plan.full_python_fallback
    assert plan.selected_tests == []
    assert [f.source for f in plan.fallbacks] == [rel]
    assert "not derivable" in plan.fallbacks[0].reason

    pytest_cmds = [c for c in plan.commands() if "pytest" in c.argv]
    assert len(pytest_cmds) == 1
    assert pytest_cmds[0].argv[-1] == "-q", "fallback must pass no path filter"


def test_conftest_change_falls_back_even_though_it_lives_under_tests():
    plan = dv.build_plan(["tests/conftest.py"])

    assert plan.full_python_fallback
    assert plan.selected_tests == []


def test_a_shared_test_asset_falls_back_rather_than_guessing():
    plan = dv.build_plan(["tests/audio/sample.wav"])

    assert plan.full_python_fallback
    assert "shared test asset" in plan.fallbacks[0].reason


def test_session_hook_change_selects_the_session_regression_tests():
    plan = dv.build_plan(["scripts/claude_session_start.py"])

    assert plan.selected_tests == ["tests/test_claude_session_hooks.py"]
    assert not plan.full_python_fallback


@pytest.mark.parametrize("rel", [
    "CLAUDE.md",
    ".claude/rules/testing.md",
    ".claude/skills/session-close/SKILL.md",
    ".claude/settings.json",
    ".github/workflows/ci.yml",
    ".gitignore",
])
def test_governed_documents_are_not_treated_as_docs_only(rel):
    """These LOOK like documentation and are not: `test_claude_session_hooks.py`
    reads each one from the real repository root and asserts on its content, so
    a docs-only classification would skip a test that can genuinely fail."""
    plan = dv.build_plan([rel])

    assert "tests/test_claude_session_hooks.py" in plan.selected_tests
    assert plan.ignored == [], f"{rel} was written off as documentation"


VERIFIED = dv.Evidence(True, "full suite verified at deadbee0", "d" * 40)


def test_handoff_selects_its_own_contract_test_not_the_slow_session_suite():
    """HANDOFF.md is asserted by `tests/test_handoff_contract.py` (file reads and
    one git call, well under a second). Routing it to the session-protocol suite
    would be defensible and useless: that suite drives subprocesses, is among the
    slowest in the repo, and never reads the real HANDOFF.md."""
    plan = dv.build_plan(["HANDOFF.md"], evidence=VERIFIED)

    assert plan.selected_tests == ["tests/test_handoff_contract.py"]
    assert plan.ignored == [], "HANDOFF.md was written off as documentation"
    assert not plan.full_python_fallback


@pytest.mark.parametrize("rel", [
    "AGENT_CONTRACT.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".agents/skills/session-close/SKILL.md",
    ".claude/settings.json",
    ".claude/skills/session-close/SKILL.md",
    ".codex/hooks.json",
    ".gitignore",
])
def test_interop_contracts_select_the_focused_regression_file(rel):
    plan = dv.build_plan([rel])

    assert "tests/test_agent_interop.py" in plan.selected_tests
    assert plan.ignored == []
    assert not plan.full_python_fallback


def test_a_closing_docs_change_set_reuses_a_verified_full_run():
    """The change set `/session-close` produces when the work commit already had
    its own full run: the closing commit is documentation, and the only thing in
    it a test can check is HANDOFF's own contract."""
    plan = dv.build_plan(["HANDOFF.md", "CHANGELOG.md"], evidence=VERIFIED)

    assert plan.closing_docs_only
    assert plan.selected_tests == ["tests/test_handoff_contract.py"]
    assert not plan.full_python_fallback
    assert [s.source for s in plan.ignored] == ["CHANGELOG.md"]
    assert not plan.py_sources_changed


@pytest.mark.parametrize("evidence,label", [
    (None, "never consulted"),
    (dv.Evidence(False, "no full-suite verification has been recorded"), "absent"),
    (dv.Evidence(False, "the recorded run exited 1 -- a failed suite is not evidence"),
     "failed run"),
    (dv.Evidence(False, "recorded by a different session"), "foreign session"),
    (dv.Evidence(False, "the verified tree is not an ancestor of HEAD"), "stale"),
])
def test_closing_docs_without_usable_evidence_falls_back_to_the_full_suite(
        evidence, label):
    """The fail-safe half, and the reason the optimisation is not a hole: the
    documents themselves prove nothing about the code, so skipping the suite is
    sound only when a real run covered this tree. Every way of NOT having that
    -- including never asking -- runs the suite."""
    plan = dv.build_plan(["HANDOFF.md", "CHANGELOG.md"], evidence=evidence)

    assert plan.closing_docs_only
    assert plan.full_python_fallback, f"{label} evidence must force the full suite"
    argvs = [c.argv for c in plan.commands()]
    assert (dv._python(), "-m", "pytest", "-q") in argvs


def test_full_verification_refuses_to_record_from_a_dirty_tree(repo: Path, capsys):
    """Evidence is keyed by commit, so it may only describe a tree a commit
    describes. On a dirty tree the suite tests HEAD-plus-edits while the record
    would say `HEAD` -- a SHA naming something other than what ran.

    Refused UP FRONT: being told after ten minutes that the result cannot be
    kept is the version of this check nobody would keep using.
    """
    (repo / "uncommitted.py").write_text("x = 1\n", encoding="utf-8")

    code = dv.run_full_verification(repo)

    assert code == 2
    assert "dirty" in capsys.readouterr().err


def test_full_verification_records_nothing_when_it_stops_early(repo: Path, capsys):
    """A clean fixture repo has no `jarvis/` to lint, so the preface fails and
    pytest never runs. Nothing may be recorded from that."""
    code = dv.run_full_verification(repo)

    assert code != 0
    assert "nothing was recorded" in capsys.readouterr().out


def test_a_source_file_in_the_set_is_not_a_closing_commit_at_all():
    """Evidence is irrelevant once the set stops being documents: the ordinary
    mapping applies, and a good record cannot wave a code change through."""
    plan = dv.build_plan(["HANDOFF.md", "jarvis/clock.py"], evidence=VERIFIED)

    assert not plan.closing_docs_only
    assert "tests/test_clock.py" in plan.selected_tests


@pytest.mark.parametrize("rel", [
    "CLAUDE.md", ".claude/rules/testing.md", ".claude/skills/session-close/SKILL.md",
])
def test_a_governed_document_is_not_a_closing_document(rel):
    """These are markdown whose content a real test asserts, so they must never
    ride the closing-docs path -- they select the session-protocol suite."""
    assert not dv.is_closing_doc(rel)

    plan = dv.build_plan(["HANDOFF.md", rel], evidence=VERIFIED)

    assert not plan.closing_docs_only
    assert "tests/test_claude_session_hooks.py" in plan.selected_tests


def test_electron_and_mobile_changes_plan_component_checks_only():
    plan = dv.build_plan([
        "electron/src/renderer/src/lib/chatStream.js",
        "mobile/lib/chat_screen.dart",
    ])

    assert plan.selected_components == ["electron", "mobile"]
    assert plan.selected_tests == []
    assert not plan.full_python_fallback
    argvs = [c.argv for c in plan.commands()]
    assert ("npm", "test") in argvs
    assert ("flutter", "analyze") in argvs
    assert not any("pytest" in argv for argv in argvs)


def test_an_untouched_component_gets_no_command():
    plan = dv.build_plan(["jarvis/clock.py"])

    assert plan.selected_components == []
    argvs = [c.argv for c in plan.commands()]
    assert not any("npm" in argv or "flutter" in argv for argv in argvs)


def test_an_unclassifiable_file_falls_back():
    plan = dv.build_plan(["some_new_thing.bin"])

    assert plan.full_python_fallback
    assert "unclassified" in plan.fallbacks[0].reason


# ── change collection ───────────────────────────────────────────────────────

def test_committed_changes_since_base_are_collected(repo: Path):
    base = _git(repo, "rev-parse", "HEAD").strip()
    _write(repo, "jarvis/clock.py")
    _git(repo, "add", "jarvis/clock.py")
    _git(repo, "commit", "-qm", "work")

    assert dv.collect_changed(repo, base) == ["jarvis/clock.py"]
    # ...and without a base, committed work is out of scope by definition
    assert dv.collect_changed(repo, None) == []


def test_staged_unstaged_and_untracked_are_all_collected(repo: Path):
    base = _git(repo, "rev-parse", "HEAD").strip()

    _write(repo, "staged.py")
    _git(repo, "add", "staged.py")
    _write(repo, "seed.txt", "modified\n")          # unstaged, tracked
    _write(repo, "untracked.py")                    # never added

    assert dv.collect_changed(repo, base) == [
        "seed.txt", "staged.py", "untracked.py",
    ]


def test_a_file_changed_in_several_ways_is_reported_once(repo: Path):
    base = _git(repo, "rev-parse", "HEAD").strip()
    _write(repo, "jarvis/clock.py", "one\n")
    _git(repo, "add", "jarvis/clock.py")
    _git(repo, "commit", "-qm", "work")
    _write(repo, "jarvis/clock.py", "two\n")
    _git(repo, "add", "jarvis/clock.py")
    _write(repo, "jarvis/clock.py", "three\n")

    assert dv.collect_changed(repo, base) == ["jarvis/clock.py"]


def test_a_git_failure_raises_instead_of_reading_as_no_changes(repo: Path):
    """The dangerous bug this forecloses: a bad `--base` producing an empty
    change set, an empty plan, and a green-looking run that tested nothing."""
    with pytest.raises(dv.GitError) as excinfo:
        dv.collect_changed(repo, "not-a-real-sha")

    assert "not-a-real-sha" in str(excinfo.value)


def test_the_cli_refuses_to_plan_from_an_unknown_change_set():
    proc = subprocess.run(
        (sys.executable, str(SCRIPT), "--base", "definitely-not-a-sha"),
        cwd=REPO_ROOT, capture_output=True, text=True,
    )

    assert proc.returncode == 2
    assert "refusing to plan" in proc.stderr
    assert "pytest" not in proc.stdout


# ── determinism ─────────────────────────────────────────────────────────────

def test_output_is_deterministic_regardless_of_input_order():
    changed = [
        "jarvis/clock.py", "README.md", "tests/test_clock.py",
        "electron/src/renderer/src/lib/chatStream.js", "jarvis/policy_guard.py",
    ]
    first = dv.format_plan(dv.build_plan(changed))
    second = dv.format_plan(dv.build_plan(list(reversed(changed))))

    assert first == second
    assert first.count("jarvis/clock.py\n") >= 1


def test_selected_tests_and_commands_are_sorted_and_deduplicated():
    plan = dv.build_plan([
        "jarvis/graph/tool_router.py", "jarvis/graph/safe_tools.py",
        "tests/test_tool_router.py",
    ])

    assert plan.selected_tests == sorted(plan.selected_tests)
    assert len(plan.selected_tests) == len(set(plan.selected_tests))
    pytest_cmds = [c for c in plan.commands() if "pytest" in c.argv]
    assert len(pytest_cmds) == 1
    paths = pytest_cmds[0].argv[4:]
    assert list(paths) == sorted(set(paths))


# ── live workloads are never automatic ──────────────────────────────────────

@pytest.mark.parametrize("harness", dv.LIVE_WORKLOADS)
def test_a_live_harness_is_never_planned_even_when_it_is_what_changed(harness):
    """Changing the A/B harness must select *tests about* it, never a run of it.

    `scripts/alpha_gate.py` legitimately selects `tests/test_alpha_gate.py` — a
    deterministic unit test named after the harness. What must never appear is
    an argument pointing back into `scripts/`, which is where every live
    workload lives.
    """
    plan = dv.build_plan([harness])

    for cmd in plan.commands():
        assert harness not in cmd.argv
        for part in cmd.argv:
            assert not str(part).startswith("scripts/"), \
                f"{cmd.display()} would execute something from scripts/"


def test_no_change_set_can_produce_a_command_outside_the_allowed_tools():
    """Structural, not a promise: the planner emits only these executables, so
    an Ollama run or a mailbox test cannot appear however the inputs vary."""
    allowed = {"git", "npm", "flutter", dv._python()}
    changed = [
        "jarvis/graph/nodes.py", "jarvis/execution/output_contract.py",
        "scripts/completion_contract_ab.py", "tests/conftest.py", "CLAUDE.md",
        "electron/src/main/index.js", "mobile/lib/app.dart", "weird.bin",
    ]

    for cmd in dv.build_plan(changed).commands():
        assert cmd.argv[0] in allowed


def test_the_plan_says_out_loud_that_live_workloads_were_not_selected():
    text = dv.format_plan(dv.build_plan(["jarvis/clock.py"]))

    assert "Live workloads: NOT SELECTED" in text


# ── execution semantics ─────────────────────────────────────────────────────

def _cmd(code: str, reason: str = "fixture") -> "dv.Command":
    return dv.Command((sys.executable, "-c", code), ".", reason)


def test_a_failing_command_propagates_a_non_zero_exit(capsys):
    rc = dv.run_commands([_cmd("raise SystemExit(3)")])

    assert rc == 1
    assert "FAILED (exit 3)" in capsys.readouterr().out


def test_everything_after_a_failure_is_reported_not_run_never_passed(capsys):
    rc = dv.run_commands([
        _cmd("raise SystemExit(1)", "first"),
        _cmd("raise SystemExit(0)", "second"),
    ])
    out = capsys.readouterr().out

    assert rc == 1
    assert "NOT RUN" in out
    assert out.count("PASSED") == 0, "an unrun check was reported as passed"


def test_all_passing_commands_exit_zero_and_say_it_is_not_full_verification(capsys):
    rc = dv.run_commands([_cmd("pass"), _cmd("pass")])
    out = capsys.readouterr().out

    assert rc == 0
    assert out.count("PASSED") == 2
    assert "not full verification" in out


def test_a_missing_executable_is_not_run_rather_than_skipped(capsys):
    rc = dv.run_commands([dv.Command(("no-such-tool-xyz",), ".", "fixture")])
    out = capsys.readouterr().out

    assert rc == 1
    assert "NOT RUN (executable missing)" in out


# ── the default mode runs nothing ───────────────────────────────────────────

def test_the_default_mode_prints_a_plan_and_runs_nothing():
    proc = subprocess.run(
        (sys.executable, str(SCRIPT), "--base", "HEAD"),
        cwd=REPO_ROOT, capture_output=True, text=True,
    )

    assert proc.returncode == 0
    assert "Nothing was run (plan only)" in proc.stdout
    assert "Commands:" in proc.stdout
    assert "PASSED" not in proc.stdout
