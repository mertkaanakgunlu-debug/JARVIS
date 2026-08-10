---
paths:
  - "jarvis/graph/**"
  - "jarvis/execution/**"
  - "jarvis/tools/**"
  - "jarvis/policy_guard.py"
  - "jarvis/tool_registry.py"
---

# Safety model — read before changing tool-calling behaviour

The confirmation gate is real and load-bearing. `jarvis/policy_guard.py` decides,
`make_confirmation_node` in `jarvis/graph/nodes.py` enforces, and all three
interfaces (CLI text, CLI/API voice, API) are wired to it. Gating is
**per-action, not per-tool**: read-only actions (`list`, `search`, …) on
otherwise-risky tools do not interrupt. Two stronger stops sit beside it — the
kill switch (`jarvis/kill_switch.py`, `/killswitch`) hard-blocks L3 with no
prompt, and `jarvis/audit_log.py` appends every risk ≥ 2 decision and outcome to
`data/audit_log.jsonl`.

External MCP tools register a `ToolSpec` into the same registry, so they inherit
the same gate with no special-casing, fail-closed: anything past page
inspection/navigation needs confirmation.

`JarvisAgent.proactive_turn()` is a second entry point (monitor-initiated). It
runs the same gate but **cannot** raise `ConfirmationRequired` — no interactive
channel exists — so an L3 interrupt becomes a notification instead of an
execution ("confirm-or-notify, never silent execution").

## Invariants worth stating because breaking them is easy

- **A guard on the path beats a guard in a node.** A gate with green unit tests
  may never actually be reached; assert the real edge map, not just the node.
- **Prove each branch separately.** A gate's approve-branch protection says
  nothing about its deny-branch. Enumerate every branch before calling a
  property proven.
- **`off` must be a true no-op.** Rollout modes (`execution_contract_mode`,
  `required_outputs_mode`) promise that `off` is indistinguishable from the code
  before the feature: same node set, same edge map, same checkpoint shape. A
  `{}`-returning node still costs a super-step — don't add one.
- **Remove the consequence, not the choice.** Measured repeatedly here: better
  docstrings and better error messages do NOT stop a model picking the wrong
  tool or argument. Make the wrong choice structurally harmless instead.
- **An ambiguous pair of tools is this codebase's most expensive recurring bug.**
  Adding a second tool that overlaps an existing one has killed whole feature
  chains. Extend the tool that already owns the capability.
- **Never widen a rollout default as a side effect.** Promotion is gated on
  pre-registered measurement, not on a change looking safe.

## Known limits — do not oversell past these

- The Electron HUD's card and transport mechanics have run live against a real
  server, graph, and `BrowserWindow`: no raw protocol leaked, approve executed
  exactly once, and deny executed zero times. The explicit-deny narration bug
  is fixed. **Approve-side terminal result binding is now code-enforced** for
  genuinely user-approved external writes: the shared finalizer uses safe
  execution-ledger provenance and actual success/failure/unknown facts, while
  post-approval model prose is buffered so streaming and voice cannot expose a
  discarded draft. In `off` and `shadow`, execution envelopes remain
  observation-only; only an `enforce_*` mode may make confirmed postconditions
  or verification failures authoritative for that visible receipt. Unknown
  outcomes are never upgraded in any mode. Deterministic fake-write graph/SSE
  coverage exists; a
  post-fix real Gmail/calendar/Drive write E2E was deliberately not run.
- The Flutter confirmation UI has run live on a real Galaxy S26 Ultra: approve
  executed exactly once, deny executed zero times, and no raw protocol appeared
  on screen. Its residual limits are cross-tab visibility and the absent
  `conversation_id`, not an unrun live E2E.
- `python_run`'s L2→L3 reclassification is access control, **not a sandbox** —
  the subprocess still has no resource or network restriction.
- A background `TaskExecutor` job that hits a confirmable action fails with a
  clear message rather than resolving the confirmation.
- Proactive turns structurally gate **L3 only**. An L2 write (e.g.
  `procedure_save`) can still fire unwatched; the mitigation is a system-prompt
  instruction on a non-deterministic model, not a hard guarantee.

`docs/SAFETY.md` has the full mechanism list and `docs/TOOLS.md` the per-tool
risk levels — but when a doc and the code disagree, **the code wins**.
