"""LaTeX-to-Unicode conversion.

The live API wraps symbols it cannot type directly in TeX math delimiters --
a breadcrumb arrow arrives as `$\\rightarrow$`. Rendered verbatim that is
markup leaking into prose, but naive stripping would destroy real formulas,
so these tests pin both directions.
"""

import pytest

from infinite_ocr.render.math import demath


class TestSymbolConversion:
    def test_breadcrumb_arrows_become_unicode(self):
        src = (r"www.mstcecommerce.com $\rightarrow$ e-procurement "
               r"$\rightarrow$ PSU/Govt depts")
        out = demath(src)
        assert out == "www.mstcecommerce.com → e-procurement → PSU/Govt depts"
        assert "$" not in out and "\\" not in out

    def test_arrow_without_surrounding_spaces(self):
        assert demath(r"RBI$\rightarrow$My menu") == "RBI→My menu"

    @pytest.mark.parametrize(
        "src,want",
        [
            (r"$\leq$", "≤"), (r"$\geq$", "≥"), (r"$\neq$", "≠"),
            (r"$\times$", "×"), (r"$\div$", "÷"), (r"$\pm$", "±"),
            (r"$\infty$", "∞"), (r"$\alpha$", "α"), (r"$\Delta$", "Δ"),
            (r"$\Rightarrow$", "⇒"), (r"$\leftarrow$", "←"),
        ],
    )
    def test_common_symbols(self, src, want):
        assert demath(src) == want

    def test_longest_match_wins(self):
        # \longrightarrow must not be read as \long + arrow.
        assert demath(r"$\longrightarrow$") == "⟶"

    def test_text_wrapper_unwrapped(self):
        assert demath(r"$\text{Total}$ amount") == "Total amount"

    def test_escaped_spaces_inside_math(self):
        # Observed live: plain words wrapped in math delimiters.
        assert demath(r"click $Attach\ Doc\ button$ now") == "click Attach Doc button now"

    def test_multiple_spans_in_one_string(self):
        assert demath(r"$\leq 100$ and $\geq 5$") == "≤ 100 and ≥ 5"


class TestFormulasArePreserved:
    """Never turn real mathematics into misleading prose."""

    @pytest.mark.parametrize(
        "src",
        [
            r"$\frac{1}{2}bh$",
            r"$x^2 + y^2 = z^2$",
            r"$\sqrt{16}$",
            r"$\sum_{i=1}^{n} i$",
            r"$\int_0^1 f(x)dx$",
            r"$a_1 + a_2$",
        ],
    )
    def test_structural_math_untouched(self, src):
        assert demath(src) == src

    def test_unknown_command_is_not_guessed(self):
        assert demath(r"$\someunknowncmd$") == r"$\someunknowncmd$"

    def test_braces_block_conversion(self):
        assert demath(r"$\mbox{x}{y}$") == r"$\mbox{x}{y}$"


class TestSafety:
    def test_plain_text_unchanged(self):
        s = "No math here at all."
        assert demath(s) is s or demath(s) == s

    def test_currency_dollar_not_mangled(self):
        # A single $ is not a math span and must survive.
        assert demath("costs $5") == "costs $5"

    def test_empty_and_none_safe(self):
        assert demath("") == ""

    def test_does_not_span_newlines(self):
        # Two separate prices, not one math span across a line break.
        src = "price $5\nand $7"
        assert demath(src) == src


class TestIntegration:
    def test_conversion_happens_at_ir_boundary(self):
        from infinite_ocr.ir.normalize import blocks_from_response

        payload = {
            "blocks": [
                {
                    "order": 0,
                    "type": "Text",
                    "bbox_xyxy": [0, 0, 100, 20],
                    "text": r"Login $\rightarrow$ My menu",
                }
            ]
        }
        block = blocks_from_response(payload, doc_id="d", page=0)[0]
        assert block.text == "Login → My menu"
