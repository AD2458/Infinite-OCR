"""Undoing visual line-wrap inside a block.

Regression origin: a Tamil loan form rendered with a hard break mid-sentence
("...பணம் செலுத்த/செலுத்த" | "பொருந்தக்கூடிய வட்டி..."). The OCR wraps text at
the printed column edge, and the renderer was turning every one of those
newlines into a <br/>.

The same judgement already existed for page boundaries. These tests pin that
the two now agree, and -- just as importantly -- that genuine breaks survive.
"""

from __future__ import annotations

from infinite_ocr.render.unwrap import unwrap_lines

# Real fragment from lbr_tam.pdf, wrapped exactly as the model returned it.
TAMIL_WRAPPED = (
    "எனது/எங்கள் கணக்கில் உருவாக்கப்பட்ட ஏதேனும் ஓவர் டிராஃப்டை பணம் செலுத்த/செலுத்த\n"
    "பொருந்தக்கூடிய வட்டி மற்றும் தவணை இல்லாமல் செலுத்துவதற்கு.(C)எனது/எங்கள் கணக்கில்\n"
    "பெறப்பட்ட தவறான வரவுகளை வங்கிக்குத் தெரிவிக்க, எந்தத் தடையும் இல்லாமல்,\n"
    "பொருந்தக்கூடிய வட்டியுடன் அதைத் திருப்பி அளித்தல். கடனைச் செயலாக்குவதற்கு வங்கியால்"
)


class TestTamilRegression:
    def test_mid_sentence_wrap_is_joined(self):
        out = unwrap_lines(TAMIL_WRAPPED)
        assert "செலுத்த/செலுத்த பொருந்தக்கூடிய வட்டி" in out

    def test_clause_after_comma_is_joined(self):
        # "...எந்தத் தடையும் இல்லாமல்," ends in a comma, not a terminator.
        out = unwrap_lines(TAMIL_WRAPPED)
        assert "இல்லாமல், பொருந்தக்கூடிய வட்டியுடன்" in out

    def test_line_count_collapses(self):
        assert TAMIL_WRAPPED.count("\n") == 3
        assert unwrap_lines(TAMIL_WRAPPED).count("\n") == 0


class TestScriptAwareTerminators:
    def test_devanagari_danda_keeps_break(self):
        src = "यह वाक्य पूरा हो गया।\nयह एक नया वाक्य है"
        assert "\n" in unwrap_lines(src)

    def test_devanagari_without_danda_joins(self):
        src = "यह वाक्य पहली पंक्ति पर शुरू होता है और\nदूसरी पंक्ति पर जारी रहता है।"
        assert "\n" not in unwrap_lines(src)

    def test_urdu_full_stop_keeps_break(self):
        src = "یہ جملہ مکمل ہے۔\nیہ نیا جملہ ہے اور جاری ہے"
        assert "\n" in unwrap_lines(src)

    def test_latin_period_keeps_break(self):
        src = "This sentence is complete.\nThis one begins afresh and continues"
        assert "\n" in unwrap_lines(src)

    def test_latin_hyphen_rejoins_without_space(self):
        src = "the word is exam-\nple and the clause carries on"
        assert "example" in unwrap_lines(src)


class TestGenuineBreaksSurvive:
    def test_list_items_stay_separate(self):
        src = "intro line that does not finish\n1. first item\n2. second item"
        assert unwrap_lines(src).count("\n") >= 2

    def test_devanagari_numbered_list_stays_separate(self):
        src = "परिचय पंक्ति जो समाप्त नहीं होती\n१. पहला\n२. दूसरा"
        assert unwrap_lines(src).count("\n") >= 2

    def test_bullets_stay_separate(self):
        src = "preamble that runs on\n• alpha\n• beta"
        assert unwrap_lines(src).count("\n") >= 2

    def test_blank_line_is_a_paragraph_break(self):
        src = "para one line A\npara one line B\n\npara two line A\npara two line B"
        out = unwrap_lines(src)
        assert out.count("\n\n") == 1

    def test_heading_with_colon_stands_alone(self):
        src = "Section Title:\nbody text that continues onto"
        assert "\n" in unwrap_lines(src)

    def test_short_line_does_not_pull_in_the_next(self):
        # A line far shorter than the column stopped deliberately, so the
        # following line is new content rather than its continuation.
        src = (
            "Short label\n"
            "a long line of running prose that fills the whole column width here\n"
            "and continues onto a second long line of the same paragraph again"
        )
        out = unwrap_lines(src)
        assert out.startswith("Short label\n")
        # ...while the two long lines that really are one sentence do join.
        assert "width here and continues" in out


