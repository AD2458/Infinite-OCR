"""PDF -> page PNGs, with content-addressed caching.

Bodhan's `indic-ocr` takes one page per request and bills per page, with no
PDF endpoint -- so rasterising, paging and not paying twice are ours to do.

The sha256 of the rendered PNG is the cache key, so re-running on an
unchanged document is free and the stitching layer stays iterable offline.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pymupdf  # PyMuPDF 2.x; the legacy `fitz` alias is deprecated

# Scans need the detail; digital-born pages don't, and would only inflate the
# base64 payload (+33%) and push token counts toward the 502 cliff.
DPI_SCANNED = 300
DPI_DIGITAL = 200
MAX_EDGE_PX = 3000  # bound the longest edge regardless of DPI


@dataclass(frozen=True)
class RenderedPage:
    index: int
    path: Path
    sha256: str
    width: int
    height: int
    dpi: int
    # True when the page carries a vector/text drawing rather than a photo.
    # Used ONLY to pick a render DPI -- never to decide whether to OCR.
    is_vector: bool = False


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _page_dpi(is_vector: bool) -> int:
    """Pick a render DPI. A quality decision only -- every page is OCR'd.

    PDF text extraction silently reorders Indic combining marks (कंप्यूटर ->
    कपंय् टर), giving well-formed but wrong words no validator can catch
    without a lexicon, so we always read glyphs off the rendered image.
    """
    return DPI_DIGITAL if is_vector else DPI_SCANNED


def _clamp_dpi(page: pymupdf.Page, dpi: int) -> int:
    """Lower the DPI if it would exceed MAX_EDGE_PX on the longest edge."""
    rect = page.rect
    longest_pt = max(rect.width, rect.height)
    if longest_pt <= 0:
        return dpi
    max_dpi = int(MAX_EDGE_PX * 72.0 / longest_pt)
    return max(72, min(dpi, max_dpi))


def page_count(pdf_path: str | Path) -> int:
    """Return the total number of pages without rendering anything."""
    with pymupdf.open(pdf_path) as doc:
        return doc.page_count


def parse_page_spec(spec: str, total: int) -> list[int]:
    """Parse a human-friendly page specification into sorted, unique 0-based indices.

    Supports:
      ''          → all pages
      '3'         → just page 3
      '1-5'       → pages 1 through 5
      '1-3,7,10-12' → pages 1,2,3,7,10,11,12
      '0'         → all pages (legacy compat)

    Input is 1-based (how humans count pages); output is 0-based.
    Out-of-range indices are silently clamped.
    """
    if not spec or not spec.strip() or spec.strip() == "0":
        return list(range(total))

    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            lo_i = max(1, int(lo.strip())) - 1
            hi_i = min(total, int(hi.strip())) - 1
            out.update(range(lo_i, hi_i + 1))
        else:
            idx = int(part.strip()) - 1
            if 0 <= idx < total:
                out.add(idx)
    return sorted(out)


def rasterize(
    pdf_path: str | Path,
    out_dir: str | Path,
    *,
    pages: list[int] | range | None = None,
) -> list[RenderedPage]:
    """Render each page to a content-addressed PNG.

    `pages` accepts a list of 0-based indices, a range, or None for all pages.
    """
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[RenderedPage] = []
    with pymupdf.open(pdf_path) as doc:
        indices = pages if pages is not None else range(doc.page_count)
        for i in indices:
            if i < 0 or i >= doc.page_count:
                continue
            page = doc[i]

            # Photographic scans need more detail than vector pages. Treat a
            # page as a scan when a raster image covers most of it.
            is_vector = not _has_large_raster(page)

            dpi = _clamp_dpi(page, _page_dpi(is_vector))
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            data = pix.tobytes("png")
            digest = _sha256(data)

            path = out_dir / f"{digest}.png"
            if not path.exists():  # content-addressed: identical page, identical file
                path.write_bytes(data)

            results.append(
                RenderedPage(
                    index=i,
                    path=path,
                    sha256=digest,
                    width=pix.width,
                    height=pix.height,
                    dpi=dpi,
                    is_vector=is_vector,
                )
            )
    return results


def _has_large_raster(page: pymupdf.Page, min_coverage: float = 0.5) -> bool:
    """True if a raster image covers at least `min_coverage` of the page.

    A logo or small figure should not drag the whole page up to scan DPI;
    a full-page photograph should.
    """
    rect = page.rect
    page_area = rect.width * rect.height
    if page_area <= 0:
        return False
    try:
        for info in page.get_image_info():
            bbox = info.get("bbox")
            if not bbox:
                continue
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            if (w * h) / page_area >= min_coverage:
                return True
    except Exception:
        # Image introspection is best-effort; default to the safer (higher) DPI.
        return True
    return False


def rerender_region(
    pdf_path: str | Path,
    page_index: int,
    bbox_px: tuple[float, float, float, float],
    out_path: str | Path,
    *,
    src_dpi: int,
    dpi: int = 600,
    pad_frac: float = 0.02,
) -> Path:
    """Re-rasterise one region straight from the PDF at a higher DPI.

    The only preprocessing worth doing for a vision model: upscaling the
    cached PNG only interpolates, while re-rendering the vector content adds
    real detail. Deskew/binarise are omitted on purpose -- they suit
    pixel-template engines and tend to hurt a VLM.

    `bbox_px` is in the original render's space (`src_dpi`), as stored in the
    IR. MAX_EDGE_PX is bypassed: it bounds whole pages, and would cancel the
    upscale the caller asked for.
    """
    pdf_path, out_path = Path(pdf_path), Path(out_path)
    x0, y0, x1, y1 = bbox_px
    dx, dy = (x1 - x0) * pad_frac, (y1 - y0) * pad_frac
    scale = 72.0 / float(src_dpi)  # page pixels -> PDF points

    with pymupdf.open(pdf_path) as doc:
        if page_index < 0 or page_index >= doc.page_count:
            raise IndexError(f"page {page_index} out of range")
        page = doc[page_index]
        clip = pymupdf.Rect(
            max(page.rect.x0, (x0 - dx) * scale),
            max(page.rect.y0, (y0 - dy) * scale),
            min(page.rect.x1, (x1 + dx) * scale),
            min(page.rect.y1, (y1 + dy) * scale),
        )
        if clip.is_empty or clip.width <= 0 or clip.height <= 0:
            raise ValueError("bbox does not intersect the page")
        pix = page.get_pixmap(dpi=dpi, clip=clip, alpha=False)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(pix.tobytes("png"))
    return out_path


def crop_png(
    page_png: str | Path,
    bbox: tuple[float, float, float, float],
    out_path: str | Path,
    *,
    pad_frac: float = 0.02,
) -> Path:
    """Crop a region from a cached page PNG, for a targeted block redo.

    A tighter crop frequently succeeds where the full page failed (fewer
    blocks, so less chance of tripping the per-block max_tokens limit), and
    costs a single page-billing unit.
    """
    from PIL import Image

    page_png, out_path = Path(page_png), Path(out_path)
    with Image.open(page_png) as im:
        w, h = im.size
        x0, y0, x1, y1 = bbox
        dx, dy = (x1 - x0) * pad_frac, (y1 - y0) * pad_frac
        box = (
            max(0, int(x0 - dx)),
            max(0, int(y0 - dy)),
            min(w, int(x1 + dx)),
            min(h, int(y1 + dy)),
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        im.crop(box).save(out_path, format="PNG")
    return out_path
