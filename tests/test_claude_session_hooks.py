"""Session Lifecycle v1 — the SessionStart preflight and SessionEnd recovery hooks.

These hooks run outside the app, on every session, and their whole value is that
a human stops having to re-derive repository state by hand. That makes two
properties worth more than any feature here:

* **fail-open** — a preflight that can block a session start is worse than none,
  so every failure path must still produce valid hook JSON and exit 0;
* **no leakage** — the preflight's output goes straight into a model's context,
  so it must carry no absolute user path and no file contents.

Both are tested by driving the real scripts as SUBPROCESSES with real hook JSON
on stdin, because that is the actual contract Claude Code uses — importing the
functions would test the code while skipping the interface.

Every test builds its own throwaway git repository under `tmp_path`. Nothing
here writes to the owner's real repository or real `~/.claude`; the only real
files touched are read-only config assertions at the bottom.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
START_SCRIPT = REPO_ROOT / "scripts" / "claude_session_start.py"
END_SCRIPT = REPO_ROOT / "scripts" / "claude_session_end.py"

#: The project's runtime interpreter, as referenced by the hook commands. It
#: lives under the gitignored `.venv/`, so it is expected to be ABSENT from a
#: clean checkout -- see test_hook_commands_use_repo_relative_paths_that_exist.
EXPECTED_INTERPRETER = ".venv/Scripts/python.exe"


def _split_command_paths(command: str) -> tuple[list[str], list[str]]:
    """A hook command's path tokens, separated by what they actually are.

    Scripts are repository artefacts and must exist; the interpreter is a
    runtime environment path and must not be required to. Conflating the two
    is exactly the bug this helper exists to make impossible to repeat.
    """
    tokens = [tok.strip('"') for tok in command.split()]
    scripts = [tok for tok in tokens if tok.endswith(".py")]
    interpreters = [tok for tok in tokens if tok.endswith(".exe")]
    return scripts, interpreters


def _load(path: Path, name: str):
    """Import a script by path — `scripts/` is not an importable package."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(("git", *args), cwd=repo, capture_output=True,
                          text=True, check=True)
    return proc.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real, minimal git repo with one commit and a HANDOFF naming its SHA."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "langgraph-migration")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    # Hermetic: the developer running these tests may have a GLOBAL ignore rule
    # (this machine ignores `.claude/`), which would silently change what
    # `git add` and `git status` do here. Point the excludes file at nothing so
    # the fixture's behaviour comes from the fixture alone.
    _git(root, "config", "core.excludesFile", str(root / ".no-global-excludes"))
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "initial")
    head = _git(root, "rev-parse", "HEAD")
    (root / "HANDOFF.md").write_text(
        f"# Handoff\n\nCurrent verified state: `{head}` on langgraph-migration.\n",
        encoding="utf-8",
    )
    _git(root, "add", "HANDOFF.md")
    _git(root, "commit", "-q", "-m", "handoff")
    return root


def run_hook(script: Path, payload: dict, cwd: Path, env: dict | None = None):
    """Drive a hook exactly as Claude Code does: JSON on stdin, JSON on stdout."""
    proc = subprocess.run(
        (sys.executable, str(script)),
        input=json.dumps(payload), cwd=str(cwd),
        capture_output=True, text=True, timeout=60, env=env,
    )
    return proc


def start_context(repo: Path, source: str = "startup", **extra) -> str:
    payload = {"session_id": "t-1", "cwd": str(repo),
               "hook_event_name": "SessionStart", "source": source, **extra}
    proc = run_hook(START_SCRIPT, payload, repo)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]


# ── the ordinary cases ──────────────────────────────────────────────────────

def test_a_clean_synced_repo_is_summarised(repo):
    context = start_context(repo)

    assert "SESSION PREFLIGHT" in context
    assert "langgraph-migration" in context
    assert "working tree  clean" in context
    assert "DEGRADED" not in context


