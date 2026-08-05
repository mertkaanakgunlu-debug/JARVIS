# Completion Contract Source Binding

Fixes the completion-contract pilot's most serious finding
([`completion_contract_pilot_2026-08-05.md`](completion_contract_pilot_2026-08-05.md),
"Finding 1"): asked for a chart of a file that did not exist, a completion repair
listed the workspace, found a *different* fixture, drew a chart from it, and the
contract recorded the result `SATISFIED`. Success only ever meant "a chart artifact
was declared and the working set kept it" — never "was it drawn from what the user
actually asked for". This document describes what changed and, as importantly, what
did not.

## The invariant this protects

**A source-bound chart requirement is `SATISFIED` only if the artifact that satisfies
it was produced from the exact source the user named — traced through both the tool
call that produced it *and* the working-set object that kept it.** Either leg lying
alone is possible (a call can name the right path and still register under a
different one; an object's spec can be edited independently of what actually drew
it), so both are checked. If the source cannot be determined at all, the verdict is
`EVIDENCE_UNAVAILABLE`, never a silent `SATISFIED` — the same "never blame the model
for a gap in our own instrumentation" discipline `jarvis/execution/output_contract.py`
already applied to the working-set read itself.

In `enforce` mode this is backed structurally, not just detected after the fact: a
producer call (`plot_data`) whose own argument names a different source than the one
required is blocked *before* it reaches the tools node
(`jarvis/graph/nodes.py`'s `confirmation_node`, outcome code
`blocked_output_source_mismatch`), so the wrong chart is never even drawn. The repair
directive text also tells the model not to substitute — but per the task's own rule,
that text is a courtesy, not the control; the structural block is what actually
enforces the invariant regardless of whether the model reads or obeys the text.

## Bare filename vs. full path

The resolver (`jarvis/execution/source_identity.py`) distinguishes two request
shapes, and a **live qwen3:8b smoke run caught a false positive from getting this
distinction wrong** (see "What the live smoke found" below):

* **Bare filename** ("satis.csv'nin grafiğini çiz") — `is_explicit_path=False`. The
  match is by **basename identity alone**, whatever directory the producing call's
  own path resolves to. A model reasonably calling
  `plot_data(path="Desktop/satis.csv")` for a bare "satis.csv" request is answering
  correctly, and must not be blocked for using a directory the user never
  constrained.
* **Explicit path** ("./satis.csv", an absolute path, "Desktop/satis.csv") —
  `is_explicit_path=True`. The match requires the **full canonical path** to agree
  (workspace-relative resolution, so `satis.csv`, `./satis.csv` and the absolute form
  of the *same* file all agree with each other). A same-basename file in a different
  directory correctly does **not** match.

This is a property of how the **user phrased the request**, not of whether a
`workspace` happens to be available at comparison time — see
`source_matches()`'s own docstring for why keying it the other way was the bug.

## Why a generic reference stays source-unbound

"bu dosyanın grafiği", "verilerden bir grafik", "tablodaki değerleri görselleştir" —
none of these name a file, so `extract_source_ref()` returns `None` and the
requirement carries no `source` key at all. This is deliberate: inventing a source
for a generic request would make an unbound "any chart will do" turn fail later for
"not matching" a file the user never actually named, which is worse than not checking
at all. An unbound requirement skips the entire source check — `classify()`,
`prepare_execution_node`'s guard and the repair directive all behave exactly as they
did before this phase.

## Scope limit: same basename, different directory

`source_matches()` cannot and does not try to guess which directory a *bare* filename
request means when more than one same-named file could exist. A bare-filename
request is satisfied by **any** call naming that basename — including one that
resolves to a file the user did not have in mind, if such a file happens to exist in
a different directory. Closing that gap would require either asking the user to
disambiguate or the model reliably explaining which one it means, neither of which
this phase implements. This is the direct, named consequence of the "yalnız basename
verilmişse basename karşılaştırılmalı" rule, not an oversight — see
`tests/test_source_identity.py::test_an_explicit_path_request_still_rejects_a_same_named_file_elsewhere`
for the case that *is* covered (an explicit-path request is never fooled by a same
basename elsewhere).

