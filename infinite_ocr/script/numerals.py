"""Unicode-aware numeral handling.

List-continuation detection, page-number masking and table numeric checks all
break if they assume ASCII 0-9. Devanagari, Bengali, Gujarati, Gurmukhi,
Tamil, Telugu, Kannada, Malayalam, Odia and Urdu all have their own digits.
"""

from __future__ import annotations

import re
import unicodedata

# Start codepoint of each script's 0-9 digit run.
_DIGIT_BASES: dict[str, int] = {
    "Deva": 0x0966,
    "Beng": 0x09E6,
    "Guru": 0x0A66,
    "Gujr": 0x0AE6,
    "Orya": 0x0B66,
    "Taml": 0x0BE6,
    "Telu": 0x0C66,
    "Knda": 0x0CE6,
    "Mlym": 0x0D66,
    "Arab": 0x0660,  # Arabic-Indic
}
_EXT_ARABIC_BASE = 0x06F0  # Extended Arabic-Indic, used by Urdu/Sindhi


def is_digit(ch: str) -> bool:
    """True for any Unicode decimal digit, not just ASCII."""
    return unicodedata.category(ch) == "Nd"


def to_ascii_digits(text: str) -> str:
    """Fold any script's decimal digits to ASCII, leaving other chars alone."""
    out = []
    for ch in text:
        if unicodedata.category(ch) == "Nd":
            try:
                out.append(str(unicodedata.decimal(ch)))
                continue
            except ValueError:
                pass
        out.append(ch)
    return "".join(out)


def parse_int(text: str) -> int | None:
    """Parse an integer written in any script's digits."""
    folded = to_ascii_digits(text).strip()
    return int(folded) if folded.isdigit() else None


# Leading list marker: optional bracket, digits (any script), then . ) or ]
_LIST_MARKER = re.compile(r"^\s*[\(\[]?\s*(\d+)\s*[\.\)\]]\s+")

# Bullet markers across the scripts in scope.
_BULLETS = frozenset("\u2022\u2023\u25cf\u25cb\u25a0\u25a1\u2043\u2219*-\u2013\u2014")


def list_marker_value(text: str) -> int | None:
    """Return the ordinal of a leading numbered-list marker, else None.

    Digits are folded first so that Devanagari "\u0968." and ASCII "2." are
    recognised identically.
    """
    m = _LIST_MARKER.match(to_ascii_digits(text))
    return int(m.group(1)) if m else None


def starts_with_bullet(text: str) -> bool:
    stripped = text.lstrip()
    return bool(stripped) and stripped[0] in _BULLETS


def starts_with_list_marker(text: str) -> bool:
    return starts_with_bullet(text) or list_marker_value(text) is not None


def mask_digits(text: str, placeholder: str = "#") -> str:
    """Replace every Unicode digit with a placeholder.

    Used for furniture detection: a running footer reading "Page 12" / "Page
    13" must compare equal across pages, and Devanagari page numerals must be
    masked by a Unicode-aware class rather than by `\\d` alone.
    """
    return "".join(placeholder if is_digit(ch) else ch for ch in text)
