"""Targeted development verification -- what to run *while* iterating.

The full Python suite takes ~10 minutes on this machine, so running it after
every edit is the single largest avoidable cost in a development loop. This
script answers a narrower question: *given what this task has actually changed,
which deterministic checks are worth running right now?*

It is a development aid, not a gate. `/session-close` and CI keep their existing
full-verification responsibilities untouched.

    .venv\\Scripts\\python.exe scripts\\dev_verify.py --base <TASK_BASE_SHA>
    .venv\\Scripts\\python.exe scripts\\dev_verify.py --base <TASK_BASE_SHA> --run

Without `--run` nothing is executed: the plan and the reason for every selected
check are printed and the script exits 0. Nothing is ever reported as passed
that was not actually run.

Two design rules, both of which cost more code than their opposites:

**Over-selection is safe; under-selection is not.** Every ambiguity resolves
toward running more. A Python change this script cannot map to a test set
produces `FULL PYTHON FALLBACK` -- never a silent "no tests required". The
module-reference scan is deliberately a loose substring match over the whole
test file rather than a precise import parse, for the same reason.

**Live workloads are never automatic.** No Ollama run, no A/B harness, no
completion-contract evaluation, no real mailbox, no manual acceptance driver is
ever selected by this script, in any mode. Those are measurements a task asks
for explicitly; a development loop that starts one by accident both burns the
GPU and (per `.claude/rules/testing.md`) invalidates any live measurement
sharing the machine.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Bounded like every other git call in this repo's tooling: a hung git (index
#: lock contention, a credential prompt) must cost seconds, not the session.
GIT_TIMEOUT_S = 30.0

#: Documents and configuration whose CONTENT is asserted by the session tests.
#: These look like "docs-only" changes and are not: the suite reads them from
#: the real repository root and asserts on what they say.
GOVERNED_DOCS = frozenset({
    "CLAUDE.md",
    ".gitignore",
    ".claude/settings.json",
    ".claude/skills/session-close/SKILL.md",
    ".github/workflows/ci.yml",
})

#: Shared Claude/Codex contracts and adapters have their own focused relational
#: checks. The overlap with GOVERNED_DOCS is deliberate: changing Claude's
#: adapter, settings, canonical skill, or gitignore must run BOTH suites.
INTEROP_DOCS = frozenset({
    "AGENT_CONTRACT.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".agents/skills/session-close/SKILL.md",
    ".claude/settings.json",
    ".claude/skills/session-close/SKILL.md",
    ".codex/hooks.json",
    ".gitignore",
})

#: Same, by prefix: every `.claude/rules/*.md` is checked for its path scope.
GOVERNED_PREFIXES = (".claude/rules/",)

#: Documents whose content is asserted by a NARROW, fast test file rather than
#: by the slow session-protocol suite. Kept separate from GOVERNED_DOCS on
#: purpose: routing HANDOFF.md to `test_claude_session_hooks.py` would be
#: correct-but-useless -- that suite drives subprocesses and is among the
#: slowest in the repo, and none of it reads the real HANDOFF.md.
DOC_TESTS: dict[str, tuple[str, ...]] = {
    "HANDOFF.md": ("tests/test_handoff_contract.py",),
}

#: The session lifecycle is driven as SUBPROCESSES by its tests, so a filename
#: mapping finds nothing. This is the spec's "session hook change -> session
#: regression tests" rule, written out.
SESSION_SCRIPTS = frozenset({
    "scripts/claude_session_start.py",
    "scripts/claude_session_end.py",
    "scripts/claude_session_state.py",
})

SESSION_TESTS = ("tests/test_claude_session_hooks.py",)
INTEROP_TESTS = ("tests/test_agent_interop.py",)

#: A change here changes what every other test means, so no subset is honest.
GLOBAL_PYTHON = frozenset({
    "tests/conftest.py",
    "pyproject.toml",
    "requirements.txt",
    "requirements-lock.txt",
    "requirements-gpu-windows.txt",
})

#: Cross-cutting areas where a single filename mapping is not trustworthy: the
#: graph and the execution kernel are imported by dozens of test modules that
#: are named after behaviour rather than after the file they exercise. The token
#: is the package path, which every import form contains.
CROSS_CUTTING = (
    ("jarvis/graph/", "jarvis.graph", "graph orchestration"),
    ("jarvis/execution/", "jarvis.execution", "execution kernel"),
)

DOC_SUFFIXES = frozenset({".md", ".txt", ".rst"})

#: What a CLOSING commit is allowed to contain if it wants to reuse an earlier
#: full-suite run instead of triggering another one. Deliberately narrow, and
#: deliberately NOT "anything that looks like a document": `CLAUDE.md`,
#: `.claude/rules/*.md` and the session-close skill are all markdown whose
#: content is asserted by a real test, so they are excluded here and go down the
#: ordinary governed-document path instead.
CLOSING_DOCS = frozenset({
    "HANDOFF.md",
    "CHANGELOG.md",
    "ROADMAP.md",
    "MEMORY.md",
    "ProjectState.md",
})

CLOSING_DOC_PREFIXES = ("docs/",)


def is_closing_doc(rel: str) -> bool:
    return rel in CLOSING_DOCS or rel.startswith(CLOSING_DOC_PREFIXES)

#: Named only so the plan can say out loud that it did not pick them. Nothing
#: in this script can select one; the assertion is structural, not a promise.
LIVE_WORKLOADS = (
    "scripts/completion_contract_ab.py",
    "scripts/role_ab.py",
    "scripts/plot_intent_ab.py",
    "scripts/ab_launch_server.py",
    "scripts/mvp_gate.py",
    "scripts/alpha_gate.py",
    "scripts/briefing_gate.py",
    "scripts/revision_gate.py",
    "scripts/manual_test_driver.py",
)


class GitError(RuntimeError):
    """A git command failed. Never silently read as "nothing changed"."""


@dataclass(frozen=True, order=True)
class Selection:
    """One reason one target was selected. `source` is the changed file."""

    target: str
    reason: str
    source: str


@dataclass(frozen=True, order=True)
class Fallback:
    """A changed file whose impact could not be mapped to a test subset."""

    source: str
    reason: str


@dataclass(frozen=True)
class Evidence:
    """Whether an earlier full-suite run may stand in for another one.

    Produced by `scripts/claude_session_state.py`, never by this module's own
    judgement, and never by reading a claim out of HANDOFF.md.
    """

    ok: bool
    detail: str
    head: str = ""


@dataclass(frozen=True)
class Command:
    argv: tuple[str, ...]
    cwd: str
    reason: str

    def display(self) -> str:
        shown = " ".join(self.argv)
        return shown if self.cwd == "." else f"({self.cwd}) {shown}"


@dataclass
class Plan:
    base: str | None
    changed: list[str] = field(default_factory=list)
    tests: list[Selection] = field(default_factory=list)
    fallbacks: list[Fallback] = field(default_factory=list)
    components: list[Selection] = field(default_factory=list)
    ignored: list[Selection] = field(default_factory=list)
    py_sources_changed: bool = False
    closing_docs_only: bool = False
    evidence: Evidence | None = None

    @property
    def evidence_blocks_reuse(self) -> bool:
        """A closing-docs-only set with no usable full-run evidence behind it.

        This is the fail-safe half of the session-close optimisation. Skipping
        the suite is only sound when an earlier run covered this very tree, so
        an absent, malformed, failed, foreign or stale record does not mean
        "probably fine" -- it means run the suite.
        """
        return self.closing_docs_only and not (self.evidence and self.evidence.ok)

    @property
    def full_python_fallback(self) -> bool:
        return bool(self.fallbacks) or self.evidence_blocks_reuse

    @property
    def selected_tests(self) -> list[str]:
        return sorted({s.target for s in self.tests})

    @property
    def selected_components(self) -> list[str]:
        return sorted({s.target for s in self.components})

    def commands(self) -> list[Command]:
        """The ordered, deduplicated command list. Cheapest checks first."""
        out: list[Command] = [
            Command(("git", "diff", "--check"), ".",
                    "always -- whitespace and conflict markers are free to check"),
        ]
        if self.py_sources_changed:
            out.append(Command(
                (_python(), "-m", "ruff", "check", "jarvis", "scripts", "tests"),
                ".",
                "Python sources changed -- the canonical lint command, unmodified",
            ))
        if self.full_python_fallback:
            out.append(Command(
                (_python(), "-m", "pytest", "-q"), ".",
                "FULL PYTHON FALLBACK -- impact could not be mapped (see above)",
            ))
        elif self.selected_tests:
            out.append(Command(
                (_python(), "-m", "pytest", "-q", *self.selected_tests), ".",
                f"targeted: {len(self.selected_tests)} test file(s) selected below",
            ))
        if "electron" in self.selected_components:
            out.append(Command(
                ("npm", "test"), "electron",
                "Electron sources changed -- component check (vitest)",
            ))
        if "mobile" in self.selected_components:
            out.append(Command(
                ("flutter", "analyze"), "mobile",
                "Mobile sources changed -- analyzer only during iteration; "
                "`flutter test` needs the gitignored font assets "
                "(MOBILE-ASSETS-01), so it cannot be assumed runnable on every "
                "checkout and belongs to work-completion verification, not this "
                "loop",
            ))
        return out


# ── change collection ───────────────────────────────────────────────────────

def _git(root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ("git", *args), cwd=root, capture_output=True, text=True,
            timeout=GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        raise GitError(f"git {' '.join(args)} could not run: {exc}") from exc
    if proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or '<no stderr>'}"
        )
    return proc.stdout


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def collect_changed(root: Path, base: str | None) -> list[str]:
    """Every file this task touched, from all four sources, deduplicated.

    A git failure raises. Reading it as "no changes" would turn a broken
    invocation into a clean-looking plan that skips every test, which is the
    exact failure mode this script exists to prevent.
    """
    changed: set[str] = set()
    if base:
        head = _git(root, "rev-parse", "HEAD").strip()
        changed.update(_lines(_git(root, "diff", "--name-only", base, head)))
    changed.update(_lines(_git(root, "diff", "--name-only", "--cached")))
    changed.update(_lines(_git(root, "diff", "--name-only")))
    changed.update(_lines(
        _git(root, "ls-files", "--others", "--exclude-standard")))
    return sorted(changed)


# ── test discovery ──────────────────────────────────────────────────────────

_TEXT_CACHE: dict[Path, dict[str, str]] = {}


def _test_texts(root: Path) -> dict[str, str]:
    key = root.resolve()
    cached = _TEXT_CACHE.get(key)
    if cached is None:
        cached = {}
        for path in sorted((key / "tests").glob("test_*.py")):
            try:
                cached[f"tests/{path.name}"] = path.read_text(
                    encoding="utf-8", errors="replace")
            except OSError:  # pragma: no cover -- unreadable test file
                continue
        _TEXT_CACHE[key] = cached
    return cached


def tests_referencing(root: Path, token: str) -> list[str]:
    """Test files mentioning `token` anywhere -- deliberately a loose match.

    A precise import parse would miss string-path monkeypatching and dynamic
    imports, and missing a test is the one error this script must not make.
    Matching a mention in a comment costs a few seconds of pytest.
    """
    return sorted(rel for rel, text in _test_texts(root).items() if token in text)


def _module_tokens(rel: str) -> list[str]:
    parts = rel[:-3].split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return []
    tokens = [".".join(parts)]
    if len(parts) > 1:
        tokens.append(f"from {'.'.join(parts[:-1])} import {parts[-1]}")
    return tokens


# ── classification ──────────────────────────────────────────────────────────

def _classify(rel: str, root: Path, plan: Plan) -> None:
    suffix = Path(rel).suffix
    if suffix == ".py":
        plan.py_sources_changed = True

    if rel in INTEROP_DOCS:
        for target in INTEROP_TESTS:
            plan.tests.append(Selection(
                target, "shared agent-interoperability contract tests", rel))
        if rel in GOVERNED_DOCS:
            for target in SESSION_TESTS:
                plan.tests.append(Selection(
                    target, "content asserted by the session-protocol tests", rel))
        return

    if rel in GOVERNED_DOCS or rel.startswith(GOVERNED_PREFIXES):
        for target in SESSION_TESTS:
            plan.tests.append(Selection(
                target, "content asserted by the session-protocol tests", rel))
        return

    if rel in SESSION_SCRIPTS:
        for target in SESSION_TESTS:
            plan.tests.append(Selection(
                target, "session lifecycle regression tests", rel))
        return

    if rel in DOC_TESTS:
        for target in DOC_TESTS[rel]:
            plan.tests.append(Selection(
                target, "closing-document contract asserted by this test", rel))
        return

    if rel in GLOBAL_PYTHON:
        plan.fallbacks.append(Fallback(
            rel, "changes the meaning of the whole suite -- no subset is honest"))
        return

    if rel.startswith("tests/"):
        name = Path(rel).name
        if name.startswith("test_") and suffix == ".py":
            plan.tests.append(Selection(rel, "changed test file", rel))
        else:
            plan.fallbacks.append(Fallback(
                rel, "shared test asset -- consumers are not derivable by name"))
        return

    if suffix in DOC_SUFFIXES or rel.startswith("docs/"):
        plan.ignored.append(Selection(
            "-", "documentation -- no deterministic test implication", rel))
        return

    if suffix == ".py" and (rel.startswith("jarvis/") or rel.startswith("scripts/")):
        _classify_module(rel, root, plan)
        return

    if rel.startswith("electron/"):
        plan.components.append(Selection("electron", "Electron sources changed", rel))
        return

    if rel.startswith("mobile/"):
        plan.components.append(Selection("mobile", "Mobile sources changed", rel))
        return

    plan.fallbacks.append(Fallback(rel, "unclassified change -- impact unknown"))


def _classify_module(rel: str, root: Path, plan: Plan) -> None:
    found: list[Selection] = []

    direct = f"tests/test_{Path(rel).stem}.py"
    if (root / direct).is_file():
        found.append(Selection(direct, "direct module coverage", rel))

    for token in _module_tokens(rel):
        for target in tests_referencing(root, token):
            found.append(Selection(target, f"references `{token}`", rel))

    for prefix, token, label in CROSS_CUTTING:
        if not rel.startswith(prefix):
            continue
        for target in tests_referencing(root, token):
            found.append(Selection(
                target, f"cross-cutting area: {label}", rel))

    if found:
        plan.tests.extend(found)
    else:
        plan.fallbacks.append(Fallback(
            rel, "no test file references this module -- impact not derivable"))


def build_plan(changed: list[str], *, root: Path = REPO_ROOT,
               base: str | None = None, evidence: Evidence | None = None) -> Plan:
    plan = Plan(base=base, changed=sorted(set(changed)), evidence=evidence)
    plan.closing_docs_only = bool(plan.changed) and all(
        is_closing_doc(rel) for rel in plan.changed)
    for rel in plan.changed:
        _classify(rel, root, plan)
    plan.tests.sort()
    plan.fallbacks.sort()
    plan.components.sort()
    plan.ignored.sort()
    return plan


# ── rendering ───────────────────────────────────────────────────────────────

def _python() -> str:
    """The running interpreter, shown repo-relative when it is the venv's."""
    exe = Path(sys.executable)
    try:
        return exe.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return exe.as_posix()


