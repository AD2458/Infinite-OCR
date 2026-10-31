"""Content-driven conversion of OCR text into HTML fragments.

Rule: never branch on the block's declared type alone -- it is advisory. A
real document returned a Markdown pipe table in a block typed `Text`, which
any `type == "Table"` renderer emitted as raw prose, pipes and all.

Everything here inspects the content instead, so the pipeline works on
documents it has never seen.
"""

from __future__ import annotations

import html as _html
import re

# A pipe-table row: starts and/or ends with '|' and has at least two cells.
_PIPE_ROW = re.compile(r"^\s*\|.*\|?\s*$")
# The ---|---|--- separator under a Markdown header row.
_PIPE_SEP = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$")

_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", re.DOTALL)
_CODE = re.compile(r"`([^`]+)`")
_URL = re.compile(r"(https?://[^\s<>\"')\]]+)")


def inline_md(text: str, *, escape: bool = True) -> str:
    """Render inline Markdown (bold/italic/code/links) as HTML."""
    s = _html.escape(text) if escape else text
    s = _CODE.sub(r"<code>\1</code>", s)
    s = _BOLD.sub(r"<strong>\1</strong>", s)
    s = _ITALIC.sub(r"<em>\1</em>", s)
    # Linkify bare URLs, but only outside existing tags.
    s = _URL.sub(r'<span class="url">\1</span>', s)
    return s


def _split_cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def find_pipe_table(lines: list[str], start: int) -> tuple[int, list[list[str]]] | None:
    """Parse a Markdown pipe table beginning at `start`.

    Returns (index_after_table, rows) or None.

    Re-joins soft-wrapped rows: the OCR continues long cells on the next
    physical line without a leading pipe, and treating that as prose or as a
    new row shreds the table. Such lines are appended to the last cell above.
    """
    n = len(lines)
    if start >= n or not _PIPE_ROW.match(lines[start]):
        return None

    rows: list[list[str]] = []
    i = start
    saw_separator = False

    while i < n:
        line = lines[i]
        if _PIPE_ROW.match(line):
            if _PIPE_SEP.match(line) and "-" in line:
                saw_separator = True
                i += 1
                continue
            rows.append(_split_cells(line))
            i += 1
            continue

        # A non-pipe, non-blank line directly after a row is a soft wrap.
        if line.strip() and rows:
            frag = line.strip()
            # The wrapped remainder usually carries the row's closing pipe;
            # keeping it would leave a stray '|' inside the cell text.
            if frag.endswith("|"):
                frag = frag[:-1].rstrip()
            rows[-1][-1] = (rows[-1][-1] + " " + frag).strip()
            i += 1
            continue
        break

    # Require real table shape, so a sentence containing '|' is not captured.
    if len(rows) < 2 and not saw_separator:
        return None
    if max((len(r) for r in rows), default=0) < 2:
        return None
    return i, rows


def pipe_table_to_html(rows: list[list[str]], *, header: bool = True) -> str:
    """Render parsed pipe-table rows as an HTML table."""
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    out = ["<table>"]
    body_start = 0
    if header:
        head = rows[0] + [""] * (width - len(rows[0]))
        out.append("<thead><tr>")
        out.extend(f"<th>{inline_md(c)}</th>" for c in head)
        out.append("</tr></thead>")
        body_start = 1
    out.append("<tbody>")
    for r in rows[body_start:]:
        cells = r + [""] * (width - len(r))
        out.append("<tr>")
        out.extend(f"<td>{inline_md(c)}</td>" for c in cells)
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)
