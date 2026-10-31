"""Undo visual line-wrap inside a block.

The OCR wraps text at the printed column edge, so rendering every newline as
a hard break splits sentences mid-clause. This applies the same judgement the
cross-page stitcher makes ("did the previous fragment finish a sentence?")
within a block, so both agree.

Script-aware: Dravidian scripts end on a period, Devanagari on danda,
Perso-Arabic on U+06D4. Keying on "." alone would merge every Hindi
paragraph into one blob.
"""

from __future__ import annotations

from ..script.detect import primary_script
from ..script.numerals import starts_with_list_marker
from ..script.terminators import ends_sentence, ends_with_latin_hyphen

# A line far shorter than its neighbours ended deliberately, not by wrapping.
SHORT_LINE_RATIO = 0.55


def _is_heading_like(line: str) -> bool:
    """Short, terminator-free lines that behave as headings, not prose."""
    s = line.strip()
    if not s or len(s) > 80:
        return False

    if s.endswith(":"):  # script-independent label signal
        return True

    # Caps only mean something in cased scripts. Indic is caseless, so
    # `isupper()` is True for any Tamil line holding a Latin acronym (IV,
    # KYC) -- which misread prose as a heading and kept its wrap break.
    if primary_script(s) != "Latn":
        return False
    return s.isupper() and any(c.isalpha() for c in s)


def unwrap_lines(text: str, script: str | None = None) -> str:
    """Join soft-wrapped lines; keep genuine breaks.

    A newline survives if the line before finishes a sentence, the next
    starts a list item, either side looks like a heading, or the line is
    conspicuously short. Blank lines always separate paragraphs.
    """
    if not text or "\n" not in text:
        return text

    sc = script or primary_script(text)

    # Blank lines separate paragraphs; unwrap within each.
    paragraphs = text.split("\n\n")
    out_paras: list[str] = []

    for para in paragraphs:
        lines = [l.rstrip() for l in para.split("\n")]
        lines = [l for l in lines if l.strip() or len(lines) == 1]
        if not lines:
            continue

        widths = [len(l.strip()) for l in lines if l.strip()]
        typical = max(widths) if widths else 0

        merged: list[str] = [lines[0]]
        for nxt in lines[1:]:
            prev = merged[-1]
            if _keep_break(prev, nxt, sc, typical):
                merged.append(nxt)
            elif ends_with_latin_hyphen(prev):
                # Split word: rejoin with no space. Latin only -- Indic
                # scripts rarely hyphenate at a break.
                merged[-1] = prev.rstrip()[:-1].rstrip() + nxt.lstrip()
            else:
                merged[-1] = prev.rstrip() + " " + nxt.lstrip()
        out_paras.append("\n".join(merged))

    return "\n\n".join(out_paras)


def _keep_break(prev: str, nxt: str, script: str, typical: int) -> bool:
    """True if the newline between two lines is real, not a wrap."""
    p, n = prev.strip(), nxt.strip()
    if not p or not n:
        return True

    if ends_sentence(p, script):  # decisive; same signal the stitcher uses
        return True
    if starts_with_list_marker(n):
        return True
    if _is_heading_like(p) or _is_heading_like(n):
        return True
    if typical and len(p) < typical * SHORT_LINE_RATIO:
        return True
    return False


# ---------------------------------------------------------------------------
# Table-cell <br/> unwrapping
# ---------------------------------------------------------------------------

def unwrap_table_cells(html: str) -> str:
    """Unwrap visual line-breaks inside table cells.

    The OCR reproduces every visual line break in narrow table columns as
    ``<br/>``, so a sentence split across three printed lines appears as
    three ``<br/>``-separated fragments. This joins them exactly the way
    ``unwrap_lines`` does for plain text, keeping genuine breaks (after
    sentence terminators, before list markers, etc.).
    """
    if "<table" not in html.lower():
        return html
    try:
        from lxml import html as lxml_html
        from lxml import etree
    except ImportError:
        return html

    try:
        root = lxml_html.fragment_fromstring(html, create_parent="div")
    except Exception:
        return html

    changed = False
    for cell in root.iter("td", "th"):
        # Extract text with <br/> -> \n
        lines: list[str] = []
        for node in cell.iter():
            if node.tag == "br":
                lines.append("\n")
            if node.text:
                lines.append(node.text)
            if node.tail and node is not cell:
                lines.append(node.tail)
        raw = "".join(lines).strip()
        if "\n" not in raw:
            continue

        unwrapped = unwrap_lines(raw)
        if unwrapped == raw:
            continue

        # Clear the cell and rebuild with <br/> for surviving newlines.
        changed = True
        cell.text = None
        for child in list(cell):
            cell.remove(child)

        parts = unwrapped.split("\n")
        cell.text = parts[0]
        for part in parts[1:]:
            br = etree.SubElement(cell, "br")
            br.tail = part

    if not changed:
        return html

    # Serialize back, stripping the wrapper <div>.
    out = etree.tostring(root, encoding="unicode", method="html")
    if out.startswith("<div>") and out.endswith("</div>"):
        out = out[5:-6]
    return out
