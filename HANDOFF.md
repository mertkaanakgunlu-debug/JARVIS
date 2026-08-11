---
handoff_schema: 1
branch: langgraph-migration
covered_through_sha: 82c567996f031e3c025f2508c49e45d023ec2368
---

# HANDOFF — current state

Single-state snapshot, rewritten at each session close. It is not history:
`git log` and `CHANGELOG.md` own that. If this document conflicts with the
repository, the repository is right and this file is the bug.

## 1. Current verified state

- The active development branch is `langgraph-migration`. At preparation time
  on 2026-08-11, local and remote `main` both resolved to `5f6f6ff` and were
  untouched. Derive changing branch relationships live.
- NVIDIA NIM remains evaluation-only and no cloud model passed the existing
  deployment gates. Production FAST, REASONING, LOCAL, and failure fallback
  remain `qwen3:8b`; `nvidia_cloud_first` remains disabled.
- Scorer v2 recognizes the explicit missing-source phrase `not there` without
  changing the immutable earlier artifact. Model timeouts are hard failures in
  harness v2 rather than ledger-only successes.
- NVIDIA-to-Qwen fallback rows now carry secret-safe primary/fallback identity,
  graph node and role, exception type, sanitized category, safe HTTP status,
  and fallback tier. Provider bodies, credentials, and tokens are not logged.
- The authoritative corrective protocol, results, artifact hashes, and rollout
  decision are in `docs/eval/nvidia_corrective_investigation.md`.

## 2. Last completed work

Work through `82c5679` investigated the retained NVIDIA tournament and added
minimal deterministic corrections:

- The old Super `en-missing-source` blocker was a scorer-v1 false positive plus
  unexecuted XML tool markup. A new scorer-v2 safety/source run nevertheless
  found a separate genuine source hallucination, so Super remains blocked.
- Ultra's profile worked intermittently, but the authoritative six-row
  diagnostic produced only two NVIDIA-primary successes and four provider
  `APIError` fallbacks. Ultra failed admission to the final comparison.
- Turkish `hava şu an nasıl` now reaches the weather tool. The dependent Drive
  oracle and synthetic backend now follow the production `search` then `read`
  contract instead of rejecting correct model calls.
- The pre-registered final comparison scored Super 13/16 (81.25%) and Qwen
  14/16 (87.50%); the unchanged selector returned no FAST or REASONING winner.

## 3. Operational modes and rollout decisions

- No production promotion was made. Explicit NVIDIA role configuration retains
  local Qwen fallback, but the default cloud-first switch remains off.
- The source/safety phase is authoritative for blockers; the smaller final
  subset cannot erase the observed Super and Qwen source hallucinations.
- Ultra is not a usable primary on the NVIDIA prototype endpoint. Its unchanged
  profile alternated between real primary completion and provider `APIError` /
  timeout behavior, so no deterministic config incompatibility fix was made.
- The final live workloads used only Super, admitted Ultra diagnostics, and
  Qwen. GLM and Mistral were not run. All external services and writes were
  synthetic or scratch-isolated; no real external write occurred.

## 4. Tests and CI

Evidence collected on 2026-08-11:

- Targeted provider/router/scorer/trace suites were run during iteration; the
  final targeted command
  `.venv\Scripts\python.exe -m pytest -q tests\test_model_tournament.py tests\test_nvidia_tournament_scenarios.py tests\test_llm_trace.py tests\test_nvidia_provider.py`
  reported `48 passed, 2 warnings`.
- The first full selector run exposed one backward-compatibility failure after
  `3556 passed`; `LlmCallTrace.role` was made optional and the rerun
  `.venv\Scripts\python.exe scripts\dev_verify.py --base a25fd937f039457e9685a0d5432fee073fbfda6f --run`
  passed with `3557 passed, 5 deselected, 410 warnings`.
- Exact-tree recorder run
  `.venv\Scripts\python.exe scripts\dev_verify.py --full` passed at work commit
  `82c5679`: diff check and Ruff passed; pytest reported
  `3559 passed, 5 deselected, 410 warnings in 568.04s`.
- Live synthetic measurement sizes were 12 rows/model for source/safety,
  16 rows/model for the Super/Qwen final, and 6 rows for Ultra admission.
  Detailed metrics and immutable hashes live in the corrective result document.
- Post-push GitHub CI for this work had not run at preparation time. Read
  Python, Electron, and Mobile jobs separately from the live run.

## 5. Known open issues

- No NVIDIA candidate is deployable. Super and Qwen both produced a genuine
  no-tool source hallucination in the registered source/safety phase; Ultra is
  unreliable on the prototype endpoint.
- Qwen still failed the corrected dependent Drive chain 0/2 in the final run;
  Super passed 2/2, proving the shared harness fault is fixed while Qwen's
  residual dependent-call behavior remains.
- NVIDIA production/commercial pricing, entitlement, and stable numeric account
  limits remain unknown; prototype trial availability is not production SLA.
- Mobile and Electron still do not persist and send a stable per-client
  `conversation_id` for every newly initiated chat/upload turn.
- `MOBILE-ASSETS-01`: a clean clone lacks gitignored font binaries required for
  a full mobile build/test. The wake-word ONNX model is also absent.
- `chromadb` remains unpinned. Flutter defaults/version pinning and full
  selector coverage remain incomplete.
- `python_run` is confirmation-gated but not sandboxed. Proactive L2 behavior
  still relies on prompt-level mitigation.
- `.Codex/worktrees/*` contains historical scratch worktrees with commits not
  all reachable from `langgraph-migration`; do not remove them without approval.

## 6. Next engineering priority

Keep local routing in production. The next product priority remains stable
per-client `conversation_id` transport/persistence. Any future NVIDIA work
should require a production entitlement and a structural source-binding fix
before another promotion gate.

## 7. Human-required actions

- Obtain an NVIDIA production subscription and authoritative commercial quote
  before considering production traffic.
- Complete Google OAuth re-consent when real Google integration testing resumes.
- Supply or license the mobile font binaries needed for clean-clone builds.
- Decide which Flutter version is canonical and whether Flutter tests join the
  default selector loop.
- Decide the long-term default-branch and `.github` layout.
- Preserve machine-specific Android SDK setup as local-only configuration.

## 8. Session recovery notes

- Session identity is machine-authored. Never infer an id or hand-write
  recovery JSON; use `scripts/claude_session_state.py` for every transition.
- The reusable exact-tree full-verification record belongs to work commit
  `82c5679`, branch `langgraph-migration`, and the current lifecycle identity.
- A correctly prepared close has exactly one documentation commit after the
  covered work SHA above. Derive marker, repository, remote, and CI state live.
