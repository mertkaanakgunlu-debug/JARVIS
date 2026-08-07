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
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
START_SCRIPT = REPO_ROOT / "scripts" / "claude_session_start.py"
END_SCRIPT = REPO_ROOT / "scripts" / "claude_session_end.py"
STATE_SCRIPT = REPO_ROOT / "scripts" / "claude_session_state.py"

RECOVERY = Path(".claude") / "session-recovery"


def _isolated_env(**overrides: str) -> dict:
    """`os.environ` with the developer's global and system git config removed.

    A fixture repository must behave identically on every machine. This one's
    owner has a GLOBAL ignore rule for `.claude/`, which silently changed what
    `git add` and `git status` did inside these fixtures; `core.autocrlf`,
    `init.defaultBranch` and global aliases can do the same. Git 2.32+ honours
    these variables, so pointing them at paths that do not exist gives every
    test the same empty configuration rather than the machine's.
    """
    nowhere = Path(tempfile.gettempdir()) / "jarvis-tests-no-such-gitconfig"
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = str(nowhere)
    env["GIT_CONFIG_SYSTEM"] = str(nowhere)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env.update(overrides)
    return env

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
                          text=True, check=True, env=_isolated_env())
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
    # The real repository gitignores the recovery directory, and the fixture has
    # to model that: SessionStart writes the identity record there, so without
    # the rule every preflight would report its OWN write as a dirty file.
    (root / ".gitignore").write_text(".claude/session-recovery/\n", encoding="utf-8")
    _git(root, "add", "README.md", ".gitignore")
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
        capture_output=True, text=True, timeout=60, env=env or _isolated_env(),
    )
    return proc


def start_context(repo: Path, source: str = "startup", **extra) -> str:
    payload = {"session_id": "t-1", "cwd": str(repo),
               "hook_event_name": "SessionStart", "source": source, **extra}
    proc = run_hook(START_SCRIPT, payload, repo)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]


