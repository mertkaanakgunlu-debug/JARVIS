# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-15 — GitHub push + Faz 8 follow-up (remaining Faz 0 bugs, Flutter verification)

**Context:** Two separate asks in the same session. First: the repo had never been pushed anywhere
(`git remote -v` was empty) — owner asked to push it, chose GitHub/public and "merge
`langgraph-migration` → `main` first, then push `main`" from an explicit either/or question. Second:
"continue completing all other missing items, no hardware yet, install what's needed (Flutter
etc.)" — picked up the prior session's own "recommended next steps": the merge decision, the four
Faz-0 bugs left "opportunistic, deferred," `WsClient.reconnect()`'s dead param bug, `_OllamaEF`'s
missing `embed_query()`, and the `flutter analyze` verification that had no Flutter SDK to run on.

## What happened this session

1. **Merged and pushed to GitHub.** `langgraph-migration` → `main` was a pure fast-forward (`main`
   had zero commits of its own — frozen at a 2026-05-09 baseline, 67 commits behind). Before
   pushing anywhere, scanned the *entire* git history for ever-committed secrets: filenames
   (`.env`, `credentials.json`, `token.json`, `.pem`/`.key`) via `git log --all --diff-filter=A`,
   and content patterns (Google/OpenAI/Slack API-key shapes, PEM private-key headers) via
   `git log --all -p -G'<pattern>'` — both came back clean; `.env`/`data/` were already correctly
   gitignored, only the placeholder-only `.env.example` is tracked. Owner then created an empty
   GitHub repo and gave the URL; added as `origin`, pushed `main`. **Current state:** `origin` →
   `github.com/mertkaanakgunlu-debug/JARVIS` (public), `main` tracks `origin/main`.
   `langgraph-migration` stays local-only (identical commit to `main`, nothing lost).
2. **Flutter SDK installed** — no official winget package exists (`winget search flutter` only
   surfaces unrelated apps tagged "flutter"); installed via `git clone
   https://github.com/flutter/flutter.git -b stable --depth 1 C:\flutter`, added to user PATH.
   `flutter doctor`: SDK itself fine; Android toolchain and Visual Studio both absent — installing
   either is a separate multi-GB undertaking, deliberately not done unprompted. `flutter pub get` +
   `flutter analyze` in `mobile/` both ran: **zero mentions of `ws_client.dart`** anywhere in the
   analyzer output (confirmed by grepping the full output, not just skimming) and **zero
   `error`-severity findings anywhere in the app** — only 69 pre-existing `info`-level deprecation
   notices (`withOpacity`→`withValues`, `partialResults`→`SpeechListenOptions`), all unrelated to
   this session, left untouched. This is the first real compiler-verified confirmation that Faz 8's
   `ws_client.dart` changes (BUG-mob-tls, BUG-reconnect) actually compile, not just look right on
   inspection. `flutter build apk` was not attempted (needs the Android SDK).
3. **`WsClient.reconnect(host, apiKey)` fixed** (`mobile/lib/core/ws_client.dart`, task_a9cee697) —
   `_host`/`_apiKey` were `final`, so the new parameters were silently discarded. Now mutable,
   reassigned at the top of `reconnect()` before it calls `connect()`. Still no call sites (dead
   code), but now correct whenever mobile settings-switching gets wired in.
4. **Four Faz-0 bugs fixed**, each root-caused live (not read-and-assumed) before fixing:
   - **BUG-15** (`jarvis/tools/finance.py`) — `_sync_burgan()`'s `msg_ids` regex expected a bare
     `"[id]"` at line start; `jarvis/tools/gmail.py`'s `_fmt_message()` actually emits
     `"• [id]  Subject"` (bullet-prefixed) — never matched, so sync always reported zero messages.
     Same root cause broke the "read" step's subject/body extraction (searched for a
     `"Konu:"`/`"---"` shape that's never been produced). All three regexes fixed to match the real
     format; verified via a new test that drives real `gmail_control` formatting output through
     `finance_control(action="sync")` with only the Google API service object and the LLM
     extraction call mocked.
   - **BUG-16** (`jarvis/graph/tools.py`) — `todo('add')`'s `asyncio.create_task(_bg_analyze())`
     result was never referenced anywhere. asyncio only holds a *weak* reference to a task; an
     unreferenced one is eligible for garbage collection before it finishes — no exception, no log,
     the prioritization just silently never lands. Fixed with a module-level `_todo_bg_tasks`
     strong-reference set, pruned via a per-task done-callback once it actually completes.
   - **BUG-17** (`jarvis/gcp_quota.py`) — `_try_fetch_cloud_quotas()`'s freshness check tested
     `cached.get("rpm_pro") is not None`, but no code anywhere ever wrote a key literally named
     `"rpm_pro"` (only `"rpm_pro_used"`/`"rpm_flash_used"`/etc.) — never matched, so every call
     attempted a live Cloud Monitoring fetch regardless of cache state. `_load_cache()` already
     filters by TTL, so the fix is just `if cached: return cached`.
   - **BUG-18** (`jarvis/gcp_quota.py`) — `quota_forecast()` read `usage.json`'s `last_updated`,
     which `jarvis/usage.py` refreshes on *every* save (effectively always "now") — `days_elapsed`
     collapsed to 1 on every call, so `daily_rate` became the entire all-time cost total instead of
     a real per-day average (a wildly overstated forecast). Fixed by adding `first_seen` to
     `usage.py` (set once via `setdefault`, never overwritten after) and anchoring the forecast on
     that instead of `last_updated`.