def test_a_dirty_repo_names_its_dirty_files(repo):
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("x\n", encoding="utf-8")

    context = start_context(repo)

    assert "1 dirty" not in context, "two files changed"
    assert "README.md" in context and "untracked.txt" in context


def test_a_modified_dotfile_keeps_its_leading_dot(repo):
    """Regression: `git status --porcelain` output is stripped as a whole, which
    drops the leading space of the FIRST line only. A MODIFIED TRACKED file's
    line is ` M path` -- the one shape whose leading space exists -- so a fixed
    `line[3:]` slice ate one character of exactly that filename, and
    `.claude/settings.local.json` was reported as `claude/settings.local.json`.

    Tracked-and-modified is the faithful reproduction: an untracked dotfile is
    collapsed by git to `?? .claude/` and would not exercise the bug at all.
    """
    dotdir = repo / ".claude"
    dotdir.mkdir()
    target = dotdir / "settings.local.json"
    target.write_text("{}\n", encoding="utf-8")
    _git(repo, "add", ".claude/settings.local.json")
    _git(repo, "commit", "-q", "-m", "add local settings")
    target.write_text('{"changed": true}\n', encoding="utf-8")

    context = start_context(repo)

    assert ".claude/settings.local.json" in context
    assert "1 dirty: claude/" not in context, "the leading dot was eaten"


def test_the_context_block_stays_within_its_line_budget(repo):
    """It is injected into the model's first turn, so it competes with real work."""
    module = _load(START_SCRIPT, "hook_start_budget")
    for i in range(40):
        (repo / f"f{i}.txt").write_text("x\n", encoding="utf-8")

    context = start_context(repo)

    assert len(context.splitlines()) <= module.MAX_CONTEXT_LINES


@pytest.mark.parametrize("source", ["clear", "compact"])
def test_clear_and_compact_get_only_a_short_state_summary(repo, source):
    """These re-enter an already-oriented session; re-printing the full preflight
    would spend context re-stating what the session already had."""
    context = start_context(repo, source=source)

    assert "SESSION STATE" in context
    assert "First actions:" not in context
    assert len(context.splitlines()) <= 4


# ── HANDOFF ancestry ────────────────────────────────────────────────────────

def test_a_missing_handoff_is_reported_not_assumed(repo):
    (repo / "HANDOFF.md").unlink()

    context = start_context(repo)

    assert "HANDOFF.md    MISSING" in context


def test_a_handoff_naming_an_ancestor_sha_is_reported_current(repo):
    context = start_context(repo)
    assert "is ancestor of HEAD" in context


def test_a_stale_handoff_sha_is_reported_as_not_an_ancestor(repo):
    """A HANDOFF written on a history this HEAD never saw -- the SHA resolves,
    so a naive 'does it exist' check would call it fine."""
    _git(repo, "checkout", "-q", "--orphan", "sidetrack")
    (repo / "other.md").write_text("side\n", encoding="utf-8")
    _git(repo, "add", "other.md")
    _git(repo, "commit", "-q", "-m", "unrelated")
    orphan = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "langgraph-migration")
    (repo / "HANDOFF.md").write_text(f"verified: `{orphan}`\n", encoding="utf-8")

    context = start_context(repo)

    assert "NOT an ancestor of HEAD" in context


def test_a_handoff_with_no_commit_sha_says_so(repo):
    (repo / "HANDOFF.md").write_text("# Handoff\n\nNo SHAs here at all.\n",
                                     encoding="utf-8")

    context = start_context(repo)

    assert "no commit SHA found" in context


def test_a_hex_token_that_is_not_a_commit_is_not_mistaken_for_one(repo):
    """sha256 prefixes and hashed filenames appear in this repo's eval docs."""
    (repo / "HANDOFF.md").write_text(
        "raw sha256 `deadbeefdeadbeefdeadbeef` -- not a commit\n", encoding="utf-8")

    context = start_context(repo)

    assert "no commit SHA found" in context


