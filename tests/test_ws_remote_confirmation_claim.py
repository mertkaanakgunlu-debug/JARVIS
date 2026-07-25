"""jarvis/api.py's /ws remote-audio session -- the THIRD confirmation-claim
call site (review remediation, 2026-07-23). CLI (jarvis/cli.py) and the
API's own local wakeword/PTT loop (jarvis/voice_api.py) both route a
transcript through JarvisAgent.claim_pending_confirmation() before treating
it as a confirmation answer -- test_pending_confirmations_ttl.py and
test_voice_confirmation_resume.py cover that primitive and the shared
resolve_confirmation()/is_confirmation_still_pending() helpers directly.
Neither of those two callers' own outer closures has a dedicated test
(this repo's established precedent for thin per-transport wiring -- see
HANDOFF.md's /workflow CLI command note); the /ws remote-audio session is
the third, structurally identical call site, and unlike the other two it
had ZERO coverage of any kind before this file. This proves the ACTUAL
_handle_transcript closure in jarvis/api.py's ws_endpoint(), not a
reimplementation of its logic.

Uses starlette.testclient.TestClient against the real FastAPI app, same
no-lifespan pattern as test_api_upload.py (jarvis.api's module-level
_agent/_settings globals monkeypatched directly, no `with` block so ASGI
startup never runs). jarvis.voice.engine's RealtimeVoiceEngine/
get_shared_voice_models are stubbed so this never touches real STT/TTS
models; jarvis.voice.session.drive_voice_session is replaced with a fake
that calls the real, captured _handle_transcript twice in sequence (first
utterance arms a pending confirmation via a stubbed run_one_response,
exactly as a real turn that hits a confirmable interrupt would; second
utterance is the one under test) instead of driving real audio frames --
everything downstream of that capture (claim_pending_confirmation, the
claimed-vs-None branch, which of resolve_confirmation/run_one_response
gets called) is the real jarvis/api.py code.
"""
from __future__ import annotations

import json
import time

from starlette.testclient import TestClient

import jarvis.api as api
import jarvis.voice.engine as voice_engine
import jarvis.voice.session as voice_session
import jarvis.voice_api as voice_api
from jarvis.config import Settings
from jarvis.voice.session import PendingConfirmation

API_KEY = "test-remote-ws-key"


class _FakeEngine:
    async def load(self) -> None:
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


class _FakeAgent:
    """Just enough surface for _handle_transcript: claim_pending_confirmation()
    plus truthiness (ws_endpoint does `if agent: ...` right after connect)."""

    def __init__(self, pending_ids=()):
        self._pending = set(pending_ids)

    def claim_pending_confirmation(self, conf_id):
        if conf_id not in self._pending:
            return None
        self._pending.discard(conf_id)
        return {"claimed": conf_id}


async def _noop_snapshot(*args, **kwargs):
    return None


async def _noop_models(*args, **kwargs):
    return None


def _install_common_stubs(monkeypatch, agent, calls):
    monkeypatch.setattr(api, "_agent", agent)
    monkeypatch.setattr(api, "_settings", Settings(_env_file=None, jarvis_api_key=API_KEY))
    monkeypatch.setattr(api, "live_data_snapshot", _noop_snapshot)
    monkeypatch.setattr(voice_engine, "RealtimeVoiceEngine", lambda *a, **k: _FakeEngine())
    monkeypatch.setattr(voice_engine, "get_shared_voice_models", _noop_models)
    monkeypatch.setattr(voice_api, "pause_local_voice", lambda: None)
    monkeypatch.setattr(voice_api, "resume_local_voice", lambda: None)

    async def _stub_run_one_response(
        agent_arg, engine, text, lang, *, transport, set_pending_confirmation=None, state=None,
    ):
        # `state` (the per-session VoiceState) is accepted and recorded rather
        # than ignored: transport parity means this /ws session threads the
        # same reducer the CLI does, and a stub that silently dropped it would
        # hide a future regression where the wiring is lost.
        calls.append(("run_one_response", text, state is not None))
        if text == "first utterance" and set_pending_confirmation is not None:
            set_pending_confirmation(PendingConfirmation("conf-1", {"tools": []}))

    async def _stub_resolve_confirmation(agent_arg, engine, pending, text, lang, **kwargs):
        calls.append(("resolve_confirmation", pending.conf_id, text, kwargs.get("pre_claimed")))

    monkeypatch.setattr(voice_api, "run_one_response", _stub_run_one_response)
    monkeypatch.setattr(voice_session, "resolve_confirmation", _stub_resolve_confirmation)

    async def _fake_drive_voice_session(engine, on_transcript, **kwargs):
        r1 = await on_transcript("first utterance", "tr")
        if r1 is not None:
            await r1
        r2 = await on_transcript("second utterance", "tr")
        if r2 is not None:
            await r2
        return "ended"

    monkeypatch.setattr(voice_session, "drive_voice_session", _fake_drive_voice_session)


def _run_remote_audio_session(monkeypatch, agent, calls) -> None:
    _install_common_stubs(monkeypatch, agent, calls)
    client = TestClient(api.app)
    with client.websocket_connect(f"/ws?token={API_KEY}") as ws:
        ws.send_text(json.dumps({"type": "audio_session_start", "sample_rate": 16000}))
        # A "state: idle" broadcast fires right after connect (agent is
        # truthy) before the ack for THIS control message -- skip frames
        # until the one that actually answers audio_session_start.
        for _ in range(10):
            frame = json.loads(ws.receive_text())
            if frame.get("type") == "audio_session_ack":
                break
        else:
            raise AssertionError("never received audio_session_ack")
        # Give the background _run_session() task (started via
        # asyncio.create_task) a chance to run the two fake utterances
        # above to completion before the connection tears down.
        for _ in range(200):
            if len(calls) >= 2:
                break
            time.sleep(0.01)


def test_claim_succeeds_routes_into_resolve_confirmation(monkeypatch):
    """The agent still genuinely holds conf-1 -- the second utterance must
    be treated as this confirmation's yes/no answer, claimed atomically,
    and handed to resolve_confirmation() with the claimed dict."""
    calls: list = []
    agent = _FakeAgent(pending_ids=("conf-1",))

    _run_remote_audio_session(monkeypatch, agent, calls)

    assert calls == [
        ("run_one_response", "first utterance", True),
        ("resolve_confirmation", "conf-1", "second utterance", {"claimed": "conf-1"}),
    ]


def test_externally_resolved_confirmation_is_treated_as_a_new_utterance(monkeypatch):
    """Review remediation (2026-07-23): the agent no longer holds conf-1 --
    it was already resolved through a different transport (Electron's
    /chat/confirm, the local wakeword loop) in the gap between this
    session's local PendingConfirmation flag being armed and the second
    utterance arriving. claim_pending_confirmation() must return None, and
    the second utterance must fall through to run_one_response() as a
    brand-new turn -- never silently consumed as a stale yes/no answer,
    and resolve_confirmation() must never be called at all."""
    calls: list = []
    agent = _FakeAgent(pending_ids=())  # conf-1 already resolved elsewhere

    _run_remote_audio_session(monkeypatch, agent, calls)

    assert calls == [
        ("run_one_response", "first utterance", True),
        ("run_one_response", "second utterance", True),
    ]
