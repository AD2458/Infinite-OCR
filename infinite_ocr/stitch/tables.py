"""Cross-page table reconstruction -- the project's headline requirement.

A table split by a page break arrives as two independent `Table` blocks the
model never sees together, so merging them is pure post-processing.

A false merge silently corrupts data and is worse than a missed one, so we
require positive agreement on column count and drop the repeated header.

`table_format` defaults to `html`: it preserves merged cells via
rowspan/colspan, needed for multi-level Indian exam/budget/census headers.
"""

from __future__ import annotations

import re

from lxml import html as lxml_html

from ..ir.models import Block, Link
from ..script.detect import primary_script
from ..script.normalize import comparable
from ..script.numerals import to_ascii_digits
from ..script.terminators import ends_sentence, ends_with_latin_hyphen


def _parse_rows(fragment: str) -> list[list[str]] | None:
    """Extract rows as cell-text lists, or None if not parseable HTML.

    Both colspan and rowspan are expanded so every row reports the true grid
    width. Without rowspan handling a cell spanning 3 rows makes those rows
    look 1 column narrower, which triggers a false column-count mismatch and
    blocks a valid cross-page merge.
    """
    if "<" not in fragment:
        return None
    try:
        root = lxml_html.fragment_fromstring(fragment, create_parent="div")
    except Exception:
        return None
    table = root.find(".//table")
    if table is None:
        return None

    # pending[col] = remaining rows that column is still occupied by a rowspan.
    pending: dict[int, int] = {}
    rows: list[list[str]] = []

    for tr in table.iter("tr"):
        cells: list[str] = []
        col = 0
        for c in tr.iter("td", "th"):
            # Skip columns still occupied by a previous rowspan.
            while pending.get(col, 0) > 0:
                cells.append("")
                pending[col] -= 1
                col += 1

            text = " ".join((c.text_content() or "").split())
            try:
                cspan = max(1, int(c.get("colspan", 1)))
            except (TypeError, ValueError):
                cspan = 1
            try:
                rspan = max(1, int(c.get("rowspan", 1)))
            except (TypeError, ValueError):
                rspan = 1

            cells.append(text)
            if rspan > 1:
                pending[col] = rspan - 1
            col += 1
            # Pad colspan with empties.
            for _ in range(cspan - 1):
                cells.append("")
                if rspan > 1:
                    pending[col] = rspan - 1
                col += 1

        # Drain any trailing rowspan placeholders.
        while pending.get(col, 0) > 0:
            cells.append("")
            pending[col] -= 1
            col += 1

        if cells:
            rows.append(cells)
    return rows or None


def _effective_cols(rows: list[list[str]]) -> int:
    """True column count of a table.

    Takes the widest row rather than the modal one: `_parse_rows` already
    pads colspan cells, so the widest row is the real grid width, and a
    short trailing row cannot drag the count down.
    """
    if not rows:
        return 0
    return max(len(r) for r in rows)


def _md_rows(fragment: str) -> list[list[str]] | None:
    """Parse a Markdown pipe table into rows, skipping the separator line."""
    lines = [l.strip() for l in fragment.strip().splitlines() if l.strip()]
    if not lines or not all(l.startswith("|") or "|" in l for l in lines):
        return None
    rows: list[list[str]] = []
    for line in lines:
        if re.fullmatch(r"[\s|:\-]+", line):  # separator row
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows.append(cells)
    return rows or None


def table_rows(fragment: str) -> list[list[str]] | None:
    return _parse_rows(fragment) or _md_rows(fragment)


def _is_row_label(cell: str) -> bool:
    """True if a cell looks like a row's leading serial number ('1.', 'ii)').

    Used in two places: to stop a numbered data row being mistaken for a
    repeated header, and to tell a genuine new row from a continuation.
    """
    s = to_ascii_digits(cell.strip())
    return bool(re.fullmatch(r"[\(\[]?\s*(?:\d{1,3}|[ivxlcIVXLC]{1,6})\s*[\.\)\]]?", s))


def headers_match(a: list[str], b: list[str]) -> bool:
    """True if two header rows are the same, ignoring script-neutral noise.

    `comparable()` masks digits in any script and folds joiners, so a header
    repeated across pages matches even with OCR-level whitespace variation.
    """
    if len(a) != len(b):
        return False
    norm_a = [comparable(c) for c in a]
    norm_b = [comparable(c) for c in b]
    if not any(norm_a):
        return False

    # Digit masking also makes "1."/"2." compare equal, so without this guard
    # two numbered data rows look like a repeated header and one is DELETED.
    if any(_is_row_label(c) for c in a if c.strip()):
        return False

    same = sum(1 for x, y in zip(norm_a, norm_b) if x == y and x)
    if same < max(1, len(a) - 1):  # tolerate one mis-OCR'd header cell
        return False

    # Require at least one substantial matching cell: single characters and
    # short codes match too easily once digits are masked.
    return any(len(x) >= 3 for x, y in zip(norm_a, norm_b) if x == y and x)


def propose_table_link(prev: Block, nxt: Block) -> Link | None:
    """Propose merging two Table blocks across a page break, or None."""
    if prev.type != "Table" or nxt.type != "Table":
        return None

    rows_a = table_rows(prev.text)
    rows_b = table_rows(nxt.text)
    if not rows_a or not rows_b:
        return None

    cols_a, cols_b = _effective_cols(rows_a), _effective_cols(rows_b)
    if cols_a == 0 or cols_a != cols_b:
        return None  # positive agreement required; otherwise never merge

    reasons = [f"equal column count ({cols_a})"]
    score = 0.6

    if headers_match(rows_a[0], rows_b[0]):
        reasons.append("continuation repeats the header row")
        score += 0.3
    else:
        reasons.append("continuation starts with a data row")
        score += 0.1

    return Link(
        kind="table",
        from_block=prev.id,
        to_block=nxt.id,
        confidence=round(min(1.0, score), 3),
        reason="; ".join(reasons),
    )


