"""Decorative images, edge footnotes, and stitch-candidate selection.

Three bugs shared one shape: a non-prose block sitting between the real
content and the page edge. It leaked into the output AND, by being last on
the page, stood in for the real stitch candidate so every merge across that
boundary was silently rejected.
"""

from __future__ import annotations

from infinite_ocr.ir.furniture import mark_furniture
from infinite_ocr.ir.models import BBox, Block

PAGE_H, PAGE_W = 1000, 800


def mk(page, order, btype, text, x0, y0, x1, y1):
    return Block(
        id=f"p{page:03d}-b{order:02d}", doc_id="d", page=page, order=order,
        type=btype, bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1), text=text,
    )


def body(page, order, text="some ordinary body prose on the page"):
    return mk(page, order, "Text", text, 50, 200 + order * 60, 750, 250 + order * 60)


class TestDecorativeImages:
    def test_small_image_at_bottom_is_removed(self):
        # A signature: tiny, textless, just above the footer.
        sig = mk(0, 9, "Picture", "", 100, 880, 220, 960)
        blocks = [body(0, 1), sig]
        mark_furniture(blocks, {0: PAGE_H})
        assert sig.is_furniture
        assert "decoration" in sig.flags

    def test_small_image_at_top_is_removed(self):
        logo = mk(0, 0, "Picture", "", 60, 30, 180, 110)
        # Include a block low on the page so the inferred page extent is
        # realistic -- otherwise the whole "page" is only as tall as its
        # tallest block and the top band collapses.
        blocks = [logo, body(0, 1), mk(0, 8, "Text", "footer-ish line",
                                       50, 930, 750, 980)]
        mark_furniture(blocks, {0: PAGE_H})
        assert logo.is_furniture

    def test_large_figure_is_kept(self):
        # A real chart: 40% of the page. Must survive even near an edge.
        chart = mk(0, 9, "Picture", "", 50, 820, 750, 990)
        blocks = [body(0, 1), chart]
        mark_furniture(blocks, {0: PAGE_H})
        assert not chart.is_furniture

    def test_mid_page_image_is_kept(self):
        # Small but in the body: a diagram, not decoration.
        dia = mk(0, 5, "Picture", "", 300, 450, 420, 560)
        blocks = [body(0, 1), dia, body(0, 9)]
        mark_furniture(blocks, {0: PAGE_H})
        assert not dia.is_furniture

    def test_image_only_page_is_kept(self):
        # A page that is essentially one scan must not lose its only block.
        only = mk(0, 0, "Picture", "", 10, 900, 90, 980)
        blocks = [only]
        mark_furniture(blocks, {0: PAGE_H})
        assert not only.is_furniture

    def test_captioned_image_is_kept(self):
        # Text of its own means it carries information.
        fig = mk(0, 9, "Picture", "Figure 3: revenue by quarter", 100, 900, 220, 970)
        blocks = [body(0, 1), fig]
        mark_furniture(blocks, {0: PAGE_H})
        assert not fig.is_furniture


class TestEdgeFootnotes:
    def test_footnote_at_page_bottom_is_furniture(self):
        fn = mk(0, 9, "Footnote", "1- issued electronically, no signature needed",
                50, 930, 750, 980)
        blocks = [body(0, 1), fn]
        mark_furniture(blocks, {0: PAGE_H})
        assert fn.is_furniture

    def test_footnote_in_body_is_kept(self):
        # A genuine mid-page footnote is content; deleting it loses meaning.
        fn = mk(0, 5, "Footnote", "* see paragraph 4 above", 50, 500, 750, 540)
        blocks = [body(0, 1), fn, body(0, 9)]
        mark_furniture(blocks, {0: PAGE_H})
        assert not fn.is_furniture


class TestStitchCandidateSelection:
    """The real failure: a footnote or signature last on the page became the
    stitch candidate, so the table/sentence behind it was never considered."""

    def _doc(self, trailing):
        from infinite_ocr.ir.models import Document, Page
        from infinite_ocr.stitch.links import propose_links

        doc = Document(doc_id="d", source_path="x.pdf")
        for i in (0, 1):
            doc.pages.append(
                Page(index=i, width=PAGE_W, height=PAGE_H,
                     sha256=f"{i:064d}", dpi=300)
            )
        # page 0: prose that does not finish, then the trailing block
        doc.blocks.append(
            mk(0, 1, "Text", "the applicant shall submit all documents and",
               50, 200, 750, 260)
        )
        doc.blocks.append(trailing)
        # page 1: the continuation
        doc.blocks.append(
            mk(1, 0, "Text", "any further particulars the Bank may require.",
               50, 100, 750, 160)
        )
        mark_furniture(doc.blocks, {0: PAGE_H, 1: PAGE_H})
        return propose_links(doc)

    def test_trailing_footnote_does_not_block_stitch(self):
        fn = mk(0, 9, "Footnote", "1- issued electronically", 50, 940, 750, 985)
        links = self._doc(fn)
        assert any(l.kind == "sentence" for l in links)

    def test_trailing_signature_does_not_block_stitch(self):
        sig = mk(0, 9, "Picture", "", 100, 900, 220, 975)
        links = self._doc(sig)
        assert any(l.kind == "sentence" for l in links)

    def test_trailing_caption_does_not_block_stitch(self):
        cap = mk(0, 9, "Caption", "Table 2: fee schedule", 50, 900, 750, 950)
        links = self._doc(cap)
        assert any(l.kind == "sentence" for l in links)
