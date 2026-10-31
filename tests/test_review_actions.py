"""Reviewer action panel: locking, link overrides, and meaningful redo.

Design premise under test: `indic-ocr` is deterministic, so re-sending the
same pixels reproduces the same error. A redo is only useful if it changes
the model's input -- a nudged bbox, more padding, or genuinely more detail
from a higher-DPI re-render.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from infinite_ocr.ingest.rasterize import rerender_region
from infinite_ocr.ir.furniture import mark_furniture
from infinite_ocr.ir.models import Document, Page
from infinite_ocr.ir.normalize import blocks_from_response
from infinite_ocr.render.markdown import render
from infinite_ocr.review.confidence import flag_blocks
from infinite_ocr.review.redo import (
    force_merge,
    refresh_links,
    reject_link,
    set_block_text,
    set_locked,
)
from infinite_ocr.stitch.links import apply_links

FIX = Path(__file__).parent / "fixtures"
PAGE_H, PAGE_W = 2992, 2116

# The fixtures are named in sha256 order, which is NOT document order: their
# page footers read 7, 9, 8. Cross-page stitching only makes sense in true
# document order, so map fixture index -> real page position here.
FIXTURE_ORDER = [0, 2, 1]


def _doc():
    doc = Document(doc_id="rbi", source_path="rbi.pdf")
    for page_idx, fix_idx in enumerate(FIXTURE_ORDER):
        payload = json.loads(
            (FIX / f"rbi_page{fix_idx}.json").read_text(encoding="utf-8")
        )
        doc.pages.append(
            Page(
                index=page_idx, width=PAGE_W, height=PAGE_H,
                sha256=f"{page_idx:064d}", dpi=300,
            )
        )
        doc.blocks.extend(blocks_from_response(payload, doc_id="rbi", page=page_idx))
    mark_furniture(doc.blocks, {i: PAGE_H for i in range(3)})
    apply_links(doc)
    flag_blocks(doc)
    return doc


class TestLocking:
    def test_human_edit_locks_the_block(self):
        doc = _doc()
        bid = doc.flow_blocks()[0].id
        set_block_text(doc, bid, "corrected text")
        assert doc.by_id(bid).locked is True
        assert doc.by_id(bid).source == "human"

    def test_locked_block_refuses_redo(self):
        from infinite_ocr.client.cache import PageCache
        from infinite_ocr.review.redo import redo_block

        doc = _doc()
        bid = doc.flow_blocks()[0].id
        set_block_text(doc, bid, "corrected text")
        with pytest.raises(PermissionError):
            redo_block(doc, bid, PageCache("store"))

    def test_lock_can_be_released(self):
        doc = _doc()
        bid = doc.flow_blocks()[0].id
        set_block_text(doc, bid, "x")
        set_locked(doc, bid, False)
        assert doc.by_id(bid).locked is False

    def test_unknown_block_rejected(self):
        with pytest.raises(KeyError):
            set_locked(_doc(), "p999-b99", True)


class TestReplayPreservesHumanWork:
    """The regression that makes the whole review loop meaningful."""

    def _rebuild(self, previous):
        from infinite_ocr.ingest.rasterize import RenderedPage
        from infinite_ocr.pipeline import build_document

        pages = [
            RenderedPage(
                index=i, path=Path(f"{i}.png"), sha256=f"{i:064d}",
                width=PAGE_W, height=PAGE_H, dpi=300,
            )
            for i in range(3)
        ]
        responses = {
            f"{page_idx:064d}": json.loads(
                (FIX / f"rbi_page{fix_idx}.json").read_text(encoding="utf-8")
            )
            for page_idx, fix_idx in enumerate(FIXTURE_ORDER)
        }
        return build_document(
            "rbi.pdf", pages, responses, doc_id="rbi", preserve=previous
        )

    def test_locked_text_survives_a_full_replay(self):
        doc = _doc()
        bid = doc.flow_blocks()[0].id
        set_block_text(doc, bid, "HUMAN CORRECTED VALUE")

        rebuilt = self._rebuild(doc)
        assert rebuilt.by_id(bid).text == "HUMAN CORRECTED VALUE"
        assert rebuilt.by_id(bid).locked is True

    def test_unlocked_blocks_are_refreshed_normally(self):
        doc = _doc()
        victim = doc.flow_blocks()[1]
        victim.text = "scribble that should be discarded"

        rebuilt = self._rebuild(doc)
        assert rebuilt.by_id(victim.id).text != "scribble that should be discarded"

    def test_rejected_link_survives_replay(self):
        doc = _doc()
        if not doc.links:
            pytest.skip("fixture produced no links")
        link = doc.links[0]
        reject_link(doc, link.from_block)

        rebuilt = self._rebuild(doc)
        match = next(l for l in rebuilt.links if l.from_block == link.from_block)
        assert match.accepted is False


class TestLinkOverrides:
    def test_force_merge_creates_a_missing_link(self):
        doc = _doc()
        flow = doc.flow_blocks()
        a, b = flow[0], flow[1]
        for l in list(doc.links):
            if l.from_block == a.id:
                doc.links.remove(l)

        link = force_merge(doc, a.id, b.id, kind="sentence")
        assert link.accepted and link.confidence == 1.0
        assert link.reason.startswith("manual:")

    def test_force_merge_rejects_self_link(self):
        doc = _doc()
        bid = doc.flow_blocks()[0].id
        with pytest.raises(ValueError):
            force_merge(doc, bid, bid)

    def test_force_merge_rejects_unknown_block(self):
        doc = _doc()
        with pytest.raises(KeyError):
            force_merge(doc, doc.flow_blocks()[0].id, "p999-b99")

    def test_break_then_merge_round_trip(self):
        doc = _doc()
        if not doc.links:
            pytest.skip("fixture produced no links")
        link = doc.links[0]
        reject_link(doc, link.from_block)
        assert not next(
            l for l in doc.links if l.from_block == link.from_block
        ).accepted

        force_merge(doc, link.from_block, link.to_block, kind=link.kind)
        assert next(l for l in doc.links if l.from_block == link.from_block).accepted

    def test_manual_decisions_survive_refresh(self):
        doc = _doc()
        if not doc.links:
            pytest.skip("fixture produced no links")
        link = doc.links[0]
        reject_link(doc, link.from_block)
        refresh_links(doc)
        match = next(l for l in doc.links if l.from_block == link.from_block)
        assert match.accepted is False


class TestLinksRefreshAfterEdit:
    def test_render_still_works_after_refresh(self):
        doc = _doc()
        set_block_text(doc, doc.flow_blocks()[0].id, "edited")
        assert render(doc, with_anchors=False, bidi=False).strip()

    def test_edit_triggers_link_re_evaluation(self):
        doc = _doc()
        sent = next((l for l in doc.links if l.kind == "sentence"), None)
        if sent is None:
            pytest.skip("fixture has no sentence link")
        set_block_text(doc, sent.from_block, "Now a complete sentence.")
        still = [
            l for l in doc.links
            if l.from_block == sent.from_block and l.kind == "sentence" and l.accepted
        ]
        assert not still


class TestHighDpiRerender:
    PDF = Path("store/uploads/test1.pdf")

    @pytest.mark.skipif(not PDF.exists(), reason="sample PDF not present")
    def test_higher_dpi_yields_a_larger_image(self, tmp_path):
        from PIL import Image

        bbox = (299.0, 242.0, 520.0, 305.0)
        lo = rerender_region(
            self.PDF, 0, bbox, tmp_path / "lo.png", src_dpi=272, dpi=272
        )
        hi = rerender_region(
            self.PDF, 0, bbox, tmp_path / "hi.png", src_dpi=272, dpi=600
        )
        with Image.open(lo) as a, Image.open(hi) as b:
            assert b.width > a.width * 1.8
            assert b.height > a.height * 1.8

    @pytest.mark.skipif(not PDF.exists(), reason="sample PDF not present")
    def test_bypasses_the_page_edge_clamp(self, tmp_path):
        from PIL import Image

        out = rerender_region(
            self.PDF, 0, (0.0, 0.0, 600.0, 200.0), tmp_path / "x.png",
            src_dpi=272, dpi=1200,
        )
        with Image.open(out) as im:
            assert im.width > 600

    @pytest.mark.skipif(not PDF.exists(), reason="sample PDF not present")
    def test_rejects_bad_page_index(self, tmp_path):
        with pytest.raises(IndexError):
            rerender_region(
                self.PDF, 99, (0.0, 0.0, 100.0, 100.0),
                tmp_path / "z.png", src_dpi=272, dpi=600,
            )
