"""Gemini Vision-based PDF reading for visual content.

Use this when the PDF contains charts, maps, seismic sections, contour maps,
or any image-heavy pages that marker-pdf / pdfplumber cannot meaningfully extract.

The full PDF (or a page subset) is base64-encoded and sent directly to Gemini's
multimodal endpoint, which can 'see' the visual content.

Limitations:
- Sends the whole file to the API (watch file size for very large PDFs)
- Only works with Vertex AI or AI Studio (needs GEMINI_API_KEY or ADC)
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.config import Settings

MAX_PDF_BYTES = 20 * 1024 * 1024  # 20 MB safety cap before sending to API


def _extract_pages(pdf_path: Path, pages: str | None) -> bytes:
    """Return the full PDF bytes, or a subset of pages as a new PDF.

    pages format: "1", "1,3,5", "2-4", or None (entire file).
    Requires pypdf (already pulled in transitively by pdfplumber).
    """
    if pages is None:
        return pdf_path.read_bytes()

    try:
        from pypdf import PdfWriter, PdfReader
    except ImportError:
        # pypdf not available — return full file
        return pdf_path.read_bytes()

    reader = PdfReader(str(pdf_path))
    total = len(reader.pages)
    indices: set[int] = set()

    for part in pages.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            indices.update(range(int(lo) - 1, min(int(hi), total)))
        else:
            idx = int(part) - 1
            if 0 <= idx < total:
                indices.add(idx)

    writer = PdfWriter()
    for i in sorted(indices):
        writer.add_page(reader.pages[i])

    import io
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def read_pdf_vision(
    path: str | Path,
    question: str,
    settings: "Settings",
    pages: str | None = None,
) -> str:
    """Send a PDF (or specific pages) to Gemini Vision and answer a question.

    Args:
        path:     Path to the PDF file.
        question: What to ask about the visual content.
        settings: App settings (Vertex ADC or AI Studio key).
        pages:    Optional page subset — "1", "2-4", "1,3,5" (1-indexed).
                  None means send the entire PDF.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_core.messages import HumanMessage

    p = Path(path)
    if not p.exists():
        return f"[ERROR] File not found: {p}"
    if p.suffix.lower() != ".pdf":
        return f"[ERROR] Not a PDF: {p}"

    pdf_bytes = _extract_pages(p, pages)
    if len(pdf_bytes) > MAX_PDF_BYTES:
        size_mb = len(pdf_bytes) / 1_048_576
        return (
            f"[ERROR] PDF too large for Vision API: {size_mb:.1f} MB "
            f"(limit {MAX_PDF_BYTES // 1_048_576} MB). "
            "Use pages= to send a subset, e.g. pages='1-5'."
        )

    pdf_b64 = base64.b64encode(pdf_bytes).decode()

    if settings.use_vertex:
        llm = ChatGoogleGenerativeAI(
            model=settings.vertex_model_fast,
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.google_cloud_region,
            max_output_tokens=2048,
        )
    else:
        llm = ChatGoogleGenerativeAI(
            model=settings.effective_cloud_model,
            google_api_key=settings.gemini_api_key or None,
            max_output_tokens=2048,
        )

    page_note = f" (pages {pages})" if pages else ""
    message = HumanMessage(content=[
        {
            "type": "media",
            "data": pdf_b64,
            "mime_type": "application/pdf",
        },
        {
            "type": "text",
            "text": question,
        },
    ])

    try:
        response = llm.invoke([message])
        content = response.content
        if isinstance(content, list):
            content = "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
        return f"[Gemini Vision — {p.name}{page_note}]\n\n{content}"
    except Exception as exc:
        return f"[ERROR] Gemini Vision failed: {exc}"
