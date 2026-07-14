"""Single-owner arbitration for "who is the active audio session right now."

Faz 3 adds a remote-audio path (RemoteWsAudioIO) alongside the pre-existing
local wakeword/PTT loop (DuplexAudioIO) — both want exclusive use of one
conversation's turn-taking at a time. This is a single-user, single-agent-
instance project (no multi-tenant complexity needed), so first-claim-wins with
an explicit reject is sufficient — no lock needed: the check-then-set has no
`await` in between, so it's atomic in practice on one event loop (same
reasoning as the existing unlocked _ptt_event/_ww_stop module state in
jarvis/voice_api.py).

Scope note: this only arbitrates within one `--api` process (between the local
wakeword/PTT loop and remote-ws clients live inside that process). It does not
prevent someone from separately running `python -m jarvis --voice` in another
terminal at the same time — that's a pre-existing multi-invocation hazard
(separate sounddevice streams, shared SQLite-backed agent state), out of scope
for this arbitration.
"""

from __future__ import annotations

LOCAL_OWNER = "local"

_owner: str | None = None


def try_claim(owner_id: str) -> bool:
    """Returns True if owner_id now holds the claim (either it already did, or
    no one else did). Returns False if a DIFFERENT owner currently holds it."""
    global _owner
    if _owner is not None and _owner != owner_id:
        return False
    _owner = owner_id
    return True


def release(owner_id: str) -> None:
    """No-op if owner_id doesn't currently hold the claim (e.g. already
    released, or never held it) — safe to call defensively from a finally block."""
    global _owner
    if _owner == owner_id:
        _owner = None


def current_owner() -> str | None:
    return _owner
