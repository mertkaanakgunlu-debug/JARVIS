"""jarvis/execution/source_identity.py -- the one place "is this the same
data source" gets decided, shared by the NLU resolver, the completion
contract's classifier and the pre-execution guard.

Numbered comments below refer to GPT_Prompts/Pr_2.md section 9's "NLU ve
identity" list (tests 1-8).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.execution.source_identity import (
    extract_source_ref,
    normalize_source_ref,
    safe_source_label,
    source_matches,
)


# ── 1/2/3/7: extraction from the user's own words ──────────────────────────

def test_1_a_named_csv_produces_a_source_ref():
    ref = extract_source_ref("satis.csv'nin grafiğini çiz")
    assert ref == {
        "type": "file", "raw": "satis.csv", "basename": "satis.csv", "is_explicit_path": False,
    }


@pytest.mark.parametrize("query", [
    '"satis.csv" dosyasının grafiğini çiz',
    "'satis.csv' dosyasının grafiğini çiz",
    "grafiğini çiz: \u201csatis.csv\u201d",
])
def test_2_a_quoted_filename_produces_a_source_ref_without_the_quotes(query):
    ref = extract_source_ref(query)
    assert ref is not None
    assert ref["raw"] == "satis.csv"
    assert ref["basename"] == "satis.csv"
    assert '"' not in ref["raw"] and "'" not in ref["raw"]


def test_3_an_absolute_windows_path_is_preserved():
    query = r"C:\Users\mertk\Desktop\satis.csv dosyasının grafiğini çiz"
    ref = extract_source_ref(query)
    assert ref is not None
    assert ref["raw"] == r"C:\Users\mertk\Desktop\satis.csv"
    assert ref["basename"] == "satis.csv"
    assert ref["path"] != "", "an already-absolute reference needs no workspace to canonicalize"


def test_3b_extraction_never_touches_the_filesystem(monkeypatch):
    """No I/O at extraction time -- Path.resolve() must never be called by
    extract_source_ref itself (only source_matches, given a workspace, may)."""
    def _boom(self, strict=False):
        raise AssertionError("extract_source_ref must not resolve the filesystem")
    monkeypatch.setattr(Path, "resolve", _boom)
    extract_source_ref(r"C:\Users\mertk\Desktop\satis.csv dosyasının grafiğini çiz")
    extract_source_ref("satis.csv'nin grafiğini çiz")


@pytest.mark.parametrize("query", [
    "bu dosyanın grafiğini çiz",
    "verilerden bir grafik hazırla",
    "tablodaki değerleri görselleştir",
    "draw a chart of the data",
    "",
    "   ",
])
def test_7_a_generic_reference_never_fabricates_a_source(query):
    assert extract_source_ref(query) is None


@pytest.mark.parametrize("query", [None, 42, ["satis.csv"]])
def test_a_non_string_query_produces_no_source_ref(query):
    assert extract_source_ref(query) is None


def test_extraction_picks_the_named_file_not_the_surrounding_words():
    """The regression this module exists to prevent: a permissive character
    class that allows spaces would swallow the Turkish words before the
    filename into the match."""
    ref = extract_source_ref("Masaüstündeki satis.csv dosyasının aylık satış grafiğini çiz")
    assert ref["raw"] == "satis.csv"
    assert ref["basename"] == "satis.csv"


# ── 4/5/6: matching rules ───────────────────────────────────────────────────

def test_4_relative_dotslash_and_canonical_absolute_forms_match(tmp_path):
    (tmp_path / "satis.csv").write_text("x", encoding="utf-8")
    absolute = str(tmp_path / "satis.csv")
    required = {"type": "file", "raw": "./satis.csv"}
    actual = {"type": "file", "raw": absolute}
    assert source_matches(required, actual, workspace=tmp_path) is True


def test_4b_bare_relative_and_dotslash_and_absolute_all_share_one_identity(tmp_path):
    absolute = str(tmp_path / "satis.csv")
    variants = ["satis.csv", "./satis.csv", absolute]
    refs = [normalize_source_ref(v, workspace=tmp_path) for v in variants]
    paths = {r["path"] for r in refs}
    assert len(paths) == 1, f"expected one identity, got {paths}"


@pytest.mark.parametrize("required_raw,actual_raw", [
    ("SATIS.CSV", "satis.csv"),
    ("satis.csv", "SaTiS.CsV"),
    # Turkish ş/Ş/ü/Ü have no dotted-I-style ambiguity, unlike İ/I/ı -- plain
    # Unicode casefold (what the task asks for) resolves them cleanly
    # without needing tr_TR-locale-specific casing rules. Deliberately avoids
    # any I/i/İ/ı in this pair -- see test_5b below for why.
    ("ŞUBAT.csv", "şubat.csv"),
])
def test_5_case_and_unicode_equivalents_match(required_raw, actual_raw):
    assert source_matches(
        {"type": "file", "raw": required_raw}, {"type": "file", "raw": actual_raw},
    ) is True


def test_5b_turkish_dotted_i_is_a_known_plain_casefold_limit():
    """Documented, not silently swallowed: plain Unicode casefold (no
    tr_TR-locale rules) does NOT equate İ and i across case -- 'İZİN.csv'
    casefolds to 'i̇zin.csv' (dotted i + combining mark), not 'izin.csv'. The
    task asks for "Unicode normalizasyonu ve casefold", not a Turkish-locale
    casing table, so this is accepted scope, not a bug -- pinned here so a
    future change either fixes it on purpose or this test explains why not."""
    assert source_matches(
        {"type": "file", "raw": "İZİN.csv"}, {"type": "file", "raw": "izin.csv"},
    ) is False


def test_6_different_basenames_never_match():
    assert source_matches(
        {"type": "file", "raw": "olmayan.csv"}, {"type": "file", "raw": "satis.csv"},
    ) is False


def test_6b_different_basenames_never_match_even_with_a_workspace(tmp_path):
    assert source_matches(
        {"type": "file", "raw": "olmayan.csv"}, {"type": "file", "raw": "satis.csv"},
        workspace=tmp_path,
    ) is False


def test_bare_basename_request_matches_any_call_naming_that_basename():
    """'Yalnız basename verilmişse basename karşılaştırılmalı' -- no workspace
    needed when neither side offers a resolvable path."""
    assert source_matches(
        {"type": "file", "raw": "satis.csv"}, {"type": "file", "raw": "satis.csv"},
    ) is True


def test_a_bare_basename_request_matches_a_call_that_used_a_subdirectory(tmp_path):
    """Regression: a live smoke run (qwen3:8b) asked for a bare "satis.csv"
    ('Masaüstündeki satis.csv') and the model -- correctly -- called
    plot_data(path="Desktop/satis.csv"). A version of source_matches that
    compared full canonical paths whenever a workspace happened to be
    available rejected the model's OWN correct file as a "mismatch", which
    is precisely the false-positive block the task exists to avoid, not
    produce. The bare request must be satisfied by basename identity alone,
    regardless of which directory the call resolves to."""
    (tmp_path / "Desktop").mkdir()
    (tmp_path / "Desktop" / "satis.csv").write_text("x", encoding="utf-8")
    required = {"type": "file", "raw": "satis.csv"}
    actual = {"type": "file", "raw": "Desktop/satis.csv"}
    assert source_matches(required, actual, workspace=tmp_path) is True


def test_an_explicit_path_request_still_rejects_a_same_named_file_elsewhere(tmp_path):
    """The mirror case, so the fix above is not a blanket 'basenames always
    win': when the REQUEST itself names a directory, a same-basename file in
    a DIFFERENT one is correctly still a mismatch."""
    (tmp_path / "Desktop").mkdir()
    (tmp_path / "Desktop" / "satis.csv").write_text("x", encoding="utf-8")
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Downloads" / "satis.csv").write_text("y", encoding="utf-8")
    required = {"type": "file", "raw": "Desktop/satis.csv"}
    actual = {"type": "file", "raw": "Downloads/satis.csv"}
    assert source_matches(required, actual, workspace=tmp_path) is False


def test_is_explicit_path_distinguishes_a_bare_name_from_a_directory_reference():
    assert normalize_source_ref("satis.csv")["is_explicit_path"] is False
    assert normalize_source_ref("./satis.csv")["is_explicit_path"] is True
    assert normalize_source_ref("Desktop/satis.csv")["is_explicit_path"] is True
    assert normalize_source_ref(r"C:\Users\mertk\satis.csv")["is_explicit_path"] is True


# ── 8: explicit file vs inline data ─────────────────────────────────────────

def test_8_a_named_file_request_never_matches_inline_data():
    assert source_matches(
        {"type": "file", "raw": "satis.csv"}, {"type": "inline"},
    ) is False


def test_8b_inline_request_never_matches_a_file_call():
    """Symmetry: this build never emits an inline-typed requirement today,
    but the comparison itself must not silently accept the reverse pairing."""
    assert source_matches({"type": "inline"}, {"type": "file", "raw": "satis.csv"}) is False


# ── never a silent match ────────────────────────────────────────────────────

@pytest.mark.parametrize("required,actual", [
    (None, {"type": "file", "raw": "satis.csv"}),
    ({"type": "file", "raw": "satis.csv"}, None),
    (None, None),
    ({}, {"type": "file", "raw": "satis.csv"}),
    ({"type": "file", "raw": ""}, {"type": "file", "raw": "satis.csv"}),
    ({"type": "unknown"}, {"type": "unknown"}),
])
def test_missing_or_unusable_evidence_never_silently_matches(required, actual):
    assert source_matches(required, actual) is False


# ── normalize_source_ref shape and idempotency ──────────────────────────────

def test_normalize_is_idempotent():
    once = normalize_source_ref("./satis.csv")
    twice = normalize_source_ref(once)
    assert once == twice


def test_normalize_of_empty_is_unknown():
    ref = normalize_source_ref("")
    assert ref["type"] == "unknown"
    assert ref["basename"] == ""
    assert "path" not in ref


def test_normalize_strips_surrounding_quotes_directly():
    ref = normalize_source_ref('"satis.csv"')
    assert ref["raw"] == "satis.csv"


def test_normalize_inline_marker_has_no_basename():
    ref = normalize_source_ref({"type": "inline"})
    assert ref == {"type": "inline", "raw": "", "basename": ""}


def test_normalize_without_workspace_and_relative_raw_has_no_path_key():
    ref = normalize_source_ref("satis.csv")
    assert ref["type"] == "file"
    assert ref["basename"] == "satis.csv"
    assert "path" not in ref


# ── safe_source_label: telemetry must never leak an absolute path ──────────

@pytest.mark.parametrize("value,expected", [
    ({"type": "file", "raw": "satis.csv", "basename": "satis.csv"}, "satis.csv"),
    ({"type": "inline"}, "inline data"),
    ({"type": "unknown"}, "unknown source"),
    ("satis.csv", "satis.csv"),
])
def test_safe_label_matches_expected(value, expected):
    assert safe_source_label(value) == expected


def test_safe_label_never_contains_a_directory_component():
    label = safe_source_label(r"C:\Users\mertk\Desktop\satis.csv")
    assert label == "satis.csv"
    assert "\\" not in label and "/" not in label
    assert "Users" not in label and "mertk" not in label


def test_safe_label_preserves_original_case_unlike_the_comparison_basename():
    """basename (used for matching) is casefolded; the display label is not
    -- a telemetry line should read like the file the user actually typed."""
    ref = normalize_source_ref("Satis_Ocak.CSV")
    assert ref["basename"] == "satis_ocak.csv"
    assert safe_source_label(ref) == "Satis_Ocak.CSV"
