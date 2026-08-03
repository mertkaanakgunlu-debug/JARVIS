"""Scoring and aggregation for the live gates in `scripts/`.

Pure logic only: no model, no filesystem, no environment mutation. The scripts
that drive a real model import FROM here; nothing here imports them.

That direction is the whole point. `scripts/revision_gate.py` sets JARVIS_HOME,
CLOUD_POLICY and JARVIS_SKIP_DOTENV, calls os.chdir(), imports jarvis.agent and
monkeypatches a global callback class -- all at module import time. A test that
imported it to check one arithmetic rule would inherit every one of those side
effects. Keeping the arithmetic here makes it testable by `pytest` at zero cost.
"""
