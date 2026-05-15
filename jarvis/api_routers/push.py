"""FCM push token registration endpoints (Faz 19A-0)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/push", tags=["push"])

_push_store = None
_fcm_sender = None


def init_push(push_store, fcm_sender) -> None:
    global _push_store, _fcm_sender
    _push_store = push_store
    _fcm_sender = fcm_sender


class RegisterBody(BaseModel):
    token: str
    platform: str = "android"
    device_id: str


class TestBody(BaseModel):
    title: str = "JARVIS Test"
    body: str = "Bu bir test bildirimidir."


@router.post("/register")
async def register(body: RegisterBody):
    if _push_store is None:
        raise HTTPException(status_code=503, detail="Push store not initialised")
    _push_store.register(body.device_id, body.token, body.platform)
    return {"ok": True, "device_id": body.device_id}


@router.delete("/register/{device_id}")
async def unregister(device_id: str):
    if _push_store is None:
        raise HTTPException(status_code=503, detail="Push store not initialised")
    ok = _push_store.unregister(device_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"ok": True}


@router.post("/test")
async def test_push(body: TestBody):
    if _fcm_sender is None:
        raise HTTPException(status_code=503, detail="FCM not initialised")
    sent = _fcm_sender.send_to_all(
        title=body.title,
        body=body.body,
        data={"category": "test"},
    )
    return {"ok": True, "sent_to": sent}
