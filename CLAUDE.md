# CLAUDE.md — Claude Code adapter

Claude Code must follow [AGENT_CONTRACT.md](AGENT_CONTRACT.md), the canonical
permanent contract shared by every implementation platform. Read it before
acting. This file contains only Claude-specific wiring.

## Claude behaviour

- `HANDOFF.md` is imported below. Treat it as the current-state snapshot, not
  history. If it conflicts with the repository, the repository wins.
- `.claude/rules/*.md` load automatically for matching paths. Read and obey the
  relevant rule before touching its subsystem; do not remove that path-scoped
  behaviour for adapter symmetry.
- The tracked hooks in `.claude/settings.json` run the shared lifecycle scripts
  at SessionStart and SessionEnd. A degraded preflight is diagnostic only;
  re-derive state before relying on it.
- `/session-close prepare` and `/session-close finalize` are defined by the
  canonical `.claude/skills/session-close/SKILL.md`. Finalize still requires the
  owner's explicit push approval in the current chat.
- The shared one-root-session rule applies: do not actively own this checkout
  while a Codex root session owns it. Serial handoff or a separate worktree is
  required.

Do not duplicate the shared contract or HANDOFF content here. Claude-specific
convenience never weakens the shared safety, testing, reporting, or authority
rules.

---

@HANDOFF.md
