"""Standalone HTML export -- the readable view of a document.

HTML is the primary readable format because the OCR returns tables as HTML
to preserve merged cells, which pipe tables flatten away -- so rendering it
as HTML is lossless.

The output is one self-contained file: no external CSS or JS. Fonts are the
exception and degrade gracefully offline.
"""

from __future__ import annotations

import html as _html
import re

from ..ir.models import Block, Document
from ..script.detect import RTL_SCRIPTS
from ..stitch.levels import join_blocks
from .inline import find_pipe_table, inline_md, pipe_table_to_html
from .sanitize import sanitize_html
from .unwrap import unwrap_lines, unwrap_table_cells

# Noto covers every script the OCR supports. Without these, Windows renders
# Devanagari, Tamil, Urdu and others as tofu boxes.
_FONT_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?'
    "family=Noto+Sans+Devanagari&family=Noto+Sans+Bengali&family=Noto+Sans+Tamil&"
    "family=Noto+Sans+Telugu&family=Noto+Sans+Kannada&family=Noto+Sans+Malayalam&"
    "family=Noto+Sans+Gurmukhi&family=Noto+Sans+Gujarati&family=Noto+Sans+Oriya&"
    'family=Noto+Nastaliq+Urdu&display=swap" rel="stylesheet">'
)

_CSS = """
:root{--ink:#1a1b2e;--muted:#6b7090;--line:#d9dbe7;--accent:#5b4bdb;--soft:#f3f2fd}
*{box-sizing:border-box}
body{margin:0;padding:48px 24px;background:#f6f7fb;color:var(--ink);
 font:16px/1.7 "Noto Sans Devanagari","Noto Sans Bengali","Noto Sans Tamil",
 "Noto Sans Telugu","Noto Sans Kannada","Noto Sans Malayalam","Noto Sans Gurmukhi",
 "Noto Sans Gujarati","Noto Sans Oriya","Noto Nastaliq Urdu",
 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:900px;margin:0 auto;background:#fff;padding:52px 60px;
 border:1px solid var(--line);border-radius:12px}
h1,h2,h3{line-height:1.3;margin:1.6em 0 .5em;letter-spacing:-.01em}
h1{font-size:1.9em;border-bottom:2px solid var(--line);padding-bottom:.3em}
h2{font-size:1.45em} h3{font-size:1.2em}
p{margin:0 0 1em}
table{border-collapse:collapse;width:100%;margin:1.3em 0;font-size:.93em}
th,td{border:1px solid var(--line);padding:9px 12px;text-align:left;
 vertical-align:top}
th{background:var(--soft);font-weight:600}
tbody tr:nth-child(even){background:#fafaff}
blockquote{margin:1em 0;padding:.6em 1.1em;border-left:3px solid var(--accent);
 background:var(--soft);color:var(--muted)}
strong{font-weight:650}
code{background:#f2f2f8;padding:1px 5px;border-radius:4px;font-size:.92em}
.url{color:var(--accent);word-break:break-all}
.range-sep{display:flex;align-items:center;gap:12px;margin:2.2em 0 1.4em;
 color:var(--accent);font-size:.78em;font-weight:700;letter-spacing:.08em;
 text-transform:uppercase}
.range-sep::before,.range-sep::after{content:'';flex:1;height:2px;
 background:var(--soft)}
.range-sep span{background:var(--soft);padding:4px 14px;border-radius:20px;
 white-space:nowrap}
figure.fig{margin:1.4em 0;text-align:center}
figure.fig img{max-width:100%;border:1px solid var(--line);border-radius:8px}
figure.fig figcaption{font-size:.85em;color:var(--muted);margin-top:.5em}
figure.fig-missing .fig-ph{font-size:38px;padding:24px;background:var(--soft);
 border:1px dashed var(--line);border-radius:8px;display:inline-block;min-width:160px}
[dir=rtl]{text-align:right}
.meta{max-width:900px;margin:0 auto 14px;color:var(--muted);font-size:13px}
.flag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:20px;
 background:#fdf3e2;color:#b7791f;margin-left:6px;vertical-align:middle}
@media print{body{background:#fff;padding:0}main{border:0;padding:0}.flag{display:none}}
"""


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def _render_body(text: str, attrs: str) -> str:
    """Render arbitrary OCR text, handling whatever constructs it contains.

    Content-driven on purpose: the model's block `type` is advisory, and real
    documents put Markdown tables inside blocks typed `Text` or `List`. Keying
    off the declared type alone is what makes a pipeline that only works on
    the documents it was tuned against.
    """
    if "<table" in text.lower():
        return f"<div{attrs}>{sanitize_html(unwrap_table_cells(text))}</div>"

    lines = text.splitlines()
    parts: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if not buf:
            return
        chunk = "\n".join(buf).strip()
        buf.clear()
        if not chunk:
            return
        # Undo the page's visual line-wrap first, or every wrapped line
        # becomes a hard <br/> and sentences break mid-clause.
        chunk = unwrap_lines(chunk)
        for para in (p for p in chunk.split("\n\n") if p.strip()):
            body = "<br/>".join(inline_md(l) for l in para.strip().splitlines())
            parts.append(f"<p{attrs}>{body}</p>")

    i = 0
    while i < len(lines):
        line = lines[i]

        # A pipe table may begin at any line, in any block type.
        found = find_pipe_table(lines, i)
        if found is not None:
            nxt, rows = found
            flush()
            parts.append(f"<div{attrs}>{pipe_table_to_html(rows)}</div>")
            i = nxt
            continue

        m = _MD_HEADING.match(line)
        if m:
            flush()
            level = min(6, len(m.group(1)) + 1)  # '#' -> h2, keep h1 for titles
            parts.append(f"<h{level}{attrs}>{inline_md(m.group(2))}</h{level}>")
            i += 1
            continue

        buf.append(line)
        i += 1

    flush()
    return "".join(parts)


