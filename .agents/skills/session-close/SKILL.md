---
name: session-close
description: Close a JARVIS working session by executing the repository's canonical shared session-close procedure.
---

# Codex session-close adapter

This adapter contains no independent close protocol.

1. Read `.claude/skills/session-close/SKILL.md` in full. It is the one canonical
   procedure for both Claude Code and Codex.
2. Execute the requested `prepare` or `finalize` mode exactly as written there.
   If no mode was supplied, use its documented default.
3. Apply `AGENT_CONTRACT.md` and `HANDOFF.md` throughout. Do not infer that
   Codex imported HANDOFF automatically.

Do not duplicate, abbreviate, or reinterpret the canonical checklist here.
