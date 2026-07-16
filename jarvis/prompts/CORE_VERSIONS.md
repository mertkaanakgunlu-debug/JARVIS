# Core prompt versions (Faz 2 — meta memory)

Human-edited only. Bump the version and set Updated to today whenever *you*
(a human, or a Claude Code development session under direct human review —
NOT the runtime JARVIS agent) edit the corresponding `jarvis/prompts/core/*.md`
file. The runtime agent can never write to that directory at all —
`jarvis/tools/files.py`'s `write()` refuses any path under `jarvis/prompts/core/`
(`PROTECTED_WRITE_PREFIXES`) regardless of what it's asked to do. Read live via
the `/meta` CLI command.

This file itself lives one level above `jarvis/prompts/core/` deliberately —
`prompt_loader.py` globs every `*.md` file directly inside `core/` into the
composed system prompt, and this table must never end up as literal text in
that prompt.

| File | Version | Updated | Note |
|---|---|---|---|
| 01_persona.md | 1 | 2026-05-24 | Phase 1 modularization |
| 02_tool_policy.md | 3 | 2026-07-14 | Faz 4: removed "never ask for confirmation" directive — gate is now live |
| 03_voice_policy.md | 1 | 2026-05-24 | Phase 1 modularization |
| 04_agent_registry.md | 1 | 2026-05-24 | Phase 1 modularization |
| 05_memory_policy.md | 1 | 2026-05-24 | Phase 1 modularization |
| 06_context_injection.md | 3 | 2026-07-15 | GPT-5.6 review remediation Faz 3: framed retrieved blocks as untrusted reference data, not instructions |
