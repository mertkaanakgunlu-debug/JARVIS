"""Shared X-API-Key check (Faz 4) — extracted from jarvis/api.py so other
routers (jarvis/api_routers/system.py) can require auth on individual routes
without importing api.py itself and creating a circular import (api.py is
the one that imports and mounts every router)."""

from __future__ import annotations

from fastapi import HTTPException, Request


def check_auth(request: Request, api_key: str) -> None:
    """Raise 401 if api_key is set and the request's X-API-Key header doesn't
    match. No-op (auth disabled) when api_key is empty — local-only use."""
    if not api_key:
        return
    provided = request.headers.get("X-API-Key", "")
    if provided != api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header")
