"""Content-addressed cache for raw OCR responses.

The single most important cost-control mechanism in the project. Because the
key is the sha256 of the page PNG, a page is billed once *ever* -- re-running
the pipeline, or iterating on the stitching logic, costs nothing.

Responses are stored verbatim. Never parse-and-discard: every downstream
layer must stay replayable offline, forever, without spending credits.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class PageCache:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.raw_dir = self.root / "raw"
        self.pages_dir = self.root / "pages"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.pages_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, sha: str) -> Path:
        return self.raw_dir / f"{sha}.json"

    def has(self, sha: str) -> bool:
        return self._path(sha).exists()

    def get(self, sha: str) -> dict[str, Any] | None:
        p = self._path(sha)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None  # corrupt entry: treat as a miss and re-fetch

    def put(self, sha: str, payload: dict[str, Any]) -> None:
        # Write to a temp file then replace, so an interrupted run can never
        # leave a half-written JSON that would silently poison later runs.
        p = self._path(sha)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)

    def stats(self) -> dict[str, int]:
        return {
            "cached_pages": len(list(self.raw_dir.glob("*.json"))),
            "page_images": len(list(self.pages_dir.glob("*.png"))),
        }
