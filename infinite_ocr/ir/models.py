"""Document IR: the single source of truth for a digitised document.

Design rule: the rendered document is a *pure function* of this IR. That one
property delivers both project requirements at once --

  * "one continuous output"  -> page boundaries are a stitching decision
    recorded in `Link`s, not a structural fact baked into the text;
  * "only that part gets redone" -> `Block` is simultaneously the unit of
    OCR, caching, correction and versioning, so re-OCRing one block cannot
    perturb any other region.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

BlockType = Literal[
    "Title", "SectionHeader", "Text", "Table", "List", "Picture",
    "Caption", "Footnote", "PageHeader", "PageFooter", "PageNumber",
    "Formula", "Unknown",
]

# Types the API itself declares as furniture. More robust than text matching
# alone, which OCR noise makes brittle.
FURNITURE_TYPES = frozenset({"PageHeader", "PageFooter", "PageNumber"})

# Bodhan returns finer-grained `label` values (e.g. "Chapter-title") alongside
# a coarser `type`; we map the latter onto BlockType and keep `label` verbatim.
_TYPE_ALIASES: dict[str, str] = {
    "title": "Title",
    "sectionheader": "SectionHeader",
    "section-header": "SectionHeader",
    "text": "Text",
    "plaintext": "Text",
    "paragraph": "Text",
    "table": "Table",
    "list": "List",
    "listitem": "List",
    "picture": "Picture",
    "figure": "Picture",
    "image": "Picture",
    "caption": "Caption",
    "footnote": "Footnote",
    "pageheader": "PageHeader",
    "page-header": "PageHeader",
    "header": "PageHeader",
    "pagefooter": "PageFooter",
    "page-footer": "PageFooter",
    "footer": "PageFooter",
    "formula": "Formula",
    "equation": "Formula",
    # --- observed in live API responses, not in the published examples ---
    "pagenumber": "PageNumber",
    "page-number": "PageNumber",
    "folio": "PageNumber",
    "tablecaption": "Caption",
    "table-caption": "Caption",
    "figurecaption": "Caption",
    "figure-caption": "Caption",
    "chaptertitle": "Title",
    "chapter-title": "Title",
    "sectiontitle": "SectionHeader",
    "section-title": "SectionHeader",
    "subtitle": "SectionHeader",
}


def canonical_type(raw: str | None) -> BlockType:
    """Map a Bodhan `type` string onto our BlockType, defaulting to Unknown."""
    if not raw:
        return "Unknown"
    key = raw.strip().lower().replace("_", "").replace(" ", "")
    return _TYPE_ALIASES.get(key, "Unknown")  # type: ignore[return-value]


class BBox(BaseModel):
    """Axis-aligned box in page-pixel coordinates (Bodhan's `bbox_xyxy`)."""

    x0: float
    y0: float
    x1: float
    y1: float

    @classmethod
    def from_xyxy(cls, v: list[float] | tuple[float, ...]) -> "BBox":
        x0, y0, x1, y1 = (float(n) for n in v)
        # Defensive: ensure x0<x1, y0<y1 so width/height are never negative.
        return cls(x0=min(x0, x1), y0=min(y0, y1), x1=max(x0, x1), y1=max(y0, y1))

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)

    def padded(self, frac: float, w: int, h: int) -> "BBox":
        """Expand by `frac` of each side, clamped to the page.

        Used when cropping for a targeted redo: a tight crop can clip
        ascenders, matras and the tails of Perso-Arabic letterforms.
        """
        dx, dy = self.width * frac, self.height * frac
        return BBox(
            x0=max(0.0, self.x0 - dx),
            y0=max(0.0, self.y0 - dy),
            x1=min(float(w), self.x1 + dx),
            y1=min(float(h), self.y1 + dy),
        )


class Block(BaseModel):
    """One layout region. The atom of OCR, caching, correction and rendering."""

    id: str                      # "p012-b03" -- stable, renderable as an anchor
    doc_id: str
    page: int
    order: int                   # reading order within page (validated, not trusted)
    type: BlockType = "Unknown"
    label: str | None = None     # Bodhan's finer-grained label, kept verbatim
    bbox: BBox
    conf: float = 1.0
    text: str = ""               # markdown / html fragment

    # --- multilingual metadata (see infinite_ocr.script) ---
    scripts: dict[str, float] = Field(default_factory=dict)
    primary_script: str = "Zyyy"
    direction: Literal["ltr", "rtl"] = "ltr"

    # --- provenance ---
    source: Literal["ocr", "redo", "human"] = "ocr"
    version: int = 1
    # Human corrections are authoritative: never overwritten by a re-OCR or
    # a pipeline replay.
    locked: bool = False
    is_furniture: bool = False
    flags: list[str] = Field(default_factory=list)

    @property
    def is_flow(self) -> bool:
        """Participates in the continuous text flow (excludes furniture)."""
        return not self.is_furniture and self.type not in FURNITURE_TYPES

    def flag(self, name: str) -> None:
        if name not in self.flags:
            self.flags.append(name)


class Link(BaseModel):
    """A cross-page stitching decision -- auditable and rejectable.

    A false merge corrupts data silently, so every one carries a
    human-readable `reason` and can be rejected by a reviewer.
    """

    kind: Literal["sentence", "table", "list"]
    from_block: str
    to_block: str
    confidence: float
    reason: str
    accepted: bool = True


class Page(BaseModel):
    index: int                   # 0-based index in the SOURCE pdf
    width: int
    height: int
    sha256: str                  # cache key of the rendered PNG
    dpi: int
    # Contiguous-range id for custom page selections ("1-3,7" -> groups 0,1).
    # Different groups aren't adjacent, so they're never stitched.
    group: int = 0


class Document(BaseModel):
    doc_id: str
    source_path: str
    pages: list[Page] = Field(default_factory=list)
    blocks: list[Block] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)
    doc_script: str = "Zyyy"

    def blocks_in_order(self) -> list[Block]:
        return sorted(self.blocks, key=lambda b: (b.page, b.order))

    def flow_blocks(self) -> list[Block]:
        return [b for b in self.blocks_in_order() if b.is_flow]

    def by_id(self, block_id: str) -> Block | None:
        return next((b for b in self.blocks if b.id == block_id), None)

    def accepted_links(self) -> dict[str, Link]:
        """Accepted links keyed by `from_block`, for the renderer."""
        return {l.from_block: l for l in self.links if l.accepted}


def block_id(page: int, order: int) -> str:
    """Stable, sortable block id used as the render anchor."""
    return f"p{page:03d}-b{order:02d}"
