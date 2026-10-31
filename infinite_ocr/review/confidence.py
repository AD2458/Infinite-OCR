"""Automatic quality triage.

Lets a reviewer read 5% of a document instead of 100%. Every check runs on
cached data, so tuning thresholds costs no credits.

Script consistency is the highest-value signal and has no monolingual
equivalent: a block departing sharply from the document's dominant script is
usually garbled rather than legitimately multilingual.
"""

from __future__ import annotations

from ..ir.models import Block, Document
from ..script.detect import dominant_doc_script, script_outlier_score
from ..stitch.tables import table_rows

LOW_CONF = 0.60
SCRIPT_OUTLIER = 0.45

# Characters that signal a decoding failure rather than real content.
_BAD_CHARS = ("\ufffd", "\x00")


def flag_blocks(doc: Document, *, low_conf: float = LOW_CONF) -> Document:
    """Annotate blocks with quality flags, in place."""
    doc.doc_script = dominant_doc_script([b.text for b in doc.blocks if b.is_flow])

    for b in doc.blocks:
        if b.conf < low_conf:
            b.flag("low_conf")

        if any(ch in b.text for ch in _BAD_CHARS):
            b.flag("replacement_chars")

        # Script consistency -- only meaningful for prose blocks.
        if b.type in ("Text", "List", "Title", "SectionHeader") and b.text.strip():
            if script_outlier_score(b.scripts, doc.doc_script) > SCRIPT_OUTLIER:
                b.flag("script_mix")

        if b.type == "Table":
            _flag_table(b)

        if b.type == "Picture" and "needs_alt_text" not in b.flags:
            b.flag("needs_alt_text")

    # Low-confidence stitching decisions deserve a human look.
    for l in doc.links:
        if l.confidence < 0.7:
            if (blk := doc.by_id(l.from_block)) is not None:
                blk.flag(f"uncertain_{l.kind}_link")
    return doc


def _flag_table(b: Block) -> None:
    rows = table_rows(b.text)
    if not rows:
        b.flag("table_unparseable")
        return
    lengths = {len(r) for r in rows}
    if len(lengths) > 1:
        b.flag("table_ragged")


def review_queue(doc: Document) -> list[dict[str, object]]:
    """Blocks needing attention, worst first."""
    items = [
        {
            "id": b.id,
            "page": b.page,
            "type": b.type,
            "conf": b.conf,
            "flags": list(b.flags),
            "preview": b.text[:120],
        }
        for b in doc.blocks
        if b.flags
    ]
    items.sort(key=lambda d: (len(d["flags"]), -float(d["conf"])), reverse=True)  # type: ignore[arg-type]
    return items


def quality_report(doc: Document) -> dict[str, object]:
    total = len(doc.blocks)
    flagged = sum(1 for b in doc.blocks if b.flags)
    return {
        "total_blocks": total,
        "flagged_blocks": flagged,
        "clean_rate": round(1 - flagged / total, 3) if total else 1.0,
        "doc_script": doc.doc_script,
        "links": len(doc.links),
    }
