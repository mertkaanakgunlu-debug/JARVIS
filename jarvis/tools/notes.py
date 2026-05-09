"""Note-taking tool — appends to the Obsidian-compatible vault."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.memory import Memory


def append_note(topic: str, body: str, memory: "Memory") -> str:
    """Append a note to the vault and return confirmation."""
    path = memory.save_note(topic, body)
    return f"Note saved to vault: {path.name}"
