"""Gemini Vision-based reading for PDFs and images.

Use this when the file contains charts, maps, seismic sections, contour maps,
exam schedules, screenshots, or any visual content that text extractors cannot
meaningfully parse.

Supported formats: PDF, PNG, JPG/JPEG, WEBP, GIF, BMP.
The file is base64-encoded and sent directly to Gemini's multimodal endpoint.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.config import Settings

MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MB

_IMAGE_MIME = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif":  "image/gif",
    ".bmp":  "image/bmp",
}


def _extract_pages(pdf_path: Path, pages: str | None) -> bytes:
    """Return full PDF bytes or a page-subset as a new PDF.

    pages format: "1", "1,3,5", "2-4", or None (entire file).
    """
    if pages is None:
        return pdf_path.read_bytes()

    try:
        from pypdf import PdfWriter, PdfReader
    except ImportError:
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
    """Send a PDF or image to Gemini Vision and answer a question about it.

    Args:
        path:     Path to the file (PDF, PNG, JPG, WEBP, GIF, BMP).
        question: What to ask about the visual content.
        settings: App settings (Vertex ADC or AI Studio key).
        pages:    Optional page subset for PDFs — "1", "2-4", "1,3,5".
                  Ignored for image files.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_core.messages import HumanMessage

    p = Path(path)
    if not p.exists():
        return f"[ERROR] File not found: {p}"

    ext = p.suffix.lower()
    is_image = ext in _IMAGE_MIME
    is_pdf   = ext == ".pdf"

    if not is_pdf and not is_image:
        return (
            f"[ERROR] Unsupported file type '{ext}'. "
            f"Supported: PDF, {', '.join(_IMAGE_MIME)}"
        )

    if is_pdf:
        file_bytes = _extract_pages(p, pages)
        mime_type  = "application/pdf"
    else:
        file_bytes = p.read_bytes()
        mime_type  = _IMAGE_MIME[ext]
        pages      = None  # no page concept for images

    if len(file_bytes) > MAX_FILE_BYTES:
        size_mb = len(file_bytes) / 1_048_576
        return (
            f"[ERROR] File too large for Vision API: {size_mb:.1f} MB "
            f"(limit {MAX_FILE_BYTES // 1_048_576} MB)."
        )

    file_b64 = base64.b64encode(file_bytes).decode()

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
            "data": file_b64,
            "mime_type": mime_type,
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
            content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
        return f"[Gemini Vision — {p.name}{page_note}]\n\n{content}"
    except Exception as exc:
        return f"[ERROR] Gemini Vision failed: {exc}"
