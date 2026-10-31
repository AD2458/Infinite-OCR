"""Reading-order validation via bbox geometry.

The API's `order` is trusted but verified: multi-column Indic textbooks are
where it tends to break, and a wrong order interleaves columns into nonsense
no downstream stitching can repair.

Perso-Arabic columns run right-to-left, so sorting by ascending x would
reverse the document. The switch is on script, not language, since Bodhan
lists both `sd`/`sd-Arab` and `ks`/`ks-Deva`.
"""

from __future__ import annotations

from ..script.detect import RTL_SCRIPTS
from .models import Block


def detect_columns(blocks: list[Block], page_width: float, *, tol: float = 0.08) -> int:
    """Estimate the column count from horizontal block clustering.

    Heuristic: if most body blocks are narrower than ~55% of the page and
    their centres form two well-separated clusters, the page is two-column.
    """
    body = [b for b in blocks if b.type in ("Text", "List") and b.bbox.width > 0]
    if len(body) < 4 or page_width <= 0:
        return 1

    wide = sum(1 for b in body if b.bbox.width > 0.55 * page_width)
    if wide > len(body) * 0.5:
        return 1

    mid = page_width / 2
    left = [b for b in body if b.bbox.cx < mid]
    right = [b for b in body if b.bbox.cx >= mid]
    if not left or not right:
        return 1

    # Require a clear gutter between the two centre clusters.
    gap = min(b.bbox.cx for b in right) - max(b.bbox.cx for b in left)
    return 2 if gap > tol * page_width else 1


def page_is_rtl(blocks: list[Block]) -> bool:
    """True if the page's script-bearing text is predominantly RTL."""
    weighted = [(len(b.text), b.primary_script) for b in blocks if b.text.strip()]
    if not weighted:
        return False
    rtl = sum(n for n, s in weighted if s in RTL_SCRIPTS)
    total = sum(n for n, _ in weighted)
    return total > 0 and rtl / total > 0.5


def reorder_page(blocks: list[Block], page_width: float) -> list[Block]:
    """Re-derive reading order from geometry, honouring columns and direction.

    Single-column pages keep the API's order (it is reliable there). Only
    multi-column pages are re-sorted, which limits the blast radius of this
    heuristic.
    """
    if not blocks:
        return blocks

    ordered = sorted(blocks, key=lambda b: b.order)
    ncols = detect_columns(ordered, page_width)
    if ncols < 2:
        return ordered

    rtl = page_is_rtl(ordered)
    mid = page_width / 2

    # Full-width blocks (titles, wide tables) span columns and must stay in
    # their vertical position rather than being forced into a column.
    def column_of(b: Block) -> int:
        if b.bbox.width > 0.55 * page_width:
            return -1  # spanning
        left = b.bbox.cx < mid
        if rtl:
            return 0 if not left else 1  # right column reads first
        return 0 if left else 1

    spanning = [b for b in ordered if column_of(b) == -1]
    columns: dict[int, list[Block]] = {0: [], 1: []}
    for b in ordered:
        c = column_of(b)
        if c >= 0:
            columns[c].append(b)

    # Interleave: spanning blocks act as barriers at their y position.
    result: list[Block] = []
    barriers = sorted(spanning, key=lambda b: b.bbox.y0)
    prev_y = float("-inf")
    for bar in barriers + [None]:  # type: ignore[list-item]
        limit = bar.bbox.y0 if bar is not None else float("inf")
        for c in (0, 1):
            seg = [b for b in columns[c] if prev_y <= b.bbox.y0 < limit]
            result.extend(sorted(seg, key=lambda b: b.bbox.y0))
        if bar is not None:
            result.append(bar)
            prev_y = bar.bbox.y0

    for i, b in enumerate(result):
        b.order = i
    return result
