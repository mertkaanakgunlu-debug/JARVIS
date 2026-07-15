# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-15 — Faz 5 (MCP Katmanı)

**Context:** Owner said "faz 5 ile devam et, eksik kurulum varsa tamamlayalım — tarayıcı
izinlerini verebilirim" (continue with Faz 5, complete any missing setup, offered browser
permissions). Faz 4 was still fully uncommitted from the prior session (23 modified + 4 new
files) — asked and got explicit go-ahead to commit it first (`55cd807`) before starting Faz 5, so
the two phases' diffs don't mix. Then asked a second question — ROADMAP.md's Faz 5 bullet only
names ha-mcp (Home Assistant) as the eventual MCP target, but that's hardware-gated to Faz 6 and
the owner has no HA instance — which concrete external MCP server to integrate and verify against.
Owner picked the recommended option: **Playwright MCP** (Microsoft's official browser-automation
server), which also explains the "browser permissions" offer literally (granting a controlled
process permission to open/drive a real browser).

## What happened this session (all uncommitted — see Git state below)

1. **New `jarvis/mcp_integration.py`** (`McpToolManager`) — connects to configured external MCP
   servers via the official `langchain-mcp-adapters` package (new dependency,
   `langchain-mcp-adapters==0.3.0`, `mcp` pulled in transitively) and merges their tools into the
   graph as a second, dynamically-discovered tool source alongside the 36 native `@tool` wrappers
   (dual layer, unchanged, per the roadmap). Every discovered tool gets a `ToolSpec` synthesized at
   connect time via new `tool_registry.register_dynamic_spec()`, inserted into the exact same
   `TOOL_SPECS` dict the native tools live in — `policy_guard`, `audit_log`, and the async scheduler
   (`task_executor.py`'s `should_async()`) all cover MCP tools with **zero code changes** to any of
   them, since they only ever call `get_spec()`/read `TOOL_SPECS`.
2. **Ships with one real server, disabled by default:** `Settings.mcp_playwright_enabled` (+
   `mcp_playwright_headless`) — flip `MCP_PLAYWRIGHT_ENABLED=True` in `.env`.
   `Settings.mcp_servers` is a generic JSON escape hatch (`{name: {command, args, transport}}`,
   `MultiServerMCPClient`'s own config shape) for any other future server — e.g. Faz 6's ha-mcp
   should need a `.env` entry, not new Python code.
3. **Fail-closed classification (the phase's own risk callout — "no MCP tool may bypass the
   gate")**: `mcp_integration._classify()` allow-lists a short, explicit set of Playwright tool
   names confirmed (live) to be pure inspection (`browser_snapshot`, `browser_take_screenshot`,
   `browser_console_messages`, `browser_find`, `browser_network_request(s)`) → L1, or
   inconsequential navigation (`browser_navigate`, `browser_navigate_back`, `browser_wait_for`,
   `browser_resize`, `browser_close`, `browser_tabs`) → L2, both no-confirm. **Every other tool —
   click, type, fill_form, select_option, file_upload, drag, drop, hover, handle_dialog, evaluate,
   run_code_unsafe, and any name never seen before — defaults to L3 + `requires_confirmation=True`**,
   same gate as `shell_run`/`gmail send`. This is the concrete mitigation for the prompt-injection
   risk a browser tool uniquely adds beyond `web_search`/`url_read`: both already feed untrusted
   page text to the model, but only Playwright gives it hands — a poisoned page can make the model
   *want* to click/submit something, but the fail-closed default means it can't without the user
   approving that exact, described call. Bonus fix bundled in:
   `policy_guard.describe_call()`'s `_DETAIL_KEYS` gained `element`/`url`/`text` — without this a
   pending `browser_click` confirmation showed just the bare tool name, no indication of what would
   actually be clicked.
4. **Solved the actual hard part of this phase** (not in the original 2-bullet roadmap scope, a
   real correctness issue found during implementation): MCP's stdio transport needs ONE persistent
   subprocess for a session's life for a *stateful* server like browser automation — confirmed live
   (and independently corroborated by a LangChain forum thread hitting the identical symptom) that
   the adapter's default, convenience `client.get_tools()` spawns a **fresh session — and fresh,
   blank browser — per tool call**, silently breaking `browser_navigate` → `browser_click`.
   `McpToolManager` always uses the persistent `client.session()` + `load_mcp_tools(session)`
   pattern instead, held open by an `AsyncExitStack` for the manager's life.
5. **That persistent session is event-loop-bound** (same class of constraint as the LangGraph
   checkpointer — see `graph/graph.py`'s docstring), and this codebase has several independent
   loops (CLI/uvicorn's main loop vs. `TaskExecutor`'s own per-call `asyncio.run()`). So
   `JarvisAgent.connect_mcp_tools()` (new) is called **explicitly, once, from each entry point's own
   real long-lived loop** — `cli.py`'s `_run_loop`/`_run_voice_loop` (right at the top, before the
   main while-loop) and `api.py`'s `lifespan()` (before `yield`) — never lazily from whichever
   caller's `chat()` happens to fire first. `build_graph()` gained an `extra_tools` param for this;
   the pre-existing `switch_model()`/quota-fallback rebuild call sites in `agent.py` were updated to
   keep passing `self._mcp.tools` through too, so a mid-session model switch doesn't silently drop
   already-connected MCP tools. `chat()`/`chat_stream()` also call `connect_mcp_tools()` as an
   idempotent belt-and-suspenders safety net. `close_mcp_tools()` (new) is wired into `cli.py`'s
   loop-exit paths and `api.py`'s `lifespan()` shutdown so a launched npx/browser process tree
   doesn't linger.
6. **A real bug caught live, not assumed, and fixed same session:** `connect_mcp_tools()`'s graph
   rebuild was gated on `if self._mcp.tools:` alone — true forever after the first successful
   connect, so every call after the first (including the safety-net one at the top of *every single*
   `chat()`/`chat_stream()` call) was silently rebuilding the entire graph — re-constructing every
   LLM provider and re-running `.bind_tools()` across all ~60 tools — on every turn for the rest of
   the process's life. Caught by the live smoke test's repeated `bind_tools()` schema-warning
   volume; neither isolated verification script would have caught this (neither exercises
   `JarvisAgent` itself, per MEMORY.md's isolate-test-data-paths constraint). Fixed with a one-time
   `self._mcp_graph_rebuilt` guard, reset in `close_mcp_tools()`.
7. **Windows fix (confirmed live, not assumed):** `npx` is `npx.cmd`, a batch shim — Python's
   subprocess APIs (no shell by default) raise `WinError 2` spawning it directly. Every npx-based
   server config goes through `{"command": "cmd", "args": ["/c", "npx", ...]}`.
8. **Docs brought fully in sync**: `ROADMAP.md`'s Faz 5 section (checked off, full verify writeup),
   `docs/ARCHITECTURE.md` (new "MCP layer" section), `docs/TOOLS.md` (new MCP tools table, all 24
   real names with risk/confirm), `docs/SAFETY.md` ("What Faz 5 changed" section), `MEMORY.md`
   (design-decisions section mirroring the Faz 4 one), `CLAUDE.md` (safety-model paragraph),
   `CHANGELOG.md` (new entry), `.env.example` (new `MCP_*` vars + the Windows npx gotcha spelled
   out inline).

## Missing setup completed this session

- **Node.js confirmed present** (v24.18.0, installed Faz-4-post-handoff) but not on PATH in any
  fresh Bash/PowerShell tool process this session either — same documented gotcha as before (HKCU
  PATH is correct; only pre-existing shell processes don't see it). Prepended manually for every
  command that needed `node`/`npx` this session; a normal terminal the owner opens will have it
  automatically.
- **`langchain-mcp-adapters` installed** (`uv pip install`, per MEMORY.md's plain-`pip`-backtracks
  gotcha) — `requirements.txt` updated.
- **Playwright's Chromium browser binaries installed** (`npx playwright install chromium`, both the
  full and headless-shell variants — `~/AppData/Local/ms-playwright/`) — pre-installed deliberately
  so a user's first real "browse this page" request doesn't stall on a ~150MB first-run download.
- **`.playwright-mcp/` gitignored** — the server's own page-snapshot cache, written to the repo root
  (not configurable to `data/` without a flag not yet investigated), same treatment as `data/`.

## Verification performed

**Two isolated scratch scripts** (fresh temp `cwd` per run, per MEMORY.md's isolate-test-data-paths
lesson — learned this session that `kill_switch.py` *also* persists to a cwd-relative
`data/kill_switch.json`, not just `SessionStore`/`Memory`, so it needed the same isolation):
- `verify_faz5_gating.py` — 26/26 checks: real Playwright MCP tools discovered (24, matches the
  live README-documented core set), every one gets a `ToolSpec`, category tagged `"mcp"`,
  known-read-only names classified L1/no-confirm, known-nav names L2/no-confirm, every consequential
  name (click/type/fill_form/press_key/select_option/file_upload/drag/drop/hover/handle_dialog/
  evaluate/run_code_unsafe) *and* a synthetic never-seen-before name both correctly fail-closed to
  L3+confirm, kill switch (tripped) vetoes `browser_click` but correctly does not veto
  `browser_snapshot`, no MCP tool is `supports_background=True`, `describe_call()` doesn't crash on
  an MCP tool name, manager closes cleanly.
- `verify_faz5_graph.py` — 6/6 checks: a graph built with `extra_tools=None` has zero `browser_*`
  tools (no regression to today's zero-MCP behavior); the MCP-merged graph's actual compiled
  `ToolNode` (`graph.nodes["tools"].bound.tools_by_name`) both contains `browser_navigate`/
  `browser_click` and native tools are still present (dual-layer, not replaced); **the tool object
  retrieved from that real compiled graph's ToolNode was invoked directly and genuinely drove the
  real browser** — `browser_navigate("https://example.com")` then a separate `browser_snapshot()`
  call on the same session saw "Example Domain", proving state persistence through the actual
  production code path, not just a standalone script.

**Real product, real LLM (2026-07-15, `python -m jarvis`, `MCP_PLAYWRIGHT_ENABLED=True`, Ollama
was up too but this turn routed to the `reasoning` role — `gemini-2.5-pro (Vertex, reasoning)`, per
the router's own complexity-based pick):** confirmed via `data/audit_log.jsonl` (real, not a test
double) that the model, unprompted about tool names, independently decided to:
- call `browser_navigate` → logged `decision ... outcome: auto_approved`, then
  `execution_start`/`execution_end ok: true` with real returned page content ("Page Title: Example
  Domain") — a real MCP tool call executed through the full production stack.
- separately decide to call `browser_click` → logged `decision ... risk_level: 3, outcome:
  confirm_required` — **the fail-closed gate firing live, from a real model's actual decision**, not
  a synthetic policy_guard.evaluate() call in a script.

This is what caught the graph-rebuild bug (§6 above) — the isolated scripts don't touch
`JarvisAgent`, so this tier was the only one that could have found it.

**Not cleanly closed — explicit hand-off:** the CLI's interactive approve/deny prompt itself. Two
live attempts (piped stdin, both via a `/reset` + message + `y` + `/exit` input sequence) each ended
with an empty `_print_jarvis` panel instead of a rendered `[red]Confirmation required[/red]` panel +
`Approve?` prompt, even though the audit log confirms `confirmation_node` correctly reached the
`confirm_required` classification both times (once even logging it twice, 2ms apart, suggesting a
retry/second pass). Read both `jarvis/agent.py`'s `GraphInterrupt`→`ConfirmationRequired` handling
(`chat()`, ~line 770) and `jarvis/graph/nodes.py`'s `confirmation_node` (~line 358) end to end this
session looking for a bug — both look structurally correct and neither was modified by this phase.
Best-guess explanation is piped-non-TTY-stdin racing multiple sequential `Prompt.ask()` calls within
what's conceptually one turn (main-loop prompt + confirmation prompt sharing one stdin stream), not
a gating bug — but this is a guess, not a finding. **Next session (or the owner directly): re-run
the identical request from a real interactive terminal** (`MCP_PLAYWRIGHT_ENABLED=True`,
`python -m jarvis`, ask it to click something on a live page) and confirm the red panel actually
renders and typing `y` actually resumes the click. If it reproduces in a real terminal too, that's a
real bug in Faz 4's interrupt/resume plumbing surfaced by Faz 5, not a test-harness artifact, and
needs its own fix.

## Explicitly deferred / not this phase's scope

- **ha-mcp / Home Assistant** — still Faz 6, hardware-gated (no HA instance, no Zigbee dongle). The
  generic `Settings.mcp_servers` escape hatch this phase built is specifically so that phase is
  config, not code, when the hardware arrives.
- **Playwright's opt-in capability flags** (`--caps=storage,network,devtools,vision,pdf,testing`) —
  not enabled. The 24-tool core set (navigate/click/type/snapshot/screenshot/evaluate/...) is
  already a real, useful capability; the opt-in extras (cookie/localStorage manipulation, network
  request mocking, video/trace recording, coordinate-based mouse control, PDF export, test-locator
  generation) weren't asked for and would need their own risk classification pass before shipping.
- **No per-server enable/disable UI** — `.env` only, matches every other integration in this
  project (Spotify, Calendar, Drive, ...).
- **The CLI interrupt round-trip verification gap above** — genuinely unresolved, not glossed over.

## Git state as of this session

- Branch: `langgraph-migration`, **not merged to `main`**.
- **Faz 4 was committed this session** (`55cd807`, 28 files, +1601/-338) — see git log.
- **Everything from Faz 5 is uncommitted** (per this project's standing instruction: only commit
  when explicitly asked, and this session wasn't asked to commit Faz 5 specifically — only Faz 4).
  `git status`: 15 modified files (`.env.example`, `CHANGELOG.md`, `CLAUDE.md`, `MEMORY.md`,
  `ROADMAP.md`, `docs/ARCHITECTURE.md`, `docs/SAFETY.md`, `docs/TOOLS.md`, `jarvis/agent.py`,
  `jarvis/api.py`, `jarvis/cli.py`, `jarvis/config.py`, `jarvis/graph/graph.py`,
  `jarvis/policy_guard.py`, `jarvis/tool_registry.py`, `requirements.txt`, `.gitignore`), 1 new
  (`jarvis/mcp_integration.py`), +468/-22 across the modified files (not counting this rewrite of
  `HANDOFF.md` itself). `.playwright-mcp/` is untracked but now gitignored, won't show in `git add`.
  `data/audit_log.jsonl`/`data/kill_switch.json` (the latter never created — kill switch was never
  really tripped, only in isolated temp-dir tests) are gitignored as always.

## Recommended next steps (pick up here)

1. **Resolve the CLI interrupt round-trip question above** — run the same live request from a real
   (not piped) terminal before trusting the confirmation gate covers MCP tools in daily use exactly
   like it covers native ones.
2. **Decide on commit strategy for Faz 5** — one coherent phase, similar shape to Faz 4; the owner's
   call, not assumed.
3. **Faz 6 — Fiziksel Dünya / IoT** ([ROADMAP.md](ROADMAP.md)) is next per the roadmap, but stays
   `⛔ deferred` — hardware-gated (owner has only an RP2040 today; needs a Zigbee coordinator dongle
   at minimum). Nothing to do here until hardware is actually acquired.
4. **Faz 7 — Proaktiflik** is the other unstarted phase and does *not* need hardware for its
   software-proactivity half (calendar/email self-initiation) — a legitimate next-session candidate
   if Faz 6 stays blocked on hardware.
5. Confirm whether the 21 stray worktrees/branches should be cleaned up (still deferred, still
   needs owner go-ahead — destructive, unrelated to this session).

## Environment checklist to resume work

```powershell
.\.venv\Scripts\Activate.ps1
ollama serve                          # confirm it's up: curl http://localhost:11434/api/tags —
                                       # was up this session but isn't left running between sessions
python -m jarvis                      # CLI text — set MCP_PLAYWRIGHT_ENABLED=True in .env first,
                                       # then try "example.com'a git ve bir linke tıkla" — confirm
                                       # the confirmation prompt actually renders in a REAL terminal
                                       # (this is exactly what last session couldn't confirm)
```

New `.env` vars this session: `MCP_PLAYWRIGHT_ENABLED` (default `False`),
`MCP_PLAYWRIGHT_HEADLESS` (default `True`), `MCP_SERVERS` (advanced, default `{}`) — see
`.env.example` for the exact JSON shape and the Windows `cmd /c npx` note.

New Python dependency: `langchain-mcp-adapters` (installed via `uv pip install`, already in
`requirements.txt` — a fresh `.venv` needs `uv pip install -r requirements.txt`, not plain `pip`,
per MEMORY.md's existing resolution-backtracking gotcha). No new Node.js packages pinned anywhere —
`npx -y @playwright/mcp@latest` always resolves the latest published version at connect time.
