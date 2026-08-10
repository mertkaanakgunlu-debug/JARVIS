# Completion Contract Finding 2 — Diagnostic Disposition

Date: 2026-08-10
Branch: `langgraph-migration`

## Scope and historical integrity

This closes Finding 2 from
[`completion_contract_pilot_2026-08-05.md`](completion_contract_pilot_2026-08-05.md)
without changing the pre-registered gate, its corpus, thresholds, historical
trial rows, or committed summaries. The historical
`C: honest_failure_retried = 0` result therefore remains exactly what that gate
computed, including its documented blind-spot caveat. This work adds a separate
diagnostic; it does not reinterpret the old acceptance result.

## Root cause and corrected diagnostic

The historical implementation selected only final rows with
`chart_attempted=True` and `chart_executed=False`. A failed call followed by a
completion repair and a later successful call ends with `chart_executed=True`,
so final state removes the trial from the eligible population and erases the
retry from that metric.

The additive diagnostic uses temporal, call-level evidence instead:

1. the eval harness records the exact `tool_call_id` set completed when a
   completion repair is requested;
2. the always-on, append-only execution ledger identifies an actual failed call
   before that boundary; and
3. a distinct later ledger row with the same capability and a different
   `tool_call_id` proves the retry after the boundary.

Invalid-argument IDs and pre-execution block or user-denial IDs are excluded
from the diagnostic. A `MISSING_NO_ATTEMPT` boundary has no prior call and
cannot count. An `outcome=unknown` row is also excluded because it does not
prove an honest terminal failure. Multiple calls of the same tool remain
separate because correlation is by `tool_call_id`, not tool name alone.

The harness emits `honest_failure_retried_diagnostic` and redacted
`honest_failure_retry_evidence` as additive row fields and prints the diagnostic
rate separately. `_gate_verdict()` remains on its historical predicate.

## 4224-second anomaly disposition

The existing phase instrumentation and evidence in
[`completion_contract_ttfb_followup_2026-08-07.md`](completion_contract_ttfb_followup_2026-08-07.md)
were re-examined. The original 4224.29-second row predates the phase split, so
it cannot distinguish slow foreground cancellation from the then-unbounded
background-task drain. The bounded-drain and foreground/drain timing fields
added afterward produced a clean 24-trial live validation with zero timeouts,
errors, or background-drain events, and the anomaly did not recur in the 28
subsequent rows of the aborted run either.

No new reproducible evidence identifies one exact mechanism, and no new live
A/B was run for this disposition. The event is therefore closed as a historical
observability anomaly: real and preserved, but not assigned a fabricated exact
root cause. The instrumentation remains in place if it recurs.
