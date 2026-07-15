# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-15 — Faz 8 (Temizlik & Konsolidasyon, non-destructive scope)

**Context:** Owner said "sıradaki faz ile devam et" (continue with the next phase). Two things
needed resolving before starting: (1) Faz 7 was still fully uncommitted from the prior session —
asked and confirmed "commit Faz 7 first," matching the established one-phase-per-commit pattern,
so it was committed (`1039a1b`) before any Faz 8 diff began. (2) Per the phase overview table, Faz
8 is the only remaining phase that isn't hardware-gated (Faz 6) — but it bundles several
sub-tasks, two of which (merge `langgraph-migration` → `main`, delete the 21 stray
`.claude/worktrees/*` branches) are pre-flagged in this repo's own docs as needing explicit owner
go-ahead. Asked and confirmed: full non-destructive scope (legacy retirement + minimal test suite
+ all 9 remaining P2 bugs + electron/mobile hygiene), explicitly excluding the merge and worktree
cleanup.

## What happened this session

1. **Retired `jarvis/legacy/`** — confirmed via grep first (nothing outside the directory itself
   imported it) then `git rm -r`'d the old pydantic-ai orchestrator and the dead
   `jarvis/prompts/system.md` pointer file outright, not archived. Updated every doc that
   referenced either path (`CLAUDE.md`, `ROADMAP.md`, `MEMORY.md`, `ProjectState.md`,
   `docs/SAFETY.md`, `README.md`) to reflect the deletion; both are recoverable from git history
   before this commit if ever needed for reference.
2. **New minimal test suite** — `tests/` (pytest + pytest-asyncio, added to `requirements.txt`;
   neither was previously installed). 92 tests across 10 files:
   - `test_policy_guard.py` (20) — risk classification, the BUG-6 per-action read/write downgrade
     for the four mixed-risk external_api tools, kill-switch veto scoping (L3-only, never blocks
     local writes or downgraded reads), the unregistered-tool fail-safe.
   - `test_session_store.py` (9) — no-duplication across turn buckets and `last_turn_idx`
     resumption (the Faz 0 bonus fixes, never previously left a persisted test), a rollback-on-
     failure atomicity test (via a connection proxy — `sqlite3.Connection` methods can't be
     monkeypatched directly on an instance, confirmed live), a concurrent 3-writers×3-readers
     stress test.
   - `test_provider_router.py` (8) — **the offline-failover proof**: with zero cloud credentials
     configured, both `fast` and `reasoning` roles resolve to bare local Ollama, not a fallback
     wrapper around nothing; with cloud configured, local Ollama is confirmed always last in the
     `reasoning` fallback chain. No network access needed — constructing a `ChatOpenAI`/
     `ChatGoogleGenerativeAI` client doesn't itself call out to Ollama or Google.
   - `test_kill_switch.py` (6), `test_usage_tracker.py` (7) — see bug fixes below; these are the
     regression tests for the two cross-process fixes.
   - One regression test file per remaining bug fix (`test_files_tool.py`, `test_calendar_tool.py`,
     `test_pdf_tool.py`, `test_geo_math_tool.py`, `test_cli_model_switch.py`,
     `test_graph_critic_node.py`, `test_api_upload.py`).
   - New `tests/conftest.py`'s `isolated_cwd` fixture is the enforcement point for this repo's own
     isolate-test-data-paths lesson (MEMORY.md) — chdir's into a fresh `tmp_path` AND resets
     `kill_switch`'s module-level `_cache`, since that cache is keyed off cwd-relative state and
     would otherwise leak between tests. Config: `[tool.pytest.ini_options]` in `pyproject.toml`
     (`testpaths = ["tests"]`, `asyncio_mode = "auto"`). Run with `pytest` from repo root.
