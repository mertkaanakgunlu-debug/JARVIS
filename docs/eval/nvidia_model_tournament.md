# NVIDIA NIM model tournament

Status: live tournament complete; no NVIDIA model passed the production gate,
so no cloud-first promotion was made.

## Deployment question

Measure NVIDIA-hosted models against the real JARVIS LangGraph, system prompt,
role router, confirmation gate, tool schemas, execution ledger, source binding,
and response trace. The decision order is task success, tool/argument accuracy,
complex reliability, truthfulness/safety, latency, then cost. `qwen3:8b` is a
freshly measured offline/failure baseline, not the preferred primary model.

## Live availability and commercial status

Checked against NVIDIA's official catalog and the authenticated `/v1/models`
endpoint on 2026-08-11. The public catalog listed all four requested candidates:

- [NVIDIA Nemotron 3 Super 120B A12B](https://build.nvidia.com/nvidia/nemotron-3-super-120b-a12b)
- [NVIDIA Nemotron 3 Ultra 550B A55B](https://build.nvidia.com/nvidia/nemotron-3-ultra-550b-a55b)
- [Z.ai GLM 5.2](https://build.nvidia.com/z-ai/glm-5.2)
- [Mistral Medium 3.5 128B](https://build.nvidia.com/mistralai/mistral-medium-3.5-128b)

The authenticated account exposed Nemotron Super, Nemotron Ultra, and GLM 5.2.
It did **not** expose `mistralai/mistral-medium-3.5-128b`; the harness reported
and removed that requested candidate without substitution.

NVIDIA labels these hosted endpoints as free prototype endpoints. The official
[API Trial Terms](https://assets.ngc.nvidia.com/products/api-catalog/legal/NVIDIA%20API%20Trial%20Terms%20of%20Service.pdf)
limit them to trial/internal evaluation, make them subject to NVIDIA-defined
usage limits and credits, and require a separate subscription for production.
No reliable public per-token production price was found, so NVIDIA billing is
`unknown` and its tokens remain visibly unpriced. NVIDIA does not publish a
stable account-specific numeric trial rate limit in the cited materials. The
live run observed 36 rate-limited GLM turns out of its 60 shortlist turns, zero
for Nemotron Super, and zero for Nemotron Ultra. This is observed behavior, not
a claimed contractual limit.

## Live result

The valid run used commit `7dc55d6dd953d613cb1d9d6ba458c15b7bf30749`.
Its raw artifact is
`.eval-results/nvidia-model-tournament/nvidia_model_tournament_20260811T052928+0300.json`
with SHA-256
`6A867578629824F59BD7B0FC358B06388483B898191104B7F3BC0E0F7F676964`.
The table below uses the directly comparable 60-row shortlist slice (20
scenarios, three repeats) for every available model.

| Model | Overall | Turkish | Complex | Tool accuracy | Safety failures | p50 / p90 | Tokens / success | Successful tasks / min |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Nemotron Super 120B | 75.00% | 75.93% | 61.11% | 72.13% | 1 wrong-source | 15.58s / 39.69s | 11,326.67 | 2.052 |
| Nemotron Ultra 550B | 0.00% | 0.00% | 0.00% | 68.42% | 0 | 14.79s / 57.95s | n/a | 0.000 |
| GLM 5.2 | 21.67% | 22.22% | 0.00% | 59.65% | 1 wrong-source | 45.20s / 116.28s | 29,175.31 | 0.225 |
| qwen3:8b | 70.00% | 66.67% | 66.67% | 66.67% | 0 | 11.78s / 51.61s | 9,321.45 | 2.105 |

Nemotron Super had the highest shortlist overall and tool score, but one
wrong-source satisfaction is a hard deployment blocker. GLM 5.2 had the same
blocker and 36 rate-limited turns. Nemotron Ultra had no hard safety failure,
but all 60 shortlist turns and all 45 critical-finalist turns used the local
fallback somewhere in the graph, so no end-to-end NVIDIA-primary task success
could be credited. On the 105-row decision slice, Ultra was 0/105 while Qwen was
72/105 (68.57% overall, 71.05% complex) with no hard safety failure. The
pre-registered selector therefore returned no FAST or REASONING winner.

Final routing remains `FAST -> qwen3:8b`, `REASONING -> qwen3:8b`,
`LOCAL -> qwen3:8b`, with observable NVIDIA-to-Qwen fallback available but not
enabled as the production primary.

An earlier artifact at timestamp `20260811T041519+0300` is superseded and must
not be used: callback arguments written as Python-literal strings were not
normalized, producing false wrong-source failures. Commit `7dc55d6` added safe
JSON/`ast.literal_eval` normalization plus regression coverage before the valid
run.

## Pre-registered protocol

The corpus contains 20 product scenarios; 18/20 are Turkish. It covers no-tool
conversation, Calendar/files/mail/Drive/tasks/weather reads, Turkish dates and
entities, fake confirmed writes, explicit denial, dependent chains, source
binding, missing/invalid sources, tool timeout/unknown outcome, and must-call
hallucination traps. External services are fixed synthetic backends. Files,
tasks, memory, checkpoints, execution records, and audit data use a unique
temporary JARVIS home.

Stages:

1. Smoke: four requested cloud models, one run on six representative scenarios.
   Broken/incompatible models are removed, not silently replaced.
2. Shortlist: top three compatible cloud models plus a fresh local baseline,
   three runs over all 20 scenarios.
3. Finalists: top two cloud models plus the same local baseline, five additional
   runs over the critical stochastic/safety subset.
4. Selection uses only comparable shortlist/finalist rows. A model with any
   confirmation bypass, unauthorized external write, duplicate execution,
   unknown-to-success, or wrong-source satisfaction is disqualified.

The FAST interactive ceiling is pre-registered at p90 <= 15 seconds. A faster
model more than five percentage points behind the best safe overall success is
not eligible for FAST. REASONING sorts by complex success, tool accuracy,
argument correctness, then overall success. Final selection requires at least
10 scored tasks per model.

Raw rows are atomically appended under `.eval-results/nvidia-model-tournament/`
and never overwritten. Run the complete protocol alone, with no concurrent
model workload:

```powershell
$env:NVIDIA_API_KEY = "<key>"
.venv\Scripts\python.exe scripts\nvidia_model_tournament.py
```

The key is represented as a Pydantic secret, never written to results, and
constructor failures log only their exception type.
