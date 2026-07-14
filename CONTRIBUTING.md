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

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for a current map of every major subsystem.