3. **9 P2 bugs fixed**, each verified live (not just read-and-assumed) before being formalized into
   a test — see `ROADMAP.md`'s Faz 8 section for the full per-bug writeup:
   - **BUG-20** `file_write` ValueError on home paths (`tools/files.py`) — new `_display_path()`
     helper.
   - **BUG-21** calendar dedup hardcoded `+03:00` (`tools/calendar.py`) — now `zoneinfo`-based,
     DST-correct for any configured timezone, not just coincidentally-correct Istanbul.
   - **BUG-itu** IMAP connection leak on login failure (`tools/itu_mail.py`) — `MailBox(host,
     port)` opens the socket in `__init__`; now closed via `.logout()` if `.login()` raises.
   - **BUG-pdf** cache keyed on path not content (`tools/pdf.py`) — new `_content_key()` hashes
     file bytes; a same-path content swap with an *older* mtime (restored backup, archive
     extraction) no longer serves stale cached text forever.
   - **BUG-geomath** Devito fallback hardcodes `duration=0.5` (`tools/geo_math_tool.py`) — the
     runtime-failure fallback (not just "Devito not installed") now passes through the actual
     requested duration.
   - **BUG-modelswitch** unanchored `"pro" in text` substring hijack (`cli.py`) — ordinary
     messages containing "proje"/"problem"/"program"/etc. were silently swallowed into a model
     switch (the CLI `continue`s after a detected switch, dropping the user's real message
     entirely). Fixed with `\b`-anchored word-boundary regex per keyword; Turkish
     apostrophe-suffixed forms ("Pro'ya") still match correctly.
   - **BUG-emptyresp** empty LLM response saved as success (`graph/nodes.py`) — `critic_node`'s
     fast-path used to accept an empty response unconditionally regardless of revision budget; now
     redirects for a retry when budget remains, substitutes a visible message only once budget is
     truly exhausted.
   - **BUG-upload** `/chat/upload` no size cap + no cleanup (`api.py`) — chunked read with a
     50 MB cap (413 before buffering an oversized file), cleanup wired into both the PDF branch
     (immediate, after synchronous extraction) and the Excel/CSV/Word branch (in the SSE
     generator's `finally`, since the agent may read the file at any point while streaming).
   - **BUG-usage** `UsageTracker` clobbers across processes (`usage.py`) — `record()` now re-reads
     the on-disk total fresh immediately before merging its delta in, under a new lock. Narrows the
     failure window to a brief TOCTOU race rather than "guaranteed loss whenever two processes are
     alive together" — does not eliminate it (would need a real cross-process file lock, which
     nothing else in this codebase uses either).
4. **Bonus fix, found live while fixing BUG-usage** (same root cause, safety-relevant, not in the
   original backlog): `kill_switch.py`'s `_load()` cached the first successful read for the rest
   of the process's life. Since `policy_guard.evaluate()` checks `is_enabled()` on every L3 call
   specifically so a trip takes effect immediately, the load-once cache meant a trip from one
   process (e.g. the CLI's `/killswitch`) was invisible to an already-running `--api --monitor`
   server until it restarted — silently defeating the "hard stop, no prompt" guarantee in exactly
   this project's targeted always-on deployment shape. Now always re-reads from disk (the file is
   a few bytes; the only caller is already about to do far more expensive work).
5. **Electron/mobile client hygiene**:
   - **BUG-elec**: neither of Electron's two REST calls (`App.jsx`'s file-drop → `/chat/upload`,
     `HudPanels.jsx`'s `BottomBar` chat input → `/chat/stream`) ever sent `X-API-Key` — both would
     401 the moment `JARVIS_API_KEY` is configured. `BottomBar` didn't even accept an `apiKey`
     prop; now threaded through. `npm run build` confirmed clean after the fix.
   - **BUG-mob-tls**: the mobile client's `/ws` token traveled as a `?token=` query param.
     Switched to `IOWebSocketChannel` (Android-only, fine — no Flutter Web target) so it goes in an
     `X-API-Key` header instead; `jarvis/api.py`'s `ws_endpoint` checks the header first, falling
     back to the query param only for Electron (browser `WebSocket` API genuinely can't set custom
     headers — not client-fixable). **Honest scope**: this closes URL-logging exposure, not
     wire-level cleartext — no TLS termination exists on this server, so confidentiality on an
     untrusted network still depends on tunneling through Tailscale, same as before.
   - **BUG-reconnect**: WS reconnect now backs off exponentially (3s → doubling → 60s cap, reset
     on a successful `channel.ready`) instead of retrying every 3s forever.
   - **Not fixed, flagged separately**: `WsClient.reconnect(host, apiKey)` accepts new host/key
     params but never applies them (`_host`/`_apiKey` are `final`) — found in passing, currently
     dead code (zero call sites), spawned as a follow-up task rather than fixed inline (out of
     scope for this bundle, and touching `final`-field semantics deserved its own focused pass).
