"""Tests for the multilingual layer.

These matter more than they look: every assertion here corresponds to a way
an English-shaped implementation silently corrupts Indic or RTL output.
"""

import pytest

from infinite_ocr.script import (
    canonical,
    comparable,
    dominant_doc_script,
    ends_sentence,
    ends_with_latin_hyphen,
    grapheme_clusters,
    is_rtl,
    list_marker_value,
    mask_digits,
    primary_script,
    script_histogram,
    script_outlier_score,
    starts_with_list_marker,
    to_ascii_digits,
)

HINDI = "भारत एक विशाल देश है।"
ENGLISH = "India is a vast country."
URDU = "پاکستان ایک ملک ہے۔"
TAMIL = "இந்தியா ஒரு பெரிய நாடு."
BENGALI = "ভারত একটি বিশাল দেশ।"


class TestScriptDetection:
    @pytest.mark.parametrize(
        "text,expected",
        [
            (HINDI, "Deva"),
            (ENGLISH, "Latn"),
            (URDU, "Arab"),
            (TAMIL, "Taml"),
            (BENGALI, "Beng"),
        ],
    )
    def test_primary_script(self, text, expected):
        assert primary_script(text) == expected

    def test_digits_and_punctuation_are_script_neutral(self):
        # Must not be counted, or a numeric table cell would look like Latin.
        assert script_histogram("123 ... !!! 456") == {}
        assert primary_script("42") == "Zyyy"

    def test_mixed_script_page_is_proportional(self):
        hist = script_histogram(HINDI + " " + ENGLISH)
        assert set(hist) == {"Deva", "Latn"}
        assert hist["Deva"] + hist["Latn"] == pytest.approx(1.0)

    def test_rtl_detection(self):
        assert is_rtl(URDU)
        assert not is_rtl(HINDI)
        assert not is_rtl(ENGLISH)

    def test_dominant_doc_script_weighted_by_length(self):
        assert dominant_doc_script([HINDI, HINDI, HINDI, ENGLISH]) == "Deva"

    def test_outlier_score_flags_garbled_block(self):
        clean = script_histogram(HINDI)
        garbled = script_histogram("भारत एक vishaal desh hai kjfd")
        assert script_outlier_score(clean, "Deva") < 0.1
        assert script_outlier_score(garbled, "Deva") > 0.3

    def test_numeric_block_is_not_an_outlier(self):
        assert script_outlier_score(script_histogram("1,234.56"), "Deva") == 0.0

    def test_html_tags_do_not_count_as_latin(self):
        # Tag names are Latin letters; without stripping, every Devanagari
        # table would be misread as a Latin block and falsely flagged.
        table = "<table><tr><th>नाम</th><th>अंक</th></tr></table>"
        assert primary_script(table) == "Deva"
        assert script_outlier_score(script_histogram(table), "Deva") == 0.0

    def test_markdown_table_scaffolding_ignored(self):
        md = "| नाम | अंक |\n| --- | --- |\n| राम | ३० |"
        assert primary_script(md) == "Deva"

    def test_doc_script_not_skewed_by_tables(self):
        assert dominant_doc_script([
            HINDI,
            "<table><tr><th>नाम</th></tr><tr><td>राम</td></tr></table>",
        ]) == "Deva"


class TestTerminators:
    def test_danda_terminates_devanagari(self):
        assert ends_sentence(HINDI)
        assert ends_sentence("यह वाक्य है॥")

    def test_devanagari_without_danda_is_continuation(self):
        # The core cross-page sentence-merge signal.
        assert not ends_sentence("यह वाक्य अधूरा है")

    def test_urdu_full_stop(self):
        assert ends_sentence(URDU)
        assert not ends_sentence("یہ جملہ ادھورا ہے")

    def test_dravidian_uses_latin_period(self):
        assert ends_sentence(TAMIL)
        assert not ends_sentence("இந்தியா ஒரு பெரிய நாடு")

    def test_latin_period_still_works(self):
        assert ends_sentence(ENGLISH)
        assert not ends_sentence("this clause continues")

    def test_trailing_quote_does_not_hide_terminator(self):
        assert ends_sentence('He said "yes."')
        assert ends_sentence("उसने कहा।\u201d")

    def test_hyphen_rejoin_is_latin_only(self):
        # Blanket hyphen-rejoin would destroy legitimate Indic hyphens.
        assert ends_with_latin_hyphen("exam-")
        assert not ends_with_latin_hyphen("भारत-")
        assert not ends_with_latin_hyphen("no hyphen here")


class TestNumerals:
    def test_fold_indic_digits(self):
        assert to_ascii_digits("१२३") == "123"      # Devanagari
        assert to_ascii_digits("১২৩") == "123"      # Bengali
        assert to_ascii_digits("٤٥٦") == "456"      # Arabic-Indic

    def test_list_marker_in_any_script(self):
        assert list_marker_value("2. दूसरा") == 2
        assert list_marker_value("२. दूसरा") == 2   # would fail with plain \d
        assert list_marker_value("(3) third") == 3
        assert list_marker_value("no marker") is None

    def test_bullets_recognised(self):
        assert starts_with_list_marker("• item")
        assert starts_with_list_marker("१. item")
        assert not starts_with_list_marker("ordinary text")

    def test_mask_digits_normalises_page_numbers(self):
        # "Page 12" and "Page 13" must collide during furniture detection.
        assert mask_digits("Page 12") == mask_digits("Page 13")
        assert mask_digits("पृष्ठ १२") == mask_digits("पृष्ठ १३")


class TestNormalize:
    def test_canonical_is_nfc(self):
        import unicodedata

        decomposed = unicodedata.normalize("NFD", "क़")
        assert canonical(decomposed) == unicodedata.normalize("NFC", "क़")

    def test_canonical_preserves_zwj_zwnj(self):
        # These control conjunct formation and are semantically real.
        assert "\u200c" in canonical("क\u200cष")

    def test_comparable_folds_joiners_and_digits(self):
        assert comparable("क\u200cष") == comparable("कष")
        assert comparable("Page 12") == comparable("Page  13")

    def test_grapheme_clusters_count_aksharas(self):
        # "कि" is 2 codepoints but 1 user-perceived character; codepoint-level
        # CER would overstate error on Indic text.
        assert len(grapheme_clusters("कि")) == 1
        assert len(grapheme_clusters("हिंदी")) < len("हिंदी")

    def test_grapheme_clusters_latin_unaffected(self):
        assert grapheme_clusters("abc") == ["a", "b", "c"]
