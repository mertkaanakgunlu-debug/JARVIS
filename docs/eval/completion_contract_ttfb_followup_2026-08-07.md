# Completion contract — TTFB / progress-marker follow-up (2026-08-07)

**Status: FOLLOW-UP, NOT A GATE RE-RUN.** This document does not re-evaluate
the pre-registered promotion gate (`completion_contract_gate.md`) and does not
revise `completion_contract_pilot_2026-08-05.md`. Neither file was edited.
`required_outputs_mode` default is **unchanged: `off`** — see §8.

Scope: give a contracted+`enforce` turn a safe, structured progress signal
before the graph finishes, without relaxing the buffered-answer invariant that
Post-MVP Faz 6 built. New work only; no rollout decision was reconsidered.

## 1. Root cause of the pilot's Finding 3

`completion_contract_pilot_2026-08-05.md`'s own Finding 3 already named the
symptom: treatment `time_to_first_visible_output` p90 = 99.78s against a 60s
ceiling, converging on total latency (100.67s). Confirmed here at the code
level, in `jarvis/agent.py`'s `chat_stream()` and `resume_and_stream()`
(pre-change):

```python
buffered = (self.settings.required_outputs_mode == "enforce"
            and bool(state.get("required_outputs")))
...
async for delta in graph_stream_to_text(self._graph, state, config):
    ...
    chunks.append(delta)
    if not buffered:
        yield delta          # <- buffered turns never reach this line
...
if buffered:
    yield full_response      # <- the ONLY yield, after the graph is done
```