def run_state(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Drive the session-state helper as the skill does: a plain CLI call."""
    return subprocess.run(
        (sys.executable, str(STATE_SCRIPT), *args), cwd=str(repo),
        capture_output=True, text=True, timeout=60, env=_isolated_env(),
    )


def current_file(repo: Path) -> Path:
    return repo / RECOVERY / "current.json"


def marker_file(repo: Path) -> Path:
    return repo / RECOVERY / "close-marker.json"


def read_current(repo: Path) -> dict:
    return json.loads(current_file(repo).read_text(encoding="utf-8"))


def read_marker(repo: Path) -> dict:
    return json.loads(marker_file(repo).read_text(encoding="utf-8"))


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


# ── HANDOFF freshness ───────────────────────────────────────────────────────
# The second flaw the CLI acceptance run found. Freshness used to be "is the
# first hex token in the prose an ancestor of HEAD?" -- and a STALE handoff's
# opening section always names an old commit, which is by construction an
# ancestor. So the check reported "verified" for a document describing work
# three commits ago exactly as confidently as for a current one. It is now
# derived from declared metadata and a commit COUNT, which staleness cannot
# satisfy by accident.

def handoff_text(covered: str, *, branch: str = "langgraph-migration",
                 schema: str = "1", body: str = "state\n") -> str:
    return (f"---\nhandoff_schema: {schema}\nbranch: {branch}\n"
            f"covered_through_sha: {covered}\n---\n\n# HANDOFF\n\n{body}")


def commit_handoff(repo: Path, covered: str, **kw) -> None:
    """Write HANDOFF naming the work it covers, then make the closing-doc commit."""
    (repo / "HANDOFF.md").write_text(handoff_text(covered, **kw), encoding="utf-8")
    _git(repo, "add", "HANDOFF.md")
    _git(repo, "commit", "-q", "-m", "docs: refresh handoff")


def commit_work(repo: Path, name: str) -> str:
    (repo / name).write_text("work\n", encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", f"work {name}")
    return _git(repo, "rev-parse", "HEAD")


def handoff_line(context: str) -> str:
    return next(ln for ln in context.splitlines() if ln.startswith("HANDOFF.md"))


def test_a_missing_handoff_is_reported_not_assumed(repo):
    (repo / "HANDOFF.md").unlink()

    context = start_context(repo)

    assert "HANDOFF.md    MISSING" in context


def test_valid_metadata_with_one_closing_commit_reads_current(repo):
    """The shape a correct close produces: the document names the last WORK
    commit, so exactly one commit -- itself -- follows it."""
    covered = _git(repo, "rev-parse", "HEAD")
    commit_handoff(repo, covered)

    line = handoff_line(start_context(repo))

    assert f"current; covers work through {covered[:7]}" in line
    assert "closing-doc commit is HEAD" in line
    assert "STALE" not in line and "INVALID" not in line


def test_a_commit_after_the_closing_doc_reads_stale(repo):
    """The case the old heuristic could not see: work landed after the handoff,
    and the SHA in the file is still a perfectly good ancestor."""
    covered = _git(repo, "rev-parse", "HEAD")
    commit_handoff(repo, covered)
    commit_work(repo, "later.md")

    line = handoff_line(start_context(repo))

    assert "STALE" in line
    assert "HEAD contains 2 commits after covered work" in line
    assert "current;" not in line


def test_many_commits_after_the_closing_doc_report_the_real_count(repo):
    covered = _git(repo, "rev-parse", "HEAD")
    commit_handoff(repo, covered)
    for name in ("a.md", "b.md", "c.md"):
        commit_work(repo, name)

    assert "HEAD contains 4 commits after covered work" in handoff_line(start_context(repo))


def test_a_covered_sha_equal_to_head_is_reported_as_self_reference(repo):
    """The exact rule the owner set after five recurrences. Note the old check
    would have called this the freshest possible state."""
    covered = _git(repo, "rev-parse", "HEAD")
    commit_handoff(repo, covered)
    head = _git(repo, "rev-parse", "HEAD")
    (repo / "HANDOFF.md").write_text(handoff_text(head), encoding="utf-8")

    line = handoff_line(start_context(repo))

    assert "INVALID" in line
    assert "is HEAD itself" in line
    assert "must name the last WORK commit" in line


def test_a_covered_sha_from_another_history_is_invalid(repo):
    _git(repo, "checkout", "-q", "--orphan", "sidetrack")
    (repo / "other.md").write_text("side\n", encoding="utf-8")
    _git(repo, "add", "other.md")
    _git(repo, "commit", "-q", "-m", "unrelated")
    orphan = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "langgraph-migration")
    (repo / "HANDOFF.md").write_text(handoff_text(orphan), encoding="utf-8")

    line = handoff_line(start_context(repo))

    assert "INVALID" in line
    assert "NOT an ancestor of HEAD" in line


def test_metadata_declaring_another_branch_is_invalid(repo):
    """A handoff carried over from a different branch describes different work."""
    covered = _git(repo, "rev-parse", "HEAD")
    commit_handoff(repo, covered, branch="some-other-branch")

    line = handoff_line(start_context(repo))

    assert "INVALID" in line
    assert "declares branch 'some-other-branch'" in line


@pytest.mark.parametrize("covered,why", [
    ("a02d4be", "a short SHA is exactly the shape the old prose heuristic ate"),
    ("", "empty"),
    ("zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz", "not hex"),
    ("a02d4be" + "0" * 40, "too long"),
])
def test_a_covered_sha_that_is_not_a_full_forty_characters_is_invalid(repo, covered, why):
    (repo / "HANDOFF.md").write_text(handoff_text(covered), encoding="utf-8")

    line = handoff_line(start_context(repo))

    assert "INVALID" in line, why
    assert "full 40-character SHA" in line


def test_a_sha_that_is_not_a_commit_here_is_invalid(repo):
    """Forty hex characters is a shape, not a commit."""
    (repo / "HANDOFF.md").write_text(handoff_text("d" * 40), encoding="utf-8")

    line = handoff_line(start_context(repo))

    assert "INVALID" in line
    assert "is not a commit here" in line


@pytest.mark.parametrize("text", [
    "---\nhandoff_schema: 2\nbranch: langgraph-migration\n"
    "covered_through_sha: " + "0" * 40 + "\n---\n",                     # wrong schema
    "---\nbranch: langgraph-migration\n---\n",                          # no schema key
    "---\nhandoff_schema: 1\nthis line has no colon\n---\n",            # malformed line
    "---\nhandoff_schema: 1\nbranch: langgraph-migration\n",            # unterminated
])
def test_malformed_metadata_is_reported_invalid_not_guessed(repo, text):
    (repo / "HANDOFF.md").write_text(text, encoding="utf-8")

    line = handoff_line(start_context(repo))

    assert "INVALID" in line
    assert "current;" not in line and "STALE" not in line


def test_a_legacy_handoff_reports_that_freshness_is_unverifiable(repo):
    """The fixture's HANDOFF is the pre-metadata shape: a SHA in prose. It must
    NOT be classified -- the old check's whole failure was answering confidently
    from exactly this."""
    line = handoff_line(start_context(repo))

    assert "legacy format; freshness cannot be verified" in line
    assert "current;" not in line
    assert "diagnostic only" in line, "an ancestor lookup may still be OFFERED"


def test_a_legacy_handoff_with_no_sha_at_all_still_only_reports_legacy(repo):
    (repo / "HANDOFF.md").write_text("# Handoff\n\nNo SHAs here at all.\n",
                                     encoding="utf-8")

    line = handoff_line(start_context(repo))

    assert "legacy format" in line
    assert "no commit SHA found in the text" in line


def test_a_hex_token_that_is_not_a_commit_is_not_mistaken_for_one(repo):
    """sha256 prefixes and hashed filenames appear in this repo's eval docs."""
    (repo / "HANDOFF.md").write_text(
        "raw sha256 `deadbeefdeadbeefdeadbeef` -- not a commit\n", encoding="utf-8")

    line = handoff_line(start_context(repo))

    assert "no commit SHA found in the text" in line


def test_an_older_sha_in_the_prose_does_not_override_the_metadata(repo):
    """The decisive test. The document's body names an ancestor -- which is what
    the old heuristic would have latched onto and called verified -- while the
    metadata says the work is two commits behind."""
    ancestor = _git(repo, "rev-parse", "HEAD~1")
    covered = _git(repo, "rev-parse", "HEAD")
    commit_handoff(repo, covered,
                   body=f"Last verified green by CI: `{ancestor}`.\n")
    commit_work(repo, "later.md")

    line = handoff_line(start_context(repo))

    assert "STALE" in line
    assert ancestor[:7] not in line
    assert covered[:7] in line


def test_a_single_following_commit_that_is_not_the_closing_doc_is_not_current(repo):
    """Distance 1 alone is not enough: the commit after the covered work has to
    BE the handoff refresh. Here the metadata was updated but something else was
    committed last, so the file describes a tree nobody wrote it against."""
    covered = _git(repo, "rev-parse", "HEAD")
    commit_work(repo, "work.md")
    (repo / "HANDOFF.md").write_text(handoff_text(covered), encoding="utf-8")

    line = handoff_line(start_context(repo))

    assert "INVALID" in line
    assert "does not modify HANDOFF.md" in line
    assert "current;" not in line


def test_the_frontmatter_parser_needs_no_external_yaml_dependency():
    """A hook whose design constraint is that it cannot fail must not gain an
    import that can. The contract is three scalar keys; that does not need YAML."""
    source = START_SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("import yaml", "from yaml", "ruamel", "frontmatter import"):
        assert forbidden not in source, f"the hook grew a {forbidden!r} dependency"

    module = _load(START_SCRIPT, "hook_start_frontmatter")
    parsed = module._parse_frontmatter(
        "---\nhandoff_schema: 1\nbranch: langgraph-migration\n"
        'covered_through_sha: "' + "a" * 40 + '"\n---\n\nbody\n')

    assert parsed == {"handoff_schema": "1", "branch": "langgraph-migration",
                      "covered_through_sha": "a" * 40}
    assert module._parse_frontmatter("# no frontmatter\n") is None
    assert module._parse_frontmatter("---\nunterminated: yes\n") == {}


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
    """Note `identity_status`: matching ids between two records proves only that
    the two records agree. `matched` is SessionEnd's answer to "does the payload
    id equal the AUTHORITATIVE one", and it is what makes the agreement mean
    something -- see test_an_unverified_identity_never_reads_as_a_clean_close."""
    _write_recovery(repo,
                    {"session_id": "prev", "reason": "prompt_input_exit",
                     "identity_status": "matched"},
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


@pytest.mark.parametrize("code", ["CI_BLOCKING_FAILURE", "CI_INFRA_UNAVAILABLE"])
def test_the_blocked_reason_code_reaches_the_preflight(repo, code):
    """The two codes ask for different work -- fix a defect, versus accept that
    the tip was never judged. The reason code used to be a fallback shown only
    when run/jobs were BOTH absent, so a well-formed marker never showed it at
    all and the next session had to guess."""
    _write_recovery(repo,
                    {"session_id": "prev", "reason": "prompt_input_exit"},
                    {"session_id": "prev", "state": "blocked", "reason_code": code,
                     "run_id": 31117623901, "blocking_jobs": ["python", "electron"]})

    context = start_context(repo)

    assert "FINALIZE BLOCKED by CI" in context
    assert code in context, "the classification must survive into the next session"
    assert "run 31117623901" in context
    assert "jobs: python, electron" in context


def _blocked_context(repo: Path, code: str | None) -> str:
    marker = {"session_id": "prev", "state": "blocked",
              "run_id": 31117623901, "blocking_jobs": ["python", "electron"]}
    if code is not None:
        marker["reason_code"] = code
    _write_recovery(repo, {"session_id": "prev", "reason": "prompt_input_exit"}, marker)
    return start_context(repo)


def test_the_two_reason_codes_do_not_render_identically(repo):
    """Falsifiable, and measured false: driven against the PRE-CHANGE
    `claude_session_start.py`, the two contexts came out byte-identical, so this
    fails on its first assertion there. The reason code was unreachable (shown
    only when run id and job list were both absent) and the guidance did not
    exist. A preflight that renders "there is a bug on that tip" the same as
    "nothing is known about that tip" is a coin flip dressed as a report."""
    failure = _blocked_context(repo, "CI_BLOCKING_FAILURE")
    infra = _blocked_context(repo, "CI_INFRA_UNAVAILABLE")

    assert failure != infra
    assert "FAILED on that tip" in failure and "FAILED on that tip" not in infra
    assert "NO verdict" in infra and "NO verdict" not in failure
    # Whatever else differs, the verdict itself does not: neither may read clean.
    for context in (failure, infra):
        assert "FINALIZE BLOCKED by CI" in context
        assert "reconcile before new work" in context
        assert "closed cleanly" not in context


def test_an_infrastructure_block_is_not_reported_as_a_code_failure(repo):
    """`CI_INFRA_UNAVAILABLE` is the ABSENCE of a result. Reporting it as a red
    job would send the next session hunting a bug that was never demonstrated --
    and reporting it as green would be the "unrun check as passed" failure."""
    context = _blocked_context(repo, "CI_INFRA_UNAVAILABLE")

    assert "not evidence of a code failure" in context
    assert "unproven rather than failing" in context
    assert "closed cleanly" not in context, "an absent verdict is not a clean close"


def test_an_infrastructure_block_does_not_lock_the_following_session(repo):
    """Terminal for the session that earned it, not for the repository.

    Two different guarantees, each checked on the surface that actually
    enforces it: the SessionStart preflight is fail-open and reads whatever is
    on disk (`_blocked_context`'s marker is deliberately minimal -- fine for
    that surface, see the round-3 tests for why it is NOT fine for `prepare`);
    the state machine is fail-closed as of round 3 and needs a marker with the
    shape `block` actually produces to prove `prepare` really allows it through.
    """
    context = _blocked_context(repo, "CI_INFRA_UNAVAILABLE")
    assert "not by itself a bar to working" in context

    # `start_context` recorded THIS session's identity ("t-1"), which is not the
    # blocked marker's ("prev") -- so a GENUINE inherited block must not stop
    # `prepare`. Replace the preflight-only marker above with a structurally
    # valid one before exercising the state-machine half of this guarantee.
    marker_file(repo).write_text(
        json.dumps(_valid_marker(session_id="prev", state="blocked",
                                 reason_code="CI_INFRA_UNAVAILABLE")),
        encoding="utf-8")

    assert run_state(repo, "prepare").returncode == 0
    assert read_marker(repo)["state"] == "prepared"


@pytest.mark.parametrize("code", [None, "CI_SOMETHING_ELSE", "", "CI_INFRA_UNAVAILBLE"])
def test_an_unrecognised_reason_code_still_reads_as_blocked(repo, code):
    """The reader is OPEN where the writer is closed. `block` refuses codes
    outside the vocabulary, but a marker written before that vocabulary existed
    -- or by a later version that adds a code -- must still be READ. Fail-closed
    here would turn an old marker into "no detail" or, far worse, into silence."""
    context = _blocked_context(repo, code)

    assert "FINALIZE BLOCKED by CI" in context
    assert "reconcile before new work" in context
    assert "closed cleanly" not in context
    assert "run 31117623901" in context, "the detail it DOES carry must survive"


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
    env = _isolated_env(PATH=str(repo / "no-such-bin"))

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


def test_the_start_hook_writes_only_the_gitignored_identity_record(repo):
    """The invariant NARROWED, it did not loosen.

    SessionStart used to write nothing at all. It now writes exactly one file:
    the authoritative session-identity record. That write exists because this
    hook is the only component that ever sees an authoritative session id, and
    everything downstream previously had to guess one. What still holds — and is
    what this asserts — is that it modifies no tracked file and writes nothing
    outside the gitignored recovery directory.
    """
    before = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*"))

    start_context(repo, session_id="sess-W")

    after = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*"))
    assert [p for p in after if p not in before] == [
        ".claude", ".claude/session-recovery", ".claude/session-recovery/current.json",
    ]
    assert [p for p in before if p not in after] == []
    modified = [ln for ln in _git(repo, "status", "--porcelain").splitlines()
                if not ln.startswith("??")]
    assert modified == [], f"tracked files were touched: {modified}"


# ── authoritative session identity ──────────────────────────────────────────
# The hole this closes: the close marker used to be JSON the MODEL typed, so its
# `session_id` was whatever the model believed the session was called -- and a
# model can only infer that from a transcript filename, from "the newest file",
# or from a guess. A guessed id that happens to look right certifies the wrong
# session. The id now travels one way only: Claude Code's SessionStart payload ->
# current.json -> every later transition, with no argument that could carry
# another value.

def test_startup_records_the_authoritative_session_identity(repo):
    start_context(repo, source="startup", session_id="sess-startup-1")

    current = read_current(repo)

    assert current["schema"] == 1
    assert current["session_id"] == "sess-startup-1"
    assert current["source"] == "startup"
    assert current["branch"] == "langgraph-migration"
    assert current["head"] == _git(repo, "rev-parse", "HEAD")
    assert current["updated_at"]


def test_resume_updates_the_same_identity_idempotently(repo):
    start_context(repo, source="startup", session_id="sess-A")
    first = read_current(repo)

    start_context(repo, source="resume", session_id="sess-A")
    second = read_current(repo)

    assert second["session_id"] == first["session_id"] == "sess-A"
    assert second["head"] == first["head"]
    assert second["source"] == "resume"


def test_clear_replaces_the_identity_because_it_can_carry_a_new_session(repo):
    """`clear` is the source it would be most tempting to skip -- the repo state
    is unchanged, so the preflight only prints a short summary. But it can begin
    a NEW session id, and an identity record left pointing at the old one would
    make every later transition certify a session that no longer exists."""
    start_context(repo, source="startup", session_id="sess-old")

    start_context(repo, source="clear", session_id="sess-new")

    assert read_current(repo)["session_id"] == "sess-new"


def test_compact_keeps_the_identity_recorded(repo):
    start_context(repo, source="startup", session_id="sess-C")

    start_context(repo, source="compact", session_id="sess-C")

    current = read_current(repo)
    assert current["session_id"] == "sess-C"
    assert current["source"] == "compact"


def test_a_payload_without_a_session_id_leaves_the_identity_untouched(repo):
    """Fail-open, but never destructive: a degraded start must not overwrite a
    good identity record with an empty one."""
    start_context(repo, source="startup", session_id="sess-good")
    before = read_current(repo)

    context = start_context(repo, source="startup", session_id="")

    assert read_current(repo) == before
    assert "SESSION PREFLIGHT DEGRADED" in context
    assert "no session_id" in context


def test_an_unrecognised_source_is_not_recorded_but_never_blocks(repo):
    """Writing an unknown source string into the identity record would make the
    record's own provenance unverifiable, so the write is skipped -- loudly."""
    context = start_context(repo, source="telepathy", session_id="sess-X")

    assert not current_file(repo).exists()
    assert "SESSION PREFLIGHT DEGRADED" in context
    assert "unrecognised source" in context


def test_an_unwritable_recovery_path_degrades_instead_of_blocking_the_start(repo):
    """The recovery directory replaced by a FILE, so `mkdir` cannot succeed and
    the atomic write fails. A session must still start: bookkeeping that can
    lock the owner out of their own repository is worse than no bookkeeping."""
    (repo / ".claude").mkdir(exist_ok=True)
    (repo / ".claude" / "session-recovery").write_text("not a directory\n", encoding="utf-8")

    proc = run_hook(START_SCRIPT, {"session_id": "sess-B", "cwd": str(repo),
                                   "source": "startup"}, repo)

    assert proc.returncode == 0
    context = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "SESSION PREFLIGHT DEGRADED" in context
    assert "session identity NOT recorded" in context


def test_the_identity_record_carries_no_path_and_no_transcript(repo):
    """It is a local file, but it is also the input to every later transition;
    keeping it to a fixed, derived set of fields means there is no slot a path
    or a secret could occupy."""
    transcript = repo / "transcript.jsonl"
    transcript.write_text('{"secret":"TRANSCRIPT_BODY_MUST_NOT_LEAK"}\n', encoding="utf-8")

    start_context(repo, source="startup", session_id="sess-P",
                  transcript_path=str(transcript))

    raw = current_file(repo).read_text(encoding="utf-8")
    assert "transcript" not in raw.lower()
    assert "TRANSCRIPT_BODY_MUST_NOT_LEAK" not in raw
    assert os.path.expanduser("~") not in raw
    assert str(repo) not in raw
    assert set(json.loads(raw)) == {
        "schema", "session_id", "source", "updated_at", "branch", "head"}


@pytest.mark.parametrize("flag", ["--session-id", "--session_id"])
def test_prepare_refuses_a_session_id_argument(repo, flag):
    """An identity that can be passed in is an identity that can be guessed, so
    the transitions expose no such argument at all."""
    start_context(repo, source="startup", session_id="sess-A")

    proc = run_state(repo, "prepare", flag, "sess-forged")

    assert proc.returncode != 0
    assert "unrecognized arguments" in proc.stderr
    assert not marker_file(repo).exists()


def test_prepare_refuses_without_an_authoritative_identity(repo):
    proc = run_state(repo, "prepare")

    assert proc.returncode != 0
    assert "current.json" in proc.stderr
    assert "do not create one by hand" in proc.stderr
    assert not marker_file(repo).exists()


def test_prepare_takes_its_identity_only_from_the_current_record(repo):
    start_context(repo, source="startup", session_id="sess-authoritative")

    proc = run_state(repo, "prepare")

    assert proc.returncode == 0, proc.stderr
    marker = read_marker(repo)
    assert marker["state"] == "prepared"
    assert marker["session_id"] == "sess-authoritative"
    assert marker["head"] == _git(repo, "rev-parse", "HEAD")
    assert marker["branch"] == "langgraph-migration"


def test_prepare_survives_the_work_commits_a_real_session_makes(repo):
    """HEAD is compared by ancestry, not equality: a session that committed
    something is the only kind of session that has anything to close."""
    start_context(repo, source="startup", session_id="sess-A")
    (repo / "work.md").write_text("work\n", encoding="utf-8")
    _git(repo, "add", "work.md")
    _git(repo, "commit", "-q", "-m", "work commit")

    proc = run_state(repo, "prepare")

    assert proc.returncode == 0, proc.stderr
    assert read_marker(repo)["head"] == _git(repo, "rev-parse", "HEAD")


def test_prepare_refuses_when_the_recorded_history_was_rewritten(repo):
    """A reset/rebase under the session means the recorded head is no longer
    ancestral, so the identity record can no longer vouch for this tree."""
    start_context(repo, source="startup", session_id="sess-A")
    _git(repo, "checkout", "-q", "--orphan", "rewritten")
    _git(repo, "commit", "-q", "-m", "unrelated root", "--allow-empty")
    _git(repo, "branch", "-q", "-D", "langgraph-migration")
    _git(repo, "checkout", "-q", "-b", "langgraph-migration")

    proc = run_state(repo, "prepare")

    assert proc.returncode != 0
    assert "no longer an ancestor" in proc.stderr


def test_close_refuses_a_marker_from_another_session(repo):
    """The exact failure the whole change exists for: a marker left by an
    earlier session must not certify a later one."""
    start_context(repo, source="startup", session_id="sess-A")
    assert run_state(repo, "prepare").returncode == 0
    start_context(repo, source="clear", session_id="sess-B")

    proc = run_state(repo, "close")

    assert proc.returncode != 0
    assert "different session" in proc.stderr
    assert read_marker(repo)["state"] == "prepared", "the refused transition changed nothing"


def test_close_refuses_when_head_moved_since_prepare(repo):
    start_context(repo, source="startup", session_id="sess-A")
    assert run_state(repo, "prepare").returncode == 0
    (repo / "later.md").write_text("later\n", encoding="utf-8")
    _git(repo, "add", "later.md")
    _git(repo, "commit", "-q", "-m", "work after prepare")

    proc = run_state(repo, "close")

    assert proc.returncode != 0
    assert "re-run prepare" in proc.stderr
    assert read_marker(repo)["state"] == "prepared"


def test_close_refuses_when_the_branch_changed_since_prepare(repo):
    start_context(repo, source="startup", session_id="sess-A")
    assert run_state(repo, "prepare").returncode == 0
    _git(repo, "checkout", "-q", "-b", "somewhere-else")

    proc = run_state(repo, "close")

    assert proc.returncode != 0
    assert "branch" in proc.stderr
    assert read_marker(repo)["state"] == "prepared"


def test_close_promotes_a_matching_prepared_marker(repo):
    start_context(repo, source="startup", session_id="sess-A")
    assert run_state(repo, "prepare").returncode == 0
    prepared_at = read_marker(repo)["prepared_at"]

    assert run_state(repo, "close").returncode == 0

    marker = read_marker(repo)
    assert marker["state"] == "closed"
    assert marker["session_id"] == "sess-A"
    assert marker["prepared_at"] == prepared_at
    assert marker["closed_at"]


def test_close_is_idempotent_and_keeps_the_first_close_time(repo):
    start_context(repo, source="startup", session_id="sess-A")
    run_state(repo, "prepare")
    assert run_state(repo, "close").returncode == 0
    first = read_marker(repo)

    assert run_state(repo, "close").returncode == 0

    assert read_marker(repo)["closed_at"] == first["closed_at"]


def test_block_applies_the_same_identity_guards(repo):
    start_context(repo, source="startup", session_id="sess-A")
    assert run_state(repo, "prepare").returncode == 0
    start_context(repo, source="clear", session_id="sess-B")

    proc = run_state(repo, "block", "--run-id", "31046333431", "--blocking-jobs", "python")

    assert proc.returncode != 0
    assert "different session" in proc.stderr
    assert read_marker(repo)["state"] == "prepared"


def test_block_refuses_when_head_moved_since_prepare(repo):
    start_context(repo, source="startup", session_id="sess-A")
    assert run_state(repo, "prepare").returncode == 0
    (repo / "later.md").write_text("later\n", encoding="utf-8")
    _git(repo, "add", "later.md")
    _git(repo, "commit", "-q", "-m", "work after prepare")

    proc = run_state(repo, "block", "--run-id", "1", "--blocking-jobs", "python")

    assert proc.returncode != 0
    assert "re-run prepare" in proc.stderr


def test_block_records_only_its_validated_vocabulary(repo):
    start_context(repo, source="startup", session_id="sess-A")
    run_state(repo, "prepare")

    proc = run_state(repo, "block", "--run-id", "31046333431",
                     "--blocking-jobs", "python,electron")

    assert proc.returncode == 0, proc.stderr
    marker = read_marker(repo)
    assert marker["state"] == "blocked"
    assert marker["reason_code"] == "CI_BLOCKING_FAILURE"
    assert marker["run_id"] == "31046333431"
    assert marker["blocking_jobs"] == ["python", "electron"]


@pytest.mark.parametrize("args", [
    ("--run-id", "C:/Users/someone/transcript.jsonl"),
    ("--blocking-jobs", "C:/Users/someone/secret.jsonl"),
    ("--reason-code", "../../etc/passwd"),
    ("--reason-code", "free form prose with a secret"),
])
def test_block_refuses_path_like_or_free_form_metadata(repo, args):
    """`block` is the only transition that takes metadata at all, so it is the
    only place a path or a secret could enter the recovery files."""
    start_context(repo, source="startup", session_id="sess-A")
    run_state(repo, "prepare")

    proc = run_state(repo, "block", *args)

    assert proc.returncode != 0
    assert read_marker(repo)["state"] == "prepared"


def test_block_cannot_be_relabelled_as_a_clean_close(repo):
    """A blocked session's honest next step is to fix the failure and prepare
    again -- not to promote the marker it already has."""
    start_context(repo, source="startup", session_id="sess-A")
    run_state(repo, "prepare")
    assert run_state(repo, "block", "--blocking-jobs", "python").returncode == 0

    proc = run_state(repo, "close")

    assert proc.returncode != 0
    assert read_marker(repo)["state"] == "blocked"


# ── the reason-code vocabulary ──────────────────────────────────────────────
# It used to be a SHAPE check while the comment beside it claimed a vocabulary,
# so any UPPER_SNAKE string was accepted. A reason code is read by the next
# session's preflight to decide what to DO, and an unrecognised one is
# indistinguishable from a typo -- which is how a blocked session gets misread.

@pytest.mark.parametrize("code", ["CI_BLOCKING_FAILURE", "CI_INFRA_UNAVAILABLE"])
def test_block_accepts_every_code_in_the_fixed_vocabulary(repo, code):
    start_context(repo, source="startup", session_id="sess-A")
    run_state(repo, "prepare")

    proc = run_state(repo, "block", "--reason-code", code,
                     "--run-id", "31117623901", "--blocking-jobs", "python,electron")

    assert proc.returncode == 0, proc.stderr
    marker = read_marker(repo)
    assert marker["state"] == "blocked"
    assert marker["reason_code"] == code


@pytest.mark.parametrize("code", [
    "CI_SOMETHING_ELSE",     # shape-valid UPPER_SNAKE, but not a known code
    "CI_INFRA_UNAVAILBLE",   # a plausible typo of a real one
    "INFRA",
    "ci_infra_unavailable",  # right word, wrong case
])
def test_block_refuses_a_reason_code_outside_the_fixed_vocabulary(repo, code):
    """The regression that motivated the change: every one of these passes the
    old `^[A-Z][A-Z0-9_]{0,63}$` shape check, and none of them means anything
    to a reader."""
    start_context(repo, source="startup", session_id="sess-A")
    run_state(repo, "prepare")

    proc = run_state(repo, "block", "--reason-code", code, "--blocking-jobs", "python")

    assert proc.returncode != 0
    assert "fixed vocabulary" in proc.stderr
    assert read_marker(repo)["state"] == "prepared", "a refused block changed nothing"


def test_the_block_help_names_the_whole_vocabulary(repo):
    """The CLI has to be self-describing: a closed vocabulary nobody can
    enumerate is just a refusal waiting to happen."""
    proc = run_state(repo, "block", "--help")

    assert proc.returncode == 0
    assert "CI_BLOCKING_FAILURE" in proc.stdout
    assert "CI_INFRA_UNAVAILABLE" in proc.stdout


def test_an_infrastructure_block_is_no_shortcut_to_closed(repo):
    """`CI_INFRA_UNAVAILABLE` is a narrower claim than `CI_BLOCKING_FAILURE`,
    not a softer one. It must not buy a transition that the other code cannot."""
    start_context(repo, source="startup", session_id="sess-A")
    run_state(repo, "prepare")
    assert run_state(repo, "block", "--reason-code", "CI_INFRA_UNAVAILABLE",
                     "--blocking-jobs", "python,electron").returncode == 0

    proc = run_state(repo, "close")

    assert proc.returncode != 0
    assert read_marker(repo)["state"] == "blocked"


def test_a_later_session_can_prepare_over_an_inherited_blocked_marker(repo):
    """The one legitimate route out of a block -- and why `blocked` can be
    terminal without being a dead end.

    A blocked session is over. A LATER session inherits the marker as history
    rather than as a verdict on its own work, and prepares under its OWN
    identity. That is what makes forbidding both `blocked -> closed` and
    `blocked -> prepare` safe: the repository is not stranded behind an outage
    that is already over, it just changes hands.
    """
    start_context(repo, source="startup", session_id="sess-A")
    run_state(repo, "prepare")
    run_state(repo, "block", "--reason-code", "CI_INFRA_UNAVAILABLE",
              "--run-id", "31117623901", "--blocking-jobs", "python,electron")
    assert read_marker(repo)["state"] == "blocked"

    # A new session on the same, unchanged tip. Nothing was re-run, no empty
    # commit was manufactured, and the old marker was not relabelled.
    start_context(repo, source="startup", session_id="sess-B")

    assert run_state(repo, "prepare").returncode == 0
    marker = read_marker(repo)
    assert marker["state"] == "prepared"
    assert marker["session_id"] == "sess-B", "the marker must carry the NEW identity"
    assert "reason_code" not in marker, "a fresh prepare must not inherit block metadata"

    assert run_state(repo, "close").returncode == 0
    assert read_marker(repo)["state"] == "closed"


def test_the_inherited_blocked_marker_still_blocks_its_own_session(repo):
    """The other half: `close` under the SAME session must still refuse."""
    start_context(repo, source="startup", session_id="sess-A")
    run_state(repo, "prepare")
    run_state(repo, "block", "--reason-code", "CI_INFRA_UNAVAILABLE",
              "--blocking-jobs", "python")

    # Same identity, no new run, no new evidence -- `close` must still refuse.
    assert run_state(repo, "close").returncode != 0
    assert read_marker(repo)["state"] == "blocked"


@pytest.mark.parametrize("code", ["CI_BLOCKING_FAILURE", "CI_INFRA_UNAVAILABLE"])
def test_prepare_is_refused_once_this_session_is_blocked(repo, code):
    """The gap this closes, and it was open: `blocked -> closed` was refused,
    but `blocked -> prepared -> closed` was not, and those reach the same place
    one step apart. Driven against the PRE-CHANGE helper this fails outright --
    `prepare` returned 0 and walked the marker back to `prepared`, with no new
    evidence of any kind.

    Both codes, because `CI_INFRA_UNAVAILABLE` is a NARROWER claim than
    `CI_BLOCKING_FAILURE`, not a softer one: "the jobs never ran" must not buy a
    transition that "the jobs failed" cannot.
    """
    start_context(repo, source="startup", session_id="sess-A")
    run_state(repo, "prepare")
    run_state(repo, "block", "--reason-code", code,
              "--run-id", "31117623901", "--blocking-jobs", "python,electron")

    proc = run_state(repo, "prepare")

    assert proc.returncode != 0
    assert "terminal" in proc.stderr, proc.stderr
    marker = read_marker(repo)
    assert marker["state"] == "blocked", "a refused prepare must change nothing"
    assert marker["reason_code"] == code, "nor may it quietly drop the classification"
    assert marker["run_id"] == "31117623901"


def test_a_blocked_session_has_no_route_to_closed_however_many_steps(repo):
    """The two refusals composed. Neither on its own is the property worth
    having -- what matters is that no SEQUENCE of transitions reaches `closed`
    from a block, because the whole point of blocking is that the evidence for
    closing was never obtained."""
    start_context(repo, source="startup", session_id="sess-A")
    run_state(repo, "prepare")
    run_state(repo, "block", "--reason-code", "CI_BLOCKING_FAILURE",
              "--blocking-jobs", "python")

    assert run_state(repo, "close").returncode != 0        # the one-step route
    assert run_state(repo, "prepare").returncode != 0      # ...and the detour
    assert run_state(repo, "close").returncode != 0        # still refused after it

    assert read_marker(repo)["state"] == "blocked"


def test_re_blocking_still_refreshes_the_metadata_of_a_blocked_session(repo):
    """`blocked -> blocked` must survive the new `prepare` refusal. Reading more
    job logs can legitimately change the classification or add a run id, and the
    marker is the honest place for that -- unlike `prepare`, it cannot be a step
    towards `closed`."""
    start_context(repo, source="startup", session_id="sess-A")
    run_state(repo, "prepare")
    run_state(repo, "block", "--reason-code", "CI_BLOCKING_FAILURE",
              "--blocking-jobs", "python")

    proc = run_state(repo, "block", "--reason-code", "CI_INFRA_UNAVAILABLE",
                     "--run-id", "31117623901", "--blocking-jobs", "python,electron")

    assert proc.returncode == 0, proc.stderr
    marker = read_marker(repo)
    assert marker["state"] == "blocked"
    assert marker["reason_code"] == "CI_INFRA_UNAVAILABLE"
    assert marker["blocking_jobs"] == ["python", "electron"]


# ── an existing-but-unreadable marker is fail-CLOSED ────────────────────────
# `_read_json` answers `None` for BOTH "no such file" and "the file is there but
# will not parse", and `prepare`'s next act is to overwrite that file. Measured
# against the pre-fix helper, all six shapes below returned 0 and left
# `prepared` behind -- including the truncated one whose surviving text still
# read `"state": "blocked"`. Absent means nothing to lose; unreadable means a
# record is there and nobody can say what it claims.
#
# Note which side of the trust boundary this is. The SessionStart hook stays
# fail-OPEN on the same file (test_an_unreadable_recovery_record_is_reported_
# not_crashed): a session must always be able to START. Certifying a close is a
# CLAIM, and a claim over unreadable evidence is what must not be made.

CORRUPT_MARKERS = [
    # the one that actually loses evidence: a blocked marker truncated mid-write
    pytest.param('{"schema": 1, "state": "blocked", "session_id": "sess', id="truncated"),
    pytest.param("this is not json", id="not-json"),
    pytest.param("", id="empty-file"),
    pytest.param('["blocked"]', id="json-list"),
    pytest.param('"blocked"', id="json-string"),
    pytest.param("null", id="json-null"),
]


def _recovery_listing(repo: Path) -> dict:
    """Every file in the recovery directory, name -> bytes.

    Compared whole rather than per field, so one assertion covers "the marker's
    bytes are untouched" AND "nothing was renamed, replaced, repaired, or left
    behind as a `.tmp`".
    """
    return {p.name: p.read_bytes()
            for p in sorted((repo / RECOVERY).iterdir()) if p.is_file()}


def test_prepare_succeeds_when_no_marker_file_exists(repo):
    """Case A, stated on its own so the refusals below cannot be mistaken for a
    blanket one: ABSENT is still the ordinary path and still works."""
    start_context(repo, source="startup", session_id="sess-A")
    assert not marker_file(repo).exists()

    assert run_state(repo, "prepare").returncode == 0
    assert read_marker(repo)["state"] == "prepared"


@pytest.mark.parametrize("payload", CORRUPT_MARKERS)
def test_prepare_refuses_an_existing_but_unreadable_marker(repo, payload):
    """Case C: refuse, and leave the evidence exactly as found."""
    start_context(repo, source="startup", session_id="sess-A")
    marker_file(repo).write_text(payload, encoding="utf-8")
    before = _recovery_listing(repo)

    proc = run_state(repo, "prepare")

    assert proc.returncode != 0
    assert "refusing to overwrite recovery evidence" in proc.stderr, proc.stderr
    assert _recovery_listing(repo) == before, \
        "the refused prepare touched the recovery directory"
    assert marker_file(repo).read_text(encoding="utf-8") == payload


@pytest.mark.parametrize("command", ["close", "block"])
def test_close_and_block_still_refuse_an_unreadable_marker(repo, command):
    """Tightening `prepare` must not have loosened the other two transitions.
    Both already refused an unreadable marker; both must still refuse, and
    neither may write."""
    start_context(repo, source="startup", session_id="sess-A")
    marker_file(repo).write_text('{"schema": 1, "state": "blocked"', encoding="utf-8")
    before = _recovery_listing(repo)

    proc = run_state(repo, command)

    assert proc.returncode != 0
    assert _recovery_listing(repo) == before, \
        f"the refused {command} touched the recovery directory"


# ── round 3: a parseable JSON object is not necessarily a genuine marker ────
# `{}` and an incomplete `{"state": "blocked"}` both survive the parse/type
# check that closed the previous gap (they ARE dict objects), and this is the
# regression that check left open: `{}` has no `state`, so the identity guard
# read it as "not blocked" and let `prepare` overwrite it; `{"state":
# "blocked"}` with no `session_id` read as "blocked, but not THIS session's" --
# exactly the shape of a legitimately inherited marker -- and was overwritten
# on that same basis. Measured against the pre-this-round worktree, every case
# in STRUCTURALLY_INVALID_MARKERS returned rc=0 and the marker became
# `prepared`, discarding whatever the object had actually contained.

def _valid_marker(session_id: str = "sess-A", state: str = "prepared", **overrides) -> dict:
    """A structurally valid marker for `state`, built directly rather than
    through the CLI so exactly one field can be broken per test case without
    the CLI's own write-time validation getting in the way first."""
    marker = {"schema": 1, "state": state, "session_id": session_id,
              "head": "a" * 40, "branch": "langgraph-migration"}
    if state == "prepared":
        marker["prepared_at"] = "2026-01-01T00:00:00+00:00"
    elif state == "closed":
        marker.update(prepared_at="2026-01-01T00:00:00+00:00",
                      closed_at="2026-01-01T00:05:00+00:00")
    elif state == "blocked":
        marker.update(prepared_at="2026-01-01T00:00:00+00:00",
                      blocked_at="2026-01-01T00:05:00+00:00",
                      reason_code="CI_BLOCKING_FAILURE", blocking_jobs=["python"])
    marker.update(overrides)
    return marker


STRUCTURALLY_INVALID_MARKERS = [
    pytest.param({}, id="empty-object"),
    pytest.param({"state": "blocked"}, id="blocked-missing-everything-else"),
    pytest.param({"state": "blocked", "session_id": "sess-OTHER"},
                id="blocked-missing-schema-and-head"),
    pytest.param({**_valid_marker(state="prepared"), "schema": 2}, id="wrong-schema"),
    pytest.param({k: v for k, v in _valid_marker(state="prepared").items() if k != "schema"},
                id="missing-schema"),
    pytest.param({**_valid_marker(state="prepared"), "state": "bogus"}, id="unknown-state"),
    pytest.param({**_valid_marker(state="blocked"), "reason_code": None},
                id="blocked-missing-reason-code"),
    pytest.param({**_valid_marker(state="blocked"), "blocking_jobs": "python"},
                id="blocking-jobs-wrong-type"),
    pytest.param({**_valid_marker(state="closed"), "closed_at": ""},
                id="closed-missing-closed-at"),
]


@pytest.mark.parametrize("marker", STRUCTURALLY_INVALID_MARKERS)
def test_prepare_refuses_a_parseable_but_structurally_invalid_marker(repo, marker):
    """Case C's other half. Refuse, and leave the evidence exactly as found --
    the same bar as an unparsable marker, because from `prepare`'s point of view
    they are the same failure: a record it cannot trust enough to overwrite."""
    start_context(repo, source="startup", session_id="sess-A")
    payload = json.dumps(marker)
    marker_file(repo).write_text(payload, encoding="utf-8")
    before = _recovery_listing(repo)

    proc = run_state(repo, "prepare")

    assert proc.returncode != 0
    assert "not a valid recovery marker" in proc.stderr, proc.stderr
    assert _recovery_listing(repo) == before, \
        "the refused prepare touched the recovery directory"
    assert marker_file(repo).read_text(encoding="utf-8") == payload


def test_a_structurally_valid_inherited_marker_still_allows_prepare(repo):
    """Regression guard: tightening structural validation must not tighten the
    inherited-block route this whole mechanism exists to keep open. A GENUINE
    blocked marker from a different session must still let this session
    prepare."""
    start_context(repo, source="startup", session_id="sess-A")
    marker_file(repo).write_text(
        json.dumps(_valid_marker(session_id="sess-OTHER", state="blocked")),
        encoding="utf-8")

    proc = run_state(repo, "prepare")

    assert proc.returncode == 0, proc.stderr
    assert read_marker(repo)["session_id"] == "sess-A"


def test_a_structurally_valid_same_session_blocked_marker_still_refuses_prepare(repo):
    """The other regression guard: a GENUINE blocked marker belonging to THIS
    session must still be refused -- structural validation is a new gate in
    front of the existing rule, not a replacement for it."""
    start_context(repo, source="startup", session_id="sess-A")
    marker_file(repo).write_text(
        json.dumps(_valid_marker(session_id="sess-A", state="blocked")),
        encoding="utf-8")

    proc = run_state(repo, "prepare")

    assert proc.returncode != 0
    assert "terminal" in proc.stderr, proc.stderr


@pytest.mark.parametrize("command", ["close", "block"])
def test_close_and_block_distinguish_absent_from_malformed_markers(repo, command):
    """The misleading message this round also fixes. Before it, BOTH cases
    raised the byte-identical `no close marker exists -- run \\`prepare\\`
    first` -- accurate for an absent file, false for a present-but-broken one,
    and pointing at a command (`prepare`) that would, correctly, also refuse."""
    start_context(repo, source="startup", session_id="sess-A")
    assert not marker_file(repo).exists()

    absent = run_state(repo, command)
    assert absent.returncode != 0
    assert "no close marker exists" in absent.stderr

    marker_file(repo).write_text("{}", encoding="utf-8")

    malformed = run_state(repo, command)
    assert malformed.returncode != 0
    assert "no close marker exists" not in malformed.stderr, \
        "a marker that exists must not be reported as if it were absent"
    assert "close marker exists but" in malformed.stderr


def test_show_redacts_the_session_id_and_prints_no_path(repo):
    start_context(repo, source="startup", session_id="sess-abcdefghijklmnop")
    run_state(repo, "prepare")

    proc = run_state(repo, "show")

    assert proc.returncode == 0, proc.stderr
    assert "sess-abc..." in proc.stdout
    assert "sess-abcdefghijklmnop" not in proc.stdout
    assert "langgraph-migration" in proc.stdout
    assert "prepared" in proc.stdout
    assert str(repo) not in proc.stdout
    assert os.path.expanduser("~") not in proc.stdout


def test_no_test_writes_into_the_real_repository_recovery_directory(repo):
    """The hooks and the helper must resolve their paths from the repository
    they are pointed at, never from a constant. Running a whole lifecycle
    against a throwaway repo while pytest's own cwd IS the real repository is
    the faithful check -- a hardcoded path would show up here immediately."""
    real = REPO_ROOT / RECOVERY
    before = {p.name: p.read_bytes() for p in real.glob("*")} if real.exists() else {}

    start_context(repo, source="startup", session_id="sess-Z")
    assert run_state(repo, "prepare").returncode == 0
    assert run_state(repo, "close").returncode == 0
    _run_end(repo, session_id="sess-Z")

    after = {p.name: p.read_bytes() for p in real.glob("*")} if real.exists() else {}
    assert after == before, "a test wrote into the owner's real recovery directory"


def test_the_skill_forbids_hand_authored_session_identity():
    """The prohibition has to live in the skill text, because the skill is what
    the model reads. The helper can refuse a bad transition; only the skill can
    stop the model routing around the refusal with an editor."""
    text = (REPO_ROOT / ".claude" / "skills" / "session-close" / "SKILL.md").read_text(
        encoding="utf-8")

    assert "claude_session_state.py prepare" in text
    assert "claude_session_state.py close" in text
    assert "claude_session_state.py block" in text

    for forbidden in (
        "deriving a session id from a transcript filename or path",
        "treating the newest transcript as",
        "copying, retyping, or otherwise supplying a session id by hand",
        "creating `current.json` yourself",
        "writing `close-marker.json` (or any recovery JSON) directly with an editor",
        "working around a refusal from the helper by hand-writing the JSON it declined",
    ):
        assert forbidden in text, f"the skill does not forbid: {forbidden}"

    assert '{"state": "prepared", "session_id"' not in text, \
        "the hand-authored marker template is what made the identity guessable"
    assert '{"state": "closed", "session_id"' not in text


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


def test_session_end_records_a_matched_identity(repo):
    start_context(repo, source="startup", session_id="s-1")

    record = _run_end(repo, session_id="s-1")

    assert record["identity_status"] == "matched"


def test_session_end_reports_a_missing_current_record_as_unverified(repo):
    """A session that pre-dates the mechanism, or one whose SessionStart write
    failed. There is nothing to check against, so nothing is claimed."""
    record = _run_end(repo, session_id="s-1")

    assert record["identity_status"] == "current_missing"


def test_session_end_reports_a_mismatched_identity_without_substituting_it(repo):
    """Writing the recorded id into `latest.json` when the payload disagrees
    would manufacture the very agreement the field exists to measure."""
    start_context(repo, source="startup", session_id="s-authoritative")

    record = _run_end(repo, session_id="s-guessed")

    assert record["identity_status"] == "mismatch"
    assert record["session_id"] == "s-guessed"


@pytest.mark.parametrize("identity", ["mismatch", "current_missing", None])
def test_an_unverified_identity_never_reads_as_a_clean_close(repo, identity):
    """Even with a perfectly formed `closed` marker whose id matches. `None` is
    the record shape written before this field existed: absence is not proof."""
    latest = {"session_id": "prev", "reason": "prompt_input_exit"}
    if identity is not None:
        latest["identity_status"] = identity
    _write_recovery(repo, latest, {"session_id": "prev", "state": "closed"})

    context = start_context(repo)

    assert "closed cleanly" not in context
    assert "identity UNVERIFIED" in context
    assert "reconcile before new work" in context


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

    # The marker JSON itself moved into the helper (identity must not be
    # hand-authored), so what the skill must still carry is the TRANSITION and
    # its trigger -- the rule, not the file format.
    assert "claude_session_state.py block" in text, \
        "the skill must define how a blocked session is recorded"
    assert "CI_BLOCKING_FAILURE" in text
    assert "no blocking CI failure remains" in text
    # CI-MOBILE-01 was cleared at the source on 2026-08-06, so mobile no longer
    # has a wave-through signature. The skill must still NAME it -- a reader who
    # meets a red `mobile` job needs to be told the old exemption is gone rather
    # than left to rediscover it as "the known cosmetic one".
    assert "CI-MOBILE-01" in text, "mobile's classification must name CI-MOBILE-01"
    assert "never rerun it" in text, "diff-explained failures must not be rerun"


# ── a CI provider outage is a TERMINAL blocked state ────────────────────────
# Written after run 31117623901 (2026-08-06): every job died at `Set up job`
# with `Failed to resolve action download info. Error: Service Unavailable`,
# the one allowed rerun hit the same wall, and a correctly pushed tip was left
# with no verdict.
#
# The first attempt at a fix added a `workflow_dispatch` trigger so a later
# session could start a fresh run on the same tip by hand. It does not work
# here: GitHub resolves `workflow_dispatch` from the repository's DEFAULT
# branch, which is `main`, and `main` carries no `.github/` directory at all
# (verified 2026-08-07: `git ls-tree -r --name-only origin/main -- .github` is
# empty, and `gh repo view --json defaultBranchRef` says `main`). The trigger
# would have been unreachable while LOOKING like a recovery mechanism, which is
# worse than not having one -- so these tests now assert its absence.
#
# The protocol instead ends the session blocked, and the next session's own
# push earns the next verdict.

def _skill_text() -> str:
    return (REPO_ROOT / ".claude" / "skills" / "session-close" / "SKILL.md").read_text(
        encoding="utf-8")


def _skill_prose() -> str:
    """`_skill_text()` with every run of whitespace collapsed to one space.

    A markdown file's line WRAPPING is not a contract. Asserting a sentence that
    happens to fit on one line passes today and fails the moment someone reflows
    the paragraph -- which is the fragile-prose trap, not a real regression.
    (It caught this suite once already: a phrase split across a wrap.)

    Use this for assertions about what the skill SAYS. Use `_skill_text()` for
    assertions about a literal artefact -- a command line, a table cell -- where
    the line itself is the thing being checked.
    """
    return " ".join(_skill_text().split())


def test_the_skill_separates_infrastructure_failure_from_a_real_one():
    prose = _skill_prose()

    assert "CI_INFRA_UNAVAILABLE" in prose, "the infra class must be named"
    assert "CI_BLOCKING_FAILURE" in prose
    assert "CI-FLAKE-CHROMA-01" in prose, "the third class must still be distinguished"
    # The distinction has to be checkable, not vibes: a provider signature. Both
    # of these were read back verbatim from run 31117623901's own job logs
    # (2026-08-07) rather than paraphrased from the incident.
    assert "Failed to resolve action download info" in prose
    assert "The operation was canceled" in prose, \
        "an externally cancelled test run is part of this class"


def test_the_skill_requires_reading_the_log_before_calling_it_infrastructure():
    """`failure` and `cancelled` occur in every class, so a conclusion alone must
    never be enough -- that is precisely how a real red job gets excused."""
    assert "gh run view --job" in _skill_text(), \
        "the skill must say how to read a job log"
    prose = _skill_prose()

    assert "Never assign `CI_INFRA_UNAVAILABLE` from a conclusion alone" in prose
    assert "the honest default when you cannot prove" in prose, \
        "the fallback classification must be the strict one"


def test_the_skill_bounds_the_rerun_budget_to_one_with_no_third_attempt():
    """The budget has to be a number. "Rerun if it looks flaky" is how a red job
    gets re-run until it goes green, which is the same error as reporting an
    unrun check as passed."""
    prose = _skill_prose()

    assert "At most **one** rerun" in prose, "the budget must be countable"
    assert "Never rerun the same run repeatedly" in prose
    assert "there is no third attempt" in prose, \
        "spending the budget must lead somewhere, not loop"
    assert "The **first run is always reported**" in prose, \
        "a rerun must never hide the first result"
    assert "does **not** prove a root cause" in prose


def test_the_skill_gives_the_known_chroma_flake_that_same_single_rerun():
    """The one class that may be rerun at all, and it gets the same budget as
    everything else rather than a licence of its own."""
    prose = _skill_prose()

    assert "CI-FLAKE-CHROMA-01" in prose
    assert "no such table: acquire_write" in prose, \
        "the flake is claimable only from its signature"
    assert "one rerun, per the budget below" in prose


def test_the_skill_makes_an_infrastructure_block_terminal():
    """Replaces an earlier draft that routed recovery through a manually
    dispatched run. Each prohibition below is a route that was considered and
    rejected -- none of them can turn "never ran" into "passed"."""
    prose = _skill_prose()

    assert "TERMINAL for the session" in prose
    assert "Do not manufacture an empty commit" in prose
    assert "Do not keep re-running the exhausted run" in prose
    assert "Do not touch `main`" in prose


def test_the_skill_depends_on_no_manual_dispatch_recovery():
    """`workflow_dispatch` may appear here only as a PROHIBITION, with the reason
    recorded so it is not re-added by someone who rediscovers the same dead end.

    Note the asymmetry with the workflow test below: there the check is on the
    parsed trigger mapping, because prose naming a trigger is not a trigger."""
    prose = _skill_prose()

    assert "gh workflow run" not in prose, \
        "the skill must not instruct a manual CI dispatch"
    assert "Do not add a `workflow_dispatch` trigger" in prose
    assert "default branch" in prose, "the reason it cannot work must be recorded"


def test_the_skill_keeps_blocked_from_reaching_closed_by_any_route(repo):
    """Both refusals, and the fact that they are enforced by the helper rather
    than by this document -- a rule that lives only in prose is enforced by
    whoever remembers it."""
    prose = _skill_prose()

    assert "never relabelled, and never re-prepared" in prose
    assert "`blocked → closed` *and* `blocked → prepare`" in prose
    assert "no one-step and no two-step route out" in prose
    # ...and the helper it points at really does refuse both.
    start_context(repo, source="startup", session_id="sess-A")
    run_state(repo, "prepare")
    run_state(repo, "block", "--reason-code", "CI_INFRA_UNAVAILABLE",
              "--blocking-jobs", "python")
    assert run_state(repo, "close").returncode != 0
    assert run_state(repo, "prepare").returncode != 0


def test_the_skill_lets_a_later_session_work_over_an_inherited_block():
    """Terminal for that session, not for the repository. Without this the
    protocol would trade one failure mode (closing on an unproven tip) for
    another (a repository permanently frozen by an outage that is over)."""
    prose = _skill_prose()

    assert "not a debt the next session has to pay off" in prose
    assert "does not by itself stop this session working" in prose


def test_the_skill_records_the_unreadable_marker_refusal():
    """Missing and unreadable must not read as one case in the protocol either.
    The "do not repair it" half matters as much as the refusal: the tempting
    response to a corrupt marker is to rewrite it into a valid one, which
    destroys the same evidence more deliberately."""
    prose = _skill_prose()

    assert "*Missing* and *unreadable* are different states" in prose
    assert "Do not delete, repair, rename, regenerate or hand-write the marker" in prose
    assert "There is deliberately no repair command" in prose
    assert "fail-**open**" in prose, \
        "the SessionStart asymmetry must stay recorded -- starting a session is " \
        "availability, certifying a close is a claim"
    assert "not the shape a genuine marker has" in prose, \
        "a parseable-but-fake marker object (e.g. `{}`) must be documented as " \
        "unreadable too, not just a parse failure"
    assert "`close` and `block` recognise the same distinction" in prose


def test_the_skill_still_decides_per_job_for_all_three_jobs():
    """The property the outage must not erode: a workflow headline is not a
    decision source, and mobile has no exemption any more."""
    text = _skill_text()

    assert "gh run view <id> --json jobs" in text
    for job in ("`python`", "`electron`", "`mobile`"):
        assert job in text, f"{job} must have its own classification"
    assert "the old \"known cosmetic signature\" exemption is **gone**" in text, \
        "the CI-MOBILE-01 wave-through must not come back"


# ── the workflow itself ─────────────────────────────────────────────────────

def _ci_workflow() -> tuple[dict, dict]:
    """Parse `.github/workflows/ci.yml` and return `(workflow, triggers)`.

    Parsed rather than grepped on purpose. The assertions below are about the
    trigger MAPPING, and a substring search would be answered by any comment or
    prose that merely names a trigger -- in either direction: it would pass for
    a deleted trigger whose comment survived, and fail for a file that only
    mentions one it does not declare.

    Both spellings of the trigger key are accepted because the two readers
    disagree: YAML 1.1 treats a bare `on` as a boolean, so PyYAML hands it back
    under the key `True`, while GitHub reads YAML 1.2 where it stays `"on"`.
    """
    import yaml  # PyYAML==6.0.3, pinned in requirements-lock.txt -- which is what
    # CI installs. Imported here so an absent PyYAML fails these two tests loudly
    # rather than collapsing the whole module; a config contract that cannot be
    # checked should be noisy, not skipped.
    text = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict), "the workflow declares no trigger mapping"
    return workflow, triggers


def test_the_ci_workflow_is_triggered_only_by_push_and_pull_request():
    """No manual dispatch, by decision rather than by omission.

    A `workflow_dispatch` trigger was added here and then removed: GitHub
    resolves it from the DEFAULT branch, `main` carries no `.github/` at all,
    and a trigger that cannot be invoked is worse than none -- it reads as a
    recovery route while being an empty one. An exact-set assertion, so adding
    it back is a deliberate act that fails this test rather than a quiet one.
    """
    _, triggers = _ci_workflow()

    assert set(triggers) == {"push", "pull_request"}, \
        "ci.yml's trigger set changed; manual-dispatch recovery is not available here"
    for event in ("push", "pull_request"):
        assert triggers[event]["branches"] == ["main", "langgraph-migration"], \
            f"the {event} trigger's branches changed"


def test_the_ci_workflow_adds_no_new_failure_suppression():
    """Session-lifecycle work must not become a way to stop seeing failures.
    `mobile` keeps its long-standing `continue-on-error` (owner decision); the
    two blocking jobs must not acquire one."""
    workflow, _ = _ci_workflow()
    jobs = workflow["jobs"]

    assert set(jobs) == {"python", "mobile", "electron"}
    for name in ("python", "electron"):
        assert "continue-on-error" not in jobs[name], \
            f"{name} is a blocking job and must not suppress its own failure"
    assert jobs["mobile"].get("continue-on-error") is True, \
        "mobile's continue-on-error is an owner decision this work must not change"


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
