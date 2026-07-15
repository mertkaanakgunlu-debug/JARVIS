# Contributing to J.A.R.V.I.S.

## Setup

```powershell
cd C:\Users\mertk\Desktop\Jarvis
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt   # canonical install — pyproject.toml has no dep list
```

## Branch convention

Development happens on `langgraph-migration`.
`.claude/worktrees/*` branches are temporary Claude Code scratch branches — never treat them as canonical source.

## Running

See [README.md](README.md) for entry-point modes (`--voice`, `--api`, `--monitor`, `--wakeword`).

## Testing

```powershell
pytest
```

Runs the suite under `tests/` (pytest + pytest-asyncio, configured in `pyproject.toml`). It's
minimal, not exhaustive — see `CLAUDE.md` for what it covers. Any test that touches
`SessionStore`/`UsageTracker`/`kill_switch`/anything resolving `Path("data")/...` relative to
cwd must use the `isolated_cwd` fixture from `tests/conftest.py`; never let a test run against
the real project `data/` directory.

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for a current map of every major subsystem.