# ── previous-session recovery detection ─────────────────────────────────────

def _write_recovery(repo: Path, latest: dict | None, marker: dict | None) -> None:
    directory = repo / ".claude" / "session-recovery"
    directory.mkdir(parents=True, exist_ok=True)
    if latest is not None:
        (directory / "latest.json").write_text(json.dumps(latest), encoding="utf-8")
    if marker is not None:
        (directory / "close-marker.json").write_text(json.dumps(marker), encoding="utf-8")


def test_no_recovery_record_reads_as_no_previous_session(repo):
    assert "no previous session record" in start_context(repo)


def test_an_unclosed_previous_session_is_flagged(repo):
    _write_recovery(repo, {"session_id": "prev", "reason": "other"}, None)

    context = start_context(repo)

    assert "did NOT run /session-close" in context


def test_a_cleanly_closed_previous_session_is_reported_clean(repo):
    _write_recovery(repo,
                    {"session_id": "prev", "reason": "prompt_input_exit"},
                    {"session_id": "prev", "state": "closed"})

    context = start_context(repo)

    assert "closed cleanly" in context


def test_a_blocked_session_requires_reconciliation_and_is_never_clean(repo):
    """The state that was missing. A push can succeed and CI still come back
    red; that session is NOT closed, and the next one must be told so."""
    _write_recovery(repo,
                    {"session_id": "prev", "reason": "prompt_input_exit"},
                    {"session_id": "prev", "state": "blocked",
                     "reason_code": "CI_BLOCKING_FAILURE",
                     "run_id": 31039728961, "blocking_jobs": ["python"]})

    context = start_context(repo)

    assert "FINALIZE BLOCKED by CI" in context
    assert "reconcile before new work" in context
    assert "run 31039728961" in context
    assert "jobs: python" in context
    assert "closed cleanly" not in context


def test_a_blocked_marker_from_another_session_does_not_prove_a_clean_close(repo):
    """A stale marker must never vouch for a different session, in either
    direction -- neither certifying it clean nor blaming it for old CI."""
    _write_recovery(repo,
                    {"session_id": "prev-2", "reason": "other"},
                    {"session_id": "prev-1", "state": "blocked",
                     "reason_code": "CI_BLOCKING_FAILURE", "blocking_jobs": ["python"]})

    context = start_context(repo)

    assert "closed cleanly" not in context
    assert "did NOT run /session-close" in context


@pytest.mark.parametrize("marker_extra", [
    {},                                        # no detail at all
    {"reason_code": "CI_BLOCKING_FAILURE"},    # code but no run/jobs
    {"run_id": 123},                           # run but no jobs
    {"blocking_jobs": []},                     # empty job list
    {"blocking_jobs": "python"},               # wrong type
])
def test_a_blocked_marker_missing_fields_still_fails_safe(repo, marker_extra):
    """Fail-open on SHAPE, never on VERDICT: a malformed marker may lose its
    detail, but it must still read as blocked rather than as a clean close."""
    _write_recovery(repo,
                    {"session_id": "prev", "reason": "other"},
                    {"session_id": "prev", "state": "blocked", **marker_extra})

    context = start_context(repo)

    assert "FINALIZE BLOCKED by CI" in context
    assert "reconcile before new work" in context
    assert "closed cleanly" not in context


def test_a_blocked_marker_leaks_no_absolute_path(repo):
    """The marker is local and may hold machine paths; the context block is not."""
    home = os.path.expanduser("~")
    _write_recovery(repo,
                    {"session_id": "prev", "reason": "other"},
                    {"session_id": "prev", "state": "blocked",
                     "blocking_jobs": ["python"],
                     "transcript_path": f"{home}/secret/path.jsonl"})

    context = start_context(repo)

    assert home not in context
    assert "secret/path.jsonl" not in context


