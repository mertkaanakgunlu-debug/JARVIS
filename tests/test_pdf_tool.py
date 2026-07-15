"""jarvis/tools/pdf.py -- PDF extraction cache.

Covers BUG-pdf: the cache key used to be derived from the resolved PATH
string, relying entirely on mtime comparison to detect a changed file at the
same path. mtime is not reliable (restoring a backup, `cp -p`, extracting an
archive, or a download that preserves the source's Last-Modified header can
leave a *newer* file with an *older* mtime than a stale cached conversion) --
content-hash keying makes a changed file get a different key regardless.
"""
from __future__ import annotations

import os
import time

from jarvis.tools import pdf


def test_content_key_changes_with_content(tmp_path):
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"version one content")
    key1 = pdf._content_key(p)

    p.write_bytes(b"version two, totally different content")
    key2 = pdf._content_key(p)

    assert key1 != key2


def test_content_key_survives_an_older_mtime_than_a_stale_cache(tmp_path):
    """The exact BUG-pdf scenario: new content written to the same path, but
    with an mtime OLDER than a previous run would have produced -- mimics a
    restored backup or an archive extraction preserving original timestamps."""
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"original content")
    key_original = pdf._content_key(p)

    p.write_bytes(b"replaced content, but with an old timestamp")
    old_time = time.time() - 100_000
    os.utime(p, (old_time, old_time))

    key_replaced = pdf._content_key(p)
    assert key_replaced != key_original, "an mtime-based cache would have missed this change"


def test_same_content_same_key(tmp_path):
    p1 = tmp_path / "a.pdf"
    p2 = tmp_path / "b.pdf"
    p1.write_bytes(b"identical bytes")
    p2.write_bytes(b"identical bytes")
    assert pdf._content_key(p1) == pdf._content_key(p2)


def test_cache_hit_returns_previously_written_markdown(tmp_path):
    """End-to-end through the public read_pdf_multimodal() cache-hit path --
    doesn't need real PDF parsing since it never gets that far on a hit."""
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"some pdf bytes")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    key = pdf._content_key(p)
    cached_md = pdf._cache_path(p, cache_dir, key)
    cached_md.write_text("cached markdown content", encoding="utf-8")

    text, images = pdf.read_pdf_multimodal(p, cache_dir=cache_dir)
    assert "cached markdown content" in text
    assert "cache hit" in text
    assert images == []


def test_replacing_the_file_invalidates_the_old_cache_entry(tmp_path):
    """Full regression through the public API: write a cache entry keyed to
    v1's content, replace the file with v2 content (older mtime, simulating
    the bug scenario), confirm the v1 cache entry is NOT served."""
    p = tmp_path / "doc.pdf"
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    p.write_bytes(b"v1 content")
    key_v1 = pdf._content_key(p)
    pdf._cache_path(p, cache_dir, key_v1).write_text("STALE v1 markdown", encoding="utf-8")

    p.write_bytes(b"v2 content, completely different")
    old_time = time.time() - 100_000
    os.utime(p, (old_time, old_time))

    # No cache entry exists yet for v2's key, and marker-pdf/pdfplumber aren't
    # importable in this test env -- read_pdf_multimodal falls through to the
    # pdfplumber path and fails gracefully. The important assertion is what
    # it does NOT do: silently return the stale v1 markdown.
    text, _ = pdf.read_pdf_multimodal(p, cache_dir=cache_dir)
    assert "STALE v1 markdown" not in text
