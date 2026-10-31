"""Script-aware sentence termination and continuation heuristics.

The English continuation test ("ends with '.'", "next starts capitalised")
is wrong twice over here:

  1. Devanagari terminates with danda U+0964/U+0965 and Perso-Arabic with
     U+06D4, not the Latin full stop.
  2. Indic scripts have no capitalisation, so the second test carries no
     information and would suppress every legitimate merge.
"""

from __future__ import annotations

from .detect import DANDA_SCRIPTS, primary_script

# Danda and double danda: Hindi, Marathi, Sanskrit, Nepali, Maithili, Bengali,
# Odia, Punjabi, Gujarati.
DANDA = "\u0964"
DOUBLE_DANDA = "\u0965"

# Perso-Arabic full stop / question mark: Urdu, Sindhi (Arab), Kashmiri (Arab).
ARABIC_FULL_STOP = "\u06d4"
ARABIC_QUESTION = "\u061f"

_LATIN_TERMINATORS = frozenset(".!?\u2026")
_DANDA_TERMINATORS = frozenset({DANDA, DOUBLE_DANDA}) | _LATIN_TERMINATORS
_ARABIC_TERMINATORS = frozenset({ARABIC_FULL_STOP, ARABIC_QUESTION}) | _LATIN_TERMINATORS

# Trailing characters that are punctuation but do NOT end a sentence.
_CLOSERS = ")]}\u2019\u201d\"'\u00bb"


def terminators_for(script: str) -> frozenset[str]:
    """Sentence-final characters appropriate to a script.

    Tamil, Telugu, Kannada and Malayalam predominantly use the Latin period,
    so they fall through to the Latin set.
    """
    if script in DANDA_SCRIPTS:
        return _DANDA_TERMINATORS
    if script == "Arab":
        return _ARABIC_TERMINATORS
    return _LATIN_TERMINATORS


def ends_sentence(text: str, script: str | None = None) -> bool:
    """True if `text` ends at a sentence boundary for its script."""
    stripped = text.rstrip()
    if not stripped:
        return False
    # Ignore trailing quotes/brackets: `... hai."` still terminates.
    while stripped and stripped[-1] in _CLOSERS:
        stripped = stripped[:-1].rstrip()
    if not stripped:
        return False
    sc = script or primary_script(text)
    return stripped[-1] in terminators_for(sc)


def ends_with_latin_hyphen(text: str) -> bool:
    """True only for a word split by a Latin hyphen at a line break.

    Latin-gated: Indic scripts rarely hyphenate across breaks, so a blanket
    rejoin rule would destroy legitimate hyphens. Only needed across a page
    break -- the API already rejoins within a page.
    """
    stripped = text.rstrip()
    if not stripped.endswith("-"):
        return False
    tail = stripped[:-1]
    # Require a Latin letter immediately before the hyphen.
    return bool(tail) and primary_script(tail[-1:]) == "Latn"


def starts_new_sentence(text: str) -> bool:
    """Weak positive signal that `text` begins a fresh sentence.

    Deliberately conservative. Capitalisation is only consulted for Latin,
    where it is meaningful; for Indic scripts this returns False and the
    caller must rely on script-independent evidence instead (block type,
    leading bullet/numeral, bbox indentation, short last line).
    """
    stripped = text.lstrip()
    if not stripped:
        return False
    first = stripped[0]
    if primary_script(first) == "Latn":
        return first.isupper()
    return False