def test_a_prepared_but_unfinalized_session_is_distinguished(repo):
    """Different action from both other states: a close commit may exist locally,
    waiting only for push approval."""
    _write_recovery(repo,
                    {"session_id": "prev", "reason": "other"},
                    {"session_id": "prev", "state": "prepared"})

    context = start_context(repo)

    assert "PREPARED but NOT finalized" in context


def test_a_marker_from_a_different_session_does_not_certify_this_one(repo):
    """Otherwise a stale 'closed' marker would vouch for a later crashed session."""
    _write_recovery(repo,
                    {"session_id": "prev-2", "reason": "other"},
                    {"session_id": "prev-1", "state": "closed"})

    context = start_context(repo)

    assert "did NOT run /session-close" in context


def test_an_unreadable_recovery_record_is_reported_not_crashed(repo):
    directory = repo / ".claude" / "session-recovery"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "latest.json").write_text("{ not json", encoding="utf-8")

    context = start_context(repo)

    assert "unreadable" in context


# ── fail-open ───────────────────────────────────────────────────────────────

def test_a_non_repo_directory_degrades_instead_of_failing(tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    payload = {"cwd": str(plain), "source": "startup"}

    proc = run_hook(START_SCRIPT, payload, plain)

    assert proc.returncode == 0
    context = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "SESSION PREFLIGHT DEGRADED" in context


def test_git_being_unavailable_degrades_and_still_exits_zero(repo):
    """The failure the whole fail-open design exists for. PATH is emptied so the
    `git` binary cannot be resolved at all."""
    env = dict(os.environ)
    env["PATH"] = str(repo / "no-such-bin")

    proc = run_hook(START_SCRIPT, {"cwd": str(repo), "source": "startup"}, repo, env=env)

    assert proc.returncode == 0
    context = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "SESSION PREFLIGHT DEGRADED" in context


@pytest.mark.parametrize("raw", ["", "   ", "not json at all", "[]", "null", '{"broken":'])
def test_malformed_hook_input_still_produces_valid_hook_json(repo, raw):
    proc = subprocess.run(
        (sys.executable, str(START_SCRIPT)), input=raw, cwd=str(repo),
        capture_output=True, text=True, timeout=60,
    )

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert isinstance(payload["hookSpecificOutput"]["additionalContext"], str)


def test_the_start_hook_writes_nothing_at_all(repo):
    """SessionStart is read-only: it must not even create the recovery dir."""
    before = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*"))

    start_context(repo)

    after = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*"))
    assert before == after


# ── redaction ───────────────────────────────────────────────────────────────

def test_redact_replaces_user_home_in_both_slash_conventions(monkeypatch):
    module = _load(START_SCRIPT, "hook_start_redact")
    monkeypatch.setenv("USERPROFILE", r"C:\Users\someone")
    monkeypatch.setenv("HOME", r"C:\Users\someone")

    assert "someone" not in module._redact(r"path C:\Users\someone\Desktop\x.csv")
    assert "someone" not in module._redact("path C:/Users/someone/Desktop/x.csv")


def test_the_emitted_context_never_contains_the_real_home_path(repo):
    """Belt and braces against a future field that forgets to redact."""
    home = os.path.expanduser("~")

    context = start_context(repo)

    assert home not in context
    assert home.replace("\\", "/") not in context


def test_no_working_tree_file_contents_reach_the_context(repo):
    secret = "SUPER_SECRET_TOKEN_VALUE_should_never_appear"
    (repo / "config.env").write_text(f"API_KEY={secret}\n", encoding="utf-8")

    context = start_context(repo)

    assert "config.env" in context, "the NAME is legitimate state"
    assert secret not in context, "the CONTENTS are not"


# ── SessionEnd ──────────────────────────────────────────────────────────────

def _run_end(repo: Path, **payload) -> dict:
    base = {"session_id": "s-1", "cwd": str(repo), "hook_event_name": "SessionEnd",
            "reason": "prompt_input_exit",
            "transcript_path": str(repo / "transcript.jsonl")}
    base.update(payload)
    proc = run_hook(END_SCRIPT, base, repo)
    assert proc.returncode == 0, proc.stderr
    record_path = repo / ".claude" / "session-recovery" / "latest.json"
    assert record_path.exists(), "the recovery breadcrumb is the hook's only job"
    return json.loads(record_path.read_text(encoding="utf-8"))


def test_session_end_writes_the_recovery_record(repo):
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")

    record = _run_end(repo)

    assert record["session_id"] == "s-1"
    assert record["reason"] == "prompt_input_exit"
    assert record["branch"] == "langgraph-migration"
    assert record["head"] == _git(repo, "rev-parse", "HEAD")
    assert "README.md" in record["dirty_files"]
    assert record["timestamp"]
    assert record["session_close_marker"]["state"] == "none"


def test_session_end_reports_the_close_marker_when_one_exists(repo):
    _write_recovery(repo, None, {"session_id": "s-1", "state": "prepared"})

    record = _run_end(repo)

    assert record["session_close_marker"]["state"] == "prepared"


def test_session_end_never_copies_transcript_contents(repo):
    transcript = repo / "transcript.jsonl"
    transcript.write_text('{"secret":"TRANSCRIPT_BODY_MUST_NOT_LEAK"}\n', encoding="utf-8")

    record = _run_end(repo, transcript_path=str(transcript))

    assert record["transcript_path"], "the PATH is the recovery handle"
    assert "TRANSCRIPT_BODY_MUST_NOT_LEAK" not in json.dumps(record)


def test_session_end_modifies_no_tracked_file(repo):
    """The hook fires on crashes too. If it could rewrite HANDOFF/MEMORY it would
    overwrite a good handoff with a worse one at the worst possible moment."""
    tracked_before = _git(repo, "ls-files", "-s")
    head_before = _git(repo, "rev-parse", "HEAD")

    _run_end(repo)

    assert _git(repo, "ls-files", "-s") == tracked_before
    assert _git(repo, "rev-parse", "HEAD") == head_before
    porcelain = _git(repo, "status", "--porcelain")
    modified = [ln for ln in porcelain.splitlines() if not ln.startswith("??")]
    assert modified == [], f"tracked files were touched: {modified}"


def test_session_end_survives_malformed_input_without_breaking_exit(repo):
    proc = subprocess.run(
        (sys.executable, str(END_SCRIPT)), input="not json", cwd=str(repo),
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0


def test_session_end_in_a_non_repo_directory_still_exits_zero(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()

    proc = run_hook(END_SCRIPT, {"cwd": str(plain), "reason": "other"}, plain)

    assert proc.returncode == 0


def test_a_killed_write_never_leaves_half_a_json_file(repo):
    """Written to a temp file then os.replace'd, so the next SessionStart cannot
    read a truncated record and report the previous session as 'unreadable'."""
    record = _run_end(repo)
    assert record  # parsed cleanly
    directory = repo / ".claude" / "session-recovery"
    assert not list(directory.glob("*.tmp")), "no temp file may survive the write"


# ── static configuration: the wiring, not the behaviour ─────────────────────

def test_project_settings_declare_both_session_hooks():
    settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    hooks = settings["hooks"]

    for event in ("SessionStart", "SessionEnd"):
        assert event in hooks, f"{event} hook is not configured"
        commands = [h["command"] for group in hooks[event] for h in group["hooks"]]
        assert commands, f"{event} declares no command"


@pytest.mark.parametrize("event,script_name", [
    ("SessionStart", "claude_session_start.py"),
    ("SessionEnd", "claude_session_end.py"),
])
def test_hook_commands_use_repo_relative_paths_that_exist(event, script_name):
    """Two different kinds of token, two different contracts.

    The original version of this test treated `.py` and `.exe` alike and
    required BOTH to exist in the repository. That is wrong for the
    interpreter: `.venv/` is gitignored runtime environment, so
    `.venv/Scripts/python.exe` exists on a developer machine and never in a
    clean checkout. The test therefore passed locally for the wrong reason and
    failed the moment CI ran it (run 31039728961) -- a green result that was
    measuring the developer's venv, not the repository.

    The real contract:
      * EVERY path token must be relative and free of a drive letter -- an
        absolute or user-specific path breaks on any other checkout.
      * The configured `.py` SCRIPT must exist in the repository; a typo there
        fails silently at session start where nobody is watching.
      * The INTERPRETER must be the expected relative runtime path, but its
        existence is deliberately NOT asserted.
    """
    settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    commands = [h["command"] for group in settings["hooks"][event] for h in group["hooks"]]
    command = next(c for c in commands if script_name in c)

    script_refs, interpreter_refs = _split_command_paths(command)

    assert script_refs, f"no .py script found in: {command}"
    assert interpreter_refs, f"no interpreter found in: {command}"

    for ref in script_refs + interpreter_refs:
        assert not Path(ref).is_absolute(), f"{ref} must be repository-relative"
        assert ":" not in ref, f"{ref} looks like a drive-absolute path"

    for ref in script_refs:
        assert (REPO_ROOT / ref).is_file(), f"{ref} does not exist in the repo"

    for ref in interpreter_refs:
        assert ref == EXPECTED_INTERPRETER, (
            f"interpreter is {ref!r}, expected the project runtime "
            f"{EXPECTED_INTERPRETER!r}"
        )


def test_the_path_contract_holds_in_a_clean_checkout_without_a_venv(tmp_path):
    """The regression, reproduced structurally. A fresh checkout has the scripts
    but NO `.venv/` -- exactly CI. The script assertion must still hold and the
    interpreter assertion must NOT depend on the interpreter existing."""
    checkout = tmp_path / "clean-checkout"
    (checkout / "scripts").mkdir(parents=True)
    for name in ("claude_session_start.py", "claude_session_end.py"):
        (checkout / "scripts" / name).write_text("# stub\n", encoding="utf-8")
    assert not (checkout / ".venv").exists(), "a clean checkout has no venv"

    settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    for event in ("SessionStart", "SessionEnd"):
        for group in settings["hooks"][event]:
            for hook in group["hooks"]:
                scripts, interpreters = _split_command_paths(hook["command"])

                for ref in scripts:
                    assert (checkout / ref).is_file(), \
                        f"{ref} must exist even in a venv-less checkout"
                for ref in interpreters:
                    assert not (checkout / ref).exists(), \
                        "the fixture must genuinely lack the interpreter"
                    assert ref == EXPECTED_INTERPRETER
                    assert not Path(ref).is_absolute() and ":" not in ref


@pytest.mark.parametrize("bad_command", [
    r'C:\Python313\python.exe scripts/claude_session_start.py',
    "/usr/bin/python3 scripts/claude_session_start.py",
])
def test_an_absolute_interpreter_path_is_rejected(bad_command):
    """An absolute interpreter is machine-specific: it leaks a local layout and
    breaks on every other checkout."""
    scripts, interpreters = _split_command_paths(bad_command)
    offenders = [r for r in scripts + interpreters
                 if Path(r).is_absolute() or ":" in r or r != EXPECTED_INTERPRETER]

    assert offenders, "an absolute interpreter path must not pass the contract"


@pytest.mark.parametrize("bad_command", [
    ".venv/Scripts/python.exe scripts/does_not_exist.py",
    ".venv/Scripts/python.exe",
])
def test_a_missing_or_absent_script_reference_is_rejected(bad_command):
    """The half of the contract that must stay strict: a typo'd or absent
    script path fails silently at session start, where nobody is watching."""
    scripts, _ = _split_command_paths(bad_command)

    if not scripts:
        return  # no script at all is itself a rejection (asserted in the real test)
    assert not all((REPO_ROOT / ref).is_file() for ref in scripts)


def test_the_skill_forbids_closing_while_a_blocking_ci_failure_remains():
    """The rule that was broken once: a session wrote `closed` while the branch
    tip's python job was red."""
    text = (REPO_ROOT / ".claude" / "skills" / "session-close" / "SKILL.md").read_text(
        encoding="utf-8")

    assert '"blocked"' in text, "the skill must define the blocked marker state"
    assert "CI_BLOCKING_FAILURE" in text
    assert "no blocking CI failure remains" in text
    assert "CI-MOBILE-01" in text, "mobile's non-blocking signature must be named"
    assert "never rerun it" in text, "diff-explained failures must not be rerun"


def test_the_session_close_skill_exists_and_declares_both_modes():
    skill = REPO_ROOT / ".claude" / "skills" / "session-close" / "SKILL.md"
    assert skill.exists(), "the close protocol has no skill to run"
    text = skill.read_text(encoding="utf-8")

    assert text.startswith("---"), "a skill needs YAML frontmatter to be discoverable"
    assert "name: session-close" in text
    assert "## Mode: prepare" in text and "## Mode: finalize" in text


def test_the_recovery_directory_is_gitignored():
    """It holds per-machine session state (exit reason, transcript path) and must
    never become a tracked repository fact."""
    proc = subprocess.run(
        ("git", "check-ignore", "-q", ".claude/session-recovery/latest.json"),
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, ".claude/session-recovery/ is not gitignored"


@pytest.mark.parametrize("rule", ["graph-safety", "testing", "mobile", "documentation"])
def test_each_path_scoped_rule_declares_its_paths(rule):
    path = REPO_ROOT / ".claude" / "rules" / f"{rule}.md"
    assert path.exists(), f"{rule}.md is missing"
    text = path.read_text(encoding="utf-8")

    assert text.startswith("---"), f"{rule}.md has no frontmatter"
    frontmatter = text.split("---", 2)[1]
    assert "paths:" in frontmatter, f"{rule}.md declares no path scope"


# ── local settings must never be shared ─────────────────────────────────────
# `.claude/settings.local.json` is Claude Code's PER-MACHINE settings file. It
# accumulates this machine's permission allowlist -- absolute user paths and
# past shell commands -- so committing it both leaks local state and leaves the
# working tree permanently dirty. These pin the untracking so it cannot silently
# come back via a future `git add -A`.

def _git_repo(*args: str) -> subprocess.CompletedProcess:
    """Run git against the real repo with the developer's GLOBAL excludes
    neutralised, so these assertions test the REPOSITORY's rules and cannot
    pass (or fail) because of one machine's personal git config."""
    return subprocess.run(
        ("git", "-c", f"core.excludesFile={REPO_ROOT / '.no-such-global-excludes'}", *args),
        cwd=REPO_ROOT, capture_output=True, text=True,
    )


def test_local_settings_are_ignored_by_the_repository_gitignore():
    """Asserted against the .gitignore TEXT as well as git's own answer: the
    text check is completely independent of git configuration, and the
    check-ignore run confirms the rule is the one actually winning."""
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".claude/settings.local.json" in gitignore.split(), \
        "the repository .gitignore must carry the rule itself"

    proc = _git_repo("check-ignore", "-v", ".claude/settings.local.json")

    assert proc.returncode == 0, "git does not consider the file ignored"
    source = proc.stdout.split(":", 1)[0]
    assert source.endswith(".gitignore"), f"rule came from {source!r}, not the repo .gitignore"


def test_local_settings_are_not_tracked():
    """`git ls-files` reads the repository INDEX, so this is deterministic and
    unaffected by any global git configuration."""
    proc = _git_repo("ls-files", ".claude/settings.local.json")

    assert proc.stdout.strip() == "", "settings.local.json is still tracked"


def test_no_local_settings_file_is_tracked_anywhere():
    """Guards the general case, not just the one known path."""
    proc = _git_repo("ls-files")
    tracked = proc.stdout.splitlines()

    offenders = [p for p in tracked if p.endswith("settings.local.json")]
    assert offenders == [], f"per-machine settings are tracked: {offenders}"


def test_shared_settings_carry_no_machine_specific_absolute_path():
    """The shared file is committed and reviewed; an absolute user path in it
    would both leak a username and break on any other checkout."""
    text = (REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8")
    lowered = text.lower()

    for needle in ("c:\\users", "c:/users", "/users/", "/home/", "%userprofile%", "$home"):
        assert needle not in lowered, f"{needle!r} appears in shared settings"
    assert os.path.expanduser("~").lower() not in lowered


def test_shared_settings_grant_no_broad_push_or_bypass_permissions():
    """A shared settings file is the wrong place to pre-authorise anything
    irreversible: push, history rewriting, or skipping the permission prompt.
    Those stay a per-action human decision (see CLAUDE.md's push authority)."""
    text = (REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8")
    lowered = text.lower()

    for forbidden in (
        "git push", "--force", "-f ", "force-push", "--no-verify",
        "dangerouslyskippermissions", "bypasspermissions", "acceptedits",
        "autoapprove", "allowedtools", "defaultmode",
    ):
        assert forbidden not in lowered, f"shared settings grant {forbidden!r}"

    settings = json.loads(text)
    assert "permissions" not in settings, \
        "shared settings must not pre-grant tool permissions at all"
    assert set(settings) <= {"$schema", "hooks"}, \
        f"unexpected shared-settings keys: {set(settings) - {'$schema', 'hooks'}}"


def test_every_hook_command_is_bounded_by_a_timeout():
    """A hook without a timeout can hang the session start or the exit -- the
    exact failure the fail-open design exists to prevent."""
    settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))

    for event in ("SessionStart", "SessionEnd"):
        for group in settings["hooks"][event]:
            for hook in group["hooks"]:
                assert hook.get("timeout"), f"{event} hook has no timeout"
                assert hook["timeout"] <= 60, f"{event} timeout is too long to be fail-open"


@pytest.mark.parametrize("script", [START_SCRIPT, END_SCRIPT])
def test_an_unexpected_internal_error_still_exits_zero(repo, script, monkeypatch):
    """The last fail-open backstop. If the hook's own logic raises, the session
    contract must hold: SessionStart still emits valid JSON, SessionEnd still
    lets the exit finish, and neither returns non-zero."""
    module = _load(script, f"hook_boom_{script.stem}")

    def _boom(*_a, **_k):
        raise RuntimeError("simulated internal failure")

    target = "build_context" if script is START_SCRIPT else "build_record"
    monkeypatch.setattr(module, target, _boom)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(
        json.dumps({"cwd": str(repo), "source": "startup", "session_id": "x"})))

    assert module.main() == 0


def test_a_missing_hook_script_cannot_be_silently_wired(tmp_path):
    """If a configured script does not exist the hook simply never runs -- so
    the wiring itself is what must be verified, and it is (above). This pins the
    complementary property: the repo's own configured paths all resolve today."""
    settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))

    for event in ("SessionStart", "SessionEnd"):
        for group in settings["hooks"][event]:
            for hook in group["hooks"]:
                scripts = [t.strip('"') for t in hook["command"].split()
                           if t.strip('"').endswith(".py")]
                assert scripts, f"{event} command references no script"
                for rel in scripts:
                    assert (REPO_ROOT / rel).is_file(), f"{rel} is configured but missing"


def test_claude_md_imports_the_handoff_and_stays_small():
    """The whole point of the refactor: permanent contract only, current state
    by import. A CLAUDE.md that drifts back into narrative costs every session."""
    text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    assert "@HANDOFF.md" in text, "current state must arrive by import, not by hand"
    assert len(text.splitlines()) < 200
