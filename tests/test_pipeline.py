"""End-to-end pipeline test with recorded responses -- zero API credits.

This is the offline development loop the whole design is built around: the
stitching, furniture, render and triage layers are exercised against cached
page JSON, so they can be tuned indefinitely without spending anything.
"""

from __future__ import annotations

import pytest

from infinite_ocr.client.cache import PageCache
from infinite_ocr.ingest.rasterize import rasterize
from infinite_ocr.ir.normalize import blocks_from_response
from infinite_ocr.pipeline import build_document
from infinite_ocr.render.markdown import render
from infinite_ocr.review.confidence import flag_blocks, review_queue
from infinite_ocr.review.redo import reject_link, set_block_text

from .make_fixture_pdf import build


def _page_payload(blocks, markdown="md"):
    return {
        "id": "indic-ocr-test",
        "model": "indic-ocr",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": markdown}}],
        "usage": {"total_tokens": 100},
        "blocks": blocks,
    }


# Mimics the documented response shape: order/label/type/bbox_xyxy/conf/text.
PAGE0 = _page_payload([
    {"order": 1, "label": "Chapter-title", "type": "Title",
     "bbox_xyxy": [72, 40, 520, 70], "conf": 0.95, "text": "अध्याय ७: निर्देशांक ज्यामिति"},
    {"order": 2, "label": "Text", "type": "Text",
     "bbox_xyxy": [72, 100, 520, 180], "conf": 0.91,
     "text": "कक्षा नौ में आपने पढ़ा कि किसी बिंदु की स्थिति ज्ञात करने के लिए हमें"},
    {"order": 3, "label": "Page-footer", "type": "PageFooter",
     "bbox_xyxy": [72, 750, 200, 765], "conf": 0.99, "text": "गणित | ७"},
])

PAGE1 = _page_payload([
    {"order": 1, "label": "Text", "type": "Text",
     "bbox_xyxy": [72, 40, 520, 70], "conf": 0.93,
     "text": "निर्देशांक अक्षों के एक युग्म की आवश्यकता होती है।"},
    {"order": 2, "label": "Table", "type": "Table",
     "bbox_xyxy": [72, 100, 520, 200], "conf": 0.88,
     "text": "<table><tr><th>नाम</th><th>अंक</th></tr><tr><td>राम</td><td>३०</td></tr></table>"},
    {"order": 3, "label": "Page-footer", "type": "PageFooter",
     "bbox_xyxy": [72, 750, 200, 765], "conf": 0.99, "text": "गणित | ८"},
])

PAGE2 = _page_payload([
    {"order": 1, "label": "Table", "type": "Table",
     "bbox_xyxy": [72, 40, 520, 140], "conf": 0.87,
     "text": "<table><tr><th>नाम</th><th>अंक</th></tr><tr><td>सीता</td><td>२८</td></tr></table>"},
    {"order": 2, "label": "Section-title", "type": "SectionHeader",
     "bbox_xyxy": [72, 180, 400, 210], "conf": 0.94, "text": "७.२ दूरी सूत्र"},
    {"order": 3, "label": "Page-footer", "type": "PageFooter",
     "bbox_xyxy": [72, 750, 200, 765], "conf": 0.99, "text": "गणित | ९"},
])


@pytest.fixture(scope="module")
def pdf(tmp_path_factory):
    return build(tmp_path_factory.mktemp("fx") / "multiscript.pdf")


@pytest.fixture
def doc(pdf, tmp_path):
    cache = PageCache(tmp_path / "store")
    pages = rasterize(pdf, cache.pages_dir)
    responses = {}
    for p, payload in zip(pages, [PAGE0, PAGE1, PAGE2]):
        cache.put(p.sha256, payload)
        responses[p.sha256] = payload
    return build_document(pdf, pages, responses, doc_id="fx")


class TestRasterize:
    def test_renders_and_caches_pages(self, pdf, tmp_path):
        cache = PageCache(tmp_path / "s")
        pages = rasterize(pdf, cache.pages_dir)
        assert len(pages) == 3
        assert all(p.path.exists() and len(p.sha256) == 64 for p in pages)

    def test_content_addressing_is_stable(self, pdf, tmp_path):
        a = rasterize(pdf, PageCache(tmp_path / "a").pages_dir)
        b = rasterize(pdf, PageCache(tmp_path / "b").pages_dir)
        # Identical input must yield identical keys, or caching never hits.
        assert [p.sha256 for p in a] == [p.sha256 for p in b]

    def test_every_page_is_rendered_for_ocr(self, pdf, tmp_path):
        # There is no text-layer bypass: PDF extraction silently reorders
        # Indic combining marks, so every page goes through the vision model.
        pages = rasterize(pdf, PageCache(tmp_path / "s").pages_dir)
        assert len(pages) == 3
        assert all(p.path.exists() for p in pages)

    def test_vector_pages_render_at_lower_dpi(self, pdf, tmp_path):
        # DPI is a quality choice only -- it never decides whether to OCR.
        from infinite_ocr.ingest.rasterize import DPI_DIGITAL

        pages = rasterize(pdf, PageCache(tmp_path / "s").pages_dir)
        assert all(p.is_vector for p in pages)
        assert all(p.dpi == DPI_DIGITAL for p in pages)


