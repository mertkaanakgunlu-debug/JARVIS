# AGENTS.md — Codex adapter

Before acting, Codex must read these repository sources in order:

1. [AGENT_CONTRACT.md](AGENT_CONTRACT.md) — the canonical permanent contract.
2. [HANDOFF.md](HANDOFF.md) — the current-state snapshot, not history. Codex
   must not assume it was automatically included or imported.
3. Every relevant `.claude/rules/*.md` file before touching paths matched by
   that rule's `paths` frontmatter.

Repository code is final truth. If code, HANDOFF, another document, or a task
specification disagree, verify against the repository and report the mismatch;
do not silently choose a convenient version. HANDOFF describes current state
only and must not be treated as an event log.

Codex-specific session-close discovery lives at
`.agents/skills/session-close/SKILL.md`. That adapter points to the single
canonical procedure at `.claude/skills/session-close/SKILL.md`; read the
canonical file in full and execute it exactly.

Push requires the owner's explicit approval in the current chat. Never infer it
from a specification or an earlier session. The shared one-root-session rule is
also binding: a Claude Code and a Codex root session may not simultaneously own
the same checkout because both use `.claude/session-recovery/`. Use serial
handoff or a separate worktree.

Keep this adapter concise. Do not copy HANDOFF or the shared contract into it.
