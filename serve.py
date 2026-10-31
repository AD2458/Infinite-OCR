"""Start the Infinite OCR web UI.

    .venv\\Scripts\\python.exe serve.py

Then open http://127.0.0.1:8000
"""

from __future__ import annotations

import os
import webbrowser

import uvicorn

import infinite_ocr  # noqa: F401  -- imports load .env so the key is available


def main() -> None:
    if not os.environ.get("BODHAN_API_KEY"):
        print("WARNING: BODHAN_API_KEY not set. The UI will load, but OCR will fail.")
        print("         Put BODHAN_API_KEY=... in a .env file in the project root.\n")
    url = "http://127.0.0.1:8000"
    print(f"Infinite OCR running at {url}  (Ctrl+C to stop)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    uvicorn.run("infinite_ocr.api.app:app", host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