def _reasons_for(selections: list[Selection], target: str) -> list[str]:
    seen = {f"{s.reason} <- {s.source}" for s in selections if s.target == target}
    return sorted(seen)


def format_plan(plan: Plan) -> str:
    out: list[str] = ["dev_verify -- targeted development verification plan"]
    out.append(f"base: {plan.base or '(none -- working tree only)'}")
    out.append("")

    if not plan.changed:
        out.append("Changed files: none")
    else:
        out.append(f"Changed files ({len(plan.changed)}):")
        out.extend(f"  {rel}" for rel in plan.changed)
    out.append("")

    if plan.closing_docs_only:
        verdict = plan.evidence.detail if plan.evidence else \
            "full-suite evidence was not consulted"
        state = "REUSABLE" if not plan.evidence_blocks_reuse else "NOT REUSABLE"
        out.append(f"Closing-docs-only change set. Earlier full run: {state}")
        out.append(f"      {verdict}")
        out.append("")

    if plan.full_python_fallback:
        out.append("FULL PYTHON FALLBACK -- the whole Python suite is required:")
        for fb in plan.fallbacks:
            out.append(f"  {fb.source}")
            out.append(f"      {fb.reason}")
        if plan.evidence_blocks_reuse:
            out.append("  (full-suite evidence)")
            out.append("      no reusable record of a full run covering this tree")
    elif plan.selected_tests:
        out.append(f"Selected Python tests ({len(plan.selected_tests)}):")
        for target in plan.selected_tests:
            out.append(f"  {target}")
            out.extend(f"      {r}" for r in _reasons_for(plan.tests, target))
    else:
        out.append("Selected Python tests: none")
        out.append("      no changed file implies Python behaviour")
    out.append("")

    if plan.selected_components:
        out.append("Component checks:")
        for target in plan.selected_components:
            out.append(f"  {target}")
            out.extend(f"      {r}" for r in _reasons_for(plan.components, target))
        out.append("")

    if plan.ignored:
        out.append("No test implication:")
        for sel in plan.ignored:
            out.append(f"  {sel.source} -- {sel.reason}")
        out.append("")

    out.append("Live workloads: NOT SELECTED -- never automatic.")
    out.append("  (Ollama runs, A/B harnesses, completion-contract evaluation,")
    out.append("   real-mailbox and manual acceptance drivers are measurements a")
    out.append("   task asks for explicitly, not development verification.)")
    out.append("")

    out.append("Commands:")
    for cmd in plan.commands():
        out.append(f"  {cmd.display()}")
        out.append(f"      {cmd.reason}")
    return "\n".join(out)


