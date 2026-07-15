# J.A.R.V.I.S. — Safety & Confirmation Model

> Updated 2026-07-15 (Faz 5). Phase 2 (ToolSpec metadata, commit `166a205`) and Phase 3
> (confirmation gate node, commit `7e7e471`) shipped 2026-05-24 but, per the 2026-07-14 review,
> didn't protect anything end-to-end. **Faz 4 closed that gap** — see "What Faz 4 changed" below.
> **Faz 5 extended it to a second, dynamically-discovered tool source (MCP)** without changing the
> gate itself at all — see "What Faz 5 changed" below.
> Treat this file as current; if it and the code disagree, trust the code and fix this file.

## Current safety mechanisms

| Mechanism | Where | What it covers |
|---|---|---|
| `DENY_PATTERNS` | `jarvis/tools/shell.py:9` | Blocks `rm -rf`, `format`, `del /f`, etc. in `shell_run` |
| System prompt rules | `jarvis/prompts/core/*.md` (see [prompt_loader.py](../jarvis/prompts/prompt_loader.py)) | "NEVER" directives (no hallucination, no fake data, etc.) — **not** `jarvis/prompts/system.md`, a dead pointer file left over from before the Phase 1 prompt-modularization split |
| `ToolSpec` risk metadata | `jarvis/tool_registry.py` | `risk_level` (L1-L3) + `requires_confirmation` per tool |
| **`policy_guard` kernel (Faz 4)** | `jarvis/policy_guard.py` | Transport-agnostic risk classification: per-*action* gating (not per-tool) for the four mixed-risk external_api tools, plus the kill-switch veto. Single choke point — the graph's confirmation node calls it. **Faz 5 confirmed the design worked as intended**: MCP tools (`jarvis/mcp_integration.py`) call the exact same `get_spec()`/`policy_guard.evaluate()` path with zero changes to this module — they're just more rows in `TOOL_SPECS`, added dynamically instead of statically. |
| **MCP tool gating (Faz 5)** | `jarvis/mcp_integration.py` (`_classify()`) | Every tool discovered from a connected MCP server gets a `ToolSpec` synthesized at connect time — fail-closed by default (L3 + `requires_confirmation=True`) unless explicitly known to be read-only/inconsequential-navigation (a short allow-list, e.g. Playwright's `browser_snapshot`). Registered into the same `TOOL_SPECS` dict via `tool_registry.register_dynamic_spec()`, so every mechanism in this table already covers it. |
| Confirmation gate | `jarvis/graph/nodes.py` (`make_confirmation_node`) | Interrupts the graph before tool calls `policy_guard` says need confirmation. **Now wired into all three interfaces** (see below) and **on by default**. |
| **Kill switch (Faz 4)** | `jarvis/kill_switch.py`, persisted at `data/kill_switch.json` | Emergency stop for external-effect (L3) actions specifically — file writes/todos/etc. (L2) are unaffected. Defaults to enabled (armed). `/killswitch off <reason>` (CLI) or `/killswitch on` to toggle; survives a process restart by design (it's a file, not a Settings field) — a trip stays tripped until someone deliberately re-arms it. |
| **Audit log (Faz 4)** | `jarvis/audit_log.py`, append-only at `data/audit_log.jsonl` | Two event kinds per risk_level ≥ 2 tool call: `decision` (policy_guard's ruling — auto_approved / confirm_required / user_approved / user_denied / blocked_kill_switch, written in `confirmation_node`) and `execution_start`/`execution_end` (the call actually ran, with outcome — written by `_HudEventCallback` in `jarvis/agent.py`, the same LangChain callback attached in `chat()`/`chat_stream()`/`resume_and_stream()`, so this fires for every transport). Never truncates/rewrites existing lines. |
| Meta-memory write-guard (Faz 2) | `jarvis/tools/files.py` (`PROTECTED_WRITE_PREFIXES`, checked in `write()`) | Unconditional, always-on — `file_write` raises `PermissionError` for any path under `jarvis/prompts/core/` (persona/safety directives), regardless of what the agent is instructed to do. Read access is unaffected. |
| SSRF guard (Faz 4) | `jarvis/tools/webfetch.py` (`_is_blocked_url`) | `url_read`/`deep_web_research` refuse to fetch localhost, private/link-local/reserved ranges, and cloud metadata endpoints (`169.254.169.254`) — checked against the *resolved* IP, not just the hostname string, so a public domain that DNS-rebinds to an internal address is still blocked. |
| `/system/wake` auth (Faz 4) | `jarvis/api_routers/system.py` | Now requires `X-API-Key` like every other mobile router; `/system/ping` stays auth-free by design (it's the liveness probe the mobile app uses to decide whether to *try* waking the PC). |
| Recursion cap (Faz 4) | `Settings.graph_recursion_limit` (default 30), passed as LangGraph's `recursion_limit` in `agent.py`'s `chat()`/`chat_stream()` config | A model stuck retrying a tool call fails with a clear `GraphRecursionError` instead of looping unbounded. |
| Agent-node timeout (Faz 4) | `Settings.agent_llm_timeout_sec` (default 90s), `jarvis/graph/nodes.py`'s `make_agent_node` | A wedged provider connection surfaces as a clear in-conversation error instead of hanging the turn (and, in voice mode, leaving JARVIS listening in silence) forever. |

## Per-action, not per-tool (BUG-6 — fixed)

The four gated `external_api` tools (`google_calendar`, `gmail`, `google_drive`, `itu_mail`) each
mix read actions with write actions under one `ToolSpec`. `policy_guard._READ_ACTIONS` downgrades
the documented read actions (list/search/read/download/...) back to L1/no-confirm for exactly
those four tools — every other tool's actions still share its `ToolSpec.risk_level` uniformly, since
they don't have this split to begin with. See `jarvis/policy_guard.py`'s module docstring for the
exact action tables.

## Kill switch vs. confirmation gate — different jobs

- **Confirmation gate**: asks the user, per call, before a risky action runs. Needs an interactive
  loop to answer it (CLI text prompt, spoken yes/no over voice, or a client hitting
  `POST /chat/confirm/{conf_id}`) — a background `TaskExecutor` job has no such channel, so a task
  that hits a confirmable action fails clearly instead (`jarvis/task_executor.py`'s
  `ConfirmationRequired` handling) rather than hanging or silently proceeding.
- **Kill switch**: a blanket "no" for every L3 (external-effect) action, with no prompt at all —
  the point is an emergency stop that works even if the interactive loop asking questions is itself
  the thing behaving badly. Checked first, inside `confirmation_node`, before the gate's own
  enabled/disabled state is even consulted.

## What Faz 4 changed (previously: "Known gap")

- `confirmation_gate_enabled` now defaults to **`True`** (`jarvis/config.py`) — was `False`.
- All three interfaces now handle an interrupted call:
  - **CLI text** (`jarvis/cli.py`): catches `ConfirmationRequired`, shows what's pending, prompts
    y/n (+ optional reason), resumes via `agent.resume_and_stream()`.
  - **Voice** (`jarvis/cli.py`'s `--voice` loop, `jarvis/voice_api.py`'s wakeword/PTT loop and the
    `/ws` remote-audio session in `jarvis/api.py`): `chat_stream()`'s `__jarvis_confirm__` JSON
    marker is detected (`jarvis/voice/session.py`'s `parse_confirm_marker`) instead of being spoken
    verbatim, replaced with a natural spoken question, and the *next* utterance is treated as the
    yes/no answer (anything not recognized as affirmative denies — fail-safe, same default as the
    CLI prompt's `default="n"`).
  - **API**: `POST /chat` now catches `ConfirmationRequired` and returns
    `{"confirmation_required": true, "id": ..., "payload": ...}` instead of an opaque 500
    (BUG-confirm-payload); `POST /chat/stream` already carried the JSON marker through as an SSE
    frame. A client still needs to actually build a UI around this — **not done this phase**
    (no Electron/mobile UI renders a confirmation prompt yet; see Known limits below).
- The system prompt (`jarvis/prompts/core/02_tool_policy.md`) no longer tells the model it never
  needs to ask — it now says the system itself pauses for risky actions and describes how to react
  to an approval/denial coming back.
- `python_run` reclassified L2→L3 + `requires_confirmation=True` (BUG-1) — it was more powerful
  than `shell_run` (arbitrary unsandboxed Python from any absolute path) while sitting at a lower
  gate. **This is an access-control fix, not a sandbox** — the subprocess itself still has no
  resource/network restrictions; true sandboxing is a deferred hardening item, not implemented.

## What Faz 5 changed

- New `jarvis/mcp_integration.py` connects to configured external MCP servers (disabled by
  default) and merges their tools into the graph as a second, dynamically-discovered tool source.
  Ships with one real server: Microsoft's official Playwright MCP (real browser automation —
  navigate/click/type/snapshot/screenshot/evaluate JS/…), flip `MCP_PLAYWRIGHT_ENABLED=True` to
  enable it.
- **A browser tool is a materially bigger step than the existing `web_search`/`url_read`** — both
  already feed untrusted web page text to the model (an existing, unchanged prompt-injection
  surface), but neither gives the model the ability to *act* on a page. Playwright does: a poisoned
  page's content could get the model to *decide* to click/submit/type something. The mitigation is
  the same gate this whole document describes, just applied here: every Playwright tool beyond pure
  inspection/navigation (click, type, fill_form, select_option, file_upload, drag, drop, hover,
  handle_dialog, evaluate, run_code_unsafe) defaults to L3 + `requires_confirmation=True`, so the
  model deciding to click something and the click actually happening are still separated by the
  user approving that specific, described call.
- `policy_guard.describe_call()`'s `_DETAIL_KEYS` gained `element`/`url`/`text` — without this, a
  pending `browser_click` confirmation would have shown just the bare tool name with no indication
  of what was about to be clicked, undermining the informed-consent point of asking at all.
- No change to the gate's mechanics, the kill switch, or the audit log — see the `policy_guard`
  kernel row above for why none of those needed to change.

## Known limits (honest, not aspirational)

- **No Electron/mobile UI for approving a confirmation.** The API returns the right structured
  payload; nothing renders a prompt from it yet — that's a real, unstarted UI component + wiring
  task, not a build/tooling problem (Node.js was installed 2026-07-15, post-Faz-4, and `npm run
  build` passes clean — see [MEMORY.md](../MEMORY.md) and [HANDOFF.md](../HANDOFF.md)).
- **Voice confirmation phrasing is functional, not fully localized** — the spoken question wrapper
  is bilingual (`jarvis/voice/session.py`'s `describe_confirmation`), but the per-call description
  embedded in it (`jarvis/policy_guard.py`'s `describe_call`) is always in English technical form
  (e.g. "send an email (to=alice@x.com, subject=Toplantı)"), even in a Turkish session.
- **A background `TaskExecutor` job cannot answer a confirmation** — by design (see above), not a
  bug, but worth knowing: an async-submitted query that turns out to need one just fails with an
  actionable error telling the user to ask interactively instead.
- **`python_run`'s reclassification is a gate, not a sandbox** (see above).
- **Kill switch scope is L3 only** — it does not block L2 (reversible local writes: `file_write`,
  `todo`, `spotify`, ...). This is deliberate (an emergency stop for JARVIS acting on the *outside
  world*, not a full halt of all functionality) but worth knowing if you expected it to block more.
