"""Stitch orchestrator: propose links at every page boundary.

Only adjacent blocks straddling a page break are considered -- within a page
the model's structure is already correct.

Precedence is table > list > sentence: a table merge is the most specific
and the most damaging to get wrong, so it is tested first.
"""

from __future__ import annotations

from ..ir.models import Block, Document, Link
from .lists import propose_list_link
from .sentences import propose_sentence_link
from .tables import propose_table_link


def _col_right(blocks: list[Block]) -> float | None:
    """Right margin of the text column on a page, for the geometric signal."""
    xs = [b.bbox.x1 for b in blocks if b.text.strip() and b.bbox.x1 > 0]
    return max(xs) if xs else None


# Non-prose blocks that may sit between the content and the page edge.
_NOT_A_CANDIDATE = frozenset({"Picture", "Caption", "Footnote"})


def _stitch_candidates(blocks: list[Block]) -> tuple[Block | None, Block | None]:
    """Last and first stitchable blocks on a page.

    Scans inward past furniture and non-prose rather than taking the last or
    first element -- a trailing footnote or signature would otherwise be
    mistaken for the candidate and block every merge at that boundary.
    """
    usable = [
        b
        for b in blocks
        if b.text.strip() and not b.is_furniture and b.type not in _NOT_A_CANDIDATE
    ]
    if not usable:
        return None, None
    return usable[-1], usable[0]


def propose_links(doc: Document) -> list[Link]:
    """Propose all cross-page links for a document."""
    by_page: dict[int, list[Block]] = {}
    for b in doc.flow_blocks():
        by_page.setdefault(b.page, []).append(b)
    for blocks in by_page.values():
        blocks.sort(key=lambda b: b.order)

    links: list[Link] = []
    pages = sorted(by_page)
    for a, b in zip(pages, pages[1:]):
        if b != a + 1:
            continue  # don't stitch across a gap (e.g. a page-range run)
        prev_blocks, next_blocks = by_page[a], by_page[b]
        if not prev_blocks or not next_blocks:
            continue
        prev, _ = _stitch_candidates(prev_blocks)
        _, nxt = _stitch_candidates(next_blocks)
        if prev is None or nxt is None:
            continue

        link = (
            propose_table_link(prev, nxt)
            or propose_list_link(prev, nxt)
            or propose_sentence_link(prev, nxt, prev_col_right=_col_right(prev_blocks))
        )
        if link is not None:
            links.append(link)
    return links


def apply_links(doc: Document) -> Document:
    """Attach proposed links to the document (idempotent)."""
    doc.links = propose_links(doc)
    return doc


def link_summary(doc: Document) -> dict[str, int]:
    out: dict[str, int] = {"sentence": 0, "table": 0, "list": 0, "rejected": 0}
    for l in doc.links:
        if not l.accepted:
            out["rejected"] += 1
            continue
        out[l.kind] = out.get(l.kind, 0) + 1
    return out
