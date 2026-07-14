"""TTS text sanitizing + exit-phrase detection — pure text logic, no audio/model deps."""

from __future__ import annotations

import difflib
import re
import unicodedata


def sanitize_for_tts(text: str) -> str:
    """Strip markdown and non-speakable characters before sending to TTS.

    TTS engines read asterisks, hash signs, and emoji descriptions verbatim,
    producing robot-like output. This function converts to clean spoken prose.
    """
    # Bold / italic
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\*(.+?)\*',     r'\1', text, flags=re.DOTALL)
    text = re.sub(r'__(.+?)__',     r'\1', text, flags=re.DOTALL)
    text = re.sub(r'_(.+?)_',       r'\1', text, flags=re.DOTALL)
    # Headers
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Inline code
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # Markdown links → anchor text only
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Bullet / numbered list markers
    text = re.sub(r'^\s*[-•*]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    # Horizontal rules
    text = re.sub(r'^[-=_]{3,}$', '', text, flags=re.MULTILINE)
    # Emojis (Unicode So + Sm + supplemental pictographs)
    text = ''.join(
        c for c in text
        if not (unicodedata.category(c) in ('So', 'Sm') or ord(c) > 0x1F000)
    )
    # Ellipsis → natural pause comma
    text = text.replace('…', ',').replace('...', ',')
    # Strip any remaining bare markdown symbols (* # _ ~)
    text = re.sub(r'[*#_~]+', '', text)
    # Paragraph breaks → sentence boundary
    text = re.sub(r'\n{2,}', '. ', text)
    text = text.replace('\n', ' ')
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


# ── Exit phrase detection ─────────────────────────────────────────────────────

_EXIT_EN = {
    "exit", "quit", "goodbye", "bye", "bye bye", "see you", "see ya",
    "see you later", "that's all", "shut down", "stop",
}
_EXIT_TR = {
    "güle güle", "gule gule", "hoşçakal", "hoscakal", "hoşça kal",
    "hosca kal", "görüşürüz", "gorusuruz", "sonra görüşürüz",
    "sonra gorusuruz", "çıkış", "cikis", "kapat", "yeter", "tamam kapat",
}
_EXIT_PHRASES = _EXIT_EN | _EXIT_TR


def _normalize(s: str) -> str:
    return s.strip().lower().rstrip(".,!?")


def is_exit_phrase(text: str) -> bool:
    """Return True when the transcribed text is a farewell/exit utterance.

    Uses fuzzy matching (0.82 similarity threshold) to catch STT variants like
    "gulei gulei" → "gule gule" or "goodbyes" → "goodbye".
    """
    norm = _normalize(text)
    for phrase in _EXIT_PHRASES:
        if norm == phrase or norm.startswith(phrase + " "):
            return True
        # Fuzzy match: STT often mis-transcribes non-English exit phrases.
        if difflib.SequenceMatcher(None, norm, phrase).ratio() >= 0.82:
            return True
    return False