# ── execution ───────────────────────────────────────────────────────────────

def _resolve(argv: tuple[str, ...]) -> tuple[str, ...] | None:
    """Absolute path for argv[0], or None when it is not on PATH."""
    exe = shutil.which(argv[0])
    return None if exe is None else (exe, *argv[1:])


def run_commands(commands: list[Command], root: Path = REPO_ROOT) -> int:
    """Run in order, stopping at the first failure.

    Everything after a failure is reported `NOT RUN`, never `PASSED`. A check
    that did not run is not evidence of anything.
    """
    results: list[tuple[Command, str]] = []
    failed = False
    for cmd in commands:
        if failed:
            results.append((cmd, "NOT RUN"))
            continue
        resolved = _resolve(cmd.argv)
        if resolved is None:
            print(f"\n$ {cmd.display()}\n  -- {cmd.argv[0]} is not on PATH")
            results.append((cmd, "NOT RUN (executable missing)"))
            failed = True
            continue
        print(f"\n$ {cmd.display()}", flush=True)
        proc = subprocess.run(resolved, cwd=root / cmd.cwd)
        if proc.returncode == 0:
            results.append((cmd, "PASSED"))
        else:
            results.append((cmd, f"FAILED (exit {proc.returncode})"))
            failed = True

    print("\nSummary:")
    for cmd, status in results:
        print(f"  {status:<28} {cmd.display()}")
    if failed:
        print("\ndev_verify: FAILED -- see above.")
        return 1
    print("\ndev_verify: all selected checks passed. This is targeted "
          "verification, not full verification.")
    return 0