## Inline data (`data_json`)

`plot_data(data_json=...)` is always classified `{"type": "inline"}` and never
matches a `"type": "file"` requirement, in either direction. A user who names a file
is never satisfied by inline data (even correct-looking inline data), and a
hypothetical future inline-typed requirement would never be satisfied by a file call
either — `source_matches()` treats a type mismatch as an unconditional `False`, not
something either side can talk its way around.

## Shadow vs. enforce

| | shadow | enforce |
|---|---|---|
| Pre-execution guard | never runs (`prepare_execution_node` only populates `source_mismatch_calls` when mode is `enforce`) | blocks a mismatched producer call before "tools" |
| Wrong-source call | executes normally, chart IS drawn from the wrong file | never executes |
| Verdict | `OUTPUT_SOURCE_MISMATCH`, computed post-hoc from the call's own args + the working-set object's spec | same verdict, reached without the wrong chart ever existing |
| Answer / messages | untouched — shadow's one rule is "classify and record, mutate nothing" | code-authored honest answer, `response_origin="output_contract"` |
| Repair | none started by this guard either way | none started by this guard; the *existing* one-shot `missing_required_output` repair is unaffected and still capped at one |

`off` is unaffected in both cases: `required_outputs_mode="off"` means
`required_outputs_for()` still resolves a `source`, but no node ever reads it —
`output_contract` is not even added to the graph, and `prepare_execution_node`'s
`mode == "enforce"` check keeps the guard a no-op.

## What this task did not change

* **The latency problem the pilot found (finding 3, treatment `time_to_first_visible`
  p90 = 99.78s against a 60s ceiling) is not addressed here.** Source Binding adds a
  bounded amount of comparison work to nodes already on the hot path; it does not
  touch the streaming buffer finding 3 is about.
* **The pre-registered promotion gate still fails.** Fixing finding 1 does not retry
  the gate — clause A (`object_created` delta) and clause B
  (`unexpected_chart_created`) failed for reasons finding 1 does not touch (model
  reliability calling `plot_data` at all, and `qwen3:8b` spontaneously drawing charts
  on the control arm too). `required_outputs_mode` stays `off` by default; nothing in
  this repository auto-promotes it.
* **The full pre-registered A/B was not rerun.** Verification here is the
  deterministic regression suite (~110 new tests across
  `tests/test_source_identity.py`, `tests/test_output_contract.py`,
  `tests/test_output_contract_graph.py` and `tests/test_prepare_execution_node.py`)
  plus one small, two-scenario live smoke (below) — not a second pilot. See
  `completion_contract_gate.md`'s own rule: a deterministic pass never substitutes
  for the live corpus, and neither does a smoke.
* **`jarvis/tools/chart_objects.py`'s `_canonical_source` (chart-revision identity)
  was deliberately NOT merged into `source_identity.normalize_source_ref`**, despite
  solving a similar problem. It backs a different, separately-tested, already-live
  mechanism (`_same_chart`, `tests/test_working_set.py`) with its own established
  correctness guarantees; routing it through the newer helper would risk a subtle
  behaviour change in a feature this task was not asked to touch. See the comment on
  `_canonical_source` itself.

## What the live smoke found (2026-08-05)

The targeted smoke required by this task has two scenarios: an existing,
explicitly-named correct file (fixture and query fixed so an unrelated
column-inference failure cannot masquerade as a source-binding failure —
`ay,satis` / `Ocak,100` / `Şubat,120` / `Mart,140`, x/y named in the prompt), and a
non-existent requested file with a differently-named decoy present.

**Round 1** (bare `"satis.csv"` request, no column names given) found
`source_matches()` blocking a model's own **correct** call
(`plot_data(path="Desktop/satis.csv")`) as a false positive: the comparison used
full-path equality whenever a `workspace` happened to be available, rather than
keying off whether the *request itself* named a directory. Fixed by adding
`is_explicit_path` to the source-ref shape (see above) — a bare-filename request
now matches by basename alone, regardless of which directory the call resolves to.

