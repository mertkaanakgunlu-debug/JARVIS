"""Async task CRUD endpoints (Faz 19A-0)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/tasks", tags=["tasks"])

_executor = None


def init_tasks(executor) -> None:
    global _executor
    _executor = executor


def _get():
    if _executor is None:
        raise HTTPException(status_code=503, detail="TaskExecutor not initialised")
    return _executor


class TaskRequest(BaseModel):
    query: str
    force_async: bool = False


@router.post("/")
async def submit_task(body: TaskRequest):
    """Submit a long-running task.  Returns task_id immediately."""
    ex = _get()
    task = ex.submit(body.query)
    return {
        "task_id": task.task_id,
        "status": task.status,
        "async": True,
    }


@router.get("/")
async def list_tasks(status: str | None = None):
    """List tasks, optionally filtered by status (comma-separated)."""
    return [t.to_dict() for t in _get().list_tasks(status=status)]


@router.get("/{task_id}")
async def get_task(task_id: str):
    task = _get().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task.to_dict()


@router.delete("/{task_id}")
async def cancel_task(task_id: str):
    """Cancel a queued task (running tasks cannot be interrupted)."""
    ex = _get()
    if ex.get(task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found")
    cancelled = ex.cancel(task_id)
    return {"ok": cancelled, "note": "running tasks finish naturally"}
