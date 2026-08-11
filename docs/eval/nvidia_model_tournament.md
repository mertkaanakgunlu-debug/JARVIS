# NVIDIA NIM model tournament

Status: implementation ready; no live result or production promotion exists yet.

## Deployment question

Measure NVIDIA-hosted models against the real JARVIS LangGraph, system prompt,
role router, confirmation gate, tool schemas, execution ledger, source binding,
and response trace. The decision order is task success, tool/argument accuracy,
complex reliability, truthfulness/safety, latency, then cost. `qwen3:8b` is a
freshly measured offline/failure baseline, not the preferred primary model.

## Live availability and commercial status

Checked against NVIDIA's official catalog on 2026-08-11. The catalog listed all
four requested candidates:

- [NVIDIA Nemotron 3 Super 120B A12B](https://build.nvidia.com/nvidia/nemotron-3-super-120b-a12b)
- [NVIDIA Nemotron 3 Ultra 550B A55B](https://build.nvidia.com/nvidia/nemotron-3-ultra-550b-a55b)
- [Z.ai GLM 5.2](https://build.nvidia.com/z-ai/glm-5.2)
- [Mistral Medium 3.5 128B](https://build.nvidia.com/mistralai/mistral-medium-3.5-128b)

Catalog presence is not substituted for the authenticated `GET /v1/models`
result. The harness checks that endpoint first and records unavailable requested
models without replacing them.

NVIDIA labels these hosted endpoints as free prototype endpoints. The official
[API Trial Terms](https://assets.ngc.nvidia.com/products/api-catalog/legal/NVIDIA%20API%20Trial%20Terms%20of%20Service.pdf)
limit them to trial/internal evaluation, make them subject to NVIDIA-defined
usage limits and credits, and require a separate subscription for production.
No reliable public per-token production price was found, so NVIDIA billing is
`unknown` and its tokens remain visibly unpriced. No account-specific rate limit
or latency has been observed because this checkout currently has no
`NVIDIA_API_KEY`.

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
