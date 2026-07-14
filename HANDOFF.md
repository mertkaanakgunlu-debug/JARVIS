# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-14 (same-day continuation, third phase)

**Context:** Picked up directly from this same day's earlier sessions, which had finished Faz 0
(memory-critical stabilization) and Faz 1 (local-first brain + model router) — both done but
still uncommitted at the start of this session. This session first committed that prior work as
a checkpoint, then implemented Faz 2 — 5-layer cognitive memory, the owner's #1 priority.

**What happened this session:**

1. **Committed Faz 0 + Faz 1** as a single combined commit (`58dd51f`) — the two phases' diffs
   were too intertwined across shared files (`agent.py`, `graph/graph.py`) to cleanly hunk-split
   without real risk of a bad split on a 600+ line diff with no test suite to catch mistakes; one
   commit matches existing precedent (the immediately prior commit also combined two phases).
2. **Faz 2 implemented — 5-layer cognitive memory complete:**
   - **Semantic:** new `jarvis/fact_extractor.py` + `jarvis/facts_store.py` (SQLite `facts`
     table) + `jarvis_facts` ChromaDB collection. Dedup at insert time via embedding-similarity
     (`Memory.find_similar_fact`). `Memory.recall()` gained a `session_id` filter — episodic
     recall no longer leaks another session's raw turns; facts stay deliberately cross-session.
   - **Procedural:** new `jarvis/procedure_store.py` (SQLite `procedures` table) +
     `jarvis_procedures` ChromaDB collection, replacing the old hardcoded
     `_DATA_REPORT_KEYWORDS` keyword match with semantic retrieval. The pre-existing
     `data_report.md` workflow auto-seeds as the first row (zero regression). New tool
     `procedure_save` (#36) — agent-invoked, explicit, not automatic silent capture.
   - **Meta:** `jarvis/tools/files.py` now refuses any `file_write` under `jarvis/prompts/core/`
     (`PermissionError`) — persona/safety directives are provably never agent-writable. New
     `jarvis/prompts/CORE_VERSIONS.md` (deliberately outside `core/`'s glob) tracks a
     human-bumped version/updated stamp per core prompt file. New `/meta` + `/facts` CLI
     commands.
   - **[BUG-25] fixed:** entity+fact extraction (`_schedule_entity_extraction` renamed
     `_schedule_memory_extraction`) now skips trivially short exchanges via
     `JarvisAgent._should_extract`, verified against all 3 call sites.
   - **Bonus fix found during verification:** first-pass recall-distance thresholds
     (0.5/0.45) were too tight for ChromaDB's default ONNX EF fallback (confirmed live-active —
     Ollama wasn't running during this session despite being installed in Faz 1) — recalibrated
     to 1.1/1.0 based on measured distances. See [MEMORY.md](MEMORY.md) for the full gotcha.
   - Full detail + exact verification performed is in [ROADMAP.md](ROADMAP.md)'s Faz 2 section.
3. **Committed Faz 2** — see git log for the commit hash; message covers all of the above.
4. **Docs updated to reflect Faz 2:** `ROADMAP.md`, `MEMORY.md`, `CHANGELOG.md`,
   `ProjectState.md` (tool table + file map + a note flagging the two parallel phase-numbering
   schemes now in play), `docs/ARCHITECTURE.md` (rewrote the Memory layers table around the
   5-layer taxonomy), `docs/TOOLS.md`, `docs/SAFETY.md` (new working-mechanisms row for the
   write-guard, kept separate from the still-broken confirmation-gate section).

**Verification performed (all isolated, no test suite exists in-repo):** scripted checks
(`22/22` passed) directly exercising fact dedup/insert/bump, cross-session fact recall, episodic
session-scoping (no leak), procedure seed+retrieval regression check, the meta-memory
write-guard (blocks core/, allows elsewhere, read still works), and the BUG-25 guard including
the `resume_and_stream()` edge case — all in a fresh `tempfile.mkdtemp()`, never the real
`data/`. A full real `JarvisAgent()` construction in an isolated `os.chdir()`'d temp dir (per the
isolate-test-data-paths lesson — `JarvisAgent` has no injectable path override, only
`os.chdir()` before construction works) confirmed seeding, `ContextBuilder.build()`, and system
prompt composition all work end-to-end, and that re-construction doesn't duplicate the seed. The
real `procedure_save` **tool object** (via `make_tools()` + `.invoke()`, not just its underlying
storage calls) was also exercised directly. Finally, a real `python -m jarvis` startup against
the actual project session loaded and shut down cleanly on EOF with no exceptions from any Faz 2
code path — the one error surfaced (Vertex ADC missing) is the same pre-existing, already-
documented Faz 1 gap, and only happened after all Faz 2 code had already run cleanly.

**Environment note (unrelated to Faz 2, spotted in passing, not fixed):** running
`python -m jarvis` with piped/non-interactive stdout on this machine's Turkish codepage
(`cp1254`) crashes in Rich's Windows console renderer trying to print the banner
(`UnicodeEncodeError`) unless `PYTHONIOENCODING=utf-8` is set first. Doesn't affect normal
interactive use (a real terminal); only hit when scripting/piping into the CLI non-interactively.
Not a Faz 2 regression — the crash point (`_print_banner`) is pre-existing code this session
never touched.

## Git state as of this session

- Branch: `langgraph-migration`, **not merged to `main`**.
- Faz 0 + Faz 1: committed (`58dd51f`).
- Faz 2: committed — see `git log --oneline -3` for the hash; nothing from this session should
  be left uncommitted. Run `git status` to confirm before starting new work.
- Still 21 stray `.claude/worktrees/*` directories from past sessions, still untouched (still
  needs explicit go-ahead — destructive, Faz 8 territory).

## Recommended next steps (pick up here)

1. **Start Faz 3** ([ROADMAP.md](ROADMAP.md)) — real-time local voice (Silero-VAD + streaming
   faster-whisper + Kokoro/Piper TTS + barge-in). Largest remaining phase (`XL` effort), highest
   day-to-day UX impact, depends on Faz 1's local token streaming.
2. Ollama needs to be running (`ollama serve`, or launch the Ollama app from the Start Menu) for
   the local-first brain *and* the better-quality embedding backend for facts/procedures/docs/
   summaries to actually be used — confirmed OFF by default between sessions on this machine; if
   it's not running everything still works via the cloud/default-ONNX fallback chains, just with
   the wider recall-distance behavior documented in [MEMORY.md](MEMORY.md).
3. Optional, not blocking anything: `gcloud auth application-default login` if Vertex Pro/Flash
   access is wanted (AI Studio Flash + local Ollama both already work without it).
4. Confirm whether the 21 stray worktrees/branches should be cleaned up (still deferred, still
   needs your go-ahead — destructive).
5. Not in this session's scope but noted in ROADMAP.md's bug backlog: `BUG-backfill` (startup
   summary backfill never actually runs — both real entry points construct `JarvisAgent` before
   their event loop starts) is a candidate for a future small fix, either standalone or folded
   into Faz 8 cleanup.

## Environment checklist to resume work

```powershell
.\.venv\Scripts\Activate.ps1
ollama serve                                          # or launch the Ollama app — local brain +
                                                       # better embedding backend; NOT auto-started,
                                                       # confirmed off at the start of this session
gcloud auth application-default print-access-token    # optional — only for Vertex Pro/Flash;
                                                       # still missing on this machine, not required
python -m jarvis                                      # or --api / --voice / --monitor
```

If `pip install -r requirements.txt` hits `resolution-too-deep`, use `uv pip install -r
requirements.txt --python .\.venv\Scripts\python.exe` instead (Google Cloud SDK's loose transitive
pins).

If running the CLI non-interactively (piped stdin/stdout, e.g. for a scripted smoke test), set
`$env:PYTHONIOENCODING = "utf-8"` first or the banner print will crash on this machine's codepage
— see the environment note above. Normal interactive use is unaffected.
