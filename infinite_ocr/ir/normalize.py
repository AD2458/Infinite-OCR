"""Turn raw Bodhan page responses into IR blocks.

Also reconciles the two views the API returns of the same page: the Markdown
string and the `blocks` array. The docs note pictorial blocks are omitted
from the Markdown but remain in `blocks`, so a Picture must be re-inserted as
a placeholder or it silently disappears from the output.
"""

from __future__ import annotations

from typing import Any

from ..render.math import demath
from ..script.detect import direction, primary_script, script_histogram
from ..script.normalize import canonical
from .models import BBox, Block, block_id, canonical_type


def blocks_from_response(
    payload: dict[str, Any],
    *,
    doc_id: str,
    page: int,
) -> list[Block]:
    """Build IR blocks from one page's API response.

    Falls back to a single synthetic Text block when `blocks` is absent, so a
    response that only carries Markdown still contributes to the flow rather
    than being dropped.
    """
    raw_blocks = payload.get("blocks") or []

    if not raw_blocks:
        md = (payload.get("choices") or [{}])[0].get("message", {}).get("content", "")
        if not md:
            return []
        return [_make_block(doc_id, page, 0, "Text", None, BBox(x0=0, y0=0, x1=0, y1=0), 1.0, md)]

    # Sort by the API's `order`, but derive ids from the array index -- live
    # responses start `order` at 0 while the docs show 1, and reconciling the
    # two produced colliding ids. A collision is silently dangerous: `by_id()`
    # returns the first match, so a redo could rewrite the wrong region.
    indexed = list(enumerate(raw_blocks))
    indexed.sort(key=lambda t: (_order_of(t[1], t[0]), t[0]))

    out: list[Block] = []
    for position, (_orig_idx, rb) in enumerate(indexed):
        bbox_raw = rb.get("bbox_xyxy") or rb.get("bbox")
        bbox = BBox.from_xyxy(bbox_raw) if bbox_raw else BBox(x0=0, y0=0, x1=0, y1=0)
        out.append(
            _make_block(
                doc_id,
                page,
                position,
                canonical_type(rb.get("type")),
                rb.get("label"),
                bbox,
                float(rb.get("conf", 1.0)),
                rb.get("text", "") or "",
            )
        )
    return out


def _order_of(rb: dict[str, Any], fallback: int) -> float:
    """Read the API's reading-order hint, tolerating 0- or 1-based values."""
    try:
        return float(rb.get("order", fallback))
    except (TypeError, ValueError):
        return float(fallback)


def _make_block(
    doc_id: str,
    page: int,
    order: int,
    btype: str,
    label: str | None,
    bbox: BBox,
    conf: float,
    text: str,
) -> Block:
    # Convert inline LaTeX (e.g. `$\rightarrow$`) at the IR boundary so every
    # renderer benefits. Genuine formulas pass through -- see render.math.
    text = demath(canonical(text))
    hist = script_histogram(text)
    primary = primary_script(text) if text else "Zyyy"
    return Block(
        id=block_id(page, order),
        doc_id=doc_id,
        page=page,
        order=order,
        type=btype,  # type: ignore[arg-type]
        label=label,
        bbox=bbox,
        conf=conf,
        text=text,
        scripts=hist,
        primary_script=primary,
        direction=direction(primary),  # type: ignore[arg-type]
    )


def attach_picture_placeholders(blocks: list[Block]) -> list[Block]:
    """Mark Picture blocks so the renderer can show the actual crop.

    Text is left empty on purpose: writing `<block-id>.png` would reference a
    file that is never created. The only real image is the API's live crop,
    so the renderer decides how to display it.
    """
    for b in blocks:
        if b.type == "Picture":
            if not b.text.strip():
                b.flag("needs_alt_text")
    return blocks
