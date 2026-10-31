"""Table extraction: HTML/Markdown fragments -> row grids.

Shared by the DOCX, CSV and XLSX exporters. Expands rowspan/colspan into a
dense grid, because spreadsheets and Word tables need every cell addressed
even when the source markup merges them.
"""

from __future__ import annotations

import re

from lxml import html as lxml_html

from ..ir.models import Document
from ..stitch.tables import table_rows
from .inline import find_pipe_table

_HTML_TABLE_RE = re.compile(r"<table\b.*?</table\s*>", re.IGNORECASE | re.DOTALL)


def _cell_text(cell) -> str:
    """Extract cell text, converting <br> to newlines.

    `text_content()` drops <br/>, so "Sector<br/>துறை" becomes "Sectorதுறை"
    with the two languages run together. Bilingual cells put the
    translation on the next line, so each <br> becomes a newline.
    """
    from lxml import etree

    parts: list[str] = []
    for node in cell.iter():
        if node.tag in ("br",):
            parts.append("\n")
        if node.text:
            parts.append(node.text)
        if node.tail and node != cell:
            parts.append(node.tail)
    # cell's own text is already captured via iter(); just need cell.tail excluded
    text = "".join(parts).strip()
    # Collapse runs of spaces within each line, but keep the newlines.
    return "\n".join(" ".join(line.split()) for line in text.split("\n"))


def grid_from_html(fragment: str) -> list[list[str]]:
    """Expand an HTML table into a dense grid.

    Spanned cells put their text in the top-left position and leave the rest
    empty, so text is never duplicated across columns and CSV/XLSX/DOCX
    consumers can still see the covered range from the empty neighbours.
    """
    try:
        root = lxml_html.fragment_fromstring(fragment, create_parent="div")
        table = root.find(".//table")
        if table is None:
            raise ValueError
    except Exception:
        return table_rows(fragment) or []

    grid: list[list[str]] = []
    occupied: dict[tuple[int, int], str] = {}
    for r, tr in enumerate(table.iter("tr")):
        c = 0
        for cell in tr.iter("td", "th"):
            while (r, c) in occupied:
                c += 1
            text = _cell_text(cell)
            try:
                rs = max(1, int(cell.get("rowspan", 1)))
                cs = max(1, int(cell.get("colspan", 1)))
            except ValueError:
                rs = cs = 1
            # Text goes only in the anchor cell; spanned cells are empty.
            occupied[(r, c)] = text
            for dr in range(rs):
                for dc in range(cs):
                    if (dr, dc) != (0, 0):
                        occupied.setdefault((r + dr, c + dc), "")
            c += cs

    if not occupied:
        return table_rows(fragment) or []

    rows = max(k[0] for k in occupied) + 1
    cols = max(k[1] for k in occupied) + 1
    for r in range(rows):
        grid.append([occupied.get((r, c), "") for c in range(cols)])
    return grid


def grids_in_text(text: str) -> list[list[list[str]]]:
    """Every table found in a text fragment, whatever its format.

    Searches by *content* rather than trusting the block's declared type: the
    model has been observed returning a Markdown pipe table inside a block
    typed `Text`/`List`, and such tables were being exported as raw prose.
    """
    if not text or not text.strip():
        return []

    if "<table" in text.lower():
        grids = []
        for m in _HTML_TABLE_RE.finditer(text):
            g = grid_from_html(m.group(0))
            if g:
                grids.append(g)
        return grids or ([grid_from_html(text)] if grid_from_html(text) else [])

    lines = text.splitlines()
    grids = []
    i = 0
    while i < len(lines):
        found = find_pipe_table(lines, i)
        if found is None:
            i += 1
            continue
        nxt, rows = found
        grids.append(rows)
        i = nxt
    return grids


def document_tables(doc: Document) -> list[tuple[str, list[list[str]]]]:
    """Every table in the document as (block_id, grid).

    Cross-page merges are already applied in the IR, so a table split across
    pages appears here once, whole. All block types are inspected, because a
    table can arrive inside a block the model typed as prose.
    """
    from ..stitch.levels import join_blocks

    links = doc.accepted_links()
    consumed: set[str] = set()
    out: list[tuple[str, list[list[str]]]] = []

    for block in doc.flow_blocks():
        if block.id in consumed:
            continue
        text = block.text
        cursor = block
        while (link := links.get(cursor.id)) is not None:
            nxt = doc.by_id(link.to_block)
            if nxt is None or nxt.id in consumed:
                break
            text = join_blocks(link, text, nxt.text)
            consumed.add(nxt.id)
            cursor = nxt
        for n, grid in enumerate(grids_in_text(text)):
            suffix = "" if n == 0 else f"-{n + 1}"
            out.append((f"{block.id}{suffix}", grid))
    return out


def to_markdown_table(grid: list[list[str]]) -> str:
    """Render a grid as a Markdown pipe table (lossy on merged cells)."""
    if not grid:
        return ""
    width = max(len(r) for r in grid)
    padded = [r + [""] * (width - len(r)) for r in grid]
    head, *body = padded
    esc = lambda c: c.replace("|", "\\|")  # noqa: E731
    lines = ["| " + " | ".join(esc(c) for c in head) + " |"]
    lines.append("| " + " | ".join("---" for _ in head) + " |")
    for r in body:
        lines.append("| " + " | ".join(esc(c) for c in r) + " |")
    return "\n".join(lines)
