"""Finance REST endpoints (Faz 19A-0)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException

from jarvis.finance_store import FinanceStore

router = APIRouter(prefix="/finance", tags=["finance"])

_store: FinanceStore | None = None


def init_finance(store: FinanceStore) -> None:
    global _store
    _store = store


def _get() -> FinanceStore:
    if _store is None:
        raise HTTPException(status_code=503, detail="FinanceStore not initialised")
    return _store


@router.get("/summary")
async def summary(year: int | None = None, month: int | None = None):
    now = datetime.now()
    y = year or now.year
    m = month or now.month
    return _get().summary(year=y, month=m)


@router.get("/recent")
async def recent(n: int = 20):
    return _get().recent_transactions(limit=n)


@router.get("/top_categories")
async def top_categories(year: int | None = None, month: int | None = None, n: int = 5):
    now = datetime.now()
    y = year or now.year
    m = month or now.month
    return _get().top_categories(year=y, month=m, n=n)


@router.get("/budget_status")
async def budget_status(year: int | None = None, month: int | None = None):
    now = datetime.now()
    y = year or now.year
    m = month or now.month
    return _get().budget_status(year=y, month=m)
