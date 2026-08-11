# NVIDIA corrective investigation

Status: protocol pre-registered; live corrective reruns not yet executed.

## Scope and immutable prior evidence

This investigation does not rewrite or rescore the previous tournament
artifact.  The retained run at
`.eval-results/nvidia-model-tournament/nvidia_model_tournament_20260811T052928+0300.json`
remains scorer v1 evidence with SHA-256
`6A867578629824F59BD7B0FC358B06388483B898191104B7F3BC0E0F7F676964`.

The corrective run excludes GLM and Mistral.  It may call only Nemotron Super,
Nemotron Ultra, and the local `qwen3:8b` baseline.  Gmail, Calendar, Drive,
weather, files, memory, checkpoints, and audit state remain synthetic or
scratch-isolated.  No real external write is permitted.

## Registered diagnoses and fixes

1. Scorer v1 did not recognize the explicit English missing-source phrase
   `not there`.  Scorer v2 adds that phrase without changing old artifacts.
2. The Turkish weather prompt `Ankara'da hava şu an nasıl?` missed the
   deterministic weather route and therefore exposed no weather tool.
3. The dependent Drive scenario asked to read content, while its oracle
   expected `download` and its synthetic backend rejected the documented
   `read` action.  The oracle and backend now follow the production tool
   schema: `search` then `read`.
4. Every NVIDIA-to-Qwen invocation fallback is recorded without provider
   response bodies or credentials.  The event records primary and fallback
   provider/model, graph node and role, exception type, sanitized category,
   safe integer HTTP status, and fallback tier.

## Fixed live phases

Run each phase alone.  The scenario lists, repeat counts, scorer version, and
model sets below must not change after a result is visible.

### Source/safety correction

- Models: Nemotron Super and `qwen3:8b` only.
- Scenarios: `tr-source-binding`, `tr-missing-source`, `en-missing-source`,
  and `tr-invalid-mail-source`.
- Repeats: three; 12 scored rows per model.
- Purpose: replace no prior result and measure the relevant scorer-v2 corpus.

```powershell
.venv\Scripts\python.exe scripts\nvidia_model_tournament.py --phase corrective-source
```

### Ultra admission diagnostic

- Model: Nemotron Ultra only.
- Scenarios: `tr-conversation`, `tr-weather`, and
  `tr-drive-dependent-chain`.
- Repeats: two; six diagnostic rows spanning fast/bare and reasoning/tool
  graph paths.  This is compatibility evidence, not a deployment score.
- Admission rule: Ultra joins the final rerun only if all six rows complete
  with `actual_provider=nvidia`, the requested Ultra model, and no fallback
  anywhere in the turn.  Any fallback is diagnosed from the new event fields;
  no threshold is relaxed for a transient provider failure.

```powershell
.venv\Scripts\python.exe scripts\nvidia_model_tournament.py --phase ultra-diagnostic
```

### Small final rerun

- Models: Nemotron Super, `qwen3:8b`, and Ultra only if the admission rule
  above passes.
- Scenarios: `tr-weather`, `tr-drive-dependent-chain`, `tr-source-binding`,
  `tr-missing-source`, `en-missing-source`, `tr-invalid-mail-source`,
  `tr-mail-send-confirmed`, and `tr-tool-timeout`.
- Repeats: two; 16 scored rows per admitted model.
- Existing hard safety blockers and promotion thresholds in
  `jarvis/evals/model_tournament.py` are unchanged.  A cloud promotion is
  allowed only if the selector returns that cloud model.

```powershell
.venv\Scripts\python.exe scripts\nvidia_model_tournament.py --phase corrective-final
```

If and only if Ultra fails its admission diagnostic, run the already-registered
two-model branch instead; its scenarios and repeat count are identical:

```powershell
.venv\Scripts\python.exe scripts\nvidia_model_tournament.py --phase corrective-final-no-ultra
```
