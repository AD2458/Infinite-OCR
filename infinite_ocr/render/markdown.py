"""Render the IR as one continuous Markdown document.

The renderer is a pure function of the IR, which is what makes a targeted
redo safe: fixing one block changes exactly one region of the output.

Anchors (`<!-- blk:p012-b03 v2 -->`) are invisible once rendered but let
edits round-trip back to the IR node that produced them.
"""

from __future__ import annotations

from ..ir.models import Block, Document
from ..script.detect import RTL_SCRIPTS
from ..stitch.levels import join_blocks
from .unwrap import unwrap_lines

# Unicode bidi isolates. Without these, an Urdu paragraph containing Latin
# acronyms or ASCII numerals renders visually scrambled.
RLI = "\u2067"  # right-to-left isolate
LRI = "\u2066"  # left-to-right isolate
PDI = "\u2069"  # pop directional isolate

_HEADING_LEVEL = {"Title": "# ", "SectionHeader": "## ", "Caption": "", "Footnote": "> "}


def anchor(block: Block) -> str:
    return f"<!-- blk:{block.id} v{block.version} -->"


def _wrap_bidi(text: str, script: str) -> str:
    """Isolate an RTL run so surrounding LTR content renders correctly."""
    if script in RTL_SCRIPTS and text.strip():
        return f"{RLI}{text}{PDI}"
    return text


def render_block(block: Block, *, with_anchors: bool = True, bidi: bool = True) -> str:
    """Render one block to Markdown."""
    text = block.text.strip()

    # A figure carries no text. Emit a readable marker rather than a Markdown
    # image link to a file that is never written -- that only ever rendered
    # as a broken image.
    if block.type == "Picture":
        label = text or f"Figure — page {block.page + 1}"
        return f"{anchor(block)}\n_[{label}]_" if with_anchors else f"_[{label}]_"

    if not text:
        return ""

    if bidi:
        text = _wrap_bidi(text, block.primary_script)

    if block.type in ("Title", "SectionHeader"):
        text = _HEADING_LEVEL[block.type] + text.replace("\n", " ")
    elif block.type == "Footnote":
        text = "> " + unwrap_lines(text, block.primary_script).replace("\n", "\n> ")
    elif block.type != "Table":
        # Table markup carries deliberate <br/> inside cells; prose does not,
        # so only prose gets its visual line-wrap undone.
        text = unwrap_lines(text, block.primary_script)

    return f"{anchor(block)}\n{text}" if with_anchors else text


def render(doc: Document, *, with_anchors: bool = True, bidi: bool = True) -> str:
    """Render the whole document as one continuous, stitched Markdown string.

    Linked blocks are fused into a single unit so that a sentence or table
    split by a page break emerges as one paragraph or one table -- the page
    boundary leaves no trace in the output.
    """
    blocks = doc.flow_blocks()
    if not blocks:
        return ""

    links = doc.accepted_links()
    consumed: set[str] = set()
    parts: list[str] = []

    # Label each selected page range, so disjoint selections are never
    # mistaken for one continuous passage.
    page_group = {p.index: p.group for p in doc.pages}
    group_pages: dict[int, list[int]] = {}
    for p in doc.pages:
        group_pages.setdefault(p.group, []).append(p.index)
    multi_group = len(group_pages) > 1
    seen_group: int | None = None

    for i, block in enumerate(blocks):
        if block.id in consumed:
            continue

        grp = page_group.get(block.page, 0)
        if multi_group and grp != seen_group:
            seen_group = grp
            idxs = group_pages.get(grp, [])
            if idxs:
                lo, hi = min(idxs) + 1, max(idxs) + 1
                label = f"Pages {lo}–{hi}" if lo != hi else f"Page {lo}"
                parts.append(f"<!-- range: {label} -->\n\n---\n\n**{label}**")

        # Follow the link chain: a table may span three or more pages.
        text = block.text
        head = block
        cursor = block
        while (link := links.get(cursor.id)) is not None:
            nxt = doc.by_id(link.to_block)
            if nxt is None or nxt.id in consumed:
                break
            text = join_blocks(link, text, nxt.text)
            consumed.add(nxt.id)
            cursor = nxt

        merged = head.model_copy(update={"text": text})
        rendered = render_block(merged, with_anchors=with_anchors, bidi=bidi)
        if rendered:
            parts.append(rendered)

    return "\n\n".join(parts).strip() + "\n"


def render_with_report(doc: Document) -> tuple[str, dict[str, object]]:
    """Render plus a summary a reviewer can act on."""
    md = render(doc)
    flagged = [
        {"id": b.id, "page": b.page, "conf": b.conf, "flags": b.flags}
        for b in doc.blocks
        if b.flags
    ]
    return md, {
        "doc_id": doc.doc_id,
        "pages": len(doc.pages),
        "blocks": len(doc.blocks),
        "links": [
            {
                "kind": l.kind,
                "from": l.from_block,
                "to": l.to_block,
                "confidence": l.confidence,
                "reason": l.reason,
            }
            for l in doc.links
        ],
        "flagged_blocks": flagged,
        "doc_script": doc.doc_script,
    }
