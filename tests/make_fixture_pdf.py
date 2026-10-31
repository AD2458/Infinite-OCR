"""Generate a synthetic multi-script PDF with a table spanning a page break.

Used to exercise the pipeline end-to-end with zero API credits. Devanagari
glyphs may render as tofu without a suitable font installed -- that does not
matter here, because this fixture validates *pagination and geometry*, not
glyph fidelity.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

OUT = Path(__file__).parent / "fixtures" / "multiscript.pdf"


def build(path: Path = OUT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()

    # --- page 1: title, a paragraph that will be cut mid-sentence, table head
    p1 = doc.new_page()
    p1.insert_text((72, 60), "Chapter 7: Coordinate Geometry", fontsize=18)
    p1.insert_text((72, 110), "In Class IX you studied how to locate a point", fontsize=11)
    p1.insert_text((72, 128), "on a plane. This paragraph deliberately stops", fontsize=11)
    p1.insert_text((72, 146), "mid-sentence so that the stitcher must join it", fontsize=11)
    p1.insert_text((72, 164), "with the text that begins on the following", fontsize=11)
    y = 220
    p1.insert_text((72, y), "Name", fontsize=11)
    p1.insert_text((200, y), "Marks", fontsize=11)
    for i, (n, m) in enumerate([("Ram", 30), ("Sita", 28), ("Arjun", 35)], start=1):
        p1.insert_text((72, y + 20 * i), n, fontsize=10)
        p1.insert_text((200, y + 20 * i), str(m), fontsize=10)
    p1.insert_text((72, 760), "Mathematics  |  7", fontsize=8)  # running footer

    # --- page 2: continuation of both the sentence and the table
    p2 = doc.new_page()
    p2.insert_text((72, 60), "page without any visible break in the prose.", fontsize=11)
    y = 120
    p2.insert_text((72, y), "Name", fontsize=11)     # repeated header
    p2.insert_text((200, y), "Marks", fontsize=11)
    for i, (n, m) in enumerate([("Meera", 32), ("Kabir", 27)], start=1):
        p2.insert_text((72, y + 20 * i), n, fontsize=10)
        p2.insert_text((200, y + 20 * i), str(m), fontsize=10)
    p2.insert_text((72, 760), "Mathematics  |  8", fontsize=8)

    # --- page 3: so furniture detection has >= 3 pages to work with
    p3 = doc.new_page()
    p3.insert_text((72, 60), "7.2 Distance Formula", fontsize=14)
    p3.insert_text((72, 100), "The distance between two points is given by", fontsize=11)
    p3.insert_text((72, 760), "Mathematics  |  9", fontsize=8)

    doc.save(path)
    doc.close()
    return path


if __name__ == "__main__":
    print(build())
