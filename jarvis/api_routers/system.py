"""System endpoints: ping + Wake-on-LAN (Faz 19A-0)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

if TYPE_CHECKING:
    from jarvis.config import Settings

router = APIRouter(prefix="/system", tags=["system"])

_start_time = time.time()
_settings: "Settings | None" = None


def init_system(settings: "Settings") -> None:
    global _settings
    _settings = settings


def _require_auth(request: Request) -> None:
    from jarvis.api_auth import check_auth
    check_auth(request, _settings.jarvis_api_key if _settings else "")


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


@router.post("/wake", dependencies=[Depends(_require_auth)])
async def wake(body: WakeRequest):
    """Send a WoL magic packet (relay use-case).  Mobile app usually sends the
    packet itself; this endpoint is reserved for future relay scenarios.

    BUG-2 (Faz 4): previously mounted with no auth dependency at all (unlike
    every other router, which is mounted with `dependencies=[Depends(_check_auth)]`
    in jarvis/api.py) — anyone who could reach this port could trigger a WoL
    broadcast. /ping stays auth-free by design (it's the liveness probe the
    mobile app uses to decide whether to *bother* trying to wake the PC).
    """
    if not body.mac:
        return {"status": "ok", "note": "no mac provided — PC already awake"}
    try:
        from jarvis.wol import send_magic_packet
        send_magic_packet(body.mac, broadcast=body.broadcast, port=body.port)
        return {"status": "sent", "mac": body.mac}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
