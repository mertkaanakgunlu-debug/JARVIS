"""Where a live gate's raw rows go, and why they survive the next run.

The Faz 4 gate wrote `revision_gate_results.json` to a RELATIVE path, and the
script `os.chdir()`s into its scratch home at import. So every run overwrote the
previous one inside a temp directory, and when a later session tried to
recompute an earlier run's numbers under a corrected rule, the data was simply
gone -- a comparison had to be withdrawn rather than fixed.

Three properties, each answering one way that went wrong:

  * **Absolute, repo-anchored path.** Not affected by the chdir.
  * **Timestamped filename.** A run never overwrites another.
  * **Atomic write.** Rows are flushed after every chain so a killed run keeps
    what it measured; writing in place would leave truncated JSON instead.

Results land in `.eval-results/` rather than `data/` (which is gitignored but
holds real runtime state) so measurement output and user data never mix.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_ROOT = _REPO_ROOT / ".eval-results"


def timestamp() -> str:
    """Local, offset-aware, filename-safe."""
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")


def default_path(gate: str, stamp: str | None = None) -> Path:
    """`.eval-results/<gate>/<gate>_<ts>.json`, absolute."""
    stamp = stamp or timestamp()
    return RESULTS_ROOT / gate / f"{gate.replace('-', '_')}_{stamp}.json"


def head_commit() -> str:
    """The commit the measurement ran against, or "" if git is unavailable.

    Recorded because a number is only interpretable against the code that
    produced it, and this repo's gates are re-run across sessions.
    """
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT,
            capture_output=True, text=True, timeout=10, check=False,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001 -- provenance is best-effort, never fatal
        return ""


@dataclass
class ResultWriter:
    """Accumulates rows and rewrites the file atomically after each flush."""

    path: Path
    metadata: dict[str, Any] = field(default_factory=dict)
    rows: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.path = Path(self.path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def extend(self, rows: list[dict]) -> None:
        self.rows.extend(rows)
        self.flush()

    def append(self, row: dict) -> None:
        self.rows.append(row)
        self.flush()

    def set_summary(self, summary: dict[str, Any]) -> None:
        self.metadata["summary"] = summary
        self.flush()

    def flush(self) -> None:
        payload = {"metadata": self.metadata, "rows": self.rows}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        os.replace(tmp, self.path)   # atomic: a killed run never leaves half a file
