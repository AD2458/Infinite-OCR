"""Cross-page stitching -- what makes the output continuous."""

from .levels import join_blocks
from .links import apply_links, link_summary, propose_links
from .lists import join_lists, propose_list_link
from .sentences import join_text, propose_sentence_link
from .tables import merge_tables, propose_table_link, table_rows

__all__ = [
    "apply_links", "join_blocks", "join_lists", "join_text", "link_summary",
    "merge_tables", "propose_links", "propose_list_link",
    "propose_sentence_link", "propose_table_link", "table_rows",
]