**Rounds 2-4** (after the fix, explicit `"Desktop/satis.csv"` request with x/y
named, per this task's own tightened fixture): the correct-file scenario reached
full `SATISFIED` proof with concrete evidence exactly once (round 4) and was
blocked by the guard twice more (rounds 2-3) — for a reason unrelated to source
binding's own logic. In both blocked rounds the model's own `plot_data` call
argument was a **malformed path with a duplicated workspace-folder segment**
(e.g. `path="home2\Desktop\satis.csv"` when the workspace itself *was*
`...\home2`, i.e. the model prefixed its own workspace folder's name onto an
otherwise-correct relative path). `source_matches()` correctly identified that
this resolves to a different, non-existent location than the one requested — and
critically, **that call would have failed with the tool's own "file not found"
error even with the guard off**, since the path genuinely does not exist; the
guard changed the diagnostic (`OUTPUT_SOURCE_MISMATCH` at the pre-execution stage
instead of `MISSING_TOOL_FAILURE` after the tool ran) but not the outcome (no
chart, either way). This was not reproduced as tied to any specific folder name
(one blocked round and the one clean round used *different* scratch-folder
names) — it reads as `qwen3:8b` path-construction variance across otherwise
identical prompts, a pre-existing model-reliability question the pilot already
documents extensively (`attempted_not_executed`), not a source-binding defect.
Two data points is not enough to characterize this precisely, and this smoke
does not attempt to.

**Round 4 (clean) — full evidence, all nine of this task's own criteria:**

| criterion | result |
|---|---|
| `plot_data` reached and ran in `tools` (not blocked) | met — `preexecution_history` empty |
| `ToolMessage` answered ok | met — no `[ERROR]`/`[BLOCKED]` prefix |
| `ToolMessage.artifact` declares a chart | met |
| PNG exists on disk and is readable | met — `satis_line_ay_satis.png`, 48410 bytes, valid PNG magic bytes, opened and visually confirmed: a line chart, x=`Ocak/Şubat/Mart`, y=`100/120/140`, matching the fixture exactly |
| Working Set chart object created | met |
| Working Set `spec.source` names the correct file | met — the object's own `source` is the resolved absolute path to `satis.csv` |
| completion contract's terminal verdict | met — `SATISFIED`, read directly from `data/execution_verification.jsonl`, not inferred |
| no source-mismatch block this turn | met |
| success reported to the user only given the above | met — the model's own final answer states the chart was created, consistent with every check above |

**Missing-file + decoy scenario, all rounds (2, 3 and 4 — the fixture/prompt
change did not alter this half):** the model always called `plot_data` with the
**exact requested (non-existent) path**, never the decoy's. The tool answered
honestly (`[ERROR] Data file not found: ...`), the contract's terminal verdict was
`MISSING_TOOL_FAILURE`, `repair_attempts_total` stayed `0` (no automatic retry
substituted the decoy), no chart artifact or Working Set object was ever created,
and no `hava_durumu`-derived file existed anywhere in the scratch home afterward
in any round.

**The pre-execution guard was never exercised live** in any round of this smoke —
the model never attempted to use the decoy or any wrong source on its own, so
there was nothing for the guard to block. Its ability to actually stop a
wrong-source call before execution is demonstrated by the **deterministic**
integration suite only —
`tests/test_prepare_execution_node.py`'s `test_a_mismatched_plot_data_call_is_flagged_in_enforce_mode`
/ `test_confirmation_node_blocks_a_flagged_call_before_the_interrupt` and
`tests/test_output_contract_graph.py`'s
`test_16_24_a_blocked_repair_substitution_ends_as_output_source_mismatch` (which
reproduces the pilot's own finding-1 scenario end to end with a scripted
tool call) — not by this live smoke, and this document does not claim otherwise.

This smoke is not a rollout promotion and did not touch
`jarvis/evals/contract_scenarios.py`, `docs/eval/completion_contract_gate.md`, or any
committed pilot result.
