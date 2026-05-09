"""PDF text extraction using pdfplumber."""

from __future__ import annotations

from pathlib import Path


def read_pdf(path: str | Path) -> str:
    """Extract text from a PDF file. Returns page-labelled markdown."""
    try:
        import pdfplumber
    except ImportError:
        return "[ERROR] pdfplumber not installed. Run: pip install pdfplumber"

    p = Path(path)
    if not p.exists():
        return f"[ERROR] File not found: {p}"
    if p.suffix.lower() != ".pdf":
        return f"[ERROR] Not a PDF file: {p}"

    pages: list[str] = []
    try:
        with pdfplumber.open(p) as pdf:
            total = len(pdf.pages)
            for i, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ""
                pages.append(f"## Page {i}/{total}\n{text.strip()}")
    except Exception as e:
        return f"[ERROR] Could not read PDF: {e}"

    if not any(p for p in pages):
        return (
            f"[WARNING] PDF has {len(pages)} pages but no extractable text. "
            "The file may be scanned/image-only and requires OCR."
        )

    return "\n\n".join(pages)