def is_cell_continuation(
    last_row: list[str], first_row: list[str]
) -> list[int] | None:
    """Detect a table row whose cells were split by the page break.

    A tall row continues on the next page with several cells filled in
    parallel and the serial column blank; appending it as a new row would
    split one logical row in two.

    Returns the column indices to merge into, or None for a genuine new row.
    The decisive signal is the row label (a continuation never restates it);
    without a label column, the cells above must be unfinished.
    """
    if not last_row or not first_row or len(last_row) != len(first_row):
        return None

    filled = [i for i, c in enumerate(first_row) if c.strip()]
    if not filled or len(filled) == len(first_row):
        # Every column filled -> a complete row of new data.
        return None

    # A continuation never restates the row's serial number, so a filled
    # label column means this is the next row, whatever else it looks like.
    for i in filled:
        if _is_row_label(first_row[i]) and _is_row_label(last_row[i]):
            return None

    # Positive evidence that the text above is unfinished; otherwise a
    # sparse-but-genuine row is swallowed into the one above. Per script, so
    # a danda or Urdu full stop counts as finished.
    unfinished = any(
        last_row[col].strip()
        and not ends_sentence(last_row[col].strip(), primary_script(last_row[col]))
        for col in filled
    )
    if not unfinished:
        return None

    # Only merge columns that actually have content above to attach to;
    # anything else would silently relocate text into the wrong column.
    return [c for c in filled if last_row[c].strip()] or None


def merge_tables(prev_text: str, next_text: str) -> str:
    """Merge two HTML table fragments across a page break.

    Handles three cases: a repeated header row on the continuation page, a
    cell split mid-way by the break, and a plain row-wise continuation.
    """
    rows_a = table_rows(prev_text)
    rows_b = table_rows(next_text)
    if not rows_a or not rows_b:
        return prev_text + "\n\n" + next_text

    body_b = rows_b[1:] if headers_match(rows_a[0], rows_b[0]) else list(rows_b)

    # Stitch split cells back together before appending any rows. Several
    # columns of one row can continue at once, so this carries a mapping
    # rather than a single column.
    carry: dict[int, str] | None = None
    if body_b:
        cols = is_cell_continuation(rows_a[-1], body_b[0])
        if cols:
            carry = {c: body_b[0][c].strip() for c in cols}
            body_b = body_b[1:]

    if "<" in prev_text:
        return _merge_html(prev_text, body_b, carry=carry)

    rows_a = [list(r) for r in rows_a]
    if carry:
        for col, frag in carry.items():
            rows_a[-1][col] = _join_cell(rows_a[-1][col], frag)
    return _merge_markdown(rows_a, body_b)


def _join_cell(head: str, tail: str) -> str:
    """Join two halves of a cell split by a page break."""
    head = head.rstrip()
    tail = tail.lstrip()
    if ends_with_latin_hyphen(head):
        return head[:-1].rstrip() + tail
    return f"{head} {tail}" if head and tail else head + tail


def _merge_html(
    prev_text: str,
    extra_rows: list[list[str]],
    *,
    carry: dict[int, str] | None = None,
) -> str:
    """Append rows to the first table's DOM, preserving its original markup.

    `carry` maps column index -> text belonging to the final row's cell in
    that column, appended in place so cells split by the page break read
    continuously.
    """
    try:
        root = lxml_html.fragment_fromstring(prev_text, create_parent="div")
        table = root.find(".//table")
        if table is None:
            raise ValueError
    except Exception:
        return prev_text

    if carry:
        rows = list(table.iter("tr"))
        if rows:
            cells = list(rows[-1].iter("td", "th"))
            for col, frag in carry.items():
                if col >= len(cells) or not frag:
                    continue
                cell = cells[col]
                # Append to the deepest trailing text node so inline markup
                # (<br/> etc.) inside the cell is preserved.
                if len(cell):
                    tail_el = cell[-1]
                    tail_el.tail = _join_cell(tail_el.tail or "", frag)
                else:
                    cell.text = _join_cell(cell.text or "", frag)

    # Determine the grid width of the first table so continuation rows
    # with fewer cells (e.g. because the OCR omitted the rowspan column)
    # are padded to match.
    prev_rows = _parse_rows(prev_text) or []
    grid_w = _effective_cols(prev_rows) if prev_rows else 0

    target = table.find("tbody")
    if target is None:
        target = table
    for cells in extra_rows:
        tr = lxml_html.Element("tr")
        # Pad narrow continuation rows with leading empties so the cells
        # land in the correct columns.
        pad = max(0, grid_w - len(cells))
        for _ in range(pad):
            tr.append(lxml_html.Element("td"))
        for cell in cells:
            td = lxml_html.Element("td")
            td.text = cell
            tr.append(td)
        target.append(tr)
    return lxml_html.tostring(table, encoding="unicode")


def _merge_markdown(rows_a: list[list[str]], extra_rows: list[list[str]]) -> str:
    header, *body = rows_a
    lines = ["| " + " | ".join(header) + " |"]
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for r in body + extra_rows:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)