5. **`_OllamaEF`/`_GeminiEF` missing `embed_query()` fixed** (`jarvis/memory.py`) — this was flagged
   in the prior session as an `_OllamaEF`-only issue with an unconfirmed caller ("whatever calls
   `.embed_query()` on it"). Live-reproduced this session (isolated temp-cwd `Memory` + real Ollama
   server, per MEMORY.md's isolate-test-data-paths lesson): the actual caller is **this project's
   installed chromadb itself** — `chromadb/api/models/CollectionCommon.py`'s `_embed(is_query=...)`
   calls `embedding_function()` for `.add()` but `embedding_function.embed_query()` for `.query()`,
   **unconditionally, no `hasattr` fallback**. So *every* semantic recall
   (`recall_facts`/`recall_procedures`/`recall`/doc RAG) against an Ollama- or Gemini-backed
   collection raised `AttributeError` the moment it queried — `.add()` alone always looked fine,
   which is exactly why this went unnoticed. `_GeminiEF` had the identical bug, not previously
   flagged (found while fixing `_OllamaEF`, same class shape). Both now have an `embed_query()`
   delegating to the same logic as `__call__` — neither backend needs genuine query/document
   asymmetry here. Ollama path confirmed live against the real local Ollama server (repro script
   crashed before the fix, succeeded after).
6. **Bonus bug found live while verifying `_GeminiEF` against the real Gemini API** (owner then
   asked to "complete everything necessary"): `_build_gemini_ef`'s hardcoded model id
   (`"models/text-embedding-004"`) turned out to be retired server-side — 404 on every real call. A
   real `client.models.list()` call found the current embedding models
   (`gemini-embedding-001`/`-2`/`-2-preview`); switched to `gemini-embedding-2`. Also added a
   construction-time smoke-test embed call (mirrors `_build_ollama_ef`'s reachability probe) so a
   future model retirement fails fast and falls through to the default ONNX EF instead of crashing
   every real recall call. Confirmed live: the error changed from `404 NOT_FOUND` to `429
   RESOURCE_EXHAUSTED` ("prepayment credits depleted") — proving the model id is now correct; the
   429 itself is this account's already-documented, pre-existing billing state (MEMORY.md), not
   something this session caused or can fix.
7. **12 new regression tests** — `tests/test_finance_tool.py`, `tests/test_gcp_quota.py`,
   `tests/test_memory_embedding.py`, `tests/test_todo_bg_analysis.py`. Full suite: **104/104 pass**.
8. **Docs synced**: `ROADMAP.md` (Faz 8's status line, the merge bullet, the `WsClient.reconnect`
   bullet, a new "Faz 8 follow-up — 2026-07-15" write-up, bug backlog appendix rows for
   BUG-15/16/17/18/task_a9cee697 checked off — also fixed two stale un-checked rows for BUG-19 and
   BUG-25, both of which were already done per their own Faz sections but never got their appendix
   checkmark), `CHANGELOG.md` (new entry), `MEMORY.md` (chromadb `embed_query()` gotcha, the
   `asyncio.create_task()` weak-reference gotcha, GitHub remote + Flutter install facts, corrected
   the stale "no test suite exists" line, the Gemini model-id-drift gotcha).

## Verification performed

Every fix was root-caused via a live, isolated repro before being changed (not read-and-assumed) —
this project's own standing practice. The `_OllamaEF`/`_GeminiEF` bug specifically was diagnosed by
constructing a real `Memory` against a real (running) local Ollama server in an isolated temp cwd,
inserting into a real Chroma collection, and reading the actual traceback — which is how the true
root cause (chromadb's own `_embed()`, not any first-party code) was found instead of assumed.
`finance.py`'s BUG-15 fix is covered by a test that exercises the real `gmail_control` formatting
end to end (only the Google API service object and the LLM call are mocked). `flutter analyze`
was run and grepped for `ws_client.dart` specifically (zero mentions) and for any `error -` line
(zero, anywhere in the app) rather than eyeballing 6000+ characters of output. Full `pytest` run
from repo root: **103/103 pass**, ~19s, confirmed via `git status`/no changes under `data/` that no
test run touched real project data.

**Not verifiable in this environment:** a full real embed call completing end-to-end against
Gemini specifically (this account's `GEMINI_API_KEY` is out of prepayment credits — a `429`, not a
code issue, see MEMORY.md) — but the model-id fix itself *was* live-confirmed correct (the error
changed from `404 NOT_FOUND` to `429 RESOURCE_EXHAUSTED` once the id was fixed, which only happens
if the model id resolved correctly). `flutter build apk` (no Android SDK on this machine — a separate multi-GB install,
deliberately not done unprompted; `flutter analyze`'s zero-errors result is the verification bar
that was actually asked for). Whether the newly-pushed GitHub repo's history is *exhaustively* free
of every possible secret pattern — the scan covered known filenames and common API-key/PEM shapes,
which is a strong but not information-theoretically complete guarantee.

## Explicitly deferred / not this session's scope

- **21 stray `.claude/worktrees/*`/`claude/*` scratch branches** — still deferred, still needs
  explicit owner go-ahead (destructive), unchanged from every prior session's note. Not pushed to
  GitHub either (deliberate — they're slated for deletion, not publishing).
- **Android SDK / Visual Studio** — neither installed. Either would unblock more of `flutter doctor`
  (`build apk` needs the Android SDK specifically) but is a separate, much larger install than what
  "install Flutter" implied; flagged rather than done unprompted.
- **Faz 6 — Fiziksel Dünya / IoT** stays hardware-gated (Zigbee coordinator dongle + Home Assistant
  instance; owner confirmed again this session: no hardware yet).
- **`WsClient.reconnect()`'s fix has no live caller yet** — still dead code (correct now, but
  nothing invokes it) until mobile settings-switching (change server/API key without restarting)
  becomes a real feature.
- **Test suite is still "minimal," not comprehensive** — 103 tests now, but most tool modules
  (spotify, todo beyond the one bg-task test, scheduler, gmail/drive actions beyond calendar's
  dedup guard and finance's sync, the sub-agent bridges, voice) still have zero coverage.

## Git state as of this session

- `main`: pushed, tracks `origin/main` (`github.com/mertkaanakgunlu-debug/JARVIS`, public).
- `langgraph-migration`: local only, currently identical commit to `main`. Currently checked out.
- All of this session's fixes (memory.py, gcp_quota.py, finance.py, graph/tools.py, usage.py,
  ws_client.dart) plus the 4 new test files and the doc updates are **uncommitted** — per this
  project's standing instruction, only commit when explicitly asked; this session wasn't asked to.
  `git status` will show modified files across `jarvis/`, `mobile/lib/core/ws_client.dart`, `tests/`,
  and the docs listed above.

## Recommended next steps (pick up here)

1. **Decide on a commit** for this session's diff — nothing has been committed yet (see above).
2. **21 stray worktree branches** — ask again if there's appetite to clean these up; still needs
   explicit go-ahead every session, hasn't been given yet.
3. **Android SDK**, only if real APK builds/device testing become a real near-term need — otherwise
   leave it; `flutter analyze` already covers "does the Dart code compile."
4. **Gemini embedding tier** — model id fixed and construction-time smoke-tested, but a full
   real embed call has never completed end-to-end (this account is out of prepayment credits, a
   billing state, not a code issue). Once credits are topped up, worth a quick real check that
   Gemini-backed recall actually returns results, not just that construction succeeds.
5. Consider extending `tests/` coverage to another tool module or two per future session touching
   that area — same standing suggestion as last session, still true.

## Environment checklist to resume work

```powershell
.\.venv\Scripts\Activate.ps1
pytest                                # 103 tests, ~19s, fully offline
ollama serve                          # confirm it's up: curl http://localhost:11434/api/tags
python -m jarvis                      # CLI
python -m jarvis --api --monitor      # long-running path

# mobile/ (Flutter now installed at C:\flutter, on user PATH in new terminals)
cd mobile
flutter pub get
flutter analyze                       # zero errors expected; 69 pre-existing info-level notices
```

No new required `.env` vars this session. No new Python dependencies. Flutter SDK is new
system-level tooling (`C:\flutter`, user PATH) — not a project dependency, doesn't touch
`requirements.txt`/`.venv`.
