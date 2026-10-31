"""HTML sanitisation for OCR output.

OCR output is untrusted: a scanned page can contain text that looks like
markup, and it flows straight into the reviewer UI via innerHTML.

Uses `nh3` (Rust/ammonia), not a regex -- regex tag-stripping is easy to
bypass, and this is the one place where being wrong is a security bug.
"""

from __future__ import annotations

import nh3

# Deliberately minimal: everything needed to express a document's structure,
# and nothing that can execute, navigate, load a resource or restyle the page.
ALLOWED_TAGS: set[str] = {
    "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption",
    "col", "colgroup",
    "p", "br", "hr", "div", "span",
    "b", "strong", "i", "em", "u", "s", "sup", "sub", "mark", "small",
    "ul", "ol", "li", "dl", "dt", "dd",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "code",
}

# No `style` (CSS can exfiltrate and overlay), no `href`/`src` (navigation and
# resource loading), no `id` (can collide with the app's own anchors).
ALLOWED_ATTRS: dict[str, set[str]] = {
    "td": {"rowspan", "colspan", "align", "valign"},
    "th": {"rowspan", "colspan", "align", "valign", "scope"},
    "col": {"span"},
    "colgroup": {"span"},
    "table": {"border"},
    # `class` is allowed but the renderer only ever emits its own fixed names
    # (url, flag); it cannot carry behaviour, unlike style/href/src.
    "span": {"class"},
    "*": {"dir", "lang"},  # needed for correct RTL rendering
}


def sanitize_html(html: str) -> str:
    """Strip everything not on the allow-list. Safe for innerHTML."""
    if not html:
        return ""
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes={k: set(v) for k, v in ALLOWED_ATTRS.items()},
        link_rel=None,
        strip_comments=True,
    )


def strip_tags(html: str) -> str:
    """Remove all markup, keeping only text. Used for plain-text export."""
    if not html:
        return ""
    return nh3.clean(html, tags=set(), attributes={}, strip_comments=True)
