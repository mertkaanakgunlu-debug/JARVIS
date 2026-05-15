"""To-do REST endpoints (Faz 19A-0)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from jarvis.todo_store import TodoStore

router = APIRouter(prefix="/todos", tags=["todos"])

_store: TodoStore | None = None


def init_todos(store: TodoStore) -> None:
    global _store
    _store = store


def _get() -> TodoStore:
    if _store is None:
        raise HTTPException(status_code=503, detail="TodoStore not initialised")
    return _store


# ── Models ──────────────────────────────────────────────────────────────────

class TodoCreate(BaseModel):
    title: str
    description: str = ""
    due_date: str = ""
    category: str = "other"
    priority: str = ""
    priority_score: float = 0.5


class TodoUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    due_date: str | None = None
    category: str | None = None
    priority: str | None = None
    priority_score: float | None = None
    instructions: str | None = None


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/")
async def list_open(category: str = ""):
    return _get().list_open(category=category)


@router.get("/today")
async def today_todos():
    """Return todos due today (or with no due date, high-priority top)."""
    from datetime import date
    today = date.today().isoformat()
    store = _get()
    all_open = store.list_open()
    today_items = [
        t for t in all_open
        if not t.get("due_date") or t["due_date"][:10] <= today
    ]
    return today_items[:20]


@router.get("/done")
async def list_done(limit: int = 20):
    return _get().list_done(limit=limit)


@router.get("/{todo_id}")
async def get_todo(todo_id: str):
    todo = _get().get(todo_id)
    if todo is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    return todo


@router.post("/")
async def add_todo(body: TodoCreate):
    store = _get()
    tid = store.add(
        body.title,
        description=body.description,
        due_date=body.due_date,
        category=body.category,
        priority=body.priority,
        priority_score=body.priority_score,
    )
    return {"id": tid, **store.get(tid)}


@router.patch("/{todo_id}")
async def update_todo(todo_id: str, body: TodoUpdate):
    store = _get()
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    ok = store.update(todo_id, **fields)
    if not ok:
        raise HTTPException(status_code=404, detail="Todo not found")
    return store.get(todo_id)


@router.post("/{todo_id}/done")
async def mark_done(todo_id: str):
    ok = _get().mark_done(todo_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Todo not found or already done")
    return {"ok": True}


@router.delete("/{todo_id}")
async def delete_todo(todo_id: str):
    ok = _get().delete(todo_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Todo not found")
    return {"ok": True}
