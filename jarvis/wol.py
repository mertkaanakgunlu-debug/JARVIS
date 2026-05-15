"""Wake-on-LAN magic packet builder and sender (Faz 19A-0)."""

from __future__ import annotations

import socket


def build_magic_packet(mac: str) -> bytes:
    mac_bytes = bytes.fromhex(mac.replace(":", "").replace("-", "").replace(".", ""))
    if len(mac_bytes) != 6:
        raise ValueError(f"Invalid MAC address: {mac!r}")
    return b"\xff" * 6 + mac_bytes * 16


def send_magic_packet(
    mac: str,
    broadcast: str = "255.255.255.255",
    port: int = 9,
) -> None:
    """Send a WoL magic packet to the given broadcast address."""
    pkt = build_magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(pkt, (broadcast, port))
