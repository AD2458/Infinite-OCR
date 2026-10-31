"""Document IR -- blocks, links, pages, and the document container."""

from .models import BBox, Block, BlockType, Document, Link, Page, block_id, canonical_type

__all__ = [
    "BBox", "Block", "BlockType", "Document", "Link", "Page",
    "block_id", "canonical_type",
]
