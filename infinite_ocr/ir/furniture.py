"""Running header/footer, page-number and decoration detection.

Furniture must leave the text flow: it corrupts the prose and, by sitting
last on a page, blocks cross-page stitching.

Detection is positional + repetition based and script-agnostic -- it matches
on `comparable()` keys, which mask digits in any script so "Page 12" and
"पृष्ठ १३" collapse together.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from ..script.normalize import comparable
from .models import FURNITURE_TYPES, Block

MIN_PAGES = 3          # repeats needed to call something furniture
EDGE_BAND = 0.12       # fraction of page height treated as top/bottom edge
SIMILARITY = 0.85      # fuzzy key match; OCR noise makes exact matching fail

EDGE_FURNITURE_TYPES = FURNITURE_TYPES | {"Footnote"}

# A textless Picture under this area near an edge is decoration (signature,
# stamp, logo). Real figures run 10-40%, so the margin is wide.
MAX_DECORATION_AREA = 0.04
DECORATION_BAND = 0.20  # signatures observed at 83-92% of page height


def mark_furniture(
    blocks: list[Block],
    page_heights: dict[int, int],
    *,
    min_pages: int = MIN_PAGES,
    edge_band: float = EDGE_BAND,
) -> list[Block]:
    """Flag headers, footers and decorations in place; returns the same list.

    Requires both repetition and edge position: a repeated phrase in body
    text is not furniture, and a one-off line at the top is just a heading.
    """
    n_pages = len({b.page for b in blocks})
    effective = _effective_heights(blocks, page_heights)

    # Pass 1: trust the API's own typing when position corroborates it.
    for b in blocks:
        if b.type in FURNITURE_TYPES:
            zone = _edge_zone(b, effective.get(b.page, 0), edge_band)
            if zone is not None or not b.text.strip():
                b.is_furniture = True
        elif b.type == "Footnote":
            # Edge only -- a mid-page footnote is content.
            if _edge_zone(b, effective.get(b.page, 0), edge_band) is not None:
                b.is_furniture = True

    _mark_decorations(blocks, effective)

    # Pass 2: repeated text near the same edge; needs >1 page to mean anything.
    if n_pages < 2:
        return blocks
    threshold = min(min_pages, n_pages)

    # Pass-1 blocks are included as evidence: excluding them would strand an
    # inconsistently typed twin below the threshold and leak it into the prose.
    zoned: list[tuple[Block, str, str]] = []
    for b in blocks:
        zone = _edge_zone(b, effective.get(b.page, 0), edge_band)
        key = comparable(b.text)
        if zone is None or not key or len(key) > 120:
            continue
        zoned.append((b, zone, key))

    # Fuzzy-cluster keys: a real run produced "7 | Page" twice and
    # "7 | Page ha" once, which exact matching would split below threshold.
    clusters: list[tuple[str, str, set[int], list[Block]]] = []
    for b, zone, key in zoned:
        for c_zone, c_key, pages, members in clusters:
            if c_zone == zone and _similar(c_key, key):
                pages.add(b.page)
                members.append(b)
                break
        else:
            clusters.append((zone, key, {b.page}, [b]))

    for zone, _key, pages, members in clusters:
        # A declared-furniture member is independent corroboration, so two
        # pages suffice. Rescues a footer mistyped as body text on one page.
        type_confirmed = any(b.type in FURNITURE_TYPES for b in members)
        needed = 2 if type_confirmed else threshold
        if len(pages) >= needed:
            for b in members:
                b.is_furniture = True
                # Preserve a semantic type where the model supplied one.
                if b.type == "Unknown":
                    b.type = "PageHeader" if zone == "top" else "PageFooter"
    return blocks


def _mark_decorations(
    blocks: list[Block], effective: dict[int, float]
) -> None:
    """Flag textless decorative images (signatures, stamps, logos) in place.

    All must hold, so a real figure is never dropped: no text, small, near an
    edge, and the page has other content (an image-only page keeps its block).
    """
    by_page: dict[int, list[Block]] = {}
    for b in blocks:
        by_page.setdefault(b.page, []).append(b)

    # Width isn't on the block; infer it from the widest bbox on the page.
    widths: dict[int, float] = {}
    for page, items in by_page.items():
        widths[page] = max((b.bbox.x1 for b in items), default=0.0)

    for page, items in by_page.items():
        pw, ph = widths.get(page, 0.0), effective.get(page, 0.0)
        if pw <= 0 or ph <= 0:
            continue
        others = [b for b in items if b.text.strip() and not b.is_furniture]
        if not others:
            continue  # page is essentially just this image; keep it

        for b in items:
            if b.type != "Picture" or b.text.strip() or b.is_furniture:
                continue
            area = (b.bbox.width * b.bbox.height) / (pw * ph)
            near_top = b.bbox.y1 <= ph * DECORATION_BAND
            near_bottom = b.bbox.y0 >= ph * (1 - DECORATION_BAND)
            if area <= MAX_DECORATION_AREA and (near_top or near_bottom):
                b.is_furniture = True
                b.flag("decoration")


def _similar(a: str, b: str, ratio: float = SIMILARITY) -> bool:
    """Fuzzy key equality, tolerant of OCR noise in running headers."""
    if a == b:
        return True
    # Cheap length guard before the O(n^2) comparison.
    if abs(len(a) - len(b)) > max(len(a), len(b)) * 0.5:
        return False
    return SequenceMatcher(None, a, b).ratio() >= ratio


def _effective_heights(
    blocks: list[Block], page_heights: dict[int, int]
) -> dict[int, float]:
    """Reconcile bbox coordinate space with the declared page height.

    If the caller's height uses a different space (PDF points vs. 300-dpi
    pixels), the edge band silently matches nothing and furniture detection
    fails document-wide. Fall back to the observed bbox extent when the
    declared height looks implausible.
    """
    observed: dict[int, float] = {}
    for b in blocks:
        observed[b.page] = max(observed.get(b.page, 0.0), b.bbox.y1)

    out: dict[int, float] = {}
    for page, obs in observed.items():
        declared = float(page_heights.get(page, 0) or 0)
        # Trust the declared height only if the blocks plausibly live in it.
        out[page] = declared if declared and obs >= declared * 0.5 else obs
    return out


def _edge_zone(b: Block, page_height: float, edge_band: float) -> str | None:
    """'top', 'bottom', or None if the block sits in the body area."""
    if page_height <= 0 or b.bbox.height <= 0:
        return None
    band = page_height * edge_band
    if b.bbox.y1 <= band:
        return "top"
    if b.bbox.y0 >= page_height - band:
        return "bottom"
    return None


def furniture_summary(blocks: list[Block]) -> dict[str, int]:
    return {
        "furniture_blocks": sum(1 for b in blocks if b.is_furniture),
        "flow_blocks": sum(1 for b in blocks if b.is_flow),
    }
