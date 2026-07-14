# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-14 (same-day continuation, fifth phase — Faz 4)

**Context:** Picked up directly from this same day's Faz 0-3 sessions (all committed — `git log`
shows `98e255f` as the prior HEAD). Implemented Faz 4 — the full security kernel + async tools —
per [ROADMAP.md](ROADMAP.md)'s pre-approved checklist. Everything below is done; nothing was
descoped without saying so explicitly (see "Explicitly deferred" at the end).

## What happened this session (all uncommitted — see Git state below)

1. **New `jarvis/policy_guard.py`** — transport-agnostic safety kernel (no LangGraph/LangChain
   imports, so it's reusable outside the graph). Single choke point for "is this call allowed,
   does it need the user's OK" — `graph/nodes.py`'s `confirmation_node` now calls into it instead
   of inlining risk checks. **Per-action, not per-tool (BUG-6):** the four mixed-risk
   `external_api` tools (`google_calendar`/`gmail`/`google_drive`/`itu_mail`) have their read
   actions (list/search/read/download) downgraded back to L1/no-confirm — only genuinely risky
   actions interrupt now.
2. **New `jarvis/kill_switch.py`** — persisted (`data/kill_switch.json`, survives a restart)
   emergency stop scoped to L3 (external-effect) actions only; does not block L2 local writes.
   `/killswitch [status|on|off <reason>]` in the CLI. **New `jarvis/audit_log.py`** — append-only
   JSONL (`data/audit_log.jsonl`), two event kinds: `decision` (policy_guard's ruling, written in
   `confirmation_node`) and `execution_start`/`execution_end` (the call actually ran + outcome,
   written by `agent.py`'s `_HudEventCallback`, matched via LangChain's `run_id` across the
   start/end pair — this fires for every transport since the callback is attached the same way in
   `chat()`/`chat_stream()`/`resume_and_stream()`).
3. **Confirmation gate now defaults on and works everywhere.** `confirmation_gate_enabled=True`
   (was `False`). All three interfaces handle an interrupted call:
   - CLI text (`cli.py`): catches `ConfirmationRequired`, shows what's pending, prompts y/n +
     optional reason, resumes via `resume_and_stream()`.
   - Voice (`cli.py`'s `--voice` loop, `voice_api.py`'s wakeword/PTT loop, `api.py`'s `/ws`
     remote-audio session — all three via new shared helpers in `jarvis/voice/session.py`):
     `chat_stream()`'s `__jarvis_confirm__` marker is detected instead of spoken as raw JSON
     (**BUG-4**, live and real now), replaced with a natural spoken question, and the next
     utterance is treated as the answer (anything not clearly affirmative denies — fail-safe).
   - API: `POST /chat` catches `ConfirmationRequired` and returns a structured
     `{"confirmation_required": true, "id", "payload"}` response instead of an opaque 500
     (**BUG-confirm-payload**). `POST /chat/stream` already carried the marker through.
   - `prompts/core/02_tool_policy.md`'s "you do NOT need to ask" directive is gone (**BUG-5**),
     replaced with guidance describing the real approve/deny round-trip.
4. **Bug fixes bundled in:** `python_run` reclassified L2→L3+confirm (**BUG-1** — it was more
   powerful than `shell_run`, arbitrary unsandboxed Python from any absolute path, while sitting at
   a lower gate; this is the access-control fix, sandboxing itself is NOT done, said so explicitly
   in `docs/SAFETY.md`). SSRF guard in `webfetch.py` (**BUG-6-ssrf** — blocks
   localhost/private/link-local/reserved/metadata-endpoint URLs, checked against the *resolved* IP
   so DNS rebinding can't bypass it). Auth on `/system/wake` (**BUG-2** — new shared
   `jarvis/api_auth.py`, `/system/ping` stays public). Recursion cap (**BUG-recursion** — new
   `Settings.graph_recursion_limit`, default 30, wired into every graph config) + agent-node LLM
   call timeout (**BUG-14** — `Settings.agent_llm_timeout_sec`, default 90s, wraps
   `llm.ainvoke()` in `asyncio.wait_for`).
5. **Async scheduler.** `task_executor.py`'s `ASYNC_KEYWORDS` now genuinely derives from
   `TOOL_SPECS[...].supports_background` (union with the old hand-picked phrases — strict
   superset, no regression). The five sub-agent tool bridges (`math_solve`/`write_content`/
   `research`/`generate_code`/`geo_math`'s analyze branch) plus `todo` converted from sync
   `@tool def` + the `_run_coro()` thread-and-fresh-event-loop bridge to native `async def`
   `@tool`s — `_run_coro()` itself became dead code in `graph/tools.py` and was deleted
   (`tools/finance.py` has its own separate, untouched `_run_coro`). Voice loops in **API mode
   only** (`voice_api.py`'s `run_one_response`, shared by the local wakeword/PTT loop and the
   remote `/ws` session — the standalone CLI `--voice` mode has no `TaskExecutor` attached and is
   unaffected) now hand a `should_async()`-flagged query to `TaskExecutor` with a short spoken
   acknowledgement instead of blocking the turn — and the mic — in silence for up to minutes;
   completion fires a Windows toast in addition to the pre-existing FCM push.
6. **A real bug caught live, not assumed:** `TaskExecutor._run()`'s background `agent.chat()` call
   could raise `ConfirmationRequired` (it's an `Exception` subclass) with no channel to answer it —
   would have surfaced as a cryptic `"confirmation_required:<uuid>"` failure. Now caught
   specifically, reworded to name the blocked action and tell the user to ask interactively.
7. **Docs brought back in sync with the code**, not just the roadmap: `docs/SAFETY.md` rewritten
   (its entire "known gap" framing was inverted by this session — now states what's fixed and an
   honest "known limits" list, not aspirational). `docs/TOOLS.md`'s `python_run` row + Phase 3
   note updated. `CLAUDE.md`'s safety section rewritten (previously told every future session "the
   gate doesn't work" — now accurate). `docs/ARCHITECTURE.md`'s gap table row updated. This
   project's own `MEMORY.md` got a full Faz 4 section plus fixes to two claims that Faz 4 made
   stale (`_run_coro` sub-agent bridge, Phase 2/3 gate status).

## Verification performed

**Two isolated (temp-dir, no real data touched) scratch scripts, not committed — no test suite
exists in-repo, see `CONTRIBUTING.md`'s gap:**
- `verify_faz4.py` — 61 checks: `policy_guard`'s per-action decisions for all four mixed-risk
  tools' full read/write split, `python_run`'s reclassification, kill-switch veto scoped correctly
  to L3-only (confirmed it does NOT veto an L2 `file_write` or a downgraded read action),
  kill-switch persistence surviving a simulated process restart (cleared the in-memory cache,
  reloaded from disk), `audit_log` writes/truncation/tail, the SSRF guard against 7 blocked
  address classes (localhost, loopback, 3 private ranges, cloud metadata, IPv6 loopback) plus a
  real public URL correctly allowed through, `voice/session.py`'s marker-parsing +
  affirmative-detection (English and Turkish) + bilingual question phrasing,
  `task_executor`'s registry-derived keywords, and source-level confirmation that
  `chat()`/`chat_stream()`'s configs carry `recursion_limit` and a transport-tagged callback.
- `verify_faz4_callback.py` — a **second, real** (unmocked) LangGraph compiled graph +
  LangChain callback manager, no LLM/credentials needed (a fake `AIMessage` with `tool_calls`
  already set stands in for the model's decision — `ToolNode` itself is genuine). This was the one
  real uncertainty pure source-reading couldn't resolve: does LangChain's callback manager really
  pass `run_id` to `on_tool_start`/`on_tool_end`, so `_HudEventCallback`'s
  `execution_start`/`execution_end` pairing actually closes. Confirmed yes, for a real registered
  L2 tool name (`file_write`), with the transport tag carried through correctly.
- **Full `python -m jarvis` startup smoke-tested end to end against real session data** (this is
  the actual product's real entry point, not a throwaway script constructing an agent — see
  [MEMORY.md](MEMORY.md)'s isolate-test-data-paths note, which is about test scripts, not about
  running the real CLI): clean banner, a real turn flowed through the new
  `confirmation_gate_enabled=True` default and the rebuilt `confirmation_node` with no new
  exception, stopped at this dev machine's pre-existing Vertex-ADC-missing gap (same finding as
  Faz 1, unrelated to this session), and shut down cleanly on EOF. No stray `data/kill_switch.json`
  or `data/audit_log.jsonl` got created by this (confirms no risk≥2 decision point was hit before
  the ADC failure — consistent with the turn failing at the LLM-provider layer, before any tool
  call was ever attempted).
- Full compile + import check across every new/modified Python file (`py_compile` plus a real
  `import jarvis.X` for each, including the heavy ones — `api.py`, `voice_api.py` — which pull in
  FastAPI/onnxruntime/faster-whisper).

**Not verifiable in this environment, explicit hand-off:**
- **A live tool-calling turn through a real LLM** — Ollama wasn't running during the Faz 4
  implementation itself (`http://localhost:11434` refused the connection); it was started
  immediately after (see "Post-handoff update" below) but no live gated chat turn was actually
  driven through it this session. Substituted with the real-LangGraph/fake-`AIMessage` callback
  test above, which exercises the identical LangChain callback machinery without needing a model.
  Ollama is up now (confirm with `curl http://localhost:11434/api/tags` — it isn't left running
  between sessions historically, see MEMORY.md) — a real `"gmail'imi kontrol et"`-shaped turn
  should show a `decision` audit entry, and a real `"alice@x.com'a mail gönder"`-shaped turn should
  actually interrupt and prompt.
- **Actually hearing the spoken confirmation question and answering by voice** — same hand-off
  category as Faz 3's unverifiable speaker/mic items; the marker-detection and question-generation
  logic is unit-tested, but perceived audio quality/timing isn't.
- **Electron/mobile confirmation UI** — not built this phase (see below), so nothing to verify
  there yet.

## Post-handoff update (same day, 2026-07-15): Node.js installed, Electron build verified

Right after the above was written, the owner asked to install what the machine needs. Did:
- **Ollama started** (`Start-Process ollama.exe serve`) — both models confirmed present
  (`qwen2.5:7b-instruct`, `nomic-embed-text`). Not left running persistently between sessions
  historically (see [MEMORY.md](MEMORY.md)) — check it's actually up before assuming so.
- **Node.js LTS installed, user-scope (no admin needed)** — see [MEMORY.md](MEMORY.md) for the
  exact path/method. This closes the "no Node.js" gap that blocked Electron verification since
  Faz 3. `cd electron; npm install` succeeded (existing `node_modules` was already on disk, just
  unusable without `node`/`npm` on PATH); **`npm run build` (electron-vite) succeeded with zero
  errors** across all 32 renderer modules + main + preload — a real build-tool verification of
  every Faz 3 `.jsx`/`.js` file (`useJarvisSocket.js`, `useRemoteAudioSession.js`,
  `pcm-capture-worklet.js`, `App.jsx`, `Widget.jsx`, `main/index.js`), not just careful reading.
  **Deliberately not done: `npm run dev`** — that launches a real, visible Electron window on the
  owner's screen; a clean build doesn't prove runtime correctness, but popping up unrequested GUI
  wasn't part of what was asked. If picking this up: `npm run dev` in `electron/` (after
  prepending Node to PATH if using a terminal opened before the install — see MEMORY.md) is the
  next real step, ideally with `python -m jarvis --api` also running so it has a backend to
  actually connect to.
- Verified nothing else was missing: all `requirements.txt` Python deps already satisfied (no
  new ones needed — confirmed again), MiKTeX/`pdflatex` present. `gcloud` (for Vertex ADC) is
  still not installed — deliberately skipped: completing ADC needs an interactive browser OAuth
  login I can't do non-interactively, Vertex is optional (local Ollama + AI Studio free tier both
  work without it), and it's been explicitly "not asked for" twice now (Faz 1, Faz 4) — install it
  if Vertex access is actually wanted, not preemptively.

## Explicitly deferred / not this phase's scope

- **No Electron/mobile UI renders a confirmation prompt**, i.e. nothing calls
  `POST /chat/confirm/{conf_id}` from a click. The API returns the right structured payload
  (`{"confirmation_required": true, "id", "payload"}` from `/chat`; the `__jarvis_confirm__` SSE
  frame from `/chat/stream`) but no UI consumes it yet. The Electron *build* is now verified clean
  (see above) — what's missing is the actual confirmation dialog component + wiring, not the
  ability to build/ship JS at all. Flutter mobile is in the same boat, unstarted.
- **`python_run` is gated, not sandboxed.** The subprocess itself still has no resource/network
  restriction — the fix this session is strictly "ask before running it," not "limit what it can
  do once approved." Said explicitly in `docs/SAFETY.md`, not glossed over.
- **No formal WHEN_IDLE/INTERRUPT work-class taxonomy.** The roadmap bullet mentioned one; built
  the minimal real thing instead (registry-derived `should_async()` + hand off to the existing
  `TaskExecutor`) since a finer-grained per-tool-call scheduler has no second consumer yet and
  would also break the ReAct loop for turns that need a tool's result to answer — see
  `ROADMAP.md`'s Faz 4 section for the full reasoning.
- **Voice confirmation phrasing isn't fully localized** — the wrapper question is bilingual, the
  embedded per-call description stays in English technical form even in a Turkish session.
- **monitor.py was not touched.** It makes zero tool calls today (pure Gmail/Calendar reads +
  toast/push, no LLM, no `agent.chat()` call anywhere in it) — there's nothing in it to route
  through `policy_guard` yet. `docs/SAFETY.md`'s old claim that "monitor auto-denies L3 actions" was
  checked against the actual code and found to not correspond to anything real; removed rather than
  left stale.
- **Cleanup of the 21 stray `.claude/worktrees/*` directories** — still deferred, still needs the
  owner's explicit go-ahead (destructive), unrelated to this session, Faz 8 territory.

## Git state as of this session

- Branch: `langgraph-migration`, **not merged to `main`**.
- **Everything from this session is uncommitted** (per this project's standing instruction: only
  commit when explicitly asked). `git status`: 23 modified files, 4 new
  (`jarvis/policy_guard.py`, `jarvis/kill_switch.py`, `jarvis/audit_log.py`,
  `jarvis/api_auth.py`). `git diff --stat`: 1071 insertions, 188 deletions across the modified
  files. Nothing under `data/` is tracked (gitignored, as expected) — `kill_switch.json`/
  `audit_log.jsonl` will appear there on first real trip/side-effecting call, not from this
  session's verification (confirmed clean).

## Recommended next steps (pick up here)

1. **Decide on commit strategy** — this is a coherent, single-purpose phase (unlike Faz 3's
   Part A/B split); one commit is probably right, but that's the owner's call, not assumed.
2. **Start Ollama and run a real gated turn** (see "Not verifiable" above) before fully trusting
   this in daily use — the logic is thoroughly tested in isolation and via a real (if LLM-free)
   LangGraph execution, but nothing this session actually watched a real model decide to call
   `gmail("send", ...)` and get interrupted.
3. **Faz 5 — MCP Katmanı** ([ROADMAP.md](ROADMAP.md)) is next per the roadmap. `policy_guard.
   evaluate()` was deliberately written to not know or care who's calling it, specifically so
   Faz 5's MCP tool integration can call the same function rather than inventing its own gate.
4. If the Electron/mobile confirmation UI is wanted, that's a real, not-yet-scoped chunk of work —
   Node.js is now available in this environment too (see "Post-handoff update" above), so a future
   session can build/syntax-check it directly; actually *seeing* it will still need `npm run dev`
   run where a human can look at the window.
5. Confirm whether the 21 stray worktrees/branches should be cleaned up (still deferred, still
   needs owner go-ahead — destructive).

## Environment checklist to resume work

```powershell
.\.venv\Scripts\Activate.ps1
ollama serve                          # confirm it's up: curl http://localhost:11434/api/tags —
                                       # started 2026-07-15 but not left running between sessions
python -m jarvis                      # CLI text — try a risky action, e.g. "briefly test gmail send"
python -m jarvis --voice              # voice — same, listen for the spoken confirmation question
python -m jarvis --api                # API mode — POST /chat with a risky message, check for
                                       # {"confirmation_required": true, ...} in the response
```

```powershell
# Electron (Node.js now installed — see MEMORY.md if PATH isn't picked up in an old terminal)
cd electron
npm run build                         # verified clean 2026-07-15 — re-run after any JS/JSX edit
npm run dev                           # launches the actual HUD window; pair with --api running
```

New CLI command this session: `/killswitch` (bare = status, `on`, `off <reason>`).

No new Python dependencies this session — `jarvis/tools/webfetch.py`'s SSRF guard uses only
`ipaddress`/`socket`/`urllib.parse`, all stdlib. Node.js LTS was installed post-handoff (see
above) — no `package.json` changes, so nothing new to `npm install` beyond what's already there.
