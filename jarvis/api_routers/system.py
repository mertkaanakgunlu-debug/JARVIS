"""System endpoints: ping + Wake-on-LAN (Faz 19A-0)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

if TYPE_CHECKING:
    pass

router = APIRouter(prefix="/system", tags=["system"])

_start_time = time.time()


@router.get("/ping")
async def ping():
    """Auth-free liveness check — mobile app polls this to detect if PC is awake."""
    return {
        "status": "awake",
        "uptime_sec": int(time.time() - _start_time),
        "ts": time.time(),
    }


class WakeRequest(BaseModel):
    mac: str = ""
    broadcast: str = "255.255.255.255"
    port: int = 9


@router.post("/wake")
async def wake(body: WakeRequest):
    """Send a WoL magic packet (relay use-case).  Mobile app usually sends the
    packet itself; this endpoint is reserved for future relay scenarios."""
    if not body.mac:
        return {"status": "ok", "note": "no mac provided — PC already awake"}
    try:
        from jarvis.wol import send_magic_packet
        send_magic_packet(body.mac, broadcast=body.broadcast, port=body.port)
        return {"status": "sent", "mac": body.mac}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
