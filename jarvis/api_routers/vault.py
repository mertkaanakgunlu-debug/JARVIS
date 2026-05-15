"""Vault REST endpoints (Faz 19A-0) — exposes memory/doc entries."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/vault", tags=["vault"])

_memory = None


def init_vault(memory) -> None:
    global _memory
    _memory = memory


def _get():
    if _memory is None:
        raise HTTPException(status_code=503, detail="Memory not initialised")
    return _memory


@router.get("/count")
async def count():
    mem = _get()
    return {
        "docs": mem.count_docs(),
        "turns": mem.count(),
    }


@router.get("/recent")
async def recent(n: int = 20):
    """Return recent vault entries across docs + memory turns."""
    mem = _get()
    try:
        entries = mem.recent_entries(n)
    except AttributeError:
        # Fallback: search for empty string to get all (sorted by insert order)
        try:
            results = mem._docs.peek(n)
            entries = [
                {
                    "id": did,
                    "text": doc[:200],
                    "metadata": meta,
                    "type": meta.get("type", "NOTE"),
                }
                for did, doc, meta in zip(
                    results["ids"], results["documents"], results["metadatas"]
                )
            ]
        except Exception:
            entries = []
    return entries
