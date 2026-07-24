"""jarvis/voice/text.py::is_exit_phrase -- the "gülen" ASR-variant fix.

Owner-confirmed live (2026-07-24): saying "güle güle" produced the Whisper
transcript "Gülen." and the session did NOT exit, because the 0.82 fuzzy
threshold misses it (difflib ratio "gülen" vs "güle güle" ≈ 0.57). A controlled
`fullmatch` regex now catches the dropped-space / trailing-n variants without
lowering the global threshold and without swallowing "Gülen'i ara" etc.

Includes one orchestration test proving the real drive_voice_session() actually
ends the session on that transcript, not just that the helper returns True.
"""
from __future__ import annotations

import pytest

from jarvis.voice.text import is_exit_phrase


@pytest.mark.parametrize("text", [
    "Gülen.",          # the exact live mis-transcription
    "gülen",
    "Gülen gülen",
    "güle gülen",
    "güle güle",       # the real phrase, still works
    "gule gule",       # no-diacritic ASR
    "GÜLE GÜLE",
    "hoşça kal",       # pre-existing exits unaffected
    "goodbye",
])
def test_exit_variants_are_recognized(text):
    assert is_exit_phrase(text) is True


@pytest.mark.parametrize("text", [
    "Güle",                        # bare "güle" must NOT exit (too broad a false-positive)
    "Gülen'i ara",                 # a command that merely starts with the token
    "Gülen hakkında bilgi ver",
    "gülen yüzler güzel",
    "tamam devam et",
    "yarın toplantı ekle",
])
def test_non_exit_phrases_do_not_close_session(text):
    assert is_exit_phrase(text) is False


# ── orchestration: the transcript actually ends the real session loop ─────────

class _FakeEngine:
    """Minimal engine for drive_voice_session: just an events() async iterator."""
    def __init__(self, events):
        self._events = events

    async def events(self):
        for e in self._events:
            yield e


@pytest.mark.asyncio
async def test_gulen_transcript_ends_drive_voice_session():
    """FinalTranscript("Gülen.") → the transcript handler returns STOP_SESSION →
    drive_voice_session returns "exit" (the real loop, not just is_exit_phrase)."""
    from jarvis.voice.events import FinalTranscript
    from jarvis.voice.session import STOP_SESSION, drive_voice_session

    engine = _FakeEngine([FinalTranscript(text="Gülen.", lang="tr")])

    async def on_transcript(text, lang):
        # Mirrors the real cli/voice_api exit check.
        return STOP_SESSION if is_exit_phrase(text) else None

    outcome = await drive_voice_session(engine, on_transcript)
    assert outcome == "exit"
