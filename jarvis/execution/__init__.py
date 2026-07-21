"""Agent Runtime rev.2 -- the execution-contract package.

New in Faz 1: typed contract/postcondition/envelope shapes plus a shared
redaction layer, wired in as a pure observer (see envelope.py and
jarvis/graph/tool_accounting.py). Faz 2+ is where these types start
influencing control flow (approval binding, idempotency, postcondition
gating) -- see the plan file referenced from HANDOFF.md for the full
9-phase roadmap.
"""
from __future__ import annotations
