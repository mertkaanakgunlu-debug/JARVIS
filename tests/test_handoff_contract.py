"""The REAL HANDOFF.md, checked against the contract the preflight enforces.

`test_claude_session_hooks.py` already proves the freshness PARSER works -- it
drives the hook against throwaway repositories carrying deliberately broken
metadata. Nothing proved that *this repository's own* HANDOFF.md satisfies that
parser, so a malformed closing document could only ever be discovered by the
next session's preflight: one session too late, by which point the bad file is
already committed and pushed.

That gap became load-bearing when `/session-close` stopped re-running the whole
Python suite for a documentation-only closing commit
(`.claude/skills/session-close/SKILL.md` §6). The closing artefact needs a check
that actually runs at closing time, so this file is deliberately cheap: file
reads, the hook's own parser, and one bounded git call.

The parser is IMPORTED rather than reimplemented. A second implementation would
only prove that the test agrees with itself, and this suite has been bitten by
exactly that before (see `.claude/rules/testing.md`'s measurement discipline).

Deliberately NOT asserted here: the self-reference rule (no `unpushed`, no
`push approval pending`, no predicted CI result). Those are claims, not tokens
-- a literal substring check would fire on a HANDOFF that merely *quotes* the
rule it is obeying, which is a false failure rather than a caught bug. That rule
stays enforced by `.claude/rules/documentation.md` and review.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import claude_session_start as hook

REPO_ROOT = Path(__file__).resolve().parent.parent
HANDOFF = REPO_ROOT / "HANDOFF.md"

#: The eight sections `.claude/rules/documentation.md` requires, in order.
REQUIRED_SECTIONS = (
    "## 1. Current verified state",
    "## 2. Last completed work",
    "## 3. Operational modes and rollout decisions",
    "## 4. Tests and CI",
    "## 5. Known open issues",
    "## 6. Next engineering priority",
    "## 7. Human-required actions",
    "## 8. Session recovery notes",
)

GIT_TIMEOUT_S = 30.0


@pytest.fixture(scope="module")
def text() -> str:
    return HANDOFF.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def meta(text: str) -> dict:
    parsed = hook._parse_frontmatter(text)
    assert parsed is not None, (
        "HANDOFF.md carries no frontmatter block -- the preflight would read it "
        "as the legacy format and could not verify freshness at all"
    )
    assert parsed, "HANDOFF.md's frontmatter block is malformed or unterminated"
    return parsed


def test_handoff_is_present_and_not_empty(text: str) -> None:
    assert text.strip(), "HANDOFF.md is empty"


def test_schema_is_the_version_the_preflight_expects(meta: dict) -> None:
    assert meta.get("handoff_schema") == str(hook.HANDOFF_SCHEMA)


def test_a_branch_is_declared(meta: dict) -> None:
    assert (meta.get("branch") or "").strip(), "the frontmatter declares no branch"


def test_covered_through_sha_is_a_full_40_character_sha(meta: dict) -> None:
    """A 7-character token is exactly the shape the pre-metadata format used, so
    accepting one would silently reintroduce the format the contract replaced."""
    sha = (meta.get("covered_through_sha") or "").lower()
    assert hook._FULL_SHA_RE.match(sha), \
        f"covered_through_sha is not a full 40-character SHA: {sha!r}"


def test_covered_through_sha_is_a_real_commit_reachable_from_head(meta: dict) -> None:
    """Catches a mistyped or invented SHA at closing time rather than at the next
    session's start.

    A missing object and a shallow checkout are DIFFERENT states and this asks
    git which one it is looking at. Treating "object absent" as "probably a
    shallow clone" and skipping would make an invented SHA -- the exact bug
    worth catching -- indistinguishable from a CI checkout, and the check would
    pass vacuously on it. (It did, in the first version of this test; the
    falsification round is what found it.) `actions/checkout@v4` fetches depth
    1, so the shallow branch is real and stays an explicit skip.
    """
    sha = (meta.get("covered_through_sha") or "").lower()

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ("git", *args), cwd=REPO_ROOT, capture_output=True, text=True,
            timeout=GIT_TIMEOUT_S,
        )

    if git("cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        if git("rev-parse", "--is-shallow-repository").stdout.strip() == "true":
            pytest.skip(
                f"shallow checkout: {sha[:7]} is not in this clone, so ancestry "
                "cannot be verified here"
            )
        pytest.fail(
            f"covered_through_sha {sha[:7]} is not a commit in this repository "
            "-- the clone is complete, so the SHA is wrong, not merely absent"
        )
    assert git("merge-base", "--is-ancestor", sha, "HEAD").returncode == 0, \
        f"covered_through_sha {sha[:7]} is not an ancestor of HEAD"


def test_all_eight_sections_are_present_and_in_order(text: str) -> None:
    positions: list[int] = []
    for heading in REQUIRED_SECTIONS:
        index = text.find(heading)
        assert index != -1, f"HANDOFF.md is missing the required section: {heading}"
        positions.append(index)
    assert positions == sorted(positions), \
        "the eight required sections are present but out of order"
