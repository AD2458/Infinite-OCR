"""Content-driven rendering: tables and inline markup anywhere.

The lesson these pin down: the model's block `type` is advisory. A real Hindi
tender returned a Markdown pipe table inside a block typed `Text`/`List`, and
a type-driven renderer emitted it as raw prose. Detection must follow the
*content*, or the pipeline only works on the documents it was tuned against.
"""

import pytest

from infinite_ocr.render.inline import (
    find_pipe_table,
    inline_md,
    pipe_table_to_html,
)
from infinite_ocr.render.tables import grids_in_text


class TestPipeTableDetection:
    def test_basic_table(self):
        lines = ["| a | b |", "|---|---|", "| 1 | 2 |"]
        end, rows = find_pipe_table(lines, 0)
        assert end == 3
        assert rows == [["a", "b"], ["1", "2"]]

    def test_soft_wrapped_row_is_rejoined(self):
        # The OCR wraps a long cell onto the next line with no leading pipe.
        lines = [
            "| क | ई-निविदा सं. | Tender Reference No: RBI/CHD/Estate/2026-",
            "27/3/CPPP |",
            "|---|---|---|",
        ]
        _end, rows = find_pipe_table(lines, 0)
        assert rows[0][2] == "Tender Reference No: RBI/CHD/Estate/2026- 27/3/CPPP"
        assert "|" not in rows[0][2], "stray closing pipe must not leak into the cell"

    def test_table_found_mid_block(self):
        lines = ["Some prose.", "", "| a | b |", "|---|---|", "| 1 | 2 |"]
        assert find_pipe_table(lines, 0) is None
        found = find_pipe_table(lines, 2)
        assert found is not None and found[1][0] == ["a", "b"]

    def test_prose_with_pipe_is_not_a_table(self):
        assert find_pipe_table(["this | that"], 0) is None

    def test_single_row_without_separator_rejected(self):
        assert find_pipe_table(["| only | row |"], 0) is None

    def test_ragged_rows_are_padded(self):
        html = pipe_table_to_html([["a", "b", "c"], ["1"]])
        assert html.count("<td>") == 3


class TestTypeIndependentExtraction:
    def test_pipe_table_in_text_block_is_found(self):
        text = "Intro line.\n\n| क | ख |\n|---|---|\n| 1 | 2 |"
        grids = grids_in_text(text)
        assert len(grids) == 1
        assert grids[0][0] == ["क", "ख"]

    def test_html_table_still_found(self):
        text = "<table><tr><td>a</td><td>b</td></tr></table>"
        assert len(grids_in_text(text)) == 1

    def test_multiple_tables_in_one_block(self):
        text = ("| a | b |\n|---|---|\n| 1 | 2 |\n\nmiddle\n\n"
                "| c | d |\n|---|---|\n| 3 | 4 |")
        assert len(grids_in_text(text)) == 2

    def test_plain_prose_yields_nothing(self):
        assert grids_in_text("Just a sentence about costs.") == []

    def test_empty_safe(self):
        assert grids_in_text("") == []


class TestInlineMarkdown:
    def test_bold(self):
        assert inline_md("**Central Public**") == "<strong>Central Public</strong>"

    def test_italic(self):
        assert inline_md("*note*") == "<em>note</em>"

    def test_bold_not_eaten_by_italic(self):
        assert "<strong>" in inline_md("**x**") and "<em>" not in inline_md("**x**")

    def test_html_is_escaped(self):
        out = inline_md("<script>alert(1)</script>")
        assert "<script" not in out and "&lt;script" in out

    def test_devanagari_preserved(self):
        assert "ई-निविदा" in inline_md("ई-निविदा **सं.**")


