"""Apply a link by joining two blocks' text with the right strategy."""

from __future__ import annotations

from ..ir.models import Link
from .lists import join_lists
from .sentences import join_text
from .tables import merge_tables


def join_blocks(link: Link, prev_text: str, next_text: str) -> str:
    """Join per the link kind. Unknown kinds fall back to a paragraph break."""
    if link.kind == "table":
        return merge_tables(prev_text, next_text)
    if link.kind == "list":
        return join_lists(prev_text, next_text)
    if link.kind == "sentence":
        return join_text(prev_text, next_text)
    return prev_text + "\n\n" + next_text
