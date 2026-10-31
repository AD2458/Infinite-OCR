"""In-process job registry for the web UI.

OCR of a long PDF takes minutes, so uploads run in a background thread and the
browser polls for progress. Jobs are kept in memory (single-process), which is
all the reviewer UI needs; the durable artefacts are the IR JSON and the
sha256 page cache on disk, so a restart never loses billed work.
"""

from __future__ import annotations

import threading
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ..ir.models import Document

Status = Literal["queued", "rasterizing", "ocr", "stitching", "done", "error"]


@dataclass
class Job:
    id: str
    filename: str
    pdf_path: Path
    status: Status = "queued"
    message: str = ""
    pages_total: int = 0
    pages_done: int = 0
    pages_cached: int = 0      # served from cache => not billed again
    pages_billed: int = 0      # actually sent to the API this run
    error: str = ""
    doc: Document | None = None
    markdown: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)

    def progress(self) -> float:
        if self.status == "done":
            return 1.0
        if not self.pages_total:
            return 0.0
        return min(0.99, self.pages_done / self.pages_total)

    def public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "filename": self.filename,
            "status": self.status,
            "message": self.message,
            "progress": round(self.progress(), 3),
            "pages_total": self.pages_total,
            "pages_done": self.pages_done,
            "pages_cached": self.pages_cached,
            "pages_billed": self.pages_billed,
            "error": self.error,
        }


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, filename: str, pdf_path: Path) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], filename=filename, pdf_path=pdf_path)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def all(self) -> list[Job]:
        with self._lock:
            return list(self._jobs.values())

    def run_async(self, job: Job, fn) -> None:
        def wrapper() -> None:
            try:
                fn(job)
            except Exception as e:  # noqa: BLE001
                job.status = "error"
                # Surface the real cause (bad key, 502, network) in the UI
                # rather than leaving the browser polling a dead job.
                job.error = f"{type(e).__name__}: {e}"
                job.message = "failed"
                traceback.print_exc()

        threading.Thread(target=wrapper, daemon=True).start()


registry = JobRegistry()
