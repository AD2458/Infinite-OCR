"""Ingest -- PDF rasterisation with content-addressed page caching.

Rasterisation is the only ingest path. The PDF's embedded text layer is
deliberately not read: extraction silently reorders Indic combining marks,
and the corrupted output is still well-formed Devanagari, so no validator
can detect it without a lexicon.
"""

from .rasterize import DPI_DIGITAL, DPI_SCANNED, RenderedPage, crop_png, rasterize

__all__ = ["DPI_DIGITAL", "DPI_SCANNED", "RenderedPage", "crop_png", "rasterize"]