def _block_html(
    block: Block, *, show_flags: bool, crop_url: str | None = None
) -> str:
    """Render one block as an HTML fragment.

    `crop_url` is a format string with a `{id}` placeholder. When supplied,
    Picture blocks render as a real <img> pointing at the live crop endpoint;
    otherwise they render as a labelled placeholder, because a standalone
    export has no server to fetch crops from.
    """
    text = block.text.strip()

    rtl = block.primary_script in RTL_SCRIPTS
    attrs = ' dir="rtl"' if rtl else ""
    flag = ""
    if show_flags and block.flags:
        flag = f'<span class="flag">{_html.escape(", ".join(block.flags))}</span>'

    # A Picture has no text of its own; show the region it came from.
    if block.type == "Picture":
        caption = _html.escape(text) if text else f"Figure &mdash; page {block.page + 1}"
        if crop_url:
            src = _html.escape(crop_url.format(id=block.id))
            return (
                f'<figure class="fig"><img src="{src}" alt="figure on page '
                f'{block.page + 1}" loading="lazy">'
                f"<figcaption>{caption}{flag}</figcaption></figure>"
            )
        return (
            f'<figure class="fig fig-missing"><div class="fig-ph">🖼️</div>'
            f"<figcaption>{caption}{flag}</figcaption></figure>"
        )

    if not text:
        return ""

    # Headings are the one case where the declared type is the better signal,
    # but only when the text is short enough to really be a heading.
    short = len(text) < 200 and "\n" not in text.strip()
    if block.type == "Title" and short:
        return f"<h1{attrs}>{inline_md(text)}{flag}</h1>"
    if block.type == "SectionHeader" and short:
        return f"<h2{attrs}>{inline_md(text)}{flag}</h2>"
    if block.type == "Caption" and short:
        return f"<h3{attrs}>{inline_md(text)}{flag}</h3>"
    if block.type == "Footnote":
        return f"<blockquote{attrs}>{_render_body(text, '')}{flag}</blockquote>"

    return _render_body(text, attrs) + flag


def render_html(
    doc: Document,
    *,
    title: str | None = None,
    show_flags: bool = False,
    crop_url: str | None = None,
) -> str:
    """Render the whole stitched document as a standalone HTML page.

    `crop_url` may contain `{id}`; when given, figures render as live images
    from that endpoint instead of placeholders.
    """
    links = doc.accepted_links()
    consumed: set[str] = set()
    parts: list[str] = []

    # Pages the user selected as separate ranges get their own labelled
    # section, so it is always clear which part of the source a passage
    # came from and that the ranges are not continuous with each other.
    page_group = {p.index: p.group for p in doc.pages}
    group_pages: dict[int, list[int]] = {}
    for p in doc.pages:
        group_pages.setdefault(p.group, []).append(p.index)
    multi_group = len(group_pages) > 1
    seen_group: int | None = None

    for block in doc.flow_blocks():
        if block.id in consumed:
            continue

        grp = page_group.get(block.page, 0)
        if multi_group and grp != seen_group:
            seen_group = grp
            idxs = group_pages.get(grp, [])
            if idxs:
                lo, hi = min(idxs) + 1, max(idxs) + 1
                label = f"Pages {lo}–{hi}" if lo != hi else f"Page {lo}"
                parts.append(f'<div class="range-sep"><span>{label}</span></div>')

        text = block.text
        cursor = block
        while (link := links.get(cursor.id)) is not None:
            nxt = doc.by_id(link.to_block)
            if nxt is None or nxt.id in consumed:
                break
            text = join_blocks(link, text, nxt.text)
            consumed.add(nxt.id)
            cursor = nxt
        merged = block.model_copy(update={"text": text})
        frag = _block_html(merged, show_flags=show_flags, crop_url=crop_url)
        if frag:
            parts.append(frag)

    name = _html.escape(title or doc.doc_id)
    meta = (
        f"{len(doc.pages)} page(s) &middot; {len(doc.blocks)} block(s) &middot; "
        f"{len([l for l in doc.links if l.accepted])} cross-page stitch(es) &middot; "
        f"script: {_html.escape(doc.doc_script)}"
    )
    return (
        "<!DOCTYPE html>\n"
        f'<html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{name}</title>{_FONT_LINK}<style>{_CSS}</style></head>"
        f'<body><div class="meta">{name} &mdash; {meta}</div>'
        f"<main>{''.join(parts)}</main></body></html>\n"
    )
