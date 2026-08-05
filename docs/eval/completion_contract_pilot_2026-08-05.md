# Completion contract pilot — 2026-08-05

Measured against the pre-registered gate in
[`completion_contract_gate.md`](completion_contract_gate.md). Nothing in the gate
was changed after these results were seen: not a threshold, not a corpus, not a
metric, not a scenario definition.

| | |
|---|---|
| Code under test | `353dddc` (`test(eval): add completion contract A/B gate`) |
| Model | `qwen3:8b`, local Ollama, `cloud_policy=off` |
| Isolation | dedicated `JARVIS_HOME`, synthetic fixtures, no external write, no cloud call |
| Runs | three sequential live runs, never in parallel |
| Smoke data | **excluded** — the corpus B smoke (1 rep) is not in any number below |

## ROLLOUT DECISION: **NO PROMOTION**

`required_outputs_mode` stays **`off`**. Two clauses of the conjunctive gate
fail; one more is measured but causally ambiguous. Enforce is not recommended.

### Rollout-state clarification (added 2026-08-05, no numbers changed)

The pre-registered gate's own rollout-decision prose (`completion_contract_gate.md`)
reads *"gate fails -> NO promotion. Stays at `shadow`."* -- language that assumes
`required_outputs_mode` was already at `shadow` before this gate ran. It was not: the
real starting **and** current operational mode is **`off`**, and no separate
pre-registered gate exists for an `off -> shadow` transition -- only `shadow ->
enforce` is gated by that document. This pilot's decision is `NO_PROMOTION`;
`required_outputs_mode` stays **`off`**, unchanged by this result. The raw harness
output and the three committed summary JSONs (`docs/eval-results/completion_contract_
pilot_{A,B,C}_summary.json`) keep their original generated `decision` string verbatim
as a historical record; `gate_decision`, `operational_mode_after_pilot` and this note
were added alongside it, not in place of it. No trial row, metric, threshold or the
pre-registered gate text itself was touched.

| clause | result | measured |
|---|---|---|
| A: `object_created` delta ≥ +2/10 | **FAIL** | **+0.8/10** |
| B: `false_positive_contract` = 0 | PASS | 0 |
| B: `unexpected_chart_created` = 0 | **FAIL** | 8 (see the caveat below) |
| C: `honest_failure_retried` = 0 | PASS | 0 (but see finding 2) |
| C: `repair_loop` = 0 | PASS | max repair_count = 1 |
| C: not INCONCLUSIVE | PASS | 12 eligible ≥ 5 |
| source mutation = 0 | PASS | 0 / 76 trials |
| treatment `attempted_not_executed` ≤ control + 1/10 | PASS | +0.0/10 |
| treatment total latency p90 ≤ control × 1.5 | PASS | 100.67s vs 107.98s |
| treatment first-visible p90 ≤ 60s (absolute) | **FAIL** | **99.78s** |
| treatment mean tool rounds ≤ control + 1.0 | PASS | 1.17 vs 1.25 |
| streaming duplicate output = 0 | UNMEASURED | proven deterministically, not live |

## Baseline, reproduced — not quoted

The task required the Faz 4 chain baseline to be re-run rather than taken from
any document. Same manifest, same fixtures, `scripts/revision_gate.py --runs 5`.

| | chart creation | full chain | gate |
|---|---|---|---|
| **Historical documented** (2026-08-03, HANDOFF) | 1/5 | — | NOT PASSED |
| **Historical documented** (earlier, ROADMAP) | 3/5 | — | NOT PASSED |
| **Newly reproduced** (2026-08-05) | **1/5** | **0/5** | **NOT PASSED**, COVERAGE INSUFFICIENT |
| **Completion-contract pilot** (corpus A, control arm) | 7/12 | n/a — single-turn | n/a |

Two things follow. The reproduced creation rate matches the more recent
document (1/5) and not the older one (3/5). And the reproduced full-chain
result is **worse** than the historical narrative: the 2026-08-03 run had one
chain scoring 7/7, this one has none. Coverage is INSUFFICIENT on both, so
neither is a stable rate — the chain gate remains unpassed and under-sampled.

The corpus A control arm (7/12 creation on a single explicit request) is **not
comparable** to the chain's step 0: different query set, single turn, no
revision steps. It is listed only to prevent the two being conflated later.

## Per-arm results

### Corpus A — happy-path chart, `chat_stream` both arms, n=12/arm

| | control (`off`) | treatment (`enforce`) |
|---|---|---|
| `object_created` | 7/12 | 8/12 |
| chart attempted | 11/12 | 12/12 |
| chart executed | 7/12 | 8/12 |
| repair triggered / success | 0 / 0 | 1 / 1 |
| source mutation | 0/12 | 0/12 |
| tool rounds (mean) | 1.25 | 1.17 |
| latency p50 / p90 | 63.65s / 107.98s | 76.08s / 100.67s |
| first-visible p50 / p90 | 21.05s / 25.89s | **75.26s / 99.78s** |
| failure classes | `attempted_not_executed` 4, `not_attempted` 1 | `attempted_not_executed` 4 |

The contract did what it was built to do — the single `not_attempted` in the
control arm has no counterpart in the treatment arm, and the one repair that
fired succeeded. That is +1/12 = +0.8/10, against a pre-registered +2/10. The
mechanism works; the effect is smaller than the threshold that was set for it.

