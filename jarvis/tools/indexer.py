"""Document indexer for JARVIS RAG (Faz 6).

Reads files, splits into overlapping chunks, and stores them in the
ChromaDB jarvis_docs collection via Memory.index_document().

Supported formats: .pdf, .md, .txt, .tex, .py, .json, .csv
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.memory import Memory

_CHUNK_SIZE = 800
_CHUNK_OVERLAP = 100

_EXT_TO_DOCTYPE: dict[str, str] = {
    ".md": "markdown",
    ".txt": "text",
    ".tex": "latex",
    ".py": "python",
    ".json": "json",
    ".csv": "csv",
    ".pdf": "pdf",
}


def _split_chunks(text: str) -> list[str]:
    """Split text into ~_CHUNK_SIZE-char chunks at paragraph boundaries."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= _CHUNK_SIZE:
            current = (current + "\n\n" + para).strip()
        else:
            if current:
                chunks.append(current)
            if len(para) > _CHUNK_SIZE:
                # Hard-split oversized paragraph with overlap
                for i in range(0, len(para), _CHUNK_SIZE - _CHUNK_OVERLAP):
                    piece = para[i : i + _CHUNK_SIZE].strip()
                    if piece:
                        chunks.append(piece)
                current = ""
            else:
                current = para
    if current:
        chunks.append(current)
    return chunks


def _read_file_content(path: Path) -> tuple[str, str]:
    """Return (text, doc_type) for a file. Returns ("", "") on failure."""
    suffix = path.suffix.lower()
    doc_type = _EXT_TO_DOCTYPE.get(suffix, "")
    if not doc_type:
        return "", ""

    try:
        if suffix == ".pdf":
            try:
                import pdfplumber

                pages: list[str] = []
                with pdfplumber.open(path) as pdf:
                    for page in pdf.pages:
                        t = page.extract_text()
                        if t:
                            pages.append(t)
                return "\n\n".join(pages), "pdf"
            except ImportError:
                return "", ""
        else:
            text = path.read_text(encoding="utf-8", errors="ignore")
            return text, doc_type
    except Exception:
        return "", ""


def index_file(path: Path, memory: "Memory") -> str:
    """Index a file into jarvis_docs for semantic search. Returns a status string."""
    if not path.exists():
        return f"[ERROR] File not found: {path}"

    text, doc_type = _read_file_content(path)
    if not text.strip():
        supported = ", ".join(sorted(_EXT_TO_DOCTYPE))
        return (
            f"[ERROR] Could not extract text from {path.name}. "
            f"Supported formats: {supported}"
        )

    chunks = _split_chunks(text)
    if not chunks:
        return f"[ERROR] No content chunks produced from {path.name}"

    n = memory.index_document(str(path), chunks, doc_type)
    ef_note = " (Gemini embeddings)" if memory._gemini_ef_active else " (default embeddings)"
    return f"Indexed '{path.name}': {n} chunks stored{ef_note}. Use vault_search to query."