When `buffered` is true, every delta is accumulated into `chunks` but **none
is yielded** until the graph fully finishes and `full_response` is produced.
Nothing crosses the generator boundary in between — so any observer of "first
visible output" (the harness's `time_to_first_visible_s`, and any real
client) necessarily measures full completion latency. This is deliberate,
documented behavior (`_contract_buffered()`'s own docstring), not a bug: it
exists because a completion repair can *replace* an already-streamed draft,
and voice cannot un-speak a sentence. The buffering was correct; it simply had
no signal of its own.

## 2. Protocol: the `__jarvis_progress__` control frame

One new internal marker, shaped like the existing `__jarvis_confirm__` /
`__jarvis_final__` markers (`jarvis/voice/session.py`):

```json
{"__jarvis_progress__": true, "phase": "preparing_required_output", "kind": "chart"}
```

- `phase`: `"preparing_required_output"` (from `chat_stream()`) or
  `"resuming_required_output"` (from `resume_and_stream()`, after a
  confirmation approval on a contracted turn).
- `kind`: the resolved requirement's `kind` (e.g. `"chart"`) when known;
  omitted otherwise. **Never** a source path, filename, tool argument,
  credential, or model prose — the marker is built from
  `required_outputs[0].get("kind")` only, never the requirement dict as a
  whole (which can carry a `source` sub-dict — see
  `completion_contract_source_binding.md`).

Emission: exactly once, immediately before the buffered graph call, from
**inside** the same `try/except (asyncio.CancelledError, GeneratorExit)`
block the streaming loop already used — so a cancellation landing at that
exact yield is covered by the pre-existing `interrupted=True` bookkeeping,
not a new, uncovered suspension point. It never joins `chunks`/
`streamed_response`, so it cannot reach history, memory, the run manifest, or
the output-contract classifier — all of those are built from
`chunks`/`full_response`, and the marker is emitted from a separate statement
entirely.

Per-transport handling (new `parse_progress_marker()` / `describe_progress()`
in `jarvis/voice/session.py`, reused everywhere — no second marker
codec was built):

| Transport | Behavior |
|---|---|
| API SSE (`jarvis/api.py::_sse_frames`) | Reframed to `{"type":"progress","phase":...,"kind"?:...}`, same pattern as `confirmation_required`/`final_answer` |
| Electron (`chatStream.js`) | Recognized, kept out of `out.text`; `onProgress` callback drives a `PREPARING OUTPUT…` label in the busy/processing surfaces that already existed (command bar placeholder, upload-drop panel header) — no new UI |
| Mobile (`chat_sse.dart` → `chat_screen.dart`) | Recognized, kept out of the assistant bubble and out of TTS; shown via the existing `_loadingNote`/typing-indicator surface (`"Grafik hazırlanıyor…"` / `"İstenen çıktı hazırlanıyor…"`) |
| Voice (`cli.py` `--voice`, `voice_api.py`, `voice/session.py`'s `resolve_confirmation`) | Spoken as a short, deterministic, non-success-claiming acknowledgement (`"Grafiği hazırlıyorum."` / `"İstenen çıktıyı hazırlıyorum."` / `"I'm preparing the requested output."`), kept out of the turn's persisted response text |

## 3. Pre-existing `__jarvis_final__` gaps found and fixed in the same pass

Section 1 of the task authorized fixing "directly related existing protocol
errors" found while touching this marker vocabulary. Four were found —
each is the same defect class: a correction marker (fires when a critic
revision or verification repair changes the graph's terminal answer, on
*any* turn, independent of buffering) fell through unrecognized and reached
the user as literal JSON:

| Site | Before | After |
|---|---|---|
| `electron/.../chatStream.js` | No `final_answer` case at all — appended as raw JSON, duplicating the draft | Recognized; **replaces** `out.text`, fires `onFinal` |
| `jarvis/voice_api.py`'s `run_one_response()` | No `__jarvis_final__` handling — spoken aloud as raw JSON | Swallowed (mirrors the one call site that already did this correctly) |
| `jarvis/voice/session.py`'s `resolve_confirmation()` | Same gap, on the confirmation-resume path specifically | Swallowed |
| `jarvis/cli.py`'s `_handle_confirmation_cli()` (text mode) | Same gap — printed as raw JSON | **Replaces** the accumulated draft (text can redraw; voice cannot) |

Mobile's `chat_screen.dart` had the equivalent gap for **both** new markers
(no structured-frame recognition at all beyond a literal `{"async":` prefix
check) — fixed via the new `classifyChatChunk()` classifier and
`TranscriptNotifier.replaceLast()`.

## 4. First aborted live run — preserved, not pooled

**Result file (untouched):**
`.eval-results/completion-contract/completion_contract_20260807T150353+0300.json`
— **29/60 rows**, corpus A, `--label ttfb-followup`. Aborted by explicit
owner instruction after an unexplained multi-thousand-second stall was found
in row 2, to avoid burning GPU time against an unproven anomaly. **Not
deleted, not rewritten, not pooled with any other run's statistics below.**

### The anomaly, row 2 (`A1-explicit-chart`, treatment, repetition 1)

| Field | Value |
|---|---|
| `elapsed_s` | **4224.29** |
| `timeout_s` (requested) | 300.0 |
| `timed_out` | `true` |
| `time_to_first_visible_s` / `first_visible_kind` | 2.532 / `"progress"` |
| `time_to_first_answer_token_s` | `null` — no answer token ever arrived |
| `tool_round_count` / `tool_calls_attempted` | 2 / 2 (`plot_data` called twice) |
| `object_created` / `chart_executed` | **`true`** / **`true`** — the chart WAS produced |
| `contract_status` / `contract_action` | `""` / `""` — both unset |
| `repair_attempted` | `false` |
| `final_response` / `streamed_len` | `""` / `0` |

### Proven facts

- The progress marker fired correctly and fast (2.5s) — the feature under
  test worked as designed for this trial.
- The underlying work genuinely completed: `plot_data` ran twice, a chart
  artifact was produced and registered in the working set
  (`object_created=true`). The turn was **not** stuck in tool execution.
- No answer token was ever observed, and `contract_status`/`contract_action`
  (set by the output-contract node once it runs) were never populated —
  whatever consumed the remaining ~4200s happened strictly **after** tool
  execution succeeded and **before** that node completed.
- `elapsed_s` at the time of this run was `time.perf_counter() - started`
  computed **after** `finally: await drain_background_tasks(agent)` — an
  **unbounded** `asyncio.gather(*pending)` with no timeout of its own. The
  pre-fix code could not distinguish "the foreground call itself took this
  long to resolve" from "the foreground call resolved reasonably and the
  background drain was what actually stalled."
- The local model client (`jarvis/providers/__init__.py::_make_local()`)
  sets `timeout=120` on `ChatOpenAI` (Ollama's OpenAI-compatible endpoint).
  `qwen3:8b` is a "thinking"-capable model (`ollama show` capabilities:
  `completion, tools, thinking`).
- The anomaly did not recur in the next 28 trials recorded before the run was
  stopped (elapsed_s ranged 29.2–101.4s), including five later repetitions of
  the identical scenario (`A1-explicit-chart`, both arms).

### Remaining hypotheses (NOT proven — this is exactly what §5's fix targets)

- Whether the ~3924s excess over the 300s nominal ceiling was spent primarily
  in cancellation not completing promptly inside `graph.astream()` (e.g. a
  streaming HTTP read whose idle-timeout never fires because a long
  "thinking" trace keeps the connection non-idle) versus in the then-
  unbounded background drain is **not established** from this row alone —
  the pre-fix harness recorded only the combined `elapsed_s`.
- Whether `timeout=120`'s semantics are a total-call cap or an idle/read cap
  was not verified against `openai`/`httpx`'s actual behavior in this
  codebase's dependency versions; stated as an open question, not a claim.
- Nothing here indicates the anomaly is caused by, or even related to, the
  progress-marker/buffering change itself — the stall occurred **after**
  tool execution and after the (correctly-fired) progress marker, in a code
  path this task did not modify. But this is a plausibility argument, not a
  proof; §5's instrumentation is what turns this into evidence next time.

## 5. Harness fix (round 2) — `scripts/completion_contract_ab.py`

Eval-only. No file under `jarvis/` was touched by this round; production
`_bg_tasks` handling is unchanged and remains correctly unbounded (a real
turn has no reason to abandon a scheduled memory-extraction job).

**Bounded background drain.** `drain_background_tasks(agent, timeout_s=30.0)`
(constant: `BACKGROUND_DRAIN_TIMEOUT_S`) now:
- waits at most `timeout_s` for pending `_bg_tasks` (`asyncio.wait`, not an
  unbounded `asyncio.gather`);
- cancels anything still pending past that, with its own short (5s) bounded
  wait for the cancellation to land;
- **never raises**, and returns telemetry instead of `None`:
  `{count, drained, timed_out, elapsed_s}`.

**Phase-split timing**, captured around the *existing* `finally` block
(unchanged in position and formula for `elapsed_s` itself — the
pre-registered gate's own field is untouched):

```python
finally:
    foreground_elapsed = time.perf_counter() - started   # NEW: before drain
    drain_info = await drain_background_tasks(agent)      # NEW: bounded + telemetry

elapsed = time.perf_counter() - started   # UNCHANGED formula/position
cancellation_cleanup = (
    round(foreground_elapsed - timeout_s, 3) if timed_out else None
)
```

New, additive row fields (never replacing `elapsed_s`/`time_to_first_visible_s`,
which keep their pre-registered meaning): `foreground_elapsed_s`,
`timeout_requested_s`, `cancellation_cleanup_s`, `background_drain_s`,
`background_drain_task_count`, `background_drain_timed_out`.

**Honest reporting split** in `report()` — three views per arm, never
silently merged or dropped:
- `[all, incl. timeouts]` — unchanged formula, matches exactly what
  `_gate_verdict()` computes from the same rows (kept for cross-checking).
- `[completed only]` — excludes timed-out/errored rows, so one stalled trial
  cannot inflate "the" latency number.
- `[timed-out]` — count plus its own `foreground`/`cancellation-cleanup`
  percentiles, always printed when any exist.

`_gate_verdict()` itself: **zero lines changed**, verified by a source-level
test that greps its body for every new field name and asserts none appear —
this remains explicitly not a new acceptance gate.

## 6. Clean live validation

Per the owner's staged plan (small check first, no automatic `--runs 10`):
`--runs 1` (6 trials) → clean → `--runs 3` (18 trials) → clean. Both used the
harness's own bounded drain and phase-timing instrumentation from §5, live,
for the first time. **Pooled below (24 trials, n=12/arm — matching the
2026-08-05 pilot's own sample size for direct comparability)**, computed by
feeding both result files' rows through the harness's own `report()`
function (not hand-rolled) — see
`.eval-results/completion-contract/completion_contract_20260807T170332+0300.json`
(6 trials) and `completion_contract_20260807T171023+0300.json` (18 trials).
**Zero timeouts, zero errors, zero background-drain events in either run** —
the aborted run's anomaly (§4) did not recur across 24 further live trials.

| | control (`off`), n=12 | treatment (`enforce`), n=12 |
|---|---|---|
| `object_created` | 6/12 | 8/12 |
| chart attempted / executed | 10/12 / 6/12 | 12/12 / 8/12 |
| repair triggered / success | 0/12 / 0/12 | 0/12 / 0/12 |
| source mutation | 0/12 | 0/12 |
| completed / timed-out / errored | 12/0/0 | 12/0/0 |
| tool rounds (mean) | 1.42 | 1.08 |
| **first-visible p50/p90** | 14.32s / 20.79s | **2.54s / 2.57s** |
| first-visible kind | `{'answer': 12}` | `{'progress': 12}` |
| **first-answer-token p50/p90** | 14.32s / 20.79s (same as first-visible — `off` never buffers) | **53.36s / 72.64s** |
| total latency p50/p90 | 70.3s / 79.77s | 54.28s / 73.47s |
| background-drain p50/p90 | 0.0s / 0.0s | 0.0s / 0.0s |
| failure classes | `attempted_not_executed` 4, `not_attempted` 2, `satisfied` 6 | `attempted_not_executed` 4, `satisfied` 8 |

**The separation the whole feature exists to produce, measured live:**
treatment first-visible (2.54s/2.57s p50/p90) is now fully decoupled from
total answer latency — a **~51s (p50) / ~70s (p90)** gap opens between "the
user sees the progress control frame" and "the real answer token arrives",
where before this work the two were the same number (pilot: treatment
first-visible p90 99.78s ≈ total latency 100.67s). `first_visible_kind` is
`progress` for 12/12 treatment trials and `answer` for 12/12 control trials —
exactly as designed, no exceptions.

**What did NOT improve, and is not claimed to have:** total latency and
first-answer-token latency remain high (treatment total p90 73.47s; the
graph still takes as long as it takes). Per the pre-registered gate,
re-evaluated here only for completeness — **not as a re-run, and not
licensing any promotion**: `object_created` delta is +1.7/10 against a
pre-registered +2/10 (still short, consistent with the original pilot's
+0.8/10 and well within plausible sampling variance at n=12); corpus B/C
were not run, so five of eleven clauses remain UNMEASURED; `ROLLOUT DECISION:
NO PROMOTION` (harness's own verdict string, unedited). This is expected and
correct — this follow-up was never going to promote anything (§8).

## 7. Deterministic verification

| Command | Result |
|---|---|
| `ruff check jarvis scripts tests` | All checks passed |
| `pytest -q` (full suite) | 3375 passed, 5 deselected, 362 warnings, 619.19s |
| `git diff --check` | clean (exit 0; only CRLF-normalization notices) |
| Electron `npm test` (vitest) | 32/32 passed (18 in `chatStream.test.js`, up from 13) |
| Electron `npm run build` | succeeds, no errors |
| `flutter analyze` | No issues found! |
| `flutter test` (full) | +14 −1 — 14 new pure-Dart tests pass; the 1 failure is the pre-existing `MOBILE-TEST-01` (`lib/app.dart`'s splash timer), unchanged, unrelated |

New test files: `tests/test_completion_contract_ttfb_metrics.py` (16 cases,
source-level, harness deliberately not imported — see its own docstring),
`tests/test_voice_progress_acknowledgement.py`, `mobile/test/chat_sse_test.dart`,
`mobile/test/transcript_provider_test.dart`; extended `tests/test_output_contract_streaming.py`,
`tests/test_terminal_response.py`, `tests/test_voice_confirmation_resume.py`,
`tests/test_cli_confirmation_resume.py`, `electron/.../chatStream.test.js`.

## 8. Rollout decision

**`required_outputs_mode` remains `off`.** This follow-up did not re-run the
pre-registered gate and could not have licensed a promotion even if it had —
the gate's `object_created` delta and `unexpected_chart_created` clauses
already failed in the 2026-08-05 pilot and were not re-measured here. This
work only addresses one specific, informational aspect of the pilot's
Finding 3 (first-*visible* latency); it does not touch, and must not be read
as resolving, the pilot's other failed clauses (Findings 1–2, the
source-substitution repair bug) or the still-unmeasured `false_positive_contract`/
`unexpected_chart_created` results.

## 9. Open issues (unchanged by this work)

- `MOBILE-TEST-01`, `MOBILE-ASSETS-01` — pre-existing, unrelated, unchanged.
- Mobile L3 confirmation: still no approve/deny UI. `chat_screen.dart` now
  recognizes a `confirmation_required` frame (via the new classifier) well
  enough not to display it as raw JSON, but nothing resolves it — the graph
  stays interrupted server-side exactly as before. Not built here; explicitly
  out of scope per the task.
- Pilot Findings 1–2 (source-substitution repair, `honest_failure_retried`
  blind spot) — untouched, still open.
- The exact mechanism behind §4's aborted-run anomaly is not fully resolved
  (see "remaining hypotheses") — only instrumented for next time.
