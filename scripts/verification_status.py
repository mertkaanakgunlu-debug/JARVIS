"""Read the execution-verification rollout metrics and the enforce-gate status.

Post-MVP Faz 1 shipped `execution_contract_mode` defaulting to "shadow": the
honesty kernel verifies every operation and every final answer, counts what it
finds, and does not touch what the user sees. Turning it up to
`enforce_reversible` is deliberately NOT a judgment call -- the plan's rule is
*"100 gercek artifact isleminde 0 false block gorulmeden enforce yok."*

That rule is unusable without a way to read the numbers, which is this script.
Run it whenever you are wondering whether the promotion is earned yet:

    .venv\\Scripts\\python.exe scripts\\verification_status.py

`--mark-false-positive "reason"` records an operator judgment that a fired gate
was WRONG -- verification contradicted a claim that was actually true. That
number is never inferred (if code could detect it, the gate would not have
fired), so it only ever gets here by a human deciding it. One is enough to keep
enforce shut.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.execution import rollout  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--mark-false-positive", metavar="REASON",
        help="record that a fired gate was wrong (see module docstring)",
    )
    args = parser.parse_args()

    if args.mark_false_positive:
        rollout.mark_false_positive(note=args.mark_false_positive)
        print(f"recorded false positive: {args.mark_false_positive}\n")

    status = rollout.enforce_gate_status()
    print(f"stream: {rollout._path()}")
    if status["verification_total"] == 0 and status["claim_gate_fired"] == 0:
        print("\nNo verification recorded yet -- either nothing has run since Faz 1,")
        print("or execution_contract_mode is 'off'.")
        return 0

    print("\nPer-operation verification")
    print(f"  total            {status['verification_total']}")
    print(f"    verified       {status['verified']}")
    print(f"    unverified     {status['unverified']}")
    print(f"    failed         {status['failed']}   <- tool said ok, disk disagreed")
    print(f"    not_applicable {status['not_applicable']}")

    print("\nUnbacked-claim gate")
    print(f"  fired            {status['claim_gate_fired']}")
    print(f"  repaired         {status['claim_gate_repaired']}")
    print(f"  blocked          {status['claim_gate_blocked']}")

    print("\nEnforce promotion gate")
    print(f"  artifact operations  {status['artifact_operations']} "
          f"/ {status['required_artifact_operations']}")
    print(f"  false positives      {status['false_positive_known']} (operator-reported)")
    print(f"  READY                {'yes' if status['ready'] else 'no'}")
    print(f"\n  note: {status['caveat']}")
    if status["ready"]:
        print("\n  Threshold met. Also confirm the streaming caveat in docs/SAFETY.md")
        print("  before flipping the mode -- an enforce-mode block cannot un-send a")
        print("  draft the HUD has already streamed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
