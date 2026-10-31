"""Tests for cross-page stitching -- the heart of 'infinite' OCR."""

from infinite_ocr.ir.models import BBox, Block, Document, Page
from infinite_ocr.render.markdown import render
from infinite_ocr.script.detect import direction, primary_script, script_histogram
from infinite_ocr.stitch.links import propose_links
from infinite_ocr.stitch.sentences import join_text, propose_sentence_link
from infinite_ocr.stitch.tables import merge_tables, propose_table_link, table_rows


def mk(bid, page, order, text, btype="Text", bbox=(0, 0, 500, 50), conf=0.99):
    ps = primary_script(text)
    return Block(
        id=bid, doc_id="d", page=page, order=order, type=btype,
        bbox=BBox.from_xyxy(bbox), conf=conf, text=text,
        scripts=script_histogram(text), primary_script=ps,
        direction=direction(ps),
    )


class TestSentenceStitch:
    def test_hindi_sentence_merges_across_page_break(self):
        prev = mk("p000-b00", 0, 0, "यह वाक्य पहले पृष्ठ पर शुरू होता है और")
        nxt = mk("p001-b00", 1, 0, "दूसरे पृष्ठ पर समाप्त होता है।")
        link = propose_sentence_link(prev, nxt, prev_col_right=500)
        assert link is not None and link.kind == "sentence"
        assert "no terminal punctuation" in link.reason

    def test_danda_blocks_the_merge(self):
        prev = mk("p000-b00", 0, 0, "यह वाक्य पूरा हो गया।")
        nxt = mk("p001-b00", 1, 0, "यह एक नया वाक्य है।")
        assert propose_sentence_link(prev, nxt, prev_col_right=500) is None

    def test_urdu_full_stop_blocks_the_merge(self):
        prev = mk("p000-b00", 0, 0, "یہ جملہ مکمل ہے۔")
        nxt = mk("p001-b00", 1, 0, "یہ نیا جملہ ہے۔")
        assert propose_sentence_link(prev, nxt, prev_col_right=500) is None

    def test_heading_is_never_merged(self):
        prev = mk("p000-b00", 0, 0, "incomplete clause")
        nxt = mk("p001-b00", 1, 0, "Chapter Two", btype="SectionHeader")
        assert propose_sentence_link(prev, nxt, prev_col_right=500) is None

    def test_list_marker_blocks_the_merge(self):
        prev = mk("p000-b00", 0, 0, "the following items apply")
        nxt = mk("p001-b00", 1, 0, "२. दूसरा आइटम")
        assert propose_sentence_link(prev, nxt, prev_col_right=500) is None

    def test_latin_hyphen_rejoins_without_space(self):
        assert join_text("exam-", "ple continues") == "example continues"

    def test_plain_join_inserts_one_space(self):
        assert join_text("भारत एक", "विशाल देश है।") == "भारत एक विशाल देश है।"