6. **Docs synced**: `ROADMAP.md` (Faz 8 section fully written up + phase table + bug backlog
   appendix rows checked off), `CHANGELOG.md` (new entry), `MEMORY.md` (legacy-retirement note +
   a new Faz 8 design-decisions entry covering the cross-process staleness pattern and the
   BUG-mob-tls partial-mitigation caveat), `CLAUDE.md` (legacy paragraph rewritten, test-suite
   section added), `CONTRIBUTING.md` (new Testing section), `ProjectState.md`/`docs/SAFETY.md`/
   `README.md` (dead-file table rows fixed).

## Verification performed

**Every bug fix was verified live before being formalized into a test** — not just read-and-
assumed correct. Ad-hoc scratch scripts (not committed) reproduced each bug's exact failure
scenario against the fixed code: BUG-20 (a real write to a Desktop-relative path outside
workspace), BUG-21 (offset computation for both Europe/Istanbul and DST-observing Europe/Berlin),
BUG-pdf (same-path content swap with a deliberately older mtime), BUG-geomath (forced the exact
except-branch via Devito's absent import), BUG-modelswitch (a battery of Turkish/English false-
positive and true-positive phrases), BUG-emptyresp (all three critic-node branches: retry-with-
budget, exhausted-budget-substitutes-message, normal-exchange-unaffected), BUG-upload (chunked-
read boundary math: under-cap/at-cap/one-byte-over/500MB-aborts-early), BUG-usage and the
kill_switch bonus fix (two-instances-sharing-one-file cross-process simulations, and a
warm-cache-then-external-write staleness simulation for kill_switch specifically). All of these
were then rewritten as the corresponding `tests/` file — **92/92 pass**, confirmed via a full
`pytest` run from repo root (not just per-file), 9 seconds, no cross-test pollution. `jarvis`
package import confirmed clean after the `jarvis/legacy/` deletion
(`python -c "import jarvis; import jarvis.agent"`). Electron `npm run build` confirmed clean
(32 modules transformed) after the `App.jsx`/`HudPanels.jsx` edits. Confirmed via `git status`/
`ls -la data/` that no test run touched the real project `data/` directory. **Real product, real
data** (matching every prior phase's own verification tier): plain `python -m jarvis` starts
clean (banner renders, no exceptions from any Faz 8 code path) and exits cleanly on EOF —
surfaced one pre-existing, unrelated issue during this run (`_OllamaEF` missing `embed_query()`,
non-fatal, flagged separately below, not caused by this session's diff). `python -m jarvis --api`
started clean and answered `GET /health` with `200 {"status":"ok",...}` ~18s after startup, clean
process shutdown after.

**Not verifiable in this environment**: the mobile Dart client (`ws_client.dart`) changes —
no Flutter/Dart SDK installed on this machine (`flutter`/`dart` both absent from PATH), so
`IOWebSocketChannel`'s header-based auth and the exponential-backoff reconnect logic could only be
reviewed by hand against the `web_socket_channel: ^3.0.1` public API (confirmed via its pubspec
entry), not compiled or run. Hand-off: run `flutter analyze`/`flutter build` on a machine with the
SDK installed before trusting this compiles, and a real device test for the header-based `/ws`
auth actually connecting.

## Explicitly deferred / not this session's scope

- **Merge `langgraph-migration` → `main`** — explicit owner go-ahead required (shared branch
  state), scoped out up front alongside the worktree cleanup, not assumed as part of "the next
  phase."
- **21 stray `.claude/worktrees/*` scratch branches** — still deferred, still needs owner
  go-ahead (destructive, unchanged from every prior session's note).
- **`WsClient.reconnect(host, apiKey)` ignoring its own parameters** (`mobile/lib/core/
  ws_client.dart`) — spawned as a follow-up task (`task_a9cee697`) rather than fixed inline; found
  in passing while fixing BUG-mob-tls/BUG-reconnect in the same file, currently dead code (no call
  sites), not safety-relevant enough to justify scope-creeping into the client-hygiene bundle.
- **`_OllamaEF` missing `embed_query()`** (`jarvis/memory.py`) — spawned as a follow-up task
  (`task_3b9a631d`); surfaced live during this session's `python -m jarvis` smoke test but
  pre-existing and unrelated to anything in this session's diff (`memory.py` was never touched).
  Non-fatal (a memory-recall path logs the error and the process keeps running), but a real,
  reproducible interface mismatch between the local Ollama embedding function and whatever calls
  `.embed_query()` on it.
- **Real TLS termination for `/ws`** — would fully close BUG-mob-tls's wire-level cleartext gap
  (the header-based fix only closes URL-logging exposure). Not built: genuinely new infrastructure
  (a cert, uvicorn `ssl_certfile`/`ssl_keyfile` config), out of proportion for a client-hygiene bug
  fix. Confidentiality on an untrusted network still depends on Tailscale.
- **Faz 6 — Fiziksel Dünya / IoT** stays hardware-gated (Zigbee coordinator dongle + Home
  Assistant instance; owner has only an RP2040 today). Unchanged from every prior session.
- **Test suite is "minimal," not comprehensive** — most tool modules (spotify, finance, todo,
  scheduler, gmail/drive actions beyond the calendar dedup-guard fix, the sub-agent bridges, voice)
  still have zero test coverage. Extend `tests/` incrementally rather than reverting to throwaway
  scratch scripts.

## Git state as of this session

- Branch: `langgraph-migration`, **not merged to `main`**.
- **Faz 7 was committed this session** (`1039a1b`) — this session started with Faz 7 fully
  uncommitted (per the prior session's own note); owner confirmed committing it first before any
  Faz 8 diff began.
- **Everything from Faz 8 is uncommitted** (per this project's standing instruction: only commit
  when explicitly asked; this session wasn't asked to commit Faz 8). `git status`: ~19 modified
  source/doc files, 3 deleted files (`jarvis/legacy/__init__.py`, `jarvis/legacy/agent_pydantic.py`,
  `jarvis/prompts/system.md`), one new untracked directory (`tests/`, 10 files + `conftest.py`).

## Recommended next steps (pick up here)

1. **Decide on commit strategy for Faz 8** — one coherent phase, same shape as every prior
   phase; the owner's call, not assumed. Given this phase bundles several genuinely separate
   concerns (legacy retirement, a new test suite, 9 unrelated bug fixes, client hygiene), a single
   session did all of it, but a single *commit* vs. several smaller ones is worth asking about
   explicitly rather than defaulting to the one-phase-per-commit pattern without checking — this
   phase's diff is broader than usual.
2. **Ask about the two explicitly-deferred destructive items** if there's appetite to unblock
   them: merging to `main` (this branch has been ahead of `main` since Faz 0) and the 21 stray
   worktree branches.
3. **`WsClient.reconnect()` follow-up** (`task_a9cee697`) is sitting as a spawned suggestion —
   pick it up if/when mobile settings-switching (change server/API key without restarting the app)
   becomes a real feature; currently dead code with no live impact.
4. **Run `flutter analyze`/`flutter build`** on a machine with the Flutter SDK to confirm the
   `ws_client.dart` changes actually compile — not verified in this environment (see above).
5. **Faz 6 — Fiziksel Dünya / IoT** stays hardware-gated. Nothing to do here until hardware is
   actually acquired.
6. Consider extending `tests/` coverage to another tool module or two per future session touching
   that area, rather than a dedicated "more tests" phase — the infrastructure (`conftest.py`,
   pytest config) is now in place, so incremental addition is cheap.

## Environment checklist to resume work

```powershell
.\.venv\Scripts\Activate.ps1
pytest                                # new this session -- 92 tests, ~9s, fully offline
ollama serve                          # confirm it's up: curl http://localhost:11434/api/tags --
                                       # not needed for the test suite (provider-router tests never
                                       # invoke a real model), only for actually running the agent
python -m jarvis                      # CLI -- retired jarvis/legacy/ import confirmed clean
python -m jarvis --api --monitor      # long-running path -- kill_switch cross-process fix matters
                                       # most here; try `/killswitch off` from a separate CLI
                                       # session and confirm this process's next L3 call is vetoed
                                       # without restarting it (not done live this session --
                                       # covered by tests/test_kill_switch.py's simulation instead)
```

No new required `.env` vars this session. New dev-only dependencies: `pytest>=8.0`,
`pytest-asyncio>=0.24` (added to `requirements.txt`, already installed in `.venv`).
