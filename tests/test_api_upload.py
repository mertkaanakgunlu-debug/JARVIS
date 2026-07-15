"""jarvis/api.py's POST /chat/upload -- size cap + cleanup.

Covers BUG-upload: the endpoint used to read an unbounded file.read() with
no size cap, and never deleted the saved copy under data/uploads/ afterward
(both PDF and the Excel/CSV/Word "other files" branch), so every upload
accumulated on disk forever.

Uses starlette.testclient.TestClient against the real FastAPI app (same
tooling precedent as Faz 3's remote-audio protocol tests, per HANDOFF.md) --
no `with TestClient(app) as client:` block, so ASGI lifespan startup (which
would try to start a real monitor/voice stack) never runs; jarvis.api's
module-level _agent/_settings globals are monkeypatched directly instead of
going through init_agent(), keeping this fully offline and fast.
"""
from __future__ import annotations

import jarvis.api as api
from starlette.testclient import TestClient


class _StubAgent:
    """Just enough of JarvisAgent's surface for /chat/upload's non-image,
    non-PDF branch to complete a full request/response cycle."""

    async def chat_stream(self, *args, **kwargs):
        yield "ok"


def _client(monkeypatch, *, agent=None) -> TestClient:
    # jarvis.api._agent/_settings are plain module globals populated by
    # init_agent() in real use -- monkeypatch them directly so this test
    # never constructs a real JarvisAgent (heavy, and per MEMORY.md's
    # isolate-test-data-paths lesson, has real side effects) or touches auth
    # (_settings=None -> jarvis_api_key resolves to "" -> auth disabled).
    monkeypatch.setattr(api, "_agent", agent if agent is not None else object())
    monkeypatch.setattr(api, "_settings", None)
    return TestClient(api.app)


def test_oversized_upload_is_rejected_with_413(monkeypatch, isolated_cwd):
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 1024)
    client = _client(monkeypatch)

    oversized = b"x" * 2048
    resp = client.post(
        "/chat/upload",
        files={"file": ("test.txt", oversized, "text/plain")},
        data={"query": "", "language": ""},
    )

    assert resp.status_code == 413
    # Must not have written anything to disk before rejecting.
    assert not (isolated_cwd / "data" / "uploads").exists() or not list(
        (isolated_cwd / "data" / "uploads").glob("*")
    )


def test_upload_within_cap_is_accepted(monkeypatch, isolated_cwd):
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 1024)
    client = _client(monkeypatch, agent=_StubAgent())

    resp = client.post(
        "/chat/upload",
        files={"file": ("test.csv", b"a,b,c\n1,2,3", "text/csv")},
        data={"query": "summarize", "language": "en"},
    )

    assert resp.status_code == 200


def test_other_files_branch_cleans_up_after_stream_completes(monkeypatch, isolated_cwd):
    """Excel/CSV/Word go through the tool-hint branch, where the file must
    stay on disk until agent.chat_stream() (which may call file_read/
    excel_read/csv_read on it) finishes iterating -- then it must be gone."""
    client = _client(monkeypatch, agent=_StubAgent())

    resp = client.post(
        "/chat/upload",
        files={"file": ("data.csv", b"a,b,c\n1,2,3", "text/csv")},
        data={"query": "", "language": ""},
    )
    assert resp.status_code == 200

    upload_dir = isolated_cwd / "data" / "uploads"
    remaining = list(upload_dir.glob("*")) if upload_dir.exists() else []
    assert remaining == [], f"upload was not cleaned up: {remaining}"


def test_pdf_branch_cleans_up_immediately_after_extraction(monkeypatch, isolated_cwd):
    """PDFs are extracted synchronously before the SSE stream even starts, so
    the raw upload can (and must) be deleted right after extraction, not
    deferred to the stream's end."""
    monkeypatch.setattr(
        "jarvis.tools.pdf.read_pdf_multimodal",
        lambda path, *a, **k: ("fake extracted markdown", []),
    )
    client = _client(monkeypatch, agent=_StubAgent())

    resp = client.post(
        "/chat/upload",
        files={"file": ("doc.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"query": "", "language": ""},
    )
    assert resp.status_code == 200

    upload_dir = isolated_cwd / "data" / "uploads"
    remaining = list(upload_dir.glob("*")) if upload_dir.exists() else []
    assert remaining == [], f"PDF upload was not cleaned up: {remaining}"


def test_image_upload_never_touches_disk(monkeypatch, isolated_cwd):
    """Images are sent as multimodal bytes directly -- no data/uploads/ entry
    should be created at all, so there's nothing to clean up."""
    client = _client(monkeypatch, agent=_StubAgent())

    resp = client.post(
        "/chat/upload",
        files={"file": ("pic.png", b"\x89PNG fake bytes", "image/png")},
        data={"query": "", "language": ""},
    )
    assert resp.status_code == 200
    assert not (isolated_cwd / "data" / "uploads").exists()