class TestTableStitch:
    HTML_A = (
        "<table><tr><th>नाम</th><th>आयु</th></tr>"
        "<tr><td>राम</td><td>३०</td></tr></table>"
    )
    HTML_B = (
        "<table><tr><th>नाम</th><th>आयु</th></tr>"
        "<tr><td>सीता</td><td>२८</td></tr></table>"
    )

    def test_parses_html_table(self):
        rows = table_rows(self.HTML_A)
        assert rows == [["नाम", "आयु"], ["राम", "३०"]]

    def test_merges_and_drops_repeated_header(self):
        prev = mk("p000-b00", 0, 0, self.HTML_A, btype="Table")
        nxt = mk("p001-b00", 1, 0, self.HTML_B, btype="Table")
        link = propose_table_link(prev, nxt)
        assert link is not None and link.confidence >= 0.9
        assert "repeats the header" in link.reason

        merged = merge_tables(self.HTML_A, self.HTML_B)
        rows = table_rows(merged)
        # One header + two data rows: the duplicate header must be gone.
        assert len(rows) == 3
        assert rows[0] == ["नाम", "आयु"]
        assert ["सीता", "२८"] in rows

    def test_column_mismatch_refuses_to_merge(self):
        # A false merge corrupts data silently, so disagreement means no link.
        three_col = "<table><tr><td>a</td><td>b</td><td>c</td></tr></table>"
        prev = mk("p000-b00", 0, 0, self.HTML_A, btype="Table")
        nxt = mk("p001-b00", 1, 0, three_col, btype="Table")
        assert propose_table_link(prev, nxt) is None

    def test_cell_split_by_page_break_is_rejoined(self):
        # A tall cell overflowing a page arrives as a row whose other cells
        # are empty. Appending it as a new row strands an orphan fragment.
        a = ("<table><tbody><tr><td>vii.</td><td>EMD</td>"
             "<td>in case of extension of the Bid, the BG also needs to</td>"
             "</tr></tbody></table>")
        b = ("<table><tbody><tr><td></td><td></td><td>be extended.</td></tr>"
             "<tr><td>viii.</td><td>Last date</td><td>Feb 02</td></tr>"
             "</tbody></table>")
        rows = table_rows(merge_tables(a, b))
        assert len(rows) == 2, "continuation must not become its own row"
        assert rows[0][2].endswith("the BG also needs to be extended.")
        assert rows[1][0] == "viii."

    def test_multiple_columns_continue_together(self):
        # A tall row broken across pages continues in SEVERAL columns at once.
        # Only the serial-number column is blank, which is the giveaway.
        a = ("<table><tbody>"
             "<tr><th>Sl.</th><th>Operation</th><th>Frequency</th></tr>"
             "<tr><td>1.</td><td>Disinfestation Spray in Main Office</td>"
             "<td>Monthly as well as whenever felt</td></tr>"
             "</tbody></table>")
        b = ("<table><tbody>"
             "<tr><td></td><td>excluding areas at Sl No. 2</td>"
             "<td>necessary by the Bank</td></tr>"
             "<tr><td>2.</td><td>Fogging</td><td>Weekly</td></tr>"
             "</tbody></table>")
        rows = table_rows(merge_tables(a, b))
        assert len(rows) == 3, "continuation must fold into the row above"
        assert rows[1][1].endswith("excluding areas at Sl No. 2")
        assert rows[1][2].endswith("necessary by the Bank")
        assert rows[2][0] == "2.", "the next numbered row must stay separate"

    def test_numbered_row_is_never_absorbed(self):
        # Serial number present => new row, even though the cell above is
        # unfinished. The label is the stronger signal.
        a = ("<table><tbody><tr><td>1.</td><td>unfinished clause</td></tr>"
             "</tbody></table>")
        b = ("<table><tbody><tr><td>2.</td><td>next item</td></tr>"
             "</tbody></table>")
        rows = table_rows(merge_tables(a, b))
        assert len(rows) == 2

    def test_only_columns_with_content_above_are_merged(self):
        # Column 2 is empty above, so its text must not be relocated there.
        a = "<table><tbody><tr><td>1.</td><td>text</td><td></td></tr></tbody></table>"
        b = "<table><tbody><tr><td></td><td>more</td><td>orphan</td></tr></tbody></table>"
        rows = table_rows(merge_tables(a, b))
        assert rows[0][1] == "text more"
        assert rows[0][2] == "", "must not invent content in an empty column"

    def test_genuine_sparse_row_is_not_swallowed(self):
        # A row with one filled cell is only a continuation if the cell above
        # is unfinished. Here it ends in a full stop, so it is real content.
        a = ("<table><tbody><tr><td>1.</td><td>x</td>"
             "<td>Complete sentence.</td></tr></tbody></table>")
        b = "<table><tbody><tr><td></td><td></td><td>Note</td></tr></tbody></table>"
        rows = table_rows(merge_tables(a, b))
        assert len(rows) == 2, "a finished cell must not absorb the next row"

    def test_multi_cell_row_is_never_a_continuation(self):
        a = ("<table><tbody><tr><td>1.</td><td>x</td>"
             "<td>unfinished clause</td></tr></tbody></table>")
        b = "<table><tbody><tr><td>2.</td><td>y</td><td>z</td></tr></tbody></table>"
        rows = table_rows(merge_tables(a, b))
        assert len(rows) == 2

    def test_danda_ends_cell_so_no_merge(self):
        # Script-aware: a Devanagari cell ending in danda is finished.
        a = ("<table><tbody><tr><td>१.</td><td>क</td>"
             "<td>यह वाक्य पूरा है।</td></tr></tbody></table>")
        b = "<table><tbody><tr><td></td><td></td><td>नया</td></tr></tbody></table>"
        rows = table_rows(merge_tables(a, b))
        assert len(rows) == 2

    def test_colspan_header_does_not_block_merge(self):
        # Regression: a header row using colspan reported fewer cells than
        # the body, so _effective_cols saw 2 vs 4 and rejected a valid merge.
        a = ("<table><thead><tr><th>SL<br/>NO.</th><th>DETAILS</th>"
             "<th>APPLICABLE</th><th>SUBMITTED</th></tr></thead>"
             "<tbody><tr><td>1.</td><td colspan='3'>Business documents</td>"
             "</tr></tbody></table>")
        b = ("<table><tbody><tr><td>c.</td><td>Last 12 months statement</td>"
             "<td></td><td></td></tr>"
             "<tr><td>d.</td><td>Audited financials</td><td></td><td></td></tr>"
             "</tbody></table>")
        link = propose_table_link(
            mk("p000-b00", 0, 0, a, btype="Table"),
            mk("p001-b00", 1, 0, b, btype="Table"),
        )
        assert link is not None
        assert "equal column count (4)" in link.reason

    def test_colspan_padding_reports_true_width(self):
        from infinite_ocr.stitch.tables import _effective_cols

        rows = table_rows(
            "<table><tr><td colspan='3'>wide</td></tr>"
            "<tr><td>a</td><td>b</td><td>c</td></tr></table>"
        )
        assert _effective_cols(rows) == 3
        assert len(rows[0]) == 3  # padded, not just one cell

    def test_rowspan_does_not_shrink_column_count(self):
        """Regression: rowspan made subsequent rows look narrower, blocking
        a valid cross-page merge with a table that reports the true width."""
        from infinite_ocr.stitch.tables import _effective_cols

        html = (
            "<table><tbody>"
            "<tr><td>A</td><td>B</td></tr>"
            "<tr><td rowspan='2'>C</td><td>D</td><td>E</td></tr>"
            "<tr><td>F</td><td>G</td></tr>"
            "</tbody></table>"
        )
        rows = table_rows(html)
        assert _effective_cols(rows) == 3
        # The rowspan row itself should report 3 cells.
        assert len(rows[1]) == 3
        # Subsequent rows should get a placeholder for the spanned column.
        assert len(rows[2]) == 3
        assert rows[2][0] == ""  # placeholder for the rowspan

    def test_rowspan_table_merge_succeeds(self):
        """A table with rowspan on page N should merge with its continuation
        on page N+1 that reports the true (expanded) column count."""
        a = (
            "<table><tbody>"
            "<tr><td>h1</td><td>h2</td></tr>"
            "<tr><td rowspan='2'>left</td><td>data1</td><td>data2</td></tr>"
            "<tr><td>data3</td><td>data4</td></tr>"
            "</tbody></table>"
        )
        b = (
            "<table><tbody>"
            "<tr><td>x</td><td>y</td><td>z</td></tr>"
            "</tbody></table>"
        )
        link = propose_table_link(
            mk("p000-b00", 0, 0, a, btype="Table"),
            mk("p001-b00", 1, 0, b, btype="Table"),
        )
        assert link is not None
        assert "equal column count (3)" in link.reason

    def test_markdown_tables_merge_too(self):
        a = "| Name | Age |\n| --- | --- |\n| Ram | 30 |"
        b = "| Name | Age |\n| --- | --- |\n| Sita | 28 |"
        merged = merge_tables(a, b)
        rows = table_rows(merged)
        assert len(rows) == 3


