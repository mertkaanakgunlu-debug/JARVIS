"""PDF extraction — marker-pdf (ML, cached) with pdfplumber fallback.

Cache strategy:
  {cache_dir}/{stem}_{sha256[:8]}.md          — markdown text
  {cache_dir}/{stem}_{sha256[:8]}_images/     — extracted PNG figures (marker-pdf only)
  Re-converts only if PDF is newer than the cached .md.

First run with marker-pdf: downloads ~2-3 GB of models to ~/.cache/marker
(one-time cost). Model objects are kept in memory across calls in a session.
"""

from __future__ import annotations

import hashlib
import io
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

def _content_key(pdf_path: Path) -> str:
    """Content-derived cache key.

    Keying on the resolved path string (the original approach) relies on file
    mtime to detect a changed file at the same path — but mtime is not reliable
    (restoring a backup, `cp -p`, extracting an archive, or a browser download
    that preserves the source's Last-Modified header can all leave a *newer*
    file with an *older* mtime than the stale cached conversion, so the cache
    would silently keep serving the wrong PDF's content forever). Hashing the
    actual bytes makes a changed file get a different key regardless of mtime.
    """
    h = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


def _cache_path(pdf_path: Path, cache_dir: Path, content_key: str) -> Path:
    return cache_dir / f"{pdf_path.stem}_{content_key}.md"


def _img_cache_dir(pdf_path: Path, cache_dir: Path, content_key: str) -> Path:
    return cache_dir / f"{pdf_path.stem}_{content_key}_images"


# ── Conversion backends ────────────────────────────────────────────────────────

def _convert_marker(pdf_path: Path, cache_dir: Path, content_key: str) -> tuple[str, list[bytes]]:
    """Convert PDF to markdown + figure PNGs via marker-pdf, write to cache.

    Returns (markdown_text, [png_bytes, ...]).
    """
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
    _cache_path(pdf_path, cache_dir, content_key).write_text(md, encoding="utf-8")

    # Extract and cache figure images
    img_bytes_list: list[bytes] = []
    raw_images = getattr(rendered, "images", None) or {}
    if raw_images:
        idir = _img_cache_dir(pdf_path, cache_dir, content_key)
        idir.mkdir(parents=True, exist_ok=True)
        for i, (name, pil_img) in enumerate(raw_images.items()):
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            data = buf.getvalue()
            img_bytes_list.append(data)
            safe_name = name.replace("/", "_").replace("\\", "_")
            (idir / f"{i:03d}_{safe_name}.png").write_bytes(data)

    return md, img_bytes_list


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
    """Read a PDF and return its content as markdown (text-only, for tool calls).

    Uses marker-pdf (ML-based, structure-preserving) when installed.
    Falls back to pdfplumber (text-only) otherwise.
    Results are cached to {cache_dir} and reused on subsequent calls.
    """
    md, _ = read_pdf_multimodal(path, cache_dir)
    return md


def read_pdf_multimodal(
    path: str | Path,
    cache_dir: Path | None = None,
    max_images: int = 20,
) -> tuple[str, list[bytes]]:
    """Extract a PDF for multimodal use.

    Returns:
        (markdown_text, [png_bytes, ...])

    markdown_text  — full text + tables as Markdown, truncated at MAX_CHARS.
    png_bytes list — figures / images extracted by marker-pdf, as PNG bytes.
                     Empty list when marker-pdf is unavailable or PDF has no figures.

    Results are cached: markdown in {cache_dir}/*.md,
    figures in {cache_dir}/*_images/*.png.
    """
    p = Path(path)
    if not p.exists():
        return f"[ERROR] File not found: {p}", []
    if p.suffix.lower() != ".pdf":
        return f"[ERROR] Not a PDF: {p}", []

    if cache_dir is None:
        from jarvis import paths
        cache_dir = paths.data_dir() / "pdf_cache"

    content_key = _content_key(p)
    cached_md = _cache_path(p, cache_dir, content_key)
    idir = _img_cache_dir(p, cache_dir, content_key)

    # ── Cache hit ──────────────────────────────────────────────────────────────
    # content_key is derived from the PDF's actual bytes, so a cache-file hit
    # here is inherently fresh — no mtime comparison needed (see _content_key).
    if cached_md.exists():
        content = cached_md.read_text(encoding="utf-8")
        header = f"[PDF→MD cache hit: {p.name}]\n\n"
        if len(content) > MAX_CHARS:
            content = content[:MAX_CHARS] + f"\n\n[TRUNCATED — {len(content):,} chars total, showing first {MAX_CHARS:,}]"
        # Load cached images if present
        images: list[bytes] = []
        if idir.exists():
            for img_path in sorted(idir.glob("*.png"))[:max_images]:
                images.append(img_path.read_bytes())
        return header + content, images

    # ── marker-pdf conversion ──────────────────────────────────────────────────
    if _marker_available():
        try:
            log.info(f"Converting {p.name} with marker-pdf...")
            md, images = _convert_marker(p, cache_dir, content_key)
            header = f"[marker-pdf conversion: {p.name}]\n\n"
            if len(md) > MAX_CHARS:
                md = md[:MAX_CHARS] + f"\n\n[TRUNCATED — {len(md):,} chars total, showing first {MAX_CHARS:,}]"
            return header + md, images[:max_images]
        except Exception as exc:
            log.warning(f"marker-pdf failed ({exc}), falling back to pdfplumber.")

    # ── pdfplumber fallback (text only) ────────────────────────────────────────
    try:
        text = _read_pdfplumber(p)
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + f"\n\n[TRUNCATED — showing first {MAX_CHARS:,} chars]"
        return text, []
    except Exception as exc:
        return f"[ERROR] Could not read PDF: {exc}", []