# ── full verification and its evidence ──────────────────────────────────────

def _state_module():
    """`claude_session_state`, imported either way this file can be loaded.

    Returns None rather than raising: a missing helper must degrade to "no
    evidence" (and therefore to running the suite), never to a crash.
    """
    try:
        from scripts import claude_session_state as state  # noqa: PLC0415
    except ImportError:
        try:
            import claude_session_state as state  # noqa: PLC0415
        except ImportError:
            return None
    return state


def evidence_for(root: Path) -> Evidence:
    """Whether a recorded full run still covers the working tree.

    Two independent conditions, and both are derived rather than claimed: the
    helper vouches for the record itself (shape, exit status, session, branch,
    ancestry), and the change set is recomputed **from the record's own SHA** so
    that a `--base` pointing elsewhere cannot shrink what has to be accounted
    for.
    """
    state = _state_module()
    if state is None:
        return Evidence(False, "the session-state helper could not be imported")
    try:
        ok, detail, head = state.full_verification_status(str(root))
    except Exception as exc:  # noqa: BLE001 -- any failure means "no evidence"
        return Evidence(False, f"verification status unavailable ({type(exc).__name__})")
    if not ok:
        return Evidence(False, detail, head)
    try:
        since = collect_changed(root, head)
    except GitError as exc:
        return Evidence(False, f"cannot derive changes since the verified tree ({exc})", head)
    stray = sorted(rel for rel in since if not is_closing_doc(rel))
    if stray:
        shown = ", ".join(stray[:5]) + (" ..." if len(stray) > 5 else "")
        return Evidence(
            False,
            f"changes since the verified tree are not closing documents: {shown}",
            head,
        )
    return Evidence(True, detail, head)


