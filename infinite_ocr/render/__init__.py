"""Deterministic rendering and export from the IR."""

from .exports import (
    render_markdown_flat,
    render_text,
    write_csvs,
    write_docx,
    write_xlsx,
)
from .html import render_html
from .markdown import anchor, render, render_block, render_with_report
from .sanitize import sanitize_html, strip_tags
from .tables import document_tables, grid_from_html, to_markdown_table
from .unwrap import unwrap_lines

__all__ = [
    "anchor", "render", "render_block", "render_with_report",
    "render_html", "render_markdown_flat", "render_text",
    "write_csvs", "write_docx", "write_xlsx",
    "sanitize_html", "strip_tags",
    "document_tables", "grid_from_html", "to_markdown_table",
    "unwrap_lines",
]
