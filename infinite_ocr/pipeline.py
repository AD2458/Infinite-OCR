"""End-to-end pipeline: PDF -> one continuous, structure-aware document.

Credit discipline is built in. Stage order is deliberate: everything after
`ocr_document` is pure post-processing over cached responses, so the stitching
and rendering layers -- the bulk of the work -- can be re-run and re-tuned
indefinitely without spending a single credit.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from .client.bodhan import BodhanOCR, BodhanPageError
from .client.cache import PageCache
from .ingest.rasterize import RenderedPage, rasterize
from .ir.furniture import mark_furniture
from .ir.models import Document, Page
from .ir.normalize import attach_picture_placeholders, blocks_from_response
from .ir.order import reorder_page
from .review.confidence import flag_blocks
from .stitch.links import apply_links


async def ocr_document(
    pages: list[RenderedPage],
    cache: PageCache,
    *,
    api_key: str | None = None,
    concurrency: int = 4,
    table_format: str = "html",
) -> dict[str, dict[str, Any]]:
    """OCR every page that is not already cached. Returns sha -> response.

    Pages already in the cache are never re-sent, so a page is billed once
    ever, no matter how many times the pipeline runs.

    There is no "use the PDF text layer" shortcut: extraction silently
    reorders Indic combining marks (कंप्यूटर -> कपंय् टर) into still
    well-formed Devanagari that no validator catches without a lexicon.
    """
    todo = [p for p in pages if not cache.has(p.sha256)]

    if todo:
        async with BodhanOCR(
            api_key, concurrency=concurrency, table_format=table_format
        ) as client:

            async def one(p: RenderedPage) -> None:
                try:
                    res = await client.ocr_page(p.path)
                    cache.put(p.sha256, res.raw)
                except BodhanPageError as e:
                    # Record the failure so the page is visible in the report
                    # rather than silently missing from the output.
                    cache.put(p.sha256, {"_failed": True, "detail": str(e)})

            await asyncio.gather(*(one(p) for p in todo))

    return {p.sha256: (cache.get(p.sha256) or {}) for p in pages}


def build_document(
    pdf_path: str | Path,
    pages: list[RenderedPage],
    responses: dict[str, dict[str, Any]],
    *,
    doc_id: str = "doc",
    preserve: Document | None = None,
) -> Document:
    """Assemble the IR from cached responses. Pure, offline, zero credits.

    `preserve` carries a previous version of the document whose locked blocks
    and manual stitch decisions must survive. Without it a replay would
    silently discard every human correction, which would make the whole
    review loop pointless.
    """
    doc = Document(doc_id=doc_id, source_path=str(pdf_path))
    page_heights: dict[int, int] = {}
    locked = {b.id: b for b in (preserve.blocks if preserve else []) if b.locked}
    manual = [
        l for l in (preserve.links if preserve else [])
        if l.reason.startswith("manual:") or not l.accepted
    ]

    # Group pages into contiguous runs: a gap means disjoint ranges were
    # selected, and text either side must never be stitched.
    groups: dict[int, int] = {}
    g = 0
    prev_idx: int | None = None
    for p in pages:
        if prev_idx is not None and p.index != prev_idx + 1:
            g += 1
        groups[p.index] = g
        prev_idx = p.index

    for p in pages:
        payload = responses.get(p.sha256) or {}
        doc.pages.append(
            Page(
                index=p.index,
                width=p.width,
                height=p.height,
                sha256=p.sha256,
                dpi=p.dpi,
                group=groups.get(p.index, 0),
            )
        )
        page_heights[p.index] = p.height
        if payload.get("_failed"):
            continue

        blocks = blocks_from_response(payload, doc_id=doc_id, page=p.index)
        blocks = reorder_page(blocks, float(p.width))
        blocks = attach_picture_placeholders(blocks)
        # Restore human corrections over the freshly parsed blocks.
        blocks = [locked.get(b.id, b) for b in blocks]
        doc.blocks.extend(blocks)

    mark_furniture(doc.blocks, page_heights)
    apply_links(doc)

    # Re-apply the reviewer's stitch overrides on top of the automatic pass.
    by_pair = {(l.from_block, l.to_block): l for l in doc.links}
    for kept in manual:
        target = by_pair.get((kept.from_block, kept.to_block))
        if target is not None:
            target.accepted = kept.accepted
            target.reason = kept.reason
        else:
            doc.links.append(kept)

    flag_blocks(doc)
    # A locked block carries a human's text; triage must not re-flag it.
    for b in doc.blocks:
        if b.locked:
            b.flags.clear()
    return doc


def run(
    pdf_path: str | Path,
    *,
    store: str | Path = "store",
    out: str | Path | None = None,
    api_key: str | None = None,
    concurrency: int = 4,
    table_format: str = "html",
    page_spec: str = "",
) -> tuple[Document, str]:
    """Run the whole pipeline and write the rendered document."""
    from .ingest.rasterize import page_count, parse_page_spec
    from .render.markdown import render_with_report

    cache = PageCache(store)
    total = page_count(pdf_path)
    indices = parse_page_spec(page_spec, total) if page_spec else None
    pages = rasterize(pdf_path, cache.pages_dir, pages=indices)
    responses = asyncio.run(
        ocr_document(
            pages,
            cache,
            api_key=api_key,
            concurrency=concurrency,
            table_format=table_format,
        )
    )
    doc = build_document(pdf_path, pages, responses, doc_id=Path(pdf_path).stem)
    markdown, report = render_with_report(doc)

    if out is not None:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(markdown, encoding="utf-8")
        out.with_suffix(".report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        out.with_suffix(".ir.json").write_text(
            doc.model_dump_json(indent=2), encoding="utf-8"
        )
    return doc, markdown