class TestNormalize:
    def test_parses_documented_response_shape(self):
        blocks = blocks_from_response(PAGE0, doc_id="d", page=0)
        assert len(blocks) == 3
        assert blocks[0].type == "Title"
        assert blocks[0].label == "Chapter-title"
        assert blocks[0].id == "p000-b00"   # order is 1-based in the API
        assert blocks[0].primary_script == "Deva"

    def test_bbox_from_xyxy(self):
        b = blocks_from_response(PAGE0, doc_id="d", page=0)[0]
        assert (b.bbox.x0, b.bbox.y0, b.bbox.x1, b.bbox.y1) == (72, 40, 520, 70)


class TestFurniture:
    def test_repeated_footer_is_removed_from_flow(self, doc):
        footers = [b for b in doc.blocks if "गणित" in b.text]
        assert len(footers) == 3
        assert all(b.is_furniture for b in footers)
        assert "गणित" not in render(doc, with_anchors=False, bidi=False)

    def test_body_blocks_survive(self, doc):
        assert any(b.is_flow and b.type == "Title" for b in doc.blocks)


class TestStitchingInPipeline:
    def test_sentence_joins_across_pages_0_and_1(self, doc):
        out = render(doc, with_anchors=False, bidi=False)
        assert "हमें निर्देशांक अक्षों" in out  # the two halves are now contiguous

    def test_table_merges_across_pages_1_and_2(self, doc):
        kinds = {l.kind for l in doc.links}
        assert "table" in kinds
        out = render(doc, with_anchors=False, bidi=False)
        # Both data rows in one table, header appearing only once.
        assert "राम" in out and "सीता" in out
        assert out.count("<th>नाम</th>") == 1

    def test_links_carry_a_human_readable_reason(self, doc):
        assert all(l.reason for l in doc.links)


class TestReviewLoop:
    def test_rejecting_a_link_unmerges_it(self, doc):
        table_link = next(l for l in doc.links if l.kind == "table")
        merged = render(doc, with_anchors=False, bidi=False)
        reject_link(doc, table_link.from_block)
        unmerged = render(doc, with_anchors=False, bidi=False)
        assert merged != unmerged
        assert unmerged.count("<th>नाम</th>") == 2  # two separate tables again

    def test_human_correction_is_pinned(self, doc):
        bid = next(b.id for b in doc.blocks if b.type == "Title")
        set_block_text(doc, bid, "अध्याय ७: सही शीर्षक")
        blk = doc.by_id(bid)
        assert blk.source == "human" and blk.version == 2
        assert "सही शीर्षक" in render(doc, with_anchors=False, bidi=False)

    def test_edit_changes_only_its_own_region(self, doc):
        before = render(doc, with_anchors=False, bidi=False).splitlines()
        bid = next(b.id for b in doc.blocks if b.type == "SectionHeader")
        set_block_text(doc, bid, "७.२ बदला हुआ शीर्षक")
        after = render(doc, with_anchors=False, bidi=False).splitlines()
        differing = [i for i, (x, y) in enumerate(zip(before, after)) if x != y]
        assert len(differing) == 1  # exactly one region moved

    def test_anchors_track_versions(self, doc):
        bid = next(b.id for b in doc.blocks if b.type == "Title")
        set_block_text(doc, bid, "नया")
        assert f"<!-- blk:{bid} v2 -->" in render(doc)

    def test_triage_flags_low_confidence(self, doc):
        for b in doc.blocks:
            if b.type == "Table":
                b.conf = 0.3
        flag_blocks(doc)
        assert any("low_conf" in i["flags"] for i in review_queue(doc))


class TestDeterminism:
    def test_same_ir_renders_identically(self, doc):
        assert render(doc) == render(doc)

    def test_ir_round_trips_through_json(self, doc):
        from infinite_ocr.ir.models import Document

        again = Document.model_validate_json(doc.model_dump_json())
        assert render(again) == render(doc)
