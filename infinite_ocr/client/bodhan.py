"""Bodhan `indic-ocr` client.

Contract (console.bodhan.ai/api-docs):
  * POST https://api.bodhan.ai/v1/chat/completions, model "indic-ocr"
  * one PNG/JPEG page per request, as a base64 data URL image_url part
  * returns choices[0].message.content (Markdown) PLUS a top-level `blocks`
    array with order/label/type/bbox_xyxy/conf/text
  * billed per page; no `language` parameter (the model is script-agnostic)

Two failure modes drive the design:

  * `max_tokens` applies PER BLOCK (default 2048); one block exceeding it
    502s the whole page. So escalate 2048 -> 4096 -> 8192, then fall back
    from `html` to the smaller `markdown` table format.
  * 401 means a bad key and must never be retried.
"""

from __future__ import annotations

import asyncio
import base64
import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

BASE_URL = os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai")
MODEL = "indic-ocr"

# Per-block token ladder: a dense table may need 4096 as HTML yet fit 2048
# as Markdown.
MAX_TOKENS_LADDER = (2048, 4096, 8192)

RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504})


class BodhanAuthError(RuntimeError):
    """401 -- missing/invalid/expired key. Fail fast, never retry."""


class BodhanPageError(RuntimeError):
    """The page could not be transcribed after exhausting all fallbacks."""


@dataclass
class OCRResult:
    """Raw API response plus the settings that produced it."""

    markdown: str
    blocks: list[dict[str, Any]]
    raw: dict[str, Any]
    max_tokens: int
    table_format: str
    attempts: int = 1
    usage: dict[str, Any] = field(default_factory=dict)


def _data_url(path: str | Path) -> str:
    p = Path(path)
    mime = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    b64 = base64.b64encode(p.read_bytes()).decode()
    return f"data:{mime};base64,{b64}"


def _is_truncation(payload: dict[str, Any]) -> bool:
    """Distinguish a max_tokens overflow from an undecodable image.

    The docs say the `detail` field separates the two causes of 502.
    """
    blob = (str(payload.get("detail", "")) + str(payload.get("error", ""))).lower()
    return any(k in blob for k in ("max_tokens", "token", "truncat", "length"))


class BodhanOCR:
    """Async client with rate-limit awareness and per-page fallback."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = BASE_URL,
        concurrency: int = 4,
        timeout: float = 180.0,
        table_format: str = "html",
    ) -> None:
        key = api_key or os.environ.get("BODHAN_API_KEY")
        if not key:
            raise BodhanAuthError(
                "BODHAN_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        self._key = key
        self._base_url = base_url.rstrip("/")
        self._table_format = table_format
        self._concurrency = concurrency
        self._sem = asyncio.Semaphore(concurrency)
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
            },
        )

    async def __aenter__(self) -> "BodhanOCR":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    def _adapt_concurrency(self, resp: httpx.Response) -> None:
        """Shrink the in-flight budget if the server advertises a lower cap."""
        raw = resp.headers.get("x-ratelimit-limit-parallel-requests")
        if not raw:
            return
        try:
            limit = int(raw)
        except ValueError:
            return
        if 0 < limit < self._concurrency:
            self._concurrency = limit
            self._sem = asyncio.Semaphore(limit)

    def _body(self, data_url: str, max_tokens: int, table_format: str) -> dict[str, Any]:
        return {
            "model": MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": data_url}}],
                }
            ],
            "max_tokens": max_tokens,
            "table_format": table_format,
        }

    async def _post(self, body: dict[str, Any], *, max_retries: int = 4) -> dict[str, Any]:
        """POST with backoff. Raises on 401; returns parsed JSON otherwise."""
        url = f"{self._base_url}/v1/chat/completions"
        last: httpx.Response | None = None
        for attempt in range(max_retries):
            async with self._sem:
                try:
                    resp = await self._client.post(url, json=body)
                except (httpx.TimeoutException, httpx.TransportError):
                    if attempt == max_retries - 1:
                        raise
                    await asyncio.sleep(2**attempt + random.random())
                    continue

            self._adapt_concurrency(resp)

            if resp.status_code == 401:
                raise BodhanAuthError("401 from Bodhan: check BODHAN_API_KEY.")
            if resp.status_code == 200:
                return resp.json()

            last = resp
            # 502 is usually deterministic truncation: surface it so the
            # caller can escalate max_tokens instead of burning retries.
            if resp.status_code == 502 or resp.status_code not in RETRY_STATUS:
                break
            await asyncio.sleep(2**attempt + random.random())

        payload: dict[str, Any] = {}
        if last is not None:
            try:
                payload = last.json()
            except Exception:
                payload = {"detail": last.text[:500]}
            payload["_status"] = last.status_code
        return payload

    async def ocr_page(self, image_path: str | Path) -> OCRResult:
        """OCR one page image, escalating max_tokens then simplifying tables."""
        data_url = _data_url(image_path)
        attempts = 0
        last_payload: dict[str, Any] = {}

        # Ladder 1: raise the per-block token budget.
        for max_tokens in MAX_TOKENS_LADDER:
            attempts += 1
            payload = await self._post(self._body(data_url, max_tokens, self._table_format))
            if "choices" in payload:
                return self._to_result(payload, max_tokens, self._table_format, attempts)
            last_payload = payload
            if not _is_truncation(payload):
                break  # undecodable image -- escalating tokens will not help

        # Ladder 2: HTML tables are much larger than Markdown ones, so
        # flattening is the last resort before giving up on the page.
        if self._table_format != "markdown" and _is_truncation(last_payload):
            attempts += 1
            payload = await self._post(
                self._body(data_url, MAX_TOKENS_LADDER[-1], "markdown")
            )
            if "choices" in payload:
                return self._to_result(payload, MAX_TOKENS_LADDER[-1], "markdown", attempts)
            last_payload = payload

        raise BodhanPageError(
            f"OCR failed for {image_path} after {attempts} attempt(s): "
            f"status={last_payload.get('_status')} detail={last_payload.get('detail')}"
        )

    @staticmethod
    def _to_result(
        payload: dict[str, Any], max_tokens: int, table_format: str, attempts: int
    ) -> OCRResult:
        choices = payload.get("choices") or [{}]
        markdown = (choices[0].get("message") or {}).get("content", "") or ""
        return OCRResult(
            markdown=markdown,
            blocks=payload.get("blocks") or [],
            raw=payload,
            max_tokens=max_tokens,
            table_format=table_format,
            attempts=attempts,
            usage=payload.get("usage") or {},
        )
