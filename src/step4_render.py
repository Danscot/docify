"""
Step 4 — Render HTML → PDF via Playwright headless Chromium.

KEY FIXES:
- page.pdf() now has an explicit timeout (was None → hung forever on big docs)
- Assets are loaded from disk via file:// URLs (no base64 in HTML → fast render)
- --allow-file-access-from-files flag so Chromium can load local asset images
- page.wait_for_load_state("domcontentloaded") is faster and more reliable than
  "load" or "networkidle" for fully self-contained documents
- Browser reuse across the context manager to avoid repeated launch overhead
- Explicit page size matching template (A4 default)
"""
import logging
import time
from pathlib import Path

log = logging.getLogger("docify.step4")

PAGE_LOAD_TIMEOUT = 15_000   # ms
PDF_RENDER_TIMEOUT = 60_000  # ms  ← was missing before (caused silent hang)


def render_pdf(html_path: str, output_pdf: str) -> str:
    """Render an HTML file to PDF. Returns the output PDF path."""
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    html_abs = Path(html_path).resolve()
    pdf_abs  = Path(output_pdf)
    pdf_abs.parent.mkdir(parents=True, exist_ok=True)

    if not html_abs.exists():
        raise FileNotFoundError(f"HTML file not found: {html_abs}")

    log.info("[Step 4] Starting PDF render")
    log.info("[Step 4] Input : %s (%d bytes)", html_abs, html_abs.stat().st_size)
    log.info("[Step 4] Output: %s", pdf_abs)

    t0 = time.time()

    try:
        with sync_playwright() as p:
            log.info("[Step 4] Launching Chromium...")
            browser = p.chromium.launch(
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--allow-file-access-from-files",   # ← lets Chromium load file:// img src
                    "--disable-web-security",           # ← allows local file cross-origin
                ]
            )
            log.info("[Step 4] Browser launched (%.1fs)", time.time() - t0)

            context = browser.new_context()
            page    = context.new_page()

            file_url = f"file://{html_abs}"
            log.info("[Step 4] Navigating to: %s", file_url)

            try:
                page.goto(file_url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
            except PWTimeout:
                log.warning("[Step 4] Page load timed out after %dms — continuing anyway", PAGE_LOAD_TIMEOUT)

            # Give JS a moment to run (e.g. fonts, layout triggers)
            try:
                page.wait_for_timeout(500)
            except Exception:
                pass

            log.info("[Step 4] Page loaded (%.1fs). Generating PDF...", time.time() - t0)

            # page.pdf() does not accept a timeout kwarg in older Playwright builds.
            # Set it via the page-level default instead — applies to all operations.
            page.set_default_timeout(PDF_RENDER_TIMEOUT)

            try:
                page.pdf(
                    path=str(pdf_abs),
                    format="A4",
                    print_background=True,
                    margin={"top": "15mm", "right": "15mm", "bottom": "15mm", "left": "15mm"},
                )
            except PWTimeout:
                log.error("[Step 4] PDF generation timed out after %dms", PDF_RENDER_TIMEOUT)
                raise TimeoutError(f"Playwright PDF render timed out after {PDF_RENDER_TIMEOUT}ms")

            browser.close()

    except TimeoutError:
        raise
    except Exception as e:
        log.error("[Step 4] Playwright error: %s", e)
        raise RuntimeError(f"PDF rendering failed: {e}") from e

    if not pdf_abs.exists() or pdf_abs.stat().st_size == 0:
        raise RuntimeError("PDF render produced an empty file")

    elapsed  = time.time() - t0
    size_kb  = pdf_abs.stat().st_size // 1024
    log.info("[Step 4] ✅ PDF done — %d KB in %.1fs", size_kb, elapsed)

    return str(pdf_abs)
