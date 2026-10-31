"""FastAPI backend for the Infinite OCR reviewer."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from ..client.cache import PageCache
from ..ir.models import Document
from ..render.markdown import render
from ..review.confidence import quality_report, review_queue
from ..review.redo import force_merge, redo_block, set_block_text, set_locked
from .jobs import Job, registry
from .runner import run_job

ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "store"
UPLOADS = STORE / "uploads"
OUT = ROOT / "out"
STATIC = Path(__file__).parent / "static"

for d in (UPLOADS, OUT, STATIC):
    d.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Infinite OCR")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict[str, object]:
    import os

    key = os.environ.get("BODHAN_API_KEY")
    return {
        "key_loaded": bool(key),
        "key_hint": f"{key[:4]}...{key[-4:]}" if key else None,
        "cache": PageCache(STORE).stats(),
    }


@app.post("/api/cache/clear")
def clear_cache(scope: str = Form("images")) -> dict[str, object]:
    """Clear cached artefacts.

    scope='images'  -> page PNGs + crops only. Free to rebuild; raw OCR
                       responses are kept so re-running costs no credits.
    scope='all'     -> also drops the raw responses. Re-running will be
                       billed again, so this is the destructive option.
    """
    cache = PageCache(STORE)
    removed = {"pages": 0, "crops": 0, "raw": 0}

    for p in cache.pages_dir.glob("*.png"):
        p.unlink(missing_ok=True)
        removed["pages"] += 1
    crops = cache.root / "crops"
    if crops.exists():
        for p in crops.iterdir():
            if p.is_file():
                p.unlink(missing_ok=True)
                removed["crops"] += 1

    if scope == "all":
        for p in cache.raw_dir.glob("*.json"):
            p.unlink(missing_ok=True)
            removed["raw"] += 1

    return {"removed": removed, "scope": scope, "cache": cache.stats()}


@app.post("/api/probe")
async def probe(file: UploadFile) -> dict[str, object]:
    """Return the page count of a PDF without OCRing anything. Free."""
    from ..ingest.rasterize import page_count

    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF.")
    dest = UPLOADS / f"{file.filename}"
    with dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    return {"filename": file.filename, "total_pages": page_count(dest)}


@app.post("/api/upload")
async def upload(
    file: UploadFile,
    table_format: str = Form("html"),
    page_spec: str = Form(""),
    concurrency: int = Form(4),
) -> dict[str, object]:
    """Upload and OCR a PDF. `page_spec` selects pages: '1-3,7,10-12'."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF.")

    dest = UPLOADS / f"{file.filename}"
    with dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)

    job = registry.create(file.filename or "upload.pdf", dest)
    registry.run_async(
        job,
        lambda j: run_job(
            j,
            store=STORE,
            out_dir=OUT,
            concurrency=concurrency,
            table_format=table_format,
            page_spec=page_spec,
        ),
    )
    return job.public()


@app.get("/api/jobs")
def list_jobs() -> list[dict[str, object]]:
    return [j.public() for j in registry.all()]


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, object]:
    return _job(job_id).public()


@app.get("/api/jobs/{job_id}/result")
def job_result(job_id: str) -> dict[str, object]:
    job = _job(job_id)
    if job.status != "done" or job.doc is None:
        raise HTTPException(409, f"Job is '{job.status}', not done.")
    doc = job.doc
    return {
        "markdown": job.markdown,
        "report": quality_report(doc),
        "queue": review_queue(doc),
        "links": [
            {
                "kind": l.kind, "from": l.from_block, "to": l.to_block,
                "confidence": l.confidence, "reason": l.reason,
                "accepted": l.accepted,
            }
            for l in doc.links
        ],
        "blocks": [
            {
                "id": b.id, "page": b.page, "order": b.order, "type": b.type,
                "conf": b.conf, "text": b.text, "flags": b.flags,
                "script": b.primary_script, "direction": b.direction,
                "version": b.version, "source": b.source,
                "locked": b.locked,
                "furniture": b.is_furniture,
                "bbox": [b.bbox.x0, b.bbox.y0, b.bbox.x1, b.bbox.y1],
            }
            for b in doc.blocks_in_order()
        ],
        "pages": [
            {"index": p.index, "width": p.width, "height": p.height,
             "sha256": p.sha256, "dpi": p.dpi}
            for p in doc.pages
        ],
    }


