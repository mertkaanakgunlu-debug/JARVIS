"""Regression guards for the shared Claude Code / Codex infrastructure.

These assertions protect relationships between small repository contracts. They
deliberately avoid full-document snapshots so wording can improve without
weakening canonical ownership, authority, or lifecycle invariants.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SHARED = REPO_ROOT / "AGENT_CONTRACT.md"
CLAUDE = REPO_ROOT / "CLAUDE.md"
CODEX = REPO_ROOT / "AGENTS.md"
HANDOFF = REPO_ROOT / "HANDOFF.md"
CANONICAL_CLOSE = REPO_ROOT / ".claude" / "skills" / "session-close" / "SKILL.md"
CODEX_CLOSE = REPO_ROOT / ".agents" / "skills" / "session-close" / "SKILL.md"
CLAUDE_HOOKS = REPO_ROOT / ".claude" / "settings.json"
CODEX_HOOKS = REPO_ROOT / ".codex" / "hooks.json"

CONTRACT_FILES = (
    SHARED,
    CLAUDE,
    CODEX,
    CANONICAL_CLOSE,
    CODEX_CLOSE,
    CLAUDE_HOOKS,
    CODEX_HOOKS,
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _commands(path: Path) -> dict[str, list[str]]:
    data = json.loads(_text(path))
    return {
        event: [hook["command"] for group in groups for hook in group["hooks"]]
        for event, groups in data["hooks"].items()
    }


def test_shared_contract_exists_and_is_the_canonical_entrypoint() -> None:
    assert SHARED.is_file()
    shared = _text(SHARED)
    assert "canonical, platform-neutral contract" in shared
    assert "AGENT_CONTRACT.md" in _text(CLAUDE)
    assert "AGENT_CONTRACT.md" in _text(CODEX)


def test_claude_adapter_preserves_platform_specific_behaviour() -> None:
    text = _text(CLAUDE)
    assert "@HANDOFF.md" in text
    assert ".claude/rules/*.md" in text
    assert ".claude/skills/session-close/SKILL.md" in text
    assert len(text.splitlines()) < 80


def test_codex_adapter_requires_the_full_read_order() -> None:
    text = _text(CODEX)
    positions = [
        text.index("AGENT_CONTRACT.md"),
        text.index("HANDOFF.md"),
        text.index(".claude/rules/*.md"),
    ]
    assert positions == sorted(positions)
    assert "must not assume it was automatically included" in text
    assert "Repository code is final truth" in text


def test_broken_importer_paths_do_not_survive() -> None:
    combined = "\n".join(_text(path) for path in CONTRACT_FILES)
    assert ".Codex/rules" not in combined
    assert ".Codex/skills" not in combined
    assert ".Codex/worktrees" not in combined


def test_contracts_contain_no_personal_absolute_home_path() -> None:
    personal_home = re.compile(r"(?i)(?:[a-z]:[\\/]+users[\\/]+|/users/|/home/)")
    offenders = [str(path.relative_to(REPO_ROOT)) for path in CONTRACT_FILES
                 if personal_home.search(_text(path))]
    assert offenders == []


def test_codex_close_skill_is_a_thin_adapter_to_the_canonical_skill() -> None:
    adapter = _text(CODEX_CLOSE)
    canonical = _text(CANONICAL_CLOSE)
    assert ".claude/skills/session-close/SKILL.md" in adapter
    assert "one canonical" in adapter
    assert len(adapter.splitlines()) < 30
    assert len(adapter) < len(canonical) // 10
    assert "## Mode: prepare" in canonical and "## Mode: finalize" in canonical


def test_both_platforms_invoke_the_same_lifecycle_scripts() -> None:
    claude = _commands(CLAUDE_HOOKS)
    codex = _commands(CODEX_HOOKS)
    assert claude == codex
    assert set(claude) == {"SessionStart", "SessionEnd"}
    assert "scripts/claude_session_start.py" in claude["SessionStart"][0]
    assert "scripts/claude_session_end.py" in claude["SessionEnd"][0]


def test_hook_commands_find_the_repo_root_from_a_subdirectory() -> None:
    for commands in (_commands(CLAUDE_HOOKS), _commands(CODEX_HOOKS)):
        for event_commands in commands.values():
            for command in event_commands:
                assert command.startswith("powershell -NoProfile -Command")
                assert "git rev-parse --show-toplevel" in command
                assert "Set-Location" in command


def test_codex_hook_file_contains_no_local_trust_or_permission_state() -> None:
    data = json.loads(_text(CODEX_HOOKS))
    assert set(data) == {"hooks"}
    lowered = _text(CODEX_HOOKS).lower()
    for forbidden in ("trusted_hash", "permissions", "token", "secret"):
        assert forbidden not in lowered


def test_handoff_is_referenced_but_not_duplicated_in_codex_adapter() -> None:
    text = _text(CODEX)
    assert "HANDOFF.md" in text
    assert "@HANDOFF.md" not in text
    assert "handoff_schema" not in text
    assert "## 1. Current verified state" not in text
    assert len(text.splitlines()) < 80


def test_push_and_force_push_invariants_live_in_the_shared_contract() -> None:
    shared = _text(SHARED)
    assert re.search(r"explicit approval.*required.*git push", shared,
                     flags=re.IGNORECASE | re.DOTALL)
    assert "Never force-push" in shared


def test_one_active_root_session_per_checkout_is_documented() -> None:
    shared = _text(SHARED).lower()
    codex = _text(CODEX).lower()
    assert "one active root implementation-agent session" in shared
    assert "concurrent root ownership" in shared
    assert "one-root-session rule" in codex
    assert ".claude/session-recovery/" in shared and ".claude/session-recovery/" in codex


def test_gitignore_does_not_blanket_hide_shared_agent_infrastructure() -> None:
    patterns = {
        line.strip() for line in _text(REPO_ROOT / ".gitignore").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "AGENTS.md" not in patterns
    assert ".agents/" not in patterns
    assert ".codex/" not in patterns
    assert ".claude/settings.local.json" in patterns
    assert ".claude/session-recovery/" in patterns
