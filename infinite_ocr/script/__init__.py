"""Script-awareness layer.

`indic-ocr` is script-agnostic and takes no `language` parameter, so a mixed
Hindi/English/Sanskrit page needs exactly one request. These helpers exist so
the *post-OCR* layers (stitching, furniture, render, metrics) are script-aware
too -- which is where English-shaped assumptions would otherwise quietly
corrupt Indic and RTL output.
"""

from .detect import (
    direction,
    dominant_doc_script,
    is_rtl,
    primary_script,
    script_histogram,
    script_outlier_score,
)
from .normalize import canonical, comparable, grapheme_clusters, grapheme_len
from .numerals import (
    list_marker_value,
    mask_digits,
    starts_with_list_marker,
    to_ascii_digits,
)
from .terminators import ends_sentence, ends_with_latin_hyphen, starts_new_sentence

__all__ = [
    "direction", "dominant_doc_script", "is_rtl", "primary_script",
    "script_histogram", "script_outlier_score",
    "canonical", "comparable", "grapheme_clusters", "grapheme_len",
    "list_marker_value", "mask_digits", "starts_with_list_marker", "to_ascii_digits",
    "ends_sentence", "ends_with_latin_hyphen", "starts_new_sentence",
]
