"""Targeted block redo -- 'only that part gets redone'.

Re-OCRs one region cropped from the cached page PNG, so the rest of the
document is untouched and the cost is one page-billing unit.

A tight crop often succeeds where the full page failed: fewer blocks means
less chance of tripping the max_tokens limit that 502s the whole page.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ..client.bodhan import BodhanOCR
from ..client.cache import PageCache
from ..ingest.rasterize import crop_png, rerender_region
from ..ir.models import BBox, Block, Document, Link
from ..script.detect import direction, primary_script, script_histogram
from ..script.normalize import canonical
from ..stitch.links import apply_links


def redo_block(
    doc: Document,
    block_id: str,
    cache: PageCache,
    *,
    api_key: str | None = None,
    table_format: str = "markdown",
    max_tokens: int = 8192,
    pad_frac: float = 0.02,
    bbox_override: tuple[float, float, float, float] | None = None,
    dpi: int | None = None,
) -> str:
    """Re-OCR one block and bump its version. Returns the new text.

    Three levers change the model's input, since repeating it unchanged
    cannot change the answer:

      * `bbox_override` -- nudged region, when the box clipped the text
      * `pad_frac`      -- more margin, when ascenders or matras were cut
      * `dpi`           -- re-render from the PDF with real added detail

    `table_format` defaults to markdown: it is far less likely to exceed the
    per-block token limit than html.
    """
    block = doc.by_id(block_id)
    if block is None:
        raise KeyError(f"no such block: {block_id}")
    if block.locked:
        raise PermissionError(
            f"{block_id} holds a human correction and is locked. "
            "Unlock it first if you really want to re-OCR."
        )

    page = next((p for p in doc.pages if p.index == block.page), None)
    if page is None:
        raise KeyError(f"no cached page for block {block_id}")

    page_png = cache.pages_dir / f"{page.sha256}.png"
    if not page_png.exists():
        raise FileNotFoundError(f"cached page image missing: {page_png}")

    bbox = BBox.from_xyxy(bbox_override) if bbox_override else block.bbox
    crop_dir = cache.root / "crops"
    crop_path = crop_dir / f"{block.id}-v{block.version + 1}.png"

    # Identical pixels return the identical error, so a redo must actually
    # change the input: a different crop, or genuinely more detail.
    if dpi is not None and dpi > page.dpi:
        rerender_region(
            doc.source_path,
            block.page,
            bbox.as_tuple(),
            crop_path,
            src_dpi=page.dpi,
            dpi=dpi,
            pad_frac=pad_frac,
        )
    else:
        crop_png(page_png, bbox.as_tuple(), crop_path, pad_frac=pad_frac)

    async def _go() -> str:
        async with BodhanOCR(
            api_key, concurrency=1, table_format=table_format
        ) as client:
            res = await client.ocr_page(crop_path)
            # Prefer the block text when the crop resolves to a single block;
            # otherwise fall back to the markdown for the whole crop.
            if len(res.blocks) == 1 and res.blocks[0].get("text"):
                return str(res.blocks[0]["text"])
            return res.markdown

    new_text = canonical(asyncio.run(_go()).strip())

    block.text = new_text
    block.version += 1
    block.source = "redo"
    block.conf = 1.0
    block.flags.clear()
    ps = primary_script(new_text)
    block.scripts = script_histogram(new_text)
    block.primary_script = ps
    block.direction = direction(ps)  # type: ignore[assignment]
    if bbox_override:
        block.bbox = bbox

    # New text invalidates links that depended on the old: a sentence link
    # exists because the block lacked a terminator, and the redo may add one.
    refresh_links(doc, around=block)
    return new_text


def refresh_links(doc: Document, *, around: Block | None = None) -> None:
    """Re-derive stitching links, preserving the reviewer's own decisions.

    Automated re-evaluation must not silently undo a human override, so any
    link a reviewer explicitly rejected or forced stays exactly as it was.
    """
    manual = {
        (l.from_block, l.to_block): l
        for l in doc.links
        if l.reason.startswith("manual:") or not l.accepted
    }
    apply_links(doc)
    by_pair = {(l.from_block, l.to_block): l for l in doc.links}
    for key, kept in manual.items():
        if key in by_pair:
            by_pair[key].accepted = kept.accepted
            by_pair[key].reason = kept.reason
        else:
            doc.links.append(kept)


def set_block_text(doc: Document, block_id: str, text: str) -> None:
    """Pin a human correction.

    `source="human"` is never overwritten by a later automated redo -- a
    reviewer's work must not be silently undone.
    """
    block = doc.by_id(block_id)
    if block is None:
        raise KeyError(f"no such block: {block_id}")
    block.text = canonical(text)
    block.version += 1
    block.source = "human"
    block.locked = True
    block.conf = 1.0
    block.flags.clear()
    ps = primary_script(block.text)
    block.scripts = script_histogram(block.text)
    block.primary_script = ps
    block.direction = direction(ps)  # type: ignore[assignment]
    refresh_links(doc, around=block)


def reject_link(doc: Document, from_block: str) -> bool:
    """Reject a stitching decision and re-render unmerged."""
    for l in doc.links:
        if l.from_block == from_block:
            l.accepted = False
            l.reason = f"manual: broken by reviewer ({l.reason})"
            return True
    return False


def set_locked(doc: Document, block_id: str, locked: bool) -> None:
    """Lock or unlock a block against automated overwriting."""
    block = doc.by_id(block_id)
    if block is None:
        raise KeyError(f"no such block: {block_id}")
    block.locked = locked


def force_merge(
    doc: Document,
    from_block: str,
    to_block: str,
    *,
    kind: str = "sentence",
) -> Link:
    """Create a stitch the automatic pass did not propose. Costs nothing.

    The stitcher is deliberately conservative -- a false merge corrupts data
    silently -- so it will miss real continuations. This is the reviewer's
    override for that case, and it is marked `manual:` so later automatic
    re-evaluation leaves it alone.
    """
    src, dst = doc.by_id(from_block), doc.by_id(to_block)
    if src is None or dst is None:
        raise KeyError("both blocks must exist")
    if src.id == dst.id:
        raise ValueError("cannot merge a block with itself")

    for existing in doc.links:
        if existing.from_block == from_block:
            if existing.to_block != to_block:
                raise ValueError(
                    f"{from_block} already links to {existing.to_block}; "
                    "break that link first"
                )
            existing.accepted = True
            existing.kind = kind  # type: ignore[assignment]
            existing.confidence = 1.0
            existing.reason = "manual: merged by reviewer"
            return existing

    link = Link(
        kind=kind,  # type: ignore[arg-type]
        from_block=from_block,
        to_block=to_block,
        confidence=1.0,
        reason="manual: merged by reviewer",
        accepted=True,
    )
    doc.links.append(link)
    return link
