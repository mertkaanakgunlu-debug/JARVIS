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

- The Electron HUD's confirmation round-trip is compile/parser-verified only;
  **no live HUD E2E against a real server+model has been run**. The Flutter app
  renders nothing for confirmations.
- `python_run`'s L2→L3 reclassification is access control, **not a sandbox** —
  the subprocess still has no resource or network restriction.
- A background `TaskExecutor` job that hits a confirmable action fails with a
  clear message rather than resolving the confirmation.
- Proactive turns structurally gate **L3 only**. An L2 write (e.g.
  `procedure_save`) can still fire unwatched; the mitigation is a system-prompt
  instruction on a non-deterministic model, not a hard guarantee.

`docs/SAFETY.md` has the full mechanism list and `docs/TOOLS.md` the per-tool
risk levels — but when a doc and the code disagree, **the code wins**.
