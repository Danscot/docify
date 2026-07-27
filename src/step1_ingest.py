"""
Step 1 — PDF → PNG images + embedded image extraction.

Outputs:
  {output_dir}/pages/page_001.png  ...   (full-page renders for vision analysis)
  {output_dir}/assets/img_001.png  ...   (extracted embedded images: logos, photos, charts)
  {output_dir}/assets/manifest.json       (list of asset paths + bounding-box metadata)
"""
import json
import logging
import os
from pathlib import Path

log = logging.getLogger("docify.step1")


def pdf_to_images(pdf_path: str, output_dir: str, dpi: int = 150) -> list[str]:
    """
    Convert each PDF page to a PNG.
    DPI lowered to 150 (was 200) to keep base64 payloads small for the API.
    Returns list of page image paths.
    """
    from pdf2image import convert_from_path

    output = Path(output_dir)
    pages_dir = output / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    log.info("[Step 1] Converting PDF → images: %s  (dpi=%d)", pdf_path, dpi)

    pages = convert_from_path(pdf_path, dpi=dpi)
    log.info("[Step 1] Found %d page(s)", len(pages))

    paths = []
    for i, page in enumerate(pages):
        img_path = pages_dir / f"page_{i+1:03d}.png"
        page.save(str(img_path), "PNG")
        log.info("[Step 1]   saved page %d → %s", i + 1, img_path)
        paths.append(str(img_path))

    # --- Extract embedded images (logos, photos) ---
    assets = _extract_embedded_images(pdf_path, output)
    log.info("[Step 1] Extracted %d embedded asset(s)", len(assets))

    return paths


def _extract_embedded_images(pdf_path: str, output_dir: Path) -> list[dict]:
    """
    Extract embedded raster images from the PDF using PyMuPDF (fitz).
    Falls back gracefully if fitz is not installed.
    Returns list of {path, page, width, height} dicts and writes manifest.json.
    """
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "assets" / "manifest.json"

    assets = []
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(pdf_path)
        idx = 0
        for page_num in range(len(doc)):
            page = doc[page_num]
            image_list = page.get_images(full=True)
            for img_info in image_list:
                xref = img_info[0]
                try:
                    base_image = doc.extract_image(xref)
                    img_bytes  = base_image["image"]
                    ext        = base_image["ext"]          # png / jpeg / etc.
                    w, h       = base_image["width"], base_image["height"]

                    # Skip tiny images (icons, bullet points, noise)
                    if w < 30 or h < 30:
                        continue

                    idx += 1
                    fname = assets_dir / f"img_{idx:03d}.{ext}"
                    fname.write_bytes(img_bytes)

                    assets.append({
                        "path":   str(fname),
                        "page":   page_num + 1,
                        "width":  w,
                        "height": h,
                        "ext":    ext,
                    })
                    log.info("[Step 1]   asset %d: %dx%d px (page %d) → %s", idx, w, h, page_num + 1, fname.name)
                except Exception as e:
                    log.warning("[Step 1]   could not extract xref %d: %s", xref, e)

        doc.close()

    except ImportError:
        log.warning("[Step 1] PyMuPDF not installed — skipping embedded image extraction. "
                    "Run: pip install pymupdf")
    except Exception as e:
        log.warning("[Step 1] Embedded image extraction failed: %s", e)

    manifest_path.write_text(json.dumps(assets, indent=2), encoding="utf-8")
    return assets
