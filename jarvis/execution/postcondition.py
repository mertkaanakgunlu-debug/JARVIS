"""PostconditionSpec / PostconditionResult -- Agent Runtime rev.2, Faz 1.

Reviewer item #3: postcondition checks must be a typed, closed-vocabulary
spec (this module), not an ad hoc list of strings a future reader has to
reverse-engineer the meaning of. Faz 1 only defines the shapes; Faz 3 adds
the runner that actually evaluates a PostconditionSpec against an
ExecutionEnvelope. Faz 8's eval_oracle.py is meant to import this module
directly rather than re-declare an equivalent vocabulary of its own (the
plan's "Uc ilke" #2 -- one verification vocabulary, not two that can drift
apart).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

VerificationStatus = Literal["verified", "unverified", "failed"]


class PostconditionSpec(BaseModel):
    kind: Literal[
        "file_exists", "path_within_workspace", "file_openable",
        "artifact_hash_matches", "row_count_matches", "series_matches",
        "exit_code_matches", "record_exists",
    ]
    params: dict[str, Any] = {}
    severity: Literal["required", "warning"] = "required"
    source: Literal["task_contract", "tool_contract", "policy"]
    validator_version: str = "1"


class PostconditionResult(BaseModel):
    """The runner's verdict on one PostconditionSpec. Faz 1 only needs the
    shape so ExecutionEnvelope can reference it -- Faz 3 builds the runner
    that actually produces these. Absence of a runnable validator is NOT
    success: status must be "unverified", never silently coerced to
    "verified" (the honesty discipline the plan's section F documents for
    postcondition coverage -- e.g. web_search has no deterministic notion
    of success today)."""
    spec: PostconditionSpec
    status: VerificationStatus
    detail: str = ""