def _run_streaming(argv: tuple[str, ...], root: Path) -> tuple[int, str]:
    """Run, echo every line as it arrives, and keep the last non-empty one.

    Streaming matters: the full suite takes minutes, and a silent capture would
    make it look hung. The kept line is pytest's own summary -- the evidence
    record refuses anything that is not one.
    """
    proc = subprocess.Popen(
        argv, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    last = ""
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        stripped = line.strip()
        if stripped:
            last = stripped
    return proc.wait(), last


def run_full_verification(root: Path = REPO_ROOT) -> int:
    """The canonical full pair, recorded as machine-checkable evidence.

    This is the ONLY producer of that evidence. Nothing else may write it and
    there is no subcommand that accepts a count, a SHA or a verdict as an
    argument -- the whole point is that `/session-close` can later reuse a run
    that demonstrably happened rather than one a document claims happened.
    """
    # Evidence is keyed by commit, so it may only be recorded for a tree that a
    # commit actually describes. On a dirty tree the suite would be testing
    # HEAD-plus-uncommitted-edits while the record said "HEAD" -- a SHA that
    # names something other than what ran is precisely the kind of
    # almost-true evidence this whole mechanism exists to refuse. Checked up
    # front rather than after: nobody should spend ten minutes to be told the
    # result cannot be kept.
    try:
        dirty = _git(root, "status", "--porcelain")
    except GitError as exc:
        print(f"dev_verify: {exc}", file=sys.stderr)
        return 2
    if dirty.strip():
        print("dev_verify: --full records evidence for a COMMITTED tree, and this "
              "working tree is dirty.\n"
              "  Commit the work first, then run this -- that is the tree "
              "/session-close would be reusing.\n"
              "  (To simply run the suite without recording anything, run pytest "
              "directly.)", file=sys.stderr)
        return 2

    preface = [
        Command(("git", "diff", "--check"), ".",
                "always -- whitespace and conflict markers are free to check"),
        Command((_python(), "-m", "ruff", "check", "jarvis", "scripts", "tests"), ".",
                "the canonical lint command, unmodified"),
    ]
    if run_commands(preface, root) != 0:
        print("\ndev_verify: full verification stopped before pytest ran; "
              "nothing was recorded.")
        return 1

    # Executed with the ABSOLUTE interpreter, recorded with the repo-relative
    # one: `_python()` is display form, and Popen would resolve it against this
    # process's cwd rather than `root`. The recorded token has to stay relative
    # anyway -- an absolute Windows path carries a colon, which the record's
    # token validator refuses precisely so no path can be smuggled into it.
    recorded = (_python(), "-m", "pytest", "-q")
    print(f"\n$ {' '.join(recorded)}", flush=True)
    code, summary = _run_streaming((sys.executable, "-m", "pytest", "-q"), root)

    state = _state_module()
    if state is None:
        print("\ndev_verify: the session-state helper could not be imported; "
              "the run is NOT recorded as reusable evidence.")
        return code

    if code != 0:
        removed = ""
        try:
            path = state.verification_path(state.repo_root(str(root)))
            if path.exists():
                path.unlink()
                removed = " Any earlier evidence was discarded."
        except Exception:  # noqa: BLE001 -- best effort; the failure below is what matters
            pass
        print(f"\ndev_verify: the full suite FAILED (exit {code}); "
              f"nothing was recorded as evidence.{removed}")
        return code

    try:
        record = state.record_full_verification(
            str(root), command=list(recorded), exit_status=code, summary=summary)
    except Exception as exc:  # noqa: BLE001 -- recording is best effort, the run still passed
        print(f"\ndev_verify: the full suite passed, but the evidence could not be "
              f"recorded ({exc}). /session-close will re-run the suite.")
        return 0
    print(f"\ndev_verify: full verification passed and recorded at "
          f"{record['head'][:8]} -- {record['summary']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dev_verify",
        description=__doc__.splitlines()[0],
    )
    parser.add_argument(
        "--base", metavar="SHA",
        help="TASK_BASE_SHA -- HEAD when the task started. Committed work since "
             "it is included alongside staged, unstaged and untracked changes.",
    )
    parser.add_argument(
        "--run", action="store_true",
        help="execute the plan (default: print it and exit 0 without running)",
    )
    parser.add_argument(
        "--full", action="store_true",
        help="ignore the plan: run the canonical full verification and record it "
             "as the evidence /session-close may later reuse",
    )
    args = parser.parse_args(argv)

    if args.full:
        return run_full_verification(REPO_ROOT)

    try:
        changed = collect_changed(REPO_ROOT, args.base)
    except GitError as exc:
        print(f"dev_verify: {exc}", file=sys.stderr)
        print("dev_verify: refusing to plan from an unknown change set.",
              file=sys.stderr)
        return 2

    plan = build_plan(changed, root=REPO_ROOT, base=args.base,
                      evidence=evidence_for(REPO_ROOT))
    print(format_plan(plan))

    if not args.run:
        print("\nNothing was run (plan only). Add --run to execute.")
        return 0
    return run_commands(plan.commands())


if __name__ == "__main__":
    raise SystemExit(main())
