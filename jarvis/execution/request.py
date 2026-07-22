"""ExecutionRequest -- Agent Runtime rev.2, Faz 2 (plan section C, reviewer #2/#6).

The immutable output of the new prepare_execution node
(jarvis/graph/nodes.py): capability resolve -> normalize -> schema validate
-> canonical path resolve -> risk classify -> TaskContract match all funnel
into one of these per tool call. confirmation_node (Faz 4's gate) now binds
its approval to exactly this object via jarvis.execution.approval, instead of
approving raw tool_call args -- "confirmation TAM OLARAK bu ExecutionRequest
onaylanir (ham arg degil)" in the plan's own words.

Deliberately does NOT carry raw or normalized argument values, only a digest
(normalized_args_digest) -- same discipline jarvis.execution.envelope and
jarvis.execution.redaction already apply, for the same reason: this object
is signed and threaded through graph state (and therefore the SqliteSaver
checkpointer) rather than only ever living in a log line.

Honest scope limits for Faz 2 (see nodes.py's make_prepare_execution_node
for the actual pipeline):
  - normalize / schema validate: a real no-op today -- no ToolSpec carries
    an args_schema yet (that's Faz 6). Args pass through unchanged; the
    digest is still computed so approval binding has something concrete to
    bind to.
  - canonical path resolve: best-effort only (a handful of well-known
    resource-ish arg keys), not full per-tool canonicalization -- see
    target_resource's docstring below.
  - TaskContract match: always "no_contract" -- nothing in this repo
    produces a TaskContract yet (contract.py only defines the shape, Faz 1).
    Reported honestly rather than silently treated as "verified", matching
    postcondition.py's own honesty discipline.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

# Best-effort resource identifier for the HMAC binding's target_resource
# field -- NOT full per-tool canonical-path resolution (that needs the
# tool's own workspace root, which no caller currently has generic access
# to; a real per-tool canonicalization pass is future territory, alongside
# args_schema). Picks the first recognizable resource-ish key present so two
# calls with different targets bind to visibly different resources; falls
# back to the bare capability name when none of these are present.
#
# Faz 7: moved here from jarvis/graph/nodes.py (make_prepare_execution_node's
# module) so the standalone workflow engine (jarvis.execution.workflow_engine
# -- deliberately separate from the single-turn chat graph, see that
# module's docstring) can mint ExecutionRequests with the exact same
# resolution logic instead of a second, drifting copy. nodes.py now imports
# this instead of defining its own.
_RESOURCE_ARG_KEYS = (
    "path", "file_path", "script_path", "output", "file_id", "event_id",
    "to", "url", "query", "title", "name",
)


def resolve_target_resource(capability: str, args: dict) -> str:
    for key in _RESOURCE_ARG_KEYS:
        value = args.get(key)
        if value:
            return f"{capability}:{value}"
    return capability


class ExecutionRequest(BaseModel):
    """Immutable -- built once by prepare_execution, never mutated after.

    A repaired/re-issued tool call (denied -> agent -> a new tool_call) goes
    through prepare_execution again from scratch and gets a brand new
    execution_id + digest + signature; there is deliberately no in-place
    update path, so "the approved request" and "the request about to
    execute" can only ever be compared, never silently drift together.
    """

    model_config = ConfigDict(frozen=True)

    execution_id: str
    capability: str
    action: str
    normalized_args_digest: str
    target_resource: str
    risk_level: int
    requires_confirmation: bool
    allowed: bool
    side_effect_type: str
    idempotency: Literal["none", "natural", "keyed"] = "none"
    task_contract_status: Literal["no_contract", "unverified"] = "no_contract"
    created_at: str
    expiry: str
    single_use_nonce: str
