# Completion contract — pre-registered acceptance gate

**Status: PRE-REGISTERED. Written before any result was produced.**

`jarvis/config.py`'s `required_outputs_mode` says promotion is "gated on a
pre-registered A/B whose threshold was written before the run -- see the plan's
measurement section". This file *is* that section, moved into the repository so the
threshold is auditable next to the code instead of living only in a local plan file.

## Provenance

| | |
|---|---|
| Source | `C:\Users\mertk\.claude\plans\de-erlendirmem-claude-bu-abundant-crystal.md` — "`required_outputs` tamamlama sözleşmesi — TASARIM rev.4", section **16. Ölçüm planı (koddan önce yazıldı)** |
| Plan file mtime | 2026-08-03 14:48 |
| Written | before `jarvis/execution/output_contract.py` existed and before any pilot ran |
| Implementation it gates | `b1ee49e..a9430c9` (5 commits), tip `a9430c9362aea334d4cf848975ac2a10565e3ff1` |
| Moved into the repo | 2026-08-04, unchanged |

The threshold below is **copied, not restated**. It has not been eased, tightened,
reinterpreted or re-derived. Where the original is a Turkish code block it is reproduced
verbatim; the surrounding English is description of what the block means, never a
substitute for it.

### Correction to the task spec

`GPT_Prompts/Pr_1.md` named
`C:\Users\mertk\.claude\plans\c-users-mertk-downloads-jarvis-post-mvp-federated-kettle.md`
as the probable source. That file exists but is a different plan ("JARVIS — Post-MVP
Mimari Sağlamlaştırma Planı", 2026-08-01) and contains **zero** occurrences of
"completion contract" or `required_outputs`. The real source is the file named above.

### What this gate is NOT

`jarvis/execution/rollout.py`'s `enforce_gate_status()` implements a **different**
threshold — "100 real artifact operations with zero reported false blocks" — belonging to
the **execution contract** (`execution_contract_mode`, the honesty kernel). The two
rollouts are independent ladders with independent evidence. Neither may stand in for the
other.

---

## Arms

```text
control_agent    required_outputs_mode="off"
treatment_agent  required_outputs_mode="enforce"
```

Two warm agents, not one agent reconfigured between trials: the mode is bound into the
graph's node closures at construction time, so mutating the setting mid-run does not take
effect.

**All three corpora use the SAME entry point on both arms.** Corpus A runs
`chat_stream()` on both — otherwise a transport difference contaminates object success,
total latency, first-visible latency and the streaming-duplicate metric at once.

## Corpora and sample sizes

| corpus | pilot | confirmation | primary metric |
|---|---|---|---|
| **A — happy-path chart** | 10/arm | **20/arm** | `object_created` |
| **B — non-chart negative control** | 10/arm | **20/arm** | `false_positive_contract`, `unexpected_chart_created` |
| **C — deterministic failure** | **5 eligible**/arm, max **15** attempts | **8 eligible**/arm, max **24** attempts | `honest_failure_retried`, `repair_loop` |

The confirmation sample size is fixed **now**; it will not be chosen after seeing the
pilot. The confirmation run is **not pooled** with the pilot.

### Corpus C eligibility

A live model may not call the tool at all, or may produce invalid args. "5 trials" is not
five honest tool-failure observations:

```text
eligible failure trial := plot_data gerçekten tools düğümüne ULAŞTI  VE  ledger ok=False
eligible hedefe ulaşılamazsa sonuç: INCONCLUSIVE   (eşiği "geçti" saymaz)
```

**"Invalid column" must NOT be used** as the failure trigger — `plot_data` repairs
requested columns before drawing via `fit_columns()` (`chart_objects.py:297`):
case/diacritic matching plus a dtype-appropriate default can make the call **succeed**.
Use instead: *a non-existent file with structurally valid args*, and *an
unsupported/corrupt file*. A controlled `ok=False` fake chart tool is for the
deterministic integration test only and **does not substitute for the live A/B corpus**.

## Metrics

Primary metrics are named per corpus in the table above.

```text
ikincil : not_attempted · attempted_not_executed · executed_no_object
          · output_evidence_mismatch · preexecution_block · evidence_unavailable
          · repair_triggered · repair_success · repair_reason
koruma  : source mutation · tool round count · tool calls attempted
          · time_to_first_visible_output (p50/p90) · total_turn_latency (p50/p90)
          · honest failure retried · false-positive repair · streaming duplicate output
```

## Promotion rule — numeric, written before any result

```text
A: treatment object_created − control ≥ +2/10
VE B: false_positive_contract = 0 VE unexpected_chart_created = 0
VE C: honest_failure_retried = 0 VE repair_loop = 0 VE sonuç INCONCLUSIVE değil
VE source mutation = 0
VE streaming duplicate output = 0
VE treatment attempted_not_executed ≤ control + 1/10
VE treatment time_to_first_visible_output p90 ≤ 60 sn        (MUTLAK, oransal değil)
VE treatment total_turn_latency p90 ≤ control p90 × 1.5
VE treatment ortalama tool round ≤ control + 1.0
```

Every clause is conjunctive (`VE` = AND). One failed clause fails the gate.

`time_to_first_visible_output p90 ≤ 60 sn` is **absolute, not relative to control** — the
buffered branch has no meaningful control ratio, and a very fast or very noisy control
would make a ratio meaningless.

## Rollout decision logic

```
gate passes   -> promotion to `enforce` is licensed
gate fails    -> NO promotion. Stays at `shadow`.
                 Classification data keeps accumulating.
threshold undefined for a required clause
              -> ROLLOUT DECISION: BLOCKED — completion-contract acceptance
                 threshold is not defined
```

Not passing is a decision, not a deferral: it licenses "do not ship this", which is worth
running the measurement for.

**The harness prints this rule itself**, with the measured value beside each clause, so
the decision cannot be re-interpreted in prose afterwards.

## Auditable derivative

```
docs/eval-results/completion_contract_*_summary.json   + sha256 of the raw run file
```

Raw runs stay out of git (model prose, absolute local paths). The committed summary
carries per-trial outcome classes and tool sequences plus the raw file's hash, so the
numbers behind a decision can be checked without shipping the transcripts.

---

## Running it

```powershell
# smoke — proves the harness works; NOT a result
.venv\Scripts\python.exe scripts\completion_contract_ab.py --corpus B --runs 1 --label smoke

# pilot, per the sample sizes above
.venv\Scripts\python.exe scripts\completion_contract_ab.py --corpus A --runs 10 --label pilot
.venv\Scripts\python.exe scripts\completion_contract_ab.py --corpus B --runs 10 --label pilot
.venv\Scripts\python.exe scripts\completion_contract_ab.py --corpus C --runs 15 --label pilot

# confirmation — only if the pilot triggers the threshold, never pooled with it
.venv\Scripts\python.exe scripts\completion_contract_ab.py --corpus A --runs 20 --label confirmation
```

**A live harness runs alone.** No other model work, no other harness, nothing
else on the GPU — a previous measurement in this repo was ruined exactly that
way. Corpus C is attempt-capped, not trial-capped: `--runs 15` buys *up to* 15
attempts at 5 eligible trials, and falling short is `INCONCLUSIVE`, not a pass.

Requirements: Ollama up with `qwen3:8b`. The harness sets its own
`JARVIS_HOME` (override with `CONTRACT_AB_HOME`), forces `CLOUD_POLICY=off`,
and never reads the real `.env`.

### What it touches, and what it must not

| | |
|---|---|
| writes | its own scratch home only — fixtures, charts, `.eval-results/` |
| reads | nothing from the user's `data/`, sessions or memory |
| calls | the local model and local tools only — no Calendar, Gmail, Contacts or any external write |
| confirms | nothing: no scenario reaches an action that needs approval |
| bounds | a per-trial `asyncio.wait_for`; no `pytest-timeout` dependency added |
| emits | basenames, never absolute paths — so a committed summary carries no username |

### Deterministic half

`tests/test_completion_contract_harness.py` covers the contract states a live
prompt cannot provoke on demand (invalid args, a pre-execution block, an
unreadable store, the wrong artifact kind), plus shadow parity per verdict and
the enforce bounds. Run with the suite:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_completion_contract_harness.py -q
```

These prove the *mechanism*. They are not live evidence and do not substitute
for the A/B corpora — see the gate's own wording above.