**The dominant failure class is untouched.** `attempted_not_executed` is 4/12 on
both arms: the model calls `plot_data` and the call does not produce a chart.
The completion contract does not address that class by design — it is
invalid-args territory — and the pilot confirms it neither helps nor harms
there.

### Corpus B — non-chart control, n=12/arm

| | control | treatment |
|---|---|---|
| `object_created` | 4/12 | 4/12 |
| chart attempted | 4/12 | 4/12 |
| `false_positive_contract` | 0 | 0 |
| failure classes | `not_attempted` 8, `satisfied` 4 | identical |

**`false_positive_contract` = 0 is the contract layer passing cleanly**: the
deterministic resolver assigned a requirement to zero non-chart queries.

**`unexpected_chart_created` = 8 fails the clause, and the cause is not the
contract.** The rate is **identical on both arms — 4/12 each**. `qwen3:8b`
spontaneously draws a chart for roughly a third of "analyse this CSV" style
questions, with the feature switched off exactly as often as with it enforced.

The threshold was not reinterpreted to accommodate this. The clause fails as
written, and it contributes to NO PROMOTION. But it should not be read as
evidence against the contract: a metric whose control and treatment arms are
equal carries no information about the treatment. Whether the clause was
intended to mean "charts the contract caused" is a question for whoever wrote
it — changing it now, after seeing the number, is exactly what the gate forbids.

### Corpus C — deterministic failure, n=14 attempts/arm

| | control | treatment |
|---|---|---|
| `object_created` | 0/14 | **2/14** |
| chart attempted | 11/14 | 14/14 |
| repair triggered / success | 0 / 0 | 2 / 2 |
| eligible failure trials | — | 12 (≥ 5, so not INCONCLUSIVE) |
| failure classes | `attempted_not_executed` 11, `not_attempted` 3 | `attempted_not_executed` 12, `satisfied` 2 |

`repair_loop = 0` holds: `repair_count` never exceeded 1.

The two treatment successes are **finding 1 below**, not a win.

## New findings — reported, not fixed

Per the task's own rule, no product-code change was smuggled into the results
commit. Both of these are open.

### Finding 1 — a completion repair can satisfy the contract from the wrong file

Corpus C asks for a chart of `yok_boyle_bir_dosya.csv`, which does not exist.
On two treatment trials the repair round ran:

```
C1-missing-file  repair=missing_required_output  tools=['file_list', 'plot_data']
C1-missing-file  repair=missing_required_output  tools=['file_list', 'plot_data', 'data_analyze', 'plot_data']
```

The model listed the directory, found the *other* fixture (`satis.csv`) and drew
a chart from it. The contract recorded `SATISFIED` and `repair_success=True`.

The user asked for a chart of file X; the system produced a chart of file Y and
called the requirement met. The repair directive invites this directly — it
says *"Infer the missing details (file path, column names) from the tool results
already in this turn"* — and the contract's success criterion (a declared chart
artifact registered in the working set) cannot tell the two files apart.

Severity: this is the completion contract *manufacturing* a satisfied
requirement out of an honest failure. It is the strongest single argument
against promotion in this pilot, and it is invisible in the gate's own clauses:
`honest_failure_retried` counts repairs on trials that **remained** failed, so a
repair that fired on an honest failure and then "succeeded" by substitution is
counted as a pass. The clause was not modified after the fact.

### Finding 2 — `honest_failure_retried` as implemented has a blind spot

Stated plainly because finding 1 hides behind it: the clause counts eligible
rows (attempted, not executed) that carry `repair_attempted`. A repair that
fires on an honest tool failure and then produces *something* moves the row out
of the eligible set, so it is never counted. The clause passed (0) while two
honest failures were in fact repaired.

The metric was left exactly as pre-registered. Fixing the definition after
seeing the result is precisely the move the gate exists to prevent — this is a
note for the next revision of the gate, not a change to this one.

### Finding 3 — the buffer's latency cost is real and fails its own clause

`time_to_first_visible_output` p90 on the treatment arm is **99.78s** against an
absolute 60s ceiling (control: 25.89s). This is not a regression, it is the
buffer working as designed: a contracted turn emits nothing until the graph
finishes, so first-visible converges on total latency. The design accepted a
TTFB cost; the pre-registered ceiling says the cost as measured is too high on
this hardware and model.

## Scope limits of this pilot

- **n is 12/arm (A, B) and 14 attempts/arm (C)**, above the pre-registered
  minimums (10/arm, max 15 attempts). Each corpus runs several scenarios, so
  "12/arm" is 4 repetitions × 3 scenarios, not 12 repetitions of one query.
- **`streaming duplicate output` was never measured live.** It is proven
  deterministically in `tests/test_output_contract_streaming.py`. The gate
  clause reads UNMEASURED, not PASS.
- **The confirmation run was not performed.** The gate only calls for it when
  the pilot triggers the threshold; it did not.
- **Corpus D (the contract's unreachable state space) is deterministic**, not
  live — invalid args, pre-execution blocks, unreadable evidence and the wrong
  artifact kind cannot be provoked from a prompt on demand.
- **One model, one machine, one day.** Nothing here generalises to a different
  local model or to a cloud tier.

## Recommended next single step

Not "tune the threshold" and not "ship it".

**Fix finding 1: bind the repair to the requested source.** The contract's
success criterion should require that the declared chart artifact derive from
the data source the user actually named, so a repair cannot satisfy the
requirement by substituting whatever file happens to be nearby. Until then the
contract can convert an honest "that file does not exist" into a confident
chart of something else — which is worse than the `not_attempted` it was built
to remove.
