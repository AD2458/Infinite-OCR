"""Cross-page list continuation.

Numbering must be read with Unicode-aware digits: a Devanagari "१२." is as
valid a list marker as "12.", and an ASCII-only regex would miss every
numbered list in a Hindi or Marathi document.
"""

from __future__ import annotations

from ..ir.models import Block, Link
from ..script.numerals import list_marker_value, starts_with_bullet


def _last_marker(text: str) -> int | None:
    for line in reversed(text.strip().splitlines()):
        v = list_marker_value(line)
        if v is not None:
            return v
    return None


def _first_marker(text: str) -> int | None:
    for line in text.strip().splitlines():
        v = list_marker_value(line)
        if v is not None:
            return v
    return None


def propose_list_link(prev: Block, nxt: Block) -> Link | None:
    """Propose merging a list split across a page break, or None."""
    if prev.type not in ("List", "Text") or nxt.type not in ("List", "Text"):
        return None
    if not prev.text.strip() or not nxt.text.strip():
        return None

    last = _last_marker(prev.text)
    first = _first_marker(nxt.text)

    if last is not None and first is not None:
        if first == last + 1:
            return Link(
                kind="list",
                from_block=prev.id,
                to_block=nxt.id,
                confidence=0.95,
                reason=f"numbering continues ({last} -> {first})",
            )
        return None

    # Bullet lists carry no ordinal, so require both sides to be bullets and
    # both blocks to be typed as lists -- a weaker, more cautious signal.
    if (
        prev.type == "List"
        and nxt.type == "List"
        and starts_with_bullet(nxt.text.strip().splitlines()[0])
    ):
        return Link(
            kind="list",
            from_block=prev.id,
            to_block=nxt.id,
            confidence=0.6,
            reason="bullet list continues across the page break",
        )
    return None


def join_lists(prev_text: str, next_text: str) -> str:
    return prev_text.rstrip() + "\n" + next_text.lstrip()
