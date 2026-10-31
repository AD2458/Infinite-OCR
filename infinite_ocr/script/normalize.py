"""Unicode normalisation for Indic text.

Two notions of "normalised" that must not be conflated:

  * `canonical()` -- NFC only; what gets stored and rendered. Never discards
    a character that carries meaning.
  * `comparable()` -- aggressive folding, for comparison only (furniture
    matching, header dedup). Never stored, never rendered.

ZWJ/ZWNJ control conjunct formation in Devanagari and Bengali, so they must
survive into the output -- but must not make identical headers compare
unequal.
"""

from __future__ import annotations

import re
import unicodedata

from .numerals import mask_digits

ZWNJ = "\u200c"  # zero-width non-joiner: forces separate forms
ZWJ = "\u200d"   # zero-width joiner: forces conjunct/chillu forms

# Invisible characters that never carry meaning and are always safe to drop.
_ALWAYS_STRIP = str.maketrans(
    "", "", "\u200b\u2060\ufeff\u00ad"  # ZWSP, word joiner, BOM, soft hyphen
)

_WS = re.compile(r"\s+")


def canonical(text: str) -> str:
    """NFC-normalise for storage. Preserves ZWJ/ZWNJ and all combining marks.

    NFC composes nukta forms consistently (\u0915\u093c -> \u0958 where a composed form
    exists) so that the same grapheme always has the same representation.
    """
    if not text:
        return text
    return unicodedata.normalize("NFC", text.translate(_ALWAYS_STRIP))


def comparable(text: str) -> str:
    """Fold to a comparison key. Lossy by design -- never store the result.

    Drops ZWJ/ZWNJ, masks digits, collapses whitespace and case-folds, so that
    "Page 12" and "Page 13", or two headers differing only in joiner usage,
    compare equal during furniture detection.
    """
    if not text:
        return ""
    t = canonical(text).replace(ZWJ, "").replace(ZWNJ, "")
    t = unicodedata.normalize("NFKC", t)
    t = mask_digits(t)
    t = _WS.sub(" ", t).strip()
    return t.casefold()


def grapheme_clusters(text: str) -> list[str]:
    """Split into extended grapheme clusters (user-perceived characters).

    Required for honest CER/WER on Indic text: a single akshara is several
    codepoints (consonant + virama + vowel sign + nukta), so codepoint-level
    edit distance systematically overstates error and makes cross-script
    comparison meaningless.

    Uses the `regex` module's \\X when available; falls back to a combining-mark
    heuristic that also keeps virama sequences and ZWJ/ZWNJ joins together.
    """
    if not text:
        return []
    try:
        import regex  # type: ignore

        return regex.findall(r"\X", text)
    except ImportError:
        pass

    out: list[str] = []
    for ch in text:
        if not out:
            out.append(ch)
            continue
        prev = out[-1]
        combining = unicodedata.combining(ch) != 0
        is_joiner = ch in (ZWJ, ZWNJ)
        # A virama or joiner at the end of the previous cluster means the next
        # consonant is part of the same conjunct.
        prev_joins = prev.endswith((ZWJ, ZWNJ)) or _ends_with_virama(prev)
        if combining or is_joiner or prev_joins:
            out[-1] = prev + ch
        else:
            out.append(ch)
    return out


def _ends_with_virama(cluster: str) -> bool:
    """True if a cluster ends in a virama/halant from any Indic block."""
    if not cluster:
        return False
    cp = ord(cluster[-1])
    # Virama sits at offset 0x4D within each Indic block's 0x80-aligned range.
    return (cp & 0x7F) == 0x4D and 0x0900 <= cp <= 0x0D7F


def grapheme_len(text: str) -> int:
    return len(grapheme_clusters(text))