class TestEmbeddedLatinAcronyms:
    """Indic scripts are caseless, so str.isupper() is True for a Tamil or
    Devanagari line the moment it contains a Latin acronym and no lowercase.
    That wrongly marked prose as a heading and kept its wrap break.
    """

    def test_tamil_line_with_roman_numeral_is_not_a_heading(self):
        from infinite_ocr.render.unwrap import _is_heading_like

        # "...mentioned in Section IV, the loan amount we/us"
        line = "மேற்கூறிய பிரிவு IV இல் குறிப்பிடப்பட்டுள்ள கடன் தொகை நான்/நாங்கள்"
        assert not _is_heading_like(line)

    def test_devanagari_line_with_acronym_is_not_a_heading(self):
        from infinite_ocr.render.unwrap import _is_heading_like

        assert not _is_heading_like("हम बैंक की KYC नीति स्वीकार करते हैं और")

    def test_tamil_wrap_with_acronym_joins(self):
        # The exact failure: a wrap kept because the next line held "IV".
        src = (
            "என்/எங்கள் பெயரில் உள்ள ஏதேனும் கணக்கு/களுக்கு\n"
            "வழங்கலாம். மேற்கூறிய பிரிவு IV இல் குறிப்பிடப்பட்டுள்ள கடன் தொகை நான்/நாங்கள்\n"
            "விண்ணப்பித்த தொகை என்பதையும், வங்கியால் மதிப்பிடப்பட்ட"
        )
        out = unwrap_lines(src)
        assert "கணக்கு/களுக்கு வழங்கலாம்" in out
        assert "நான்/நாங்கள் விண்ணப்பித்த தொகை" in out

    def test_genuine_latin_heading_still_detected(self):
        from infinite_ocr.render.unwrap import _is_heading_like

        assert _is_heading_like("TERMS AND CONDITIONS")

    def test_latin_heading_still_breaks(self):
        src = "SECTION IV\nthe body text that follows runs on across the column"
        assert "\n" in unwrap_lines(src)


class TestSafety:
    def test_single_line_unchanged(self):
        assert unwrap_lines("just one line") == "just one line"

    def test_empty_safe(self):
        assert unwrap_lines("") == ""

    def test_idempotent(self):
        once = unwrap_lines(TAMIL_WRAPPED)
        assert unwrap_lines(once) == once


class TestRendererIntegration:
    def _doc(self, text, btype="Text"):
        from infinite_ocr.ir.models import BBox, Block, Document, Page
        from infinite_ocr.script.detect import primary_script

        doc = Document(doc_id="d", source_path="x.pdf")
        doc.pages.append(
            Page(index=0, width=1000, height=1000, sha256="0" * 64, dpi=300)
        )
        doc.blocks.append(
            Block(
                id="p000-b00", doc_id="d", page=0, order=0, type=btype,
                bbox=BBox(x0=0, y0=0, x1=100, y1=100), text=text,
                primary_script=primary_script(text),
            )
        )
        return doc

    def test_html_preview_has_no_false_break(self):
        from infinite_ocr.render.html import render_html

        html = render_html(self._doc(TAMIL_WRAPPED))
        assert "செலுத்த/செலுத்த பொருந்தக்கூடிய" in html

    def test_markdown_has_no_false_break(self):
        from infinite_ocr.render.markdown import render

        md = render(self._doc(TAMIL_WRAPPED), with_anchors=False, bidi=False)
        assert "செலுத்த/செலுத்த பொருந்தக்கூடிய" in md

    def test_plain_text_has_no_false_break(self):
        from infinite_ocr.render.exports import render_text

        assert "செலுத்த/செலுத்த பொருந்தக்கூடிய" in render_text(
            self._doc(TAMIL_WRAPPED)
        )

    def test_table_cell_breaks_are_untouched(self):
        # <br/> inside a cell is deliberate structure from the API, not wrap.
        from infinite_ocr.render.markdown import render

        html_tbl = "<table><tr><td>line one<br/>line two</td></tr></table>"
        out = render(self._doc(html_tbl, btype="Table"), with_anchors=False, bidi=False)
        assert "<br/>" in out


class TestUnwrapTableCells:
    """Unwrap visual <br/> inside table cells (Hindi/Indic narrow columns)."""

    def test_hindi_cell_breaks_are_unwrapped(self):
        from infinite_ocr.render.unwrap import unwrap_table_cells

        html = (
            "<table><tbody><tr><td>"
            "(एक) में ऐसे अनुपलब्ध आंकड़ों का<br/>"
            "नया संग्रह किया जाना अन्तर्वलित<br/>"
            "नहीं होना चाहिए जिनको उपलब्ध<br/>"
            "कराना किसी अधिनियम अथवा लोक<br/>"
            "प्राधिकरण के किसी नियम या<br/>"
            "विनियम के अंतर्गत अपेक्षित नहीं है;<br/>"
            "या"
            "</td></tr></tbody></table>"
        )
        out = unwrap_table_cells(html)
        # All visual wraps should be joined.
        assert "<br" not in out
        assert "आंकड़ों का नया संग्रह" in out

    def test_paragraph_break_survives_in_cell(self):
        from infinite_ocr.render.unwrap import unwrap_table_cells

        html = (
            "<table><tr><td>"
            "first sentence ends here।<br/><br/>"
            "second paragraph starts"
            "</td></tr></table>"
        )
        out = unwrap_table_cells(html)
        # The double <br> (blank line) should survive as a paragraph break.
        assert "<br" in out

    def test_non_table_html_passes_through(self):
        from infinite_ocr.render.unwrap import unwrap_table_cells

        text = "<p>hello<br/>world</p>"
        assert unwrap_table_cells(text) == text

    def test_bilingual_label_colon_break_survives(self):
        """A <br/> after a label (colon) is a genuine break."""
        from infinite_ocr.render.unwrap import unwrap_table_cells

        html = "<table><tr><td>Sector:<br/>துறை</td></tr></table>"
        out = unwrap_table_cells(html)
        # Colon marks a heading/label, so the break after it survives.
        assert "<br" in out

    def test_html_renderer_uses_unwrap(self):
        """The HTML renderer applies cell unwrapping on table blocks."""
        from infinite_ocr.render.html import _render_body

        html = (
            "<table><tr><td>"
            "first line of text<br/>"
            "second line continues<br/>"
            "third line ends here।"
            "</td></tr></table>"
        )
        out = _render_body(html, "")
        # After the danda the break should survive if there's a following
        # paragraph, but the visual wraps before it should be joined.
        assert "first line of text second line continues" in out
