"""One corrective repair per turn — and the scope that keeps that honest.

Post-MVP Faz 6. Three bounded repairs already exist or arrive with the
completion contract:

    invalid tool-call args   confirmation_node   (Agent Runtime rev.2, Faz 6 Part 2)
    missing required output  output_contract     (this phase)
    unbacked claim           verification_node   (Post-MVP Faz 1)

Independent, they let one turn spend all three: three extra model rounds, two
of them re-answering something the third already replaced. They also compete
for the same physical headroom -- Settings.max_tool_rounds_per_turn was raised
to 4 specifically so that "the 4th round is headroom for one bounded repair,
not a 4-step plan" (see config.py). One budget is the honest reading of that.

But merging them UNCONDITIONALLY would change behaviour on turns that have no
contract at all, `required_outputs_mode="off"` included: an invalid-args
repair would consume the claim gate's only chance, on a turn the contract
never looked at. `off` promises to be indistinguishable from the code before
this feature, and a silently shortened repair budget is a distinguishable
difference.

So the shared budget is CONTRACT-SCOPED — it exists only while an enforced
contract is actually in force for this turn. The two predicates below differ
on purpose, and the difference is the whole design:

    shared_budget_spent      contract-scoped ONLY. The claim gate uses this,
                             so an unscoped turn keeps the pre-contract rule
                             (an args repair does not close the claim gate).
    corrective_repair_spent  the above OR the pre-existing per-turn flag. The
                             args gate and the completion node use this, so
                             `args_repair_attempted` stays authoritative for
                             the path that has always owned it -- including on
                             a checkpoint written before any of this existed.

Deliberately NOT in this budget: the critic's revision loop (`revise_count`,
capped at 2). That is a bare regeneration -- it calls no tool and produces no
corrected claim -- so it is a different category of retry, not a fourth
corrective repair.
"""

from __future__ import annotations

from typing import Any

RepairReason = str  # "" | "invalid_args" | "missing_required_output" | "unbacked_claim"


def contract_scoped(state: dict[str, Any] | None, settings: Any) -> bool:
    """Is a shared repair budget in force for THIS turn?

    Both halves are required. The mode alone is not enough: with the contract
    enforced globally, a "summarise the CSV" turn still carries no required
    output, so there is nothing for a completion repair to compete with and
    no reason to shorten the other gates.
    """
    mode = getattr(settings, "required_outputs_mode", "off") if settings is not None else "off"
    return mode == "enforce" and bool((state or {}).get("required_outputs"))


def shared_budget_spent(state: dict[str, Any] | None, settings: Any) -> bool:
    """Has the turn's single corrective repair been used, under the contract?

    Reads only the contract-scoped counter, never `args_repair_attempted` --
    that is what lets an unscoped turn behave exactly as it did before this
    module existed. A stale counter on an unscoped turn (an old checkpoint, a
    future caller) is ignored for the same reason.
    """
    if not contract_scoped(state, settings):
        return False
    return int((state or {}).get("repair_attempts_total") or 0) >= 1


def corrective_repair_spent(state: dict[str, Any] | None, settings: Any) -> bool:
    """As above, plus the invalid-args path's own pre-existing turn flag."""
    return bool((state or {}).get("args_repair_attempted")) or shared_budget_spent(state, settings)


def claim_budget(
    state: dict[str, Any] | None, settings: Any, reason: RepairReason,
) -> dict[str, Any]:
    """State keys recording that the budget was spent, or {} when unscoped.

    Returned as a dict to merge into a node's own return value: checking the
    budget without recording the spend would leave the next gate thinking the
    repair is still available, which is the bug this makes structurally hard
    to write.
    """
    if not contract_scoped(state, settings):
        return {}
    return {"repair_attempts_total": 1, "repair_reason": reason}
