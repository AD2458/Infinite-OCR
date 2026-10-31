"""Offline demo: the full pipeline on recorded responses, zero API credits.

Run:  .venv\\Scripts\\python.exe demo_offline.py

Demonstrates the four claims in the project brief:
  1. one continuous, structure-aware output for the whole document
  2. page-break sentences and tables reconstruct correctly
  3. a reviewer can flag a region and only that part is redone
  4. multi-script content needs no language configuration anywhere
"""

from __future__ import annotations

from pathlib import Path

from infinite_ocr.client.cache import PageCache
from infinite_ocr.ingest.rasterize import rasterize
from infinite_ocr.pipeline import build_document
from infinite_ocr.render.markdown import render
from infinite_ocr.review.confidence import quality_report, review_queue
from infinite_ocr.review.redo import reject_link, set_block_text
from tests.make_fixture_pdf import build
from tests.test_pipeline import PAGE0, PAGE1, PAGE2

STORE = Path("store")


def main() -> None:
    pdf = build(Path("tests/fixtures/multiscript.pdf"))
    cache = PageCache(STORE)
    pages = rasterize(pdf, cache.pages_dir)

    # Stand in for the API: in a real run these come from indic-ocr and are
    # cached by sha256, so each page is billed exactly once, ever.
    responses = {}
    for p, payload in zip(pages, [PAGE0, PAGE1, PAGE2]):
        cache.put(p.sha256, payload)
        responses[p.sha256] = payload

    doc = build_document(pdf, pages, responses, doc_id="demo")

    print("=" * 72)
    print("1. CONTINUOUS OUTPUT")
    print("=" * 72)
    print(render(doc, with_anchors=False, bidi=False))

    print("=" * 72)
    print("2. STITCHING DECISIONS (each one auditable and rejectable)")
    print("=" * 72)
    for l in doc.links:
        print(f"  [{l.kind:8}] {l.from_block} -> {l.to_block}  "
              f"conf={l.confidence}\n             reason: {l.reason}")

    print()
    print("=" * 72)
    print("3. TARGETED CORRECTION -- only one region changes")
    print("=" * 72)
    before = render(doc, with_anchors=False, bidi=False).splitlines()
    title = next(b for b in doc.blocks if b.type == "Title")
    set_block_text(doc, title.id, "अध्याय ७: निर्देशांक ज्यामिति (सुधारा हुआ)")
    after = render(doc, with_anchors=False, bidi=False).splitlines()
    changed = [i for i, (x, y) in enumerate(zip(before, after)) if x != y]
    print(f"  corrected {title.id} -> v{title.version} (source={title.source})")
    print(f"  lines changed in the whole document: {len(changed)} {changed}")

    print()
    print("=" * 72)
    print("4. MULTILINGUAL METADATA (no language was ever configured)")
    print("=" * 72)
    for b in doc.flow_blocks():
        if b.text.strip():
            top = ", ".join(f"{k}:{v:.2f}" for k, v in list(b.scripts.items())[:3])
            print(f"  {b.id}  {b.type:<14} {b.primary_script} [{b.direction}]  {top}")

    print()
    print("=" * 72)
    print("5. REVIEW QUEUE + QUALITY")
    print("=" * 72)
    print(" ", quality_report(doc))
    for item in review_queue(doc)[:5]:
        print(f"  {item['id']} {item['flags']}")

    print()
    print("=" * 72)
    print("6. REJECTING A BAD MERGE (reviewer disagrees with the stitcher)")
    print("=" * 72)
    tbl = next((l for l in doc.links if l.kind == "table"), None)
    if tbl:
        merged = render(doc, with_anchors=False, bidi=False)
        reject_link(doc, tbl.from_block)
        unmerged = render(doc, with_anchors=False, bidi=False)
        print(f"  merged:   {merged.count('<th>')} header cells")
        print(f"  unmerged: {unmerged.count('<th>')} header cells (link rejected)")

    print()
    print("Cache:", cache.stats(), "<- these pages are never paid for again")


if __name__ == "__main__":
    main()