class TestRenderedHtml:
    def _doc_with(self, text, btype="Text"):
        from infinite_ocr.ir.models import BBox, Block, Document, Page

        doc = Document(doc_id="d", source_path="x.pdf")
        doc.pages.append(Page(index=0, width=100, height=100, sha256="0" * 64, dpi=300))
        doc.blocks.append(
            Block(id="p000-b00", doc_id="d", page=0, order=0, type=btype,
                  bbox=BBox(x0=0, y0=0, x1=10, y1=10), text=text)
        )
        return doc

    def test_pipe_table_in_text_block_renders_as_table(self):
        from infinite_ocr.render.html import render_html

        html = render_html(self._doc_with("| a | b |\n|---|---|\n| 1 | 2 |"))
        assert "<table" in html
        assert "| a | b |" not in html, "must not emit raw pipe syntax"

    def test_bold_renders_in_prose(self):
        from infinite_ocr.render.html import render_html

        assert "<strong>" in render_html(self._doc_with("see **Part I** here"))

    @pytest.mark.parametrize("btype", ["Text", "List", "Caption", "Unknown"])
    def test_table_found_whatever_the_declared_type(self, btype):
        from infinite_ocr.render.html import render_html

        html = render_html(
            self._doc_with("| a | b |\n|---|---|\n| 1 | 2 |", btype=btype)
        )
        assert "<table" in html


class TestFigureRendering:
    """A Picture block has no text; it must not emit a dead image link."""

    def _doc(self):
        from infinite_ocr.ir.models import BBox, Block, Document, Page

        doc = Document(doc_id="d", source_path="x.pdf")
        doc.pages.append(Page(index=0, width=100, height=100, sha256="0" * 64, dpi=300))
        doc.blocks.append(
            Block(id="p000-b01", doc_id="d", page=0, order=0, type="Picture",
                  bbox=BBox(x0=0, y0=0, x1=50, y1=50), text="")
        )
        return doc

    def test_no_dead_image_link_in_markdown(self):
        from infinite_ocr.render.markdown import render

        md = render(self._doc(), with_anchors=False, bidi=False)
        # The old behaviour wrote ![figure p0](p000-b01.png) — a file that is
        # never created, so it always rendered broken.
        assert "p000-b01.png" not in md
        assert "Figure" in md

    def test_html_without_crop_url_shows_placeholder(self):
        from infinite_ocr.render.html import render_html

        html = render_html(self._doc())
        assert "fig-missing" in html
        assert "p000-b01.png" not in html

    def test_html_with_crop_url_shows_real_image(self):
        from infinite_ocr.render.html import render_html

        html = render_html(self._doc(), crop_url="/api/jobs/J/blocks/{id}/crop.png")
        assert '<img src="/api/jobs/J/blocks/p000-b01/crop.png"' in html


class TestPageRangeLabels:
    def _doc(self, page_groups):
        from infinite_ocr.ir.models import BBox, Block, Document, Page

        doc = Document(doc_id="d", source_path="x.pdf")
        for idx, grp in page_groups:
            doc.pages.append(
                Page(index=idx, width=100, height=100,
                     sha256=f"{idx:064d}", dpi=300, group=grp)
            )
            doc.blocks.append(
                Block(id=f"p{idx:03d}-b00", doc_id="d", page=idx, order=0,
                      type="Text", bbox=BBox(x0=0, y0=0, x1=10, y1=10),
                      text=f"content from page {idx + 1}")
            )
        return doc

    @staticmethod
    def _body(html: str) -> str:
        """Just the <main> content — the stylesheet also mentions range-sep."""
        s, e = html.find("<main>"), html.rfind("</main>")
        return html[s:e] if s >= 0 else html

    def test_single_range_has_no_separator(self):
        from infinite_ocr.render.html import render_html

        body = self._body(render_html(self._doc([(0, 0), (1, 0), (2, 0)])))
        assert "range-sep" not in body

    def test_disjoint_ranges_get_labels(self):
        from infinite_ocr.render.html import render_html

        # Pages 1-2 then 10-11 — two groups.
        body = self._body(
            render_html(self._doc([(0, 0), (1, 0), (9, 1), (10, 1)]))
        )
        assert "range-sep" in body
        assert "Pages 1–2" in body
        assert "Pages 10–11" in body

    def test_single_page_group_label(self):
        from infinite_ocr.render.html import render_html

        html = render_html(self._doc([(0, 0), (6, 1)]))
        assert "Page 1" in html and "Page 7" in html

    def test_markdown_also_labels_ranges(self):
        from infinite_ocr.render.markdown import render

        md = render(self._doc([(0, 0), (1, 0), (9, 1)]),
                    with_anchors=False, bidi=False)
        assert "Pages 1–2" in md
        assert "Page 10" in md
