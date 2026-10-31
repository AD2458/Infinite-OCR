"""Convert the OCR's LaTeX math into readable text.

The model wraps symbols it cannot type in TeX, so a breadcrumb arrow comes
back as `$\\rightarrow$` -- markup leaking into prose. These are not
mathematics, just missing glyphs, so mapping them to Unicode recovers the
author's intent (`a $\\rightarrow$ b` -> `a → b`).

Only unambiguous substitutions are made; anything genuinely mathematical
(fractions, integrals, superscripts) stays in TeX.
"""

from __future__ import annotations

import re

# Commands with an exact, unambiguous Unicode equivalent.
_SYMBOLS: dict[str, str] = {
    # arrows -- by far the most common case
    r"\rightarrow": "\u2192", r"\to": "\u2192", r"\longrightarrow": "\u27f6",
    r"\leftarrow": "\u2190", r"\gets": "\u2190", r"\longleftarrow": "\u27f5",
    r"\leftrightarrow": "\u2194", r"\Rightarrow": "\u21d2",
    r"\Leftarrow": "\u21d0", r"\Leftrightarrow": "\u21d4",
    r"\uparrow": "\u2191", r"\downarrow": "\u2193",
    # relations
    r"\leq": "\u2264", r"\le": "\u2264", r"\geq": "\u2265", r"\ge": "\u2265",
    r"\neq": "\u2260", r"\ne": "\u2260", r"\approx": "\u2248",
    r"\equiv": "\u2261", r"\sim": "\u223c", r"\propto": "\u221d",
    # operators
    r"\times": "\u00d7", r"\div": "\u00f7", r"\pm": "\u00b1", r"\mp": "\u2213",
    r"\cdot": "\u00b7", r"\ast": "*", r"\star": "\u22c6",
    r"\infty": "\u221e", r"\partial": "\u2202", r"\nabla": "\u2207",
    r"\degree": "\u00b0", r"\circ": "\u2218", r"\percent": "%",
    # sets / logic
    r"\in": "\u2208", r"\notin": "\u2209", r"\subset": "\u2282",
    r"\subseteq": "\u2286", r"\cup": "\u222a", r"\cap": "\u2229",
    r"\emptyset": "\u2205", r"\forall": "\u2200", r"\exists": "\u2203",
    r"\neg": "\u00ac", r"\land": "\u2227", r"\lor": "\u2228",
    # currency and misc punctuation
    r"\rupee": "\u20b9", r"\pounds": "\u00a3", r"\euro": "\u20ac",
    r"\dots": "\u2026", r"\ldots": "\u2026", r"\cdots": "\u22ef",
    r"\prime": "\u2032", r"\bullet": "\u2022", r"\dagger": "\u2020",
    # greek (lower)
    r"\alpha": "\u03b1", r"\beta": "\u03b2", r"\gamma": "\u03b3",
    r"\delta": "\u03b4", r"\epsilon": "\u03b5", r"\theta": "\u03b8",
    r"\lambda": "\u03bb", r"\mu": "\u03bc", r"\pi": "\u03c0",
    r"\sigma": "\u03c3", r"\phi": "\u03c6", r"\omega": "\u03c9",
    # greek (upper)
    r"\Delta": "\u0394", r"\Sigma": "\u03a3", r"\Omega": "\u03a9",
    r"\Phi": "\u03a6", r"\Pi": "\u03a0", r"\Lambda": "\u039b",
}

# Longest-first, so \longrightarrow is not matched as \long + arrow.
_SYMBOL_RE = re.compile(
    "|".join(re.escape(k) for k in sorted(_SYMBOLS, key=len, reverse=True))
)

# TeX constructs that carry no meaning once rendered as plain text.
_TEXT_WRAPPER = re.compile(r"\\(?:text|mathrm|mathbf|mathit|textbf|textit)\s*\{([^{}]*)\}")
_SPACING = re.compile(r"\\[,;:!> ]|\\quad|\\qquad|\\ ")
_INLINE_MATH = re.compile(r"\$([^$\n]{1,200}?)\$")
_LEFTOVER_CMD = re.compile(r"\\[a-zA-Z]+")

# A span still holding one of these after substitution is real mathematics.
_STRUCTURAL = ("\\frac", "\\sqrt", "\\sum", "\\int", "\\prod", "\\lim",
               "\\begin", "\\matrix", "^", "_")


def _simplify(inner: str) -> str | None:
    """Convert one math span to plain text, or None if it must stay TeX."""
    s = _TEXT_WRAPPER.sub(r"\1", inner)
    s = _SYMBOL_RE.sub(lambda m: _SYMBOLS[m.group(0)], s)
    s = _SPACING.sub(" ", s)

    if any(tok in s for tok in _STRUCTURAL):
        return None  # genuine formula -- leave it alone
    if _LEFTOVER_CMD.search(s):
        return None  # unknown command -- do not guess
    if "{" in s or "}" in s:
        return None

    return re.sub(r"\s{2,}", " ", s).strip()


def demath(text: str) -> str:
    """Replace inline math with Unicode where it is safe to do so.

    Real formulas are left untouched, so this never turns an equation into
    misleading prose.
    """
    if not text or "$" not in text:
        return text

    def repl(m: re.Match[str]) -> str:
        simple = _simplify(m.group(1))
        return simple if simple is not None else m.group(0)

    return _INLINE_MATH.sub(repl, text)