class TestEndToEndRender:
    def _doc(self):
        doc = Document(doc_id="d", source_path="x.pdf")
        doc.pages = [
            Page(index=0, width=500, height=700, sha256="a", dpi=300),
            Page(index=1, width=500, height=700, sha256="b", dpi=300),
        ]
        doc.blocks = [
            mk("p000-b00", 0, 0, "अध्याय एक", btype="Title", bbox=(0, 0, 500, 40)),
            mk("p000-b01", 0, 1, "यह अनुच्छेद पहले पृष्ठ पर शुरू होता है और", bbox=(0, 50, 500, 90)),
            mk("p001-b00", 1, 0, "दूसरे पृष्ठ पर जारी रहता है।", bbox=(0, 50, 500, 90)),
        ]
        doc.links = propose_links(doc)
        return doc

    def test_page_break_leaves_no_trace(self):
        out = render(self._doc(), with_anchors=False, bidi=False)
        # The split paragraph must appear as one continuous line.
        assert "शुरू होता है और दूसरे पृष्ठ पर जारी रहता है।" in out

    def test_anchors_are_stable_and_versioned(self):
        out = render(self._doc(), bidi=False)
        assert "<!-- blk:p000-b00 v1 -->" in out
        assert "<!-- blk:p000-b01 v1 -->" in out

    def test_render_is_deterministic(self):
        d1, d2 = self._doc(), self._doc()
        assert render(d1) == render(d2)

    def test_furniture_is_excluded_from_flow(self):
        doc = self._doc()
        footer = mk("p000-b09", 0, 9, "पृष्ठ १", bbox=(0, 680, 500, 700))
        footer.is_furniture = True
        doc.blocks.append(footer)
        assert "पृष्ठ १" not in render(doc, with_anchors=False, bidi=False)