@app.get("/api/jobs/{job_id}/markdown", response_class=PlainTextResponse)
def job_markdown(job_id: str) -> str:
    job = _job(job_id)
    if job.doc is None:
        raise HTTPException(409, "Not ready.")
    return render(job.doc)


@app.get("/api/jobs/{job_id}/download")
def job_download(job_id: str) -> FileResponse:
    path = OUT / f"{job_id}.md"
    if not path.exists():
        raise HTTPException(404, "No output yet.")
    return FileResponse(
        path, filename=f"{_job(job_id).filename}.md", media_type="text/markdown"
    )


_EXPORTS = {
    "md": ("text/markdown", ".md"),
    "mdflat": ("text/markdown", ".flat.md"),
    "html": ("text/html", ".html"),
    "txt": ("text/plain", ".txt"),
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    "xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
}


@app.get("/api/jobs/{job_id}/export/{fmt}")
def job_export(job_id: str, fmt: str):
    """Export the document. All formats are free -- pure post-processing."""
    from ..render.exports import (
        render_markdown_flat,
        render_text,
        write_docx,
        write_xlsx,
    )
    from ..render.html import render_html

    if fmt not in _EXPORTS:
        raise HTTPException(400, f"Unknown format '{fmt}'.")
    job = _job(job_id)
    doc = _doc(job)
    media, suffix = _EXPORTS[fmt]
    stem = Path(job.filename).stem or job.id
    path = OUT / f"{job.id}{suffix}"

    if fmt == "md":
        path.write_text(render(doc), encoding="utf-8")
    elif fmt == "mdflat":
        path.write_text(render_markdown_flat(doc), encoding="utf-8")
    elif fmt == "html":
        path.write_text(render_html(doc, title=stem), encoding="utf-8")
    elif fmt == "txt":
        path.write_text(render_text(doc), encoding="utf-8")
    elif fmt == "docx":
        write_docx(doc, path)
    elif fmt == "xlsx":
        write_xlsx(doc, path)

    return FileResponse(path, filename=f"{stem}{suffix}", media_type=media)


@app.get("/api/jobs/{job_id}/preview", response_class=HTMLResponse)
def job_preview(job_id: str) -> str:
    """Sanitised HTML fragment for the in-page rendered preview."""
    from ..render.html import render_html

    doc = _doc(_job(job_id))
    # Figures render as live crops from the API, not dead file references.
    full = render_html(
        doc,
        show_flags=True,
        crop_url=f"/api/jobs/{job_id}/blocks/{{id}}/crop.png",
    )
    start, end = full.find("<main>"), full.rfind("</main>")
    return full[start + 6 : end] if start >= 0 and end > start else full


@app.get("/api/jobs/{job_id}/blocks/{block_id}/crop.png")
def block_crop(job_id: str, block_id: str) -> FileResponse:
    """Crop a block's bbox from the cached page -- the region, not the page."""
    from ..ingest.rasterize import crop_png

    doc = _doc(_job(job_id))
    blk = doc.by_id(block_id)
    if blk is None:
        raise HTTPException(404, "No such block.")
    page = next((p for p in doc.pages if p.index == blk.page), None)
    if page is None:
        raise HTTPException(404, "No such page.")

    cache = PageCache(STORE)
    src = cache.pages_dir / f"{page.sha256}.png"
    if not src.exists():
        raise HTTPException(404, "Cached page image missing.")
    if blk.bbox.width <= 1 or blk.bbox.height <= 1:
        return FileResponse(src, media_type="image/png")

    dest = cache.root / "crops" / f"{job_id}-{block_id}-v{blk.version}.png"
    if not dest.exists():
        crop_png(src, blk.bbox.as_tuple(), dest, pad_frac=0.02)
    return FileResponse(dest, media_type="image/png")


