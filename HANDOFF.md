---
handoff_schema: 1
branch: langgraph-migration
covered_through_sha: 1fa49b51c2deedecc4c9500857c2ab5e523aa85c
---

# HANDOFF — current state

Single-state snapshot, rewritten at each session close. It is not history:
`git log` and `CHANGELOG.md` own that. If this document conflicts with the
repository, the repository is right and this file is the bug. The
`covered_through_sha` is the last work commit described here, never the closing
documentation commit.

## 1. Current verified state

- The active branch is `langgraph-migration`. At preparation time on
  2026-08-11, local and remote `main` both resolved to `5f6f6ff` and remained
  untouched. Derive changing branch relationships live.
- NVIDIA NIM is implemented as an optional role provider with observable
  fallback to `qwen3:8b`; missing credentials or model construction failures do
  not break local startup and auth-bearing errors are not logged.
- No NVIDIA model passed the live deployment gate. Production defaults remain
  local: FAST, REASONING, LOCAL, and failure fallback all resolve to
  `qwen3:8b`; `nvidia_cloud_first` remains disabled.
- The valid live tournament and its immutable artifact hash are documented in
  `docs/eval/nvidia_model_tournament.md`.
- Push authority remains owner-only. Derive remote and CI state live before any
  future finalize action.

## 2. Last completed work

Work through `1fa49b5` adds NVIDIA NIM provider profiles, role-aware routing,
safe provider metadata and usage accounting, and a deterministic real-JARVIS
model tournament:

- Authenticated `/v1/models` exposed Nemotron Super, Nemotron Ultra, and GLM
  5.2. Mistral Medium 3.5 was unavailable and was reported without
  substitution.
- The 20-scenario corpus is 18/20 Turkish and runs through the real LangGraph.
  Gmail, Calendar, Drive, weather, files, tasks, checkpoints, memory, and audit
  state are synthetic or scratch-isolated; no real external write is possible.
- Tool callback arguments accept decoded mappings, JSON, or safe Python-literal
  strings. This normalization is required for truthful argument and
  source-binding scores.
- The comparable 60-row shortlist found Super at 75.00% overall but with one
  wrong-source hard blocker; GLM also had one wrong-source blocker and 36
  rate-limited turns; Ultra used local fallback somewhere in every turn. The
  selector returned no FAST or REASONING cloud winner, so no promotion commit
  exists.

## 3. Operational modes and rollout decisions

- Default cloud policy and NVIDIA cloud-first rollout remain off. Explicit
  NVIDIA role configuration can use the measured model profiles and always
  retains `qwen3:8b` as the local fallback.
- `LOCAL` is always `qwen3:8b`; provider construction and runtime fallback are
  visible in turn traces, including provider error types and rate-limit counts.
- NVIDIA trial endpoints are evaluation-only under the cited trial terms. No
  reliable public per-token production price or stable numeric account rate
  limit was found; cost remains `unknown`.
- Approval provenance, confirmation, safety, idempotency, execution-contract,
  required-output, and Result Binding behavior are otherwise unchanged.
- No real Gmail, Calendar, Drive, or other external write was used.

## 4. Tests and CI

Evidence collected on 2026-08-11:

- Targeted provider/router/trace/scorer run
  `.venv\Scripts\python.exe -m pytest -q tests\test_nvidia_provider.py tests\test_model_tournament.py tests\test_nvidia_tournament_scenarios.py tests\test_provider_router.py tests\test_cloud_policy.py tests\test_llm_trace.py tests\test_usage_tracker.py tests\test_no_synthetic_live_data.py tests\test_role_router.py`
  reported `151 passed, 2 warnings`.
- Required selector run
  `.venv\Scripts\python.exe scripts\dev_verify.py --base fcc601b10dd41bb06322a3254aa6b273e6952a2d --run`
  selected FULL PYTHON FALLBACK; diff check and Ruff passed, and pytest reported
  `3551 passed, 5 deselected, 410 warnings in 594.21s`.
- Canonical recorder run
  `.venv\Scripts\python.exe scripts\dev_verify.py --full` passed and recorded
  exact-tree evidence for `1fa49b5`: diff check and Ruff passed; pytest reported
  `3551 passed, 5 deselected, 410 warnings in 564.42s`.
- Independent `.venv\Scripts\python.exe -m ruff check jarvis scripts tests` and
  `git diff --check` both passed.
- Post-push GitHub CI has not yet judged this work. Read Python, Electron, and
  Mobile jobs separately from the live run before closing the lifecycle marker.

## 5. Known open issues

- No measured NVIDIA candidate is deployable. Super and GLM require a fix and
  re-measurement of wrong-source behavior; Ultra requires diagnosis of why each
  graph turn invokes local fallback before any cloud-first reconsideration.
- Mistral Medium 3.5 was not available to this NVIDIA account.
- NVIDIA production/commercial per-token pricing and numeric account rate
  limits remain unknown; trial endpoints are not a production entitlement.
- Mobile and Electron still do not persist and send a stable per-client
  `conversation_id` for every newly initiated chat/upload turn.
- `MOBILE-ASSETS-01`: a clean clone lacks the gitignored font binaries required
  for a full mobile build/test. The wake-word ONNX model is also absent.
- `chromadb` remains unpinned. Flutter defaults/version pinning and full
  selector coverage remain incomplete.
- `python_run` is confirmation-gated but not sandboxed. Proactive L2 behavior
  still relies on prompt-level mitigation.
- `.Codex/worktrees/*` contains historical scratch worktrees with commits not
  all reachable from `langgraph-migration`; do not remove them without owner
  approval.

## 6. Next engineering priority

Keep local routing in production. If NVIDIA evaluation resumes, first isolate
Ultra's per-node fallback cause and Super's wrong-source case with the retained
raw rows; re-run only after deterministic regressions cover those failures.

## 7. Human-required actions

- Obtain an NVIDIA production subscription and authoritative commercial quote
  before any production traffic is enabled.
- Complete Google OAuth re-consent when real Google integration testing resumes.
- Supply or license the mobile font binaries needed for clean-clone builds.
- Decide which Flutter version is canonical and whether Flutter tests join the
  default selector loop.
- Decide the long-term default-branch and `.github` layout.
- Preserve machine-specific Android SDK setup as local-only configuration.

## 8. Session recovery notes

- Session identity is machine-authored. Never infer an ID or hand-write
  `current.json`, `close-marker.json`, or recovery JSON; use
  `scripts/claude_session_state.py` for lifecycle transitions.
- The reusable full-verification record belongs to work commit `1fa49b5`, branch
  `langgraph-migration`, and the current lifecycle identity. The helper reported
  it `REUSABLE` on 2026-08-11.
- A correctly prepared close has exactly one documentation commit after the
  covered work SHA. Derive marker, repository, remote, and CI state live rather
  than trusting remembered values.
