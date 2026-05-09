"""PDF extraction — marker-pdf (ML, cached) with pdfplumber fallback.

Cache strategy:
  {cache_dir}/{stem}_{sha256[:8]}.md
  Re-converts only if PDF is newer than the cached .md.

First run with marker-pdf: downloads ~2-3 GB of models to ~/.cache/marker
(one-time cost). Model objects are kept in memory across calls in a session.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

log = logging.getLogger(__name__)

MAX_CHARS = 120_000  # ~30k tokens — truncate beyond this to protect context

# ── Lazy marker state ──────────────────────────────────────────────────────────

_MARKER_MODELS = None       # cached model dict, loaded once per session
_MARKER_AVAILABLE: bool | None = None  # None = not yet probed


def _marker_available() -> bool:
    global _MARKER_AVAILABLE
    if _MARKER_AVAILABLE is None:
        try:
            import marker  # noqa: F401
            _MARKER_AVAILABLE = True
        except ImportError:
            _MARKER_AVAILABLE = False
    return _MARKER_AVAILABLE


def _get_models():
    """Lazy-load marker models. Heavy on first call (~10-30s from disk)."""
    global _MARKER_MODELS
    if _MARKER_MODELS is None:
        log.info("Loading marker-pdf models (first call — may take 10-30s)...")
        from marker.models import create_model_dict
        _MARKER_MODELS = create_model_dict()
        log.info("marker-pdf models loaded.")
    return _MARKER_MODELS


# ── Cache helpers ──────────────────────────────────────────────────────────────

def _cache_path(pdf_path: Path, cache_dir: Path) -> Path:
    key = hashlib.sha256(str(pdf_path.resolve()).encode()).hexdigest()[:8]
    return cache_dir / f"{pdf_path.stem}_{key}.md"


def _is_cache_fresh(pdf_path: Path, cached: Path) -> bool:
    return cached.exists() and cached.stat().st_mtime >= pdf_path.stat().st_mtime


# ── Conversion backends ────────────────────────────────────────────────────────

def _convert_marker(pdf_path: Path, cache_dir: Path) -> str:
    """Convert PDF to markdown via marker-pdf and write to cache."""
    from marker.converters.pdf import PdfConverter

    models = _get_models()
    converter = PdfConverter(artifact_dict=models)
    rendered = converter(str(pdf_path))

    # v1.x exposes .markdown directly; older builds use text_from_rendered
    if hasattr(rendered, "markdown"):
        md = rendered.markdown
    else:
        from marker.output import text_from_rendered
        md = text_from_rendered(rendered)

    cache_dir.mkdir(parents=True, exist_ok=True)
    _cache_path(pdf_path, cache_dir).write_text(md, encoding="utf-8")
    return md


def _read_pdfplumber(pdf_path: Path) -> str:
    """Fallback: raw text extraction via pdfplumber."""
    import pdfplumber

    pages: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            pages.append(f"## Page {i}/{total}\n{text.strip()}")

    joined = "\n\n".join(pages)
    if not joined.strip():
        return (
            f"[WARNING] {pdf_path.name} — {len(pages)} pages but no extractable text. "
            "The file is likely image-only. Install marker-pdf for proper extraction: "
            "`pip install marker-pdf`"
        )
    return joined


# ── Public API ─────────────────────────────────────────────────────────────────

def read_pdf(path: str | Path, cache_dir: Path | None = None) -> str:
    """Read a PDF and return its content as markdown.

    Uses marker-pdf (ML-based, structure-preserving) when installed.
    Falls back to pdfplumber (text-only) otherwise.
    Results are cached to {cache_dir} and reused on subsequent calls.
    """
    p = Path(path)
    if not p.exists():
        return f"[ERROR] File not found: {p}"
    if p.suffix.lower() != ".pdf":
        return f"[ERROR] Not a PDF: {p}"

    if cache_dir is None:
        cache_dir = Path("data") / "pdf_cache"

    cached = _cache_path(p, cache_dir)

    # ── Cache hit ──────────────────────────────────────────────────────────────
    if _is_cache_fresh(p, cached):
        content = cached.read_text(encoding="utf-8")
        header = f"[PDF→MD cache hit: {p.name}]\n\n"
        if len(content) > MAX_CHARS:
            return header + content[:MAX_CHARS] + f"\n\n[TRUNCATED — {len(content):,} chars total, showing first {MAX_CHARS:,}]"
        return header + content

    # ── marker-pdf conversion ──────────────────────────────────────────────────
    if _marker_available():
        try:
            log.info(f"Converting {p.name} with marker-pdf...")
            md = _convert_marker(p, cache_dir)
            header = f"[marker-pdf conversion: {p.name}]\n\n"
            if len(md) > MAX_CHARS:
                return header + md[:MAX_CHARS] + f"\n\n[TRUNCATED — {len(md):,} chars total, showing first {MAX_CHARS:,}]"
            return header + md
        except Exception as exc:
            log.warning(f"marker-pdf failed ({exc}), falling back to pdfplumber.")

    # ── pdfplumber fallback ────────────────────────────────────────────────────
    try:
        text = _read_pdfplumber(p)
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + f"\n\n[TRUNCATED — showing first {MAX_CHARS:,} chars]"
        return text
    except Exception as exc:
        return f"[ERROR] Could not read PDF: {exc}"
