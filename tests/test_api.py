"""API tests using FastAPI's TestClient -- no server, no credits.

The OCR call is replaced with recorded fixtures, so the whole upload ->
process -> review -> correct loop is exercised without touching the network.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from infinite_ocr.api import app as app_module
from infinite_ocr.api.app import app

from .make_fixture_pdf import build
from .test_pipeline import PAGE0, PAGE1, PAGE2


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Keep uploads/outputs inside the test's temp dir.
    monkeypatch.setattr(app_module, "STORE", tmp_path / "store")
    monkeypatch.setattr(app_module, "UPLOADS", tmp_path / "store" / "uploads")
    monkeypatch.setattr(app_module, "OUT", tmp_path / "out")
    app_module.UPLOADS.mkdir(parents=True, exist_ok=True)
    app_module.OUT.mkdir(parents=True, exist_ok=True)

    # Stand in for the API: seed the cache so no page is ever sent.
    from infinite_ocr.api import runner

    real_run = runner.run_job

    def fake_run(job, **kw):
        from infinite_ocr.client.cache import PageCache
        from infinite_ocr.ingest.rasterize import rasterize

        kw["store"] = app_module.STORE
        kw["out_dir"] = app_module.OUT
        cache = PageCache(kw["store"])
        for p, payload in zip(
            rasterize(job.pdf_path, cache.pages_dir), [PAGE0, PAGE1, PAGE2]
        ):
            cache.put(p.sha256, payload)
        return real_run(job, **kw)

    monkeypatch.setattr(runner, "run_job", fake_run)
    monkeypatch.setattr(app_module, "run_job", fake_run)
    return TestClient(app)


@pytest.fixture
def pdf(tmp_path):
    return build(tmp_path / "doc.pdf")


def _upload_and_wait(client, pdf):
    with pdf.open("rb") as fh:
        r = client.post(
            "/api/upload",
            files={"file": ("doc.pdf", fh, "application/pdf")},
            data={"max_pages": "3"},
        )
    assert r.status_code == 200
    job_id = r.json()["id"]
    for _ in range(100):
        s = client.get(f"/api/jobs/{job_id}").json()
        if s["status"] in ("done", "error"):
            break
    assert s["status"] == "done", s.get("error")
    return job_id


class TestUI:
    def test_index_serves(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "Infinite" in r.text

    def test_static_assets(self, client):
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/style.css").status_code == 200

    def test_health_reports_key(self, client):
        assert "key_loaded" in client.get("/api/health").json()

    def test_rejects_non_pdf(self, client):
        r = client.post(
            "/api/upload", files={"file": ("x.txt", b"nope", "text/plain")}
        )
        assert r.status_code == 400


class TestFlow:
    def test_upload_processes_to_done(self, client, pdf):
        job_id = _upload_and_wait(client, pdf)
        s = client.get(f"/api/jobs/{job_id}").json()
        assert s["pages_total"] == 3
        # Everything was cached, so nothing should have been billed.
        assert s["pages_billed"] == 0

    def test_result_has_document_and_links(self, client, pdf):
        job_id = _upload_and_wait(client, pdf)
        d = client.get(f"/api/jobs/{job_id}/result").json()
        assert d["markdown"].strip()
        assert d["report"]["total_blocks"] > 0
        assert any(l["kind"] == "table" for l in d["links"])
        # Script metadata must survive to the UI for multilingual display.
        assert any(b["script"] == "Deva" for b in d["blocks"])

    def test_download_markdown(self, client, pdf):
        job_id = _upload_and_wait(client, pdf)
        r = client.get(f"/api/jobs/{job_id}/download")
        assert r.status_code == 200 and len(r.content) > 0

    def test_page_image_served(self, client, pdf):
        job_id = _upload_and_wait(client, pdf)
        sha = client.get(f"/api/jobs/{job_id}/result").json()["pages"][0]["sha256"]
        assert client.get(f"/api/pages/{sha}.png").status_code == 200

    def test_page_image_rejects_traversal(self, client):
        assert client.get("/api/pages/..%2f..%2fsecret.png").status_code in (400, 404)


class TestReviewEndpoints:
    def test_set_text_pins_human_correction(self, client, pdf):
        job_id = _upload_and_wait(client, pdf)
        d = client.get(f"/api/jobs/{job_id}/result").json()
        bid = next(b["id"] for b in d["blocks"] if b["type"] == "Title")

        r = client.post(
            f"/api/jobs/{job_id}/blocks/{bid}/text", data={"text": "सही शीर्षक"}
        )
        assert r.status_code == 200

        d2 = client.get(f"/api/jobs/{job_id}/result").json()
        blk = next(b for b in d2["blocks"] if b["id"] == bid)
        assert blk["source"] == "human" and blk["version"] == 2
        assert "सही शीर्षक" in d2["markdown"]

    def test_toggling_link_changes_output(self, client, pdf):
        job_id = _upload_and_wait(client, pdf)
        d = client.get(f"/api/jobs/{job_id}/result").json()
        link = next(l for l in d["links"] if l["kind"] == "table")

        before = d["markdown"]
        r = client.post(f"/api/jobs/{job_id}/links/{link['from']}/toggle")
        assert r.json()["accepted"] is False

        after = client.get(f"/api/jobs/{job_id}/result").json()["markdown"]
        assert before != after
        assert after.count("<th>नाम</th>") == 2  # unmerged again

    def test_unknown_block_is_404(self, client, pdf):
        job_id = _upload_and_wait(client, pdf)
        r = client.post(
            f"/api/jobs/{job_id}/blocks/p999-b99/text", data={"text": "x"}
        )
        assert r.status_code == 404

    def test_unknown_job_is_404(self, client):
        assert client.get("/api/jobs/deadbeef").status_code == 404


class TestExportEndpoints:
    @pytest.mark.parametrize(
        "fmt,sniff",
        [
            ("html", b"<!DOCTYPE html"),
            ("md", b"<!-- blk:"),
            ("mdflat", b"|"),
            ("txt", b""),
            ("docx", b"PK"),   # OOXML is a zip
            ("xlsx", b"PK"),
        ],
    )
    def test_every_format_downloads(self, client, pdf, fmt, sniff):
        job_id = _upload_and_wait(client, pdf)
        r = client.get(f"/api/jobs/{job_id}/export/{fmt}")
        assert r.status_code == 200, r.text[:200]
        assert len(r.content) > 0
        if sniff:
            # Binary formats are identified by their magic bytes; text formats
            # may carry the marker anywhere in the body.
            assert r.content.startswith(sniff) or sniff in r.content

    def test_unknown_format_rejected(self, client, pdf):
        job_id = _upload_and_wait(client, pdf)
        assert client.get(f"/api/jobs/{job_id}/export/exe").status_code == 400

    def test_preview_is_sanitised_html(self, client, pdf):
        job_id = _upload_and_wait(client, pdf)
        r = client.get(f"/api/jobs/{job_id}/preview")
        assert r.status_code == 200
        body = r.text
        assert "<table" in body          # tables render as tables
        assert "<script" not in body     # nothing executable survives
        assert "onerror" not in body

    def test_block_crop_returns_png(self, client, pdf):
        job_id = _upload_and_wait(client, pdf)
        blocks = client.get(f"/api/jobs/{job_id}/result").json()["blocks"]
        bid = next(b["id"] for b in blocks if b["text"].strip())
        r = client.get(f"/api/jobs/{job_id}/blocks/{bid}/crop.png")
        assert r.status_code == 200
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_crop_unknown_block_is_404(self, client, pdf):
        job_id = _upload_and_wait(client, pdf)
        assert client.get(
            f"/api/jobs/{job_id}/blocks/p999-b99/crop.png"
        ).status_code == 404


class TestActionPanel:
    def test_lock_and_unlock(self, client, pdf):
        job_id = _upload_and_wait(client, pdf)
        bid = client.get(f"/api/jobs/{job_id}/result").json()["blocks"][0]["id"]

        assert client.post(
            f"/api/jobs/{job_id}/blocks/{bid}/lock", data={"locked": "true"}
        ).json()["locked"] is True

        blocks = client.get(f"/api/jobs/{job_id}/result").json()["blocks"]
        assert next(b for b in blocks if b["id"] == bid)["locked"] is True

        client.post(f"/api/jobs/{job_id}/blocks/{bid}/lock", data={"locked": "false"})
        blocks = client.get(f"/api/jobs/{job_id}/result").json()["blocks"]
        assert next(b for b in blocks if b["id"] == bid)["locked"] is False

    def test_saving_text_locks_the_block(self, client, pdf):
        job_id = _upload_and_wait(client, pdf)
        bid = client.get(f"/api/jobs/{job_id}/result").json()["blocks"][0]["id"]
        client.post(f"/api/jobs/{job_id}/blocks/{bid}/text", data={"text": "fixed"})
        blocks = client.get(f"/api/jobs/{job_id}/result").json()["blocks"]
        assert next(b for b in blocks if b["id"] == bid)["locked"] is True

    def test_redo_on_locked_block_is_409(self, client, pdf):
        job_id = _upload_and_wait(client, pdf)
        bid = client.get(f"/api/jobs/{job_id}/result").json()["blocks"][0]["id"]
        client.post(f"/api/jobs/{job_id}/blocks/{bid}/text", data={"text": "fixed"})
        r = client.post(f"/api/jobs/{job_id}/blocks/{bid}/redo", data={})
        assert r.status_code == 409

    def test_force_merge_endpoint(self, client, pdf):
        job_id = _upload_and_wait(client, pdf)
        blocks = [
            b for b in client.get(f"/api/jobs/{job_id}/result").json()["blocks"]
            if not b["furniture"] and b["text"].strip()
        ]
        a, b = blocks[0]["id"], blocks[1]["id"]
        r = client.post(
            f"/api/jobs/{job_id}/links/merge",
            data={"from_block": a, "to_block": b, "kind": "sentence"},
        )
        assert r.status_code == 200
        links = client.get(f"/api/jobs/{job_id}/result").json()["links"]
        assert any(
            l["from"] == a and l["to"] == b and l["reason"].startswith("manual:")
            for l in links
        )

    def test_force_merge_self_is_400(self, client, pdf):
        job_id = _upload_and_wait(client, pdf)
        bid = client.get(f"/api/jobs/{job_id}/result").json()["blocks"][0]["id"]
        r = client.post(
            f"/api/jobs/{job_id}/links/merge",
            data={"from_block": bid, "to_block": bid},
        )
        assert r.status_code == 400

    def test_probe_returns_page_count(self, client, pdf):
        with pdf.open("rb") as fh:
            r = client.post("/api/probe",
                            files={"file": ("doc.pdf", fh, "application/pdf")})
        assert r.status_code == 200
        assert r.json()["total_pages"] == 3

    def test_probe_rejects_non_pdf(self, client):
        r = client.post("/api/probe",
                        files={"file": ("x.txt", b"no", "text/plain")})
        assert r.status_code == 400

    def test_clear_images_keeps_responses(self, client, pdf):
        _upload_and_wait(client, pdf)
        before = client.get("/api/health").json()["cache"]["cached_pages"]
        r = client.post("/api/cache/clear", data={"scope": "images"})
        assert r.status_code == 200
        # Raw OCR responses survive, so re-running stays free.
        assert client.get("/api/health").json()["cache"]["cached_pages"] == before
        assert r.json()["removed"]["raw"] == 0

    def test_clear_all_drops_responses(self, client, pdf):
        _upload_and_wait(client, pdf)
        r = client.post("/api/cache/clear", data={"scope": "all"})
        assert r.status_code == 200
        assert r.json()["removed"]["raw"] > 0
        assert client.get("/api/health").json()["cache"]["cached_pages"] == 0

    def test_page_spec_limits_pages(self, client, pdf):
        with pdf.open("rb") as fh:
            r = client.post(
                "/api/upload",
                files={"file": ("doc.pdf", fh, "application/pdf")},
                data={"page_spec": "1-2"},
            )
        job_id = r.json()["id"]
        for _ in range(100):
            s = client.get(f"/api/jobs/{job_id}").json()
            if s["status"] in ("done", "error"):
                break
        assert s["pages_total"] == 2

    def test_malformed_bbox_rejected(self, client, pdf):
        job_id = _upload_and_wait(client, pdf)
        blocks = client.get(f"/api/jobs/{job_id}/result").json()["blocks"]
        bid = next(b["id"] for b in blocks if not b["locked"])
        r = client.post(
            f"/api/jobs/{job_id}/blocks/{bid}/redo", data={"bbox": "1,2,3"}
        )
        assert r.status_code == 400
