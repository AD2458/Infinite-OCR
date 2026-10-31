"""Cross-page sentence continuation.

The core of "one continuous output": is the last flow block of page N the
same paragraph as the first of page N+1?

Every signal survives the absence of capitalisation, which Indic scripts
lack. The strongest cue -- the previous line stopping short of the right
margin -- is purely geometric and so script-independent.
"""

from __future__ import annotations

from ..ir.models import Block, Link
from ..script.numerals import starts_with_list_marker
from ..script.terminators import (
    ends_sentence,
    ends_with_latin_hyphen,
    starts_new_sentence,
)

# Blocks that can never be merged across a break.
_NEVER_MERGE = frozenset(
    {"Title", "SectionHeader", "Table", "Picture", "Caption", "Formula"}
)


def propose_sentence_link(
    prev: Block, nxt: Block, *, prev_col_right: float | None = None
) -> Link | None:
    """Propose a sentence merge between two blocks, or None.

    Confidence is accumulated from independent signals so that a merge backed
    only by "no terminal punctuation" scores lower than one also backed by a
    short final line -- the reviewer sees the difference.
    """
    if prev.type in _NEVER_MERGE or nxt.type in _NEVER_MERGE:
        return None
    if not prev.text.strip() or not nxt.text.strip():
        return None
    if prev.is_furniture or nxt.is_furniture:
        return None

    reasons: list[str] = []
    score = 0.0

    # 1. Script-aware terminal punctuation (danda, Urdu full stop, period).
    if ends_sentence(prev.text, prev.primary_script):
        return None
    reasons.append("no terminal punctuation")
    score += 0.5

    # 2. Hyphen split -- Latin only; Indic scripts rarely hyphenate.
    hyphen = ends_with_latin_hyphen(prev.text)
    if hyphen:
        reasons.append("hyphen-split word")
        score += 0.35

    # 3. Successor must not look like the start of something new.
    if starts_with_list_marker(nxt.text):
        return None
    if starts_new_sentence(nxt.text) and nxt.primary_script == "Latn":
        # Latin-only evidence against; meaningless for Indic.
        score -= 0.2
        reasons.append("next starts capitalised")

    # 4. Geometric: predecessor's last line stops short of the right margin.
    #    Script-independent, and the most reliable signal available.
    if prev_col_right and prev.bbox.x1 > 0:
        shortfall = (prev_col_right - prev.bbox.x1) / prev_col_right
        if shortfall > 0.15:
            score -= 0.25
            reasons.append("previous block ends short of margin")
        else:
            score += 0.25
            reasons.append("previous block fills the column")

    # 5. Same script on both sides is weak corroboration.
    if prev.primary_script == nxt.primary_script != "Zyyy":
        score += 0.1
        reasons.append(f"same script ({prev.primary_script})")

    score = max(0.0, min(1.0, score))
    if score < 0.5:
        return None

    return Link(
        kind="sentence",
        from_block=prev.id,
        to_block=nxt.id,
        confidence=round(score, 3),
        reason="; ".join(reasons),
    )


def join_text(prev_text: str, next_text: str) -> str:
    """Join two continuation fragments into one paragraph."""
    left = prev_text.rstrip()
    right = next_text.lstrip()
    if ends_with_latin_hyphen(left):
        return left[:-1].rstrip() + right  # rejoin the split word, no space
    return f"{left} {right}"
