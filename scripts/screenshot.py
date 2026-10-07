"""Capture README screenshots of a running app.

Usage: uv run --group docs python scripts/screenshot.py [base-url] [out-dir]
       (defaults: http://127.0.0.1:8000 and docs/)
Start the app on the database to show first (`uv run uvicorn triage.main:create_app --factory`).
Needs a headless Chromium: `uv run --group docs playwright install chromium-headless-shell`.
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGES = {"dashboard.png": "/", "review-queue.png": "/review"}
VIEWPORT = {"width": 1280, "height": 860}


def main(base: str, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=2, color_scheme="light")
        for name, path in PAGES.items():
            page.goto(base + path, wait_until="networkidle")
            page.screenshot(path=out_dir / name, full_page=False)
            print(f"  saved {out_dir / name}")
        browser.close()


if __name__ == "__main__":
    base = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    main(base, Path(sys.argv[2]) if len(sys.argv) > 2 else Path("docs"))
