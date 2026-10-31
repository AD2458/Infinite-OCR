"""Infinite OCR command line."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from dotenv import load_dotenv

from .client.cache import PageCache
from .ir.models import Document
from .render.markdown import render
from .review.confidence import quality_report, review_queue
from .review.redo import redo_block, reject_link, set_block_text

app = typer.Typer(add_completion=False, help="Infinite OCR -- whole-PDF, structure-aware OCR.")
load_dotenv()


@app.command()
def run(
    pdf: Path = typer.Argument(..., exists=True, help="Input PDF."),
    out: Path = typer.Option("out/document.md", "--out", "-o"),
    store: Path = typer.Option("store", "--store"),
    concurrency: int = typer.Option(4, "--concurrency", "-c"),
    table_format: str = typer.Option("html", "--table-format", help="html|markdown"),
    pages: str = typer.Option("", "--pages", "-p",
                              help="Page spec: '1-3,7,10-12'. Empty = all."),
) -> None:
    """OCR a PDF into one continuous, structure-aware document."""
    from .pipeline import run as run_pipeline

    doc, markdown = run_pipeline(
        pdf,
        store=store,
        out=out,
        concurrency=concurrency,
        table_format=table_format,
        page_spec=pages,
    )
    rep = quality_report(doc)
    typer.echo(f"Wrote {out}  ({len(markdown)} chars)")
    typer.echo(
        f"pages={len(doc.pages)} blocks={rep['total_blocks']} "
        f"links={rep['links']} flagged={rep['flagged_blocks']} "
        f"clean={rep['clean_rate']:.1%} script={rep['doc_script']}"
    )


@app.command()
def review(
    ir: Path = typer.Argument(..., exists=True, help="The .ir.json from `run`."),
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """List blocks that need a human look, worst first."""
    doc = Document.model_validate_json(ir.read_text(encoding="utf-8"))
    queue = review_queue(doc)
    if not queue:
        typer.echo("Nothing flagged -- near-zero cleanup.")
        return
    for item in queue[:limit]:
        typer.echo(
            f"{item['id']}  p{item['page']:<3} {item['type']:<14} "
            f"conf={item['conf']:.2f}  {','.join(item['flags'])}"
        )
        typer.echo(f"    {item['preview']}")
    typer.echo(f"\n{len(queue)} flagged of {len(doc.blocks)} blocks.")


@app.command()
def redo(
    ir: Path = typer.Argument(..., exists=True),
    block: str = typer.Argument(..., help="Block id, e.g. p012-b03."),
    store: Path = typer.Option("store", "--store"),
    out: Path = typer.Option("out/document.md", "--out", "-o"),
    table_format: str = typer.Option("markdown", "--table-format"),
) -> None:
    """Re-OCR one block. Only that region changes."""
    doc = Document.model_validate_json(ir.read_text(encoding="utf-8"))
    text = redo_block(doc, block, PageCache(store), table_format=table_format)
    typer.echo(f"{block} -> v{doc.by_id(block).version}:\n{text[:400]}")
    _persist(doc, ir, out)


@app.command("set-text")
def set_text(
    ir: Path = typer.Argument(..., exists=True),
    block: str = typer.Argument(...),
    text: str = typer.Argument(..., help="Corrected text (pinned as human)."),
    out: Path = typer.Option("out/document.md", "--out", "-o"),
) -> None:
    """Pin a manual correction; a later redo will not overwrite it."""
    doc = Document.model_validate_json(ir.read_text(encoding="utf-8"))
    set_block_text(doc, block, text)
    typer.echo(f"{block} pinned as human-corrected.")
    _persist(doc, ir, out)


@app.command("reject-link")
def reject(
    ir: Path = typer.Argument(..., exists=True),
    from_block: str = typer.Argument(..., help="The link's from_block id."),
    out: Path = typer.Option("out/document.md", "--out", "-o"),
) -> None:
    """Reject a stitching decision (e.g. a wrong table merge)."""
    doc = Document.model_validate_json(ir.read_text(encoding="utf-8"))
    if reject_link(doc, from_block):
        typer.echo(f"Rejected link from {from_block}; re-rendering unmerged.")
        _persist(doc, ir, out)
    else:
        typer.echo(f"No link from {from_block}.")


@app.command()
def export(
    ir: Path = typer.Argument(..., exists=True, help="The .ir.json from `run`."),
    fmt: str = typer.Option("html", "--format", "-f",
                            help="html|docx|xlsx|csv|md|mdflat|txt"),
    out: Path = typer.Option(None, "--out", "-o"),
) -> None:
    """Export to a readable format. Free -- pure post-processing, no credits."""
    from .render.exports import (
        render_markdown_flat,
        render_text,
        write_csvs,
        write_docx,
        write_xlsx,
    )
    from .render.html import render_html

    doc = Document.model_validate_json(ir.read_text(encoding="utf-8"))
    stem = ir.name.replace(".ir.json", "")
    dest = out or Path("out") / f"{stem}.{ 'flat.md' if fmt=='mdflat' else fmt }"
    dest.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "html":
        dest.write_text(render_html(doc, title=stem), encoding="utf-8")
    elif fmt == "md":
        dest.write_text(render(doc), encoding="utf-8")
    elif fmt == "mdflat":
        dest.write_text(render_markdown_flat(doc), encoding="utf-8")
    elif fmt == "txt":
        dest.write_text(render_text(doc), encoding="utf-8")
    elif fmt == "docx":
        write_docx(doc, dest)
    elif fmt == "xlsx":
        write_xlsx(doc, dest)
    elif fmt == "csv":
        files = write_csvs(doc, dest if out else Path("out") / "csv", stem="table")
        typer.echo(f"Wrote {len(files)} CSV file(s).")
        for f in files:
            typer.echo(f"  {f}")
        return
    else:
        typer.secho(f"Unknown format '{fmt}'.", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.echo(f"Wrote {dest}")


@app.command()
def check() -> None:
    """Verify the API key is loaded and valid. Costs nothing.

    Sends a 1x1 image, which the model rejects as undecodable -- that is a
    *successful* auth check: a 401 means the key is wrong, anything else
    means the key was accepted. Run this before spending real credits.
    """
    import base64
    import os

    key = os.environ.get("BODHAN_API_KEY")
    if not key:
        typer.secho("BODHAN_API_KEY not found.", fg=typer.colors.RED)
        typer.echo("Create a .env file in the project root containing:")
        typer.echo("  BODHAN_API_KEY=your_key_here")
        raise typer.Exit(1)

    typer.echo(f"Key loaded: {key[:4]}...{key[-4:]} (length {len(key)})")

    import httpx

    # Smallest possible PNG: 1x1 transparent pixel.
    tiny = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )
    body = {
        "model": "indic-ocr",
        "messages": [{"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64," + base64.b64encode(tiny).decode()}}
        ]}],
    }
    try:
        r = httpx.post(
            "https://api.bodhan.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=body,
            timeout=60.0,
        )
    except Exception as e:  # noqa: BLE001
        typer.secho(f"Network error: {e}", fg=typer.colors.RED)
        raise typer.Exit(1)

    if r.status_code == 401:
        typer.secho("401 -- key rejected. Check it in the Bodhan console.", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.secho(f"Key accepted (HTTP {r.status_code}).", fg=typer.colors.GREEN)
    for h in ("x-ratelimit-limit-requests", "x-ratelimit-limit-parallel-requests"):
        if h in r.headers:
            typer.echo(f"  {h}: {r.headers[h]}")
    typer.echo("Ready. Try a small range first:  run <pdf> --first 0 --last 2")


@app.command()
def stats(store: Path = typer.Option("store", "--store")) -> None:
    """Show cache stats -- cached pages are pages you never pay for again."""
    typer.echo(json.dumps(PageCache(store).stats(), indent=2))


def _persist(doc: Document, ir: Path, out: Path) -> None:
    ir.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(doc), encoding="utf-8")
    typer.echo(f"Updated {ir} and {out}")


if __name__ == "__main__":
    app()
