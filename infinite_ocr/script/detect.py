"""Unicode script detection and per-block script histograms.

`indic-ocr` is script-agnostic with no `language` parameter, so no routing
is needed before OCR. This module exists to make the stitch/render layers
script-aware after it, where English-shaped heuristics would otherwise
mangle Indic and RTL text.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

# Unicode block ranges -> ISO 15924 script codes. Only the scripts Bodhan
# covers, plus Latin/Arabic which show up mixed into Indic documents.
_RANGES: list[tuple[int, int, str]] = [
    (0x0000, 0x007F, "Latn"),
    (0x0080, 0x024F, "Latn"),
    (0x0590, 0x05FF, "Hebr"),
    (0x0600, 0x06FF, "Arab"),
    (0x0750, 0x077F, "Arab"),
    (0xFB50, 0xFDFF, "Arab"),
    (0xFE70, 0xFEFF, "Arab"),
    (0x0900, 0x097F, "Deva"),
    (0x0980, 0x09FF, "Beng"),
    (0x0A00, 0x0A7F, "Guru"),
    (0x0A80, 0x0AFF, "Gujr"),
    (0x0B00, 0x0B7F, "Orya"),
    (0x0B80, 0x0BFF, "Taml"),
    (0x0C00, 0x0C7F, "Telu"),
    (0x0C80, 0x0CFF, "Knda"),
    (0x0D00, 0x0D7F, "Mlym"),
    (0x1C50, 0x1C7F, "Olck"),  # Santali / Ol Chiki
    (0xABC0, 0xABFF, "Mtei"),  # Manipuri / Meetei Mayek
]

RTL_SCRIPTS = frozenset({"Arab", "Hebr"})

# Scripts that use danda punctuation rather than the Latin full stop.
DANDA_SCRIPTS = frozenset({"Deva", "Beng", "Guru", "Gujr", "Orya"})

# Characters that carry no script signal and must not skew the histogram.
_NEUTRAL = frozenset(" \t\r\n\u00a0\u200b\u200c\u200d\u200e\u200f\u2060\ufeff")


def char_script(ch: str) -> str | None:
    """ISO 15924 script code for one character, or None if script-neutral.

    Digits, punctuation, symbols and whitespace return None: they appear in
    every script and would otherwise wash out the histogram.
    """
    if ch in _NEUTRAL:
        return None
    cat = unicodedata.category(ch)
    # N* digits/numbers, P* punctuation, S* symbols, Z* separators, C* control.
    if cat[0] in ("N", "P", "S", "Z", "C"):
        return None
    cp = ord(ch)
    for lo, hi, name in _RANGES:
        if lo <= cp <= hi:
            return name
    return None


_MARKUP = re.compile(r"<[^>]+>")
_MD_PIPE_SEP = re.compile(r"^[\s|:\-]+$", re.MULTILINE)


def strip_markup(text: str) -> str:
    """Remove HTML tags and Markdown table scaffolding.

    Essential before any script measurement: an HTML table's tag names
    (`table`, `tr`, `th`) are Latin letters, so a Devanagari table would
    otherwise be classified as Latin -- skewing the document's dominant
    script and raising false 'script_mix' flags on every table in the
    document. Tables are exactly the content this project must get right.
    """
    return _MD_PIPE_SEP.sub("", _MARKUP.sub(" ", text))


def script_histogram(text: str) -> dict[str, float]:
    """Normalised {script: proportion} over script-bearing characters.

    Returns {} when the text has no script-bearing characters at all (e.g. a
    block that is purely digits -- common for table cells and page numbers).
    """
    text = strip_markup(text)
    counts = Counter(s for ch in text if (s := char_script(ch)) is not None)
    total = sum(counts.values())
    if total == 0:
        return {}
    return {k: v / total for k, v in counts.most_common()}


def primary_script(text: str) -> str:
    """Dominant script, or 'Zyyy' (undetermined) when there is no signal."""
    hist = script_histogram(text)
    if not hist:
        return "Zyyy"
    return next(iter(hist))


def direction(script: str) -> str:
    """Base text direction implied by a script code."""
    return "rtl" if script in RTL_SCRIPTS else "ltr"


def is_rtl(text: str) -> bool:
    return direction(primary_script(text)) == "rtl"


def script_outlier_score(block_hist: dict[str, float], doc_script: str) -> float:
    """How far a block departs from the document's dominant script, in [0, 1].

    This is the single highest-value automatic quality signal for multi-script
    documents: a paragraph that is 95% Devanagari document-wide but 40% Latin
    in one block is almost certainly garbled. It costs nothing, because it is
    pure post-processing over cached OCR output.

    Blocks with no script signal score 0.0 -- a numeric table cell is not an
    anomaly.
    """
    if not block_hist:
        return 0.0
    return 1.0 - block_hist.get(doc_script, 0.0)


def dominant_doc_script(texts: list[str]) -> str:
    """Document-level dominant script, weighted by script-bearing char count."""
    counts: Counter[str] = Counter()
    for t in texts:
        for ch in strip_markup(t):
            if (s := char_script(ch)) is not None:
                counts[s] += 1
    if not counts:
        return "Zyyy"
    return counts.most_common(1)[0][0]
