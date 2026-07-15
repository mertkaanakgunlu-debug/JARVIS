"""jarvis/cli.py -- natural-language model-switch keyword detection.

Covers BUG-modelswitch: an unanchored `"pro" in text` substring match hijacked
ordinary messages containing "pro" as a substring of an unrelated word
("proje", "problem", "program", "profesyonel", ...) into a silent model
switch that swallowed the user's actual message (cli.py's REPL `continue`s
after a detected switch, never reaching the real chat turn).
"""
from __future__ import annotations

from jarvis import cli


class TestNoFalsePositives:
    """None of these should be detected as a model-switch request -- each
    contains "pro" only as a substring of an unrelated word."""

    def test_turkish_project_sentence(self):
        assert cli._detect_model_switch("Yarin icin bir proje plani hazirla") is None

    def test_turkish_problem_sentence(self):
        assert cli._detect_model_switch("Bu bir problem, cozelim") is None

    def test_turkish_program_sentence(self):
        assert cli._detect_model_switch("programi baslat") is None

    def test_turkish_professional_sentence(self):
        assert cli._detect_model_switch("profesyonel bir ozet yaz") is None

    def test_english_approve_sentence(self):
        assert cli._detect_model_switch("please approve this request") is None

    def test_english_provide_sentence(self):
        assert cli._detect_model_switch("can you provide more detail") is None


class TestStillDetectsRealSwitches:
    """Legitimate switch phrasing, including Turkish suffix forms, must still
    resolve to the right model id -- the fix must not just refuse to match."""

    def test_turkish_apostrophe_suffix(self):
        assert cli._detect_model_switch("Gemini Pro'ya gec") == "gemini-2.5-pro"

    def test_turkish_command_form(self):
        assert cli._detect_model_switch("modeli pro yap") == "gemini-2.5-pro"

    def test_english_switch_to(self):
        assert cli._detect_model_switch("switch to flash") == "gemini-2.5-flash"

    def test_local_model_keyword(self):
        assert cli._detect_model_switch("yerel modele gec") is not None

    def test_bare_pro_as_its_own_word(self):
        assert cli._resolve_model_keyword("pro") == "gemini-2.5-pro"


def test_resolve_model_keyword_word_boundary_directly():
    """Unit-level check on the helper the substring bug actually lived in."""
    assert cli._resolve_model_keyword("proje") is None
    assert cli._resolve_model_keyword("pro") == "gemini-2.5-pro"
    assert cli._resolve_model_keyword("gemini pro") == "gemini-2.5-pro"
