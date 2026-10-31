"""Run the pipeline for a web job, reporting per-page progress.

Mirrors `infinite_ocr.pipeline.run` but reports progress as each page lands
and distinguishes cached pages from billed ones, so the UI can show exactly
what a run actually cost.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from ..client.bodhan import BodhanOCR, BodhanPageError
from ..client.cache import PageCache
from ..ingest.rasterize import RenderedPage, page_count, parse_page_spec, rasterize
from ..pipeline import build_document
from ..render.markdown import render_with_report
from .jobs import Job


async def _ocr_with_progress(
    job: Job,
    pages: list[RenderedPage],
    cache: PageCache,
    *,
    concurrency: int,
    table_format: str,
) -> dict[str, dict[str, Any]]:
    todo: list[RenderedPage] = []
    for p in pages:
        if cache.has(p.sha256):
            job.pages_cached += 1
            job.pages_done += 1
            continue
        todo.append(p)

    if todo:
        job.status = "ocr"
        job.message = f"OCR {len(todo)} page(s); {job.pages_cached} from cache"
        async with BodhanOCR(
            concurrency=concurrency, table_format=table_format
        ) as client:
            lock = asyncio.Lock()

            async def one(p: RenderedPage) -> None:
                try:
                    res = await client.ocr_page(p.path)
                    cache.put(p.sha256, res.raw)
                except BodhanPageError as e:
                    # Record the failure so the page shows up in the report
                    # instead of silently vanishing from the output.
                    cache.put(p.sha256, {"_failed": True, "detail": str(e)})
                async with lock:
                    job.pages_done += 1
                    job.pages_billed += 1

            await asyncio.gather(*(one(p) for p in todo))
    else:
        job.message = "all pages served from cache -- nothing billed"

    return {p.sha256: (cache.get(p.sha256) or {}) for p in pages}


def run_job(
    job: Job,
    *,
    store: Path,
    out_dir: Path,
    concurrency: int = 4,
    table_format: str = "html",
    page_spec: str = "",
) -> None:
    """Execute a job end to end and persist its artefacts."""
    cache = PageCache(store)

    job.status = "rasterizing"
    job.message = "rendering pages"
    total = page_count(job.pdf_path)
    indices = parse_page_spec(page_spec, total) if page_spec else None
    pages = rasterize(job.pdf_path, cache.pages_dir, pages=indices)
    job.pages_total = len(pages)

    responses = asyncio.run(
        _ocr_with_progress(
            job,
            pages,
            cache,
            concurrency=concurrency,
            table_format=table_format,
        )
    )

    job.status = "stitching"
    job.message = "building IR, stitching pages"
    doc = build_document(job.pdf_path, pages, responses, doc_id=job.id)
    markdown, report = render_with_report(doc)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{job.id}.md").write_text(markdown, encoding="utf-8")
    (out_dir / f"{job.id}.ir.json").write_text(doc.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / f"{job.id}.report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    job.doc = doc
    job.markdown = markdown
    job.pages_done = job.pages_total
    job.status = "done"
    job.message = (
        f"{job.pages_billed} page(s) billed, {job.pages_cached} from cache"
    )