@app.get("/api/pages/{sha}.png")
def page_image(sha: str) -> FileResponse:
    # Guard against path traversal: only hex sha256 names are valid.
    if len(sha) != 64 or not all(c in "0123456789abcdef" for c in sha):
        raise HTTPException(400, "Bad page id.")
    path = PageCache(STORE).pages_dir / f"{sha}.png"
    if not path.exists():
        raise HTTPException(404, "No such page.")
    return FileResponse(path, media_type="image/png")


@app.post("/api/jobs/{job_id}/blocks/{block_id}/redo")
def api_redo(
    job_id: str,
    block_id: str,
    table_format: str = Form("markdown"),
    pad: float = Form(0.02),
    dpi: int = Form(0),
    bbox: str = Form(""),
) -> dict[str, object]:
    """Re-OCR a single block. Costs one page-billing unit.

    `bbox` ("x0,y0,x1,y1"), `pad` and `dpi` all change what the model sees.
    At least one should differ from the original, since re-sending identical
    pixels to a deterministic model reproduces the identical error.
    """
    job = _job(job_id)
    doc = _doc(job)

    override: tuple[float, float, float, float] | None = None
    if bbox.strip():
        try:
            parts = [float(v) for v in bbox.split(",")]
            if len(parts) != 4:
                raise ValueError
            override = (parts[0], parts[1], parts[2], parts[3])
        except ValueError:
            raise HTTPException(400, "bbox must be 'x0,y0,x1,y1'") from None

    try:
        text = redo_block(
            doc,
            block_id,
            PageCache(STORE),
            table_format=table_format,
            pad_frac=max(0.0, min(0.5, pad)),
            bbox_override=override,
            dpi=dpi or None,
        )
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"{type(e).__name__}: {e}") from e

    _persist(job, doc)
    blk = doc.by_id(block_id)
    return {"id": block_id, "text": text, "version": blk.version if blk else 0}


@app.post("/api/jobs/{job_id}/blocks/{block_id}/lock")
def api_lock(job_id: str, block_id: str, locked: bool = Form(True)) -> dict[str, object]:
    """Lock/unlock a block against automated overwriting. Free."""
    job = _job(job_id)
    doc = _doc(job)
    try:
        set_locked(doc, block_id, locked)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    _persist(job, doc)
    return {"id": block_id, "locked": locked}


@app.post("/api/jobs/{job_id}/links/merge")
def api_force_merge(
    job_id: str,
    from_block: str = Form(...),
    to_block: str = Form(...),
    kind: str = Form("sentence"),
) -> dict[str, object]:
    """Create a stitch the automatic pass missed. Free -- no API call."""
    job = _job(job_id)
    doc = _doc(job)
    try:
        link = force_merge(doc, from_block, to_block, kind=kind)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e)) from e
    _persist(job, doc)
    return {"from": link.from_block, "to": link.to_block, "kind": link.kind}


@app.post("/api/jobs/{job_id}/blocks/{block_id}/text")
def api_set_text(job_id: str, block_id: str, text: str = Form(...)) -> dict[str, object]:
    job = _job(job_id)
    doc = _doc(job)
    try:
        set_block_text(doc, block_id, text)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    _persist(job, doc)
    return {"id": block_id, "ok": True}


@app.post("/api/jobs/{job_id}/links/{from_block}/toggle")
def api_toggle_link(job_id: str, from_block: str) -> dict[str, object]:
    """Accept/reject a stitching decision and re-render."""
    job = _job(job_id)
    doc = _doc(job)
    for l in doc.links:
        if l.from_block == from_block:
            l.accepted = not l.accepted
            _persist(job, doc)
            return {"from": from_block, "accepted": l.accepted}
    raise HTTPException(404, "No such link.")


def _job(job_id: str) -> Job:
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job.")
    return job


def _doc(job: Job) -> Document:
    if job.doc is None:
        raise HTTPException(409, "Job has no document yet.")
    return job.doc


def _persist(job: Job, doc: Document) -> None:
    job.markdown = render(doc)
    (OUT / f"{job.id}.md").write_text(job.markdown, encoding="utf-8")
    (OUT / f"{job.id}.ir.json").write_text(doc.model_dump_json(indent=2), encoding="utf-8")
