"""
Step 2 — Two-pass vision analysis: styles + HTML skeleton.

IMAGE STRATEGY:
- Pass A: extract style tokens (colors, fonts)
- Pass B: extract HTML skeleton — AI marks logo position with a sentinel
           placeholder like [LOGO] or leaves an <img> tag
- Post-process: we replace all logo/image references with real base64
  data URIs from the extracted assets. AI never sees or handles base64.
"""
import base64
import json
import logging
import re
import time
from pathlib import Path

import httpx
from src._streaming import stream_completion

log = logging.getLogger("docify.step2")

MAX_PAGES      = 6
API_CONNECT_TO = 10
API_READ_TO    = 180


# ── Helpers ────────────────────────────────────────────────────────────────────

def _encode_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _make_client():
    from openai import OpenAI
    from django.conf import settings
    return OpenAI(
        api_key=settings.GEMMA_API_KEY,
        base_url=settings.GEMMA_BASE_URL,
        http_client=httpx.Client(
            timeout=httpx.Timeout(connect=API_CONNECT_TO, read=API_READ_TO,
                                  write=30.0, pool=5.0)
        ),
    )


def _strip_thinking(raw: str) -> str:
    """Remove <thought>/<thinking> blocks that some models emit."""
    text = re.sub(r'<thought[^>]*>.*?</thought[^>]*>', '', raw,  flags=re.DOTALL|re.IGNORECASE)
    text = re.sub(r'<thinking[^>]*>.*?</thinking[^>]*>', '', text, flags=re.DOTALL|re.IGNORECASE)
    text = re.sub(r'<thought[^>]*>.*',  '', text, flags=re.DOTALL|re.IGNORECASE)
    text = re.sub(r'<thinking[^>]*>.*', '', text, flags=re.DOTALL|re.IGNORECASE)
    result = text.strip()
    if len(result) < len(raw.strip()):
        log.info("[Step 2] Stripped %d chars of model thinking", len(raw.strip()) - len(result))
    return result


def _extract_json(raw: str) -> dict:
    text = _strip_thinking(raw).strip()
    if "```" in text:
        for part in text.split("```"):
            p = part.strip()
            if p.startswith("json"): p = p[4:].strip()
            if p.startswith("{"): text = p; break
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e != -1:
        text = text[s:e+1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        text = re.sub(r',(\s*[}\]])', r'\1', text)
        return json.loads(text)


def _strip_fences(raw: str) -> str:
    text = _strip_thinking(raw).strip()
    if text.startswith("```"):
        lines = text.split("\n")
        inner = lines[1:]
        if inner and inner[-1].strip() == "```": inner = inner[:-1]
        text = "\n".join(inner).strip()
        if text.startswith("html"): text = text[4:].strip()
    return text


def _sample_pages(paths: list) -> list:
    n = len(paths)
    if n <= MAX_PAGES: return paths
    indices = {0, n-1}
    step = (n-1)/(MAX_PAGES-1)
    for i in range(1, MAX_PAGES-1): indices.add(round(i*step))
    sampled = [paths[i] for i in sorted(indices)]
    log.info("[Step 2] Sampling %d/%d pages", len(sampled), n)
    return sampled


def _load_asset_paths(pages_dir: Path) -> list:
    """Load extracted assets from step1 manifest."""
    manifest = pages_dir.parent / "assets" / "manifest.json"
    if not manifest.exists():
        log.info("[Step 2] No asset manifest found at %s", manifest)
        return []
    assets = json.loads(manifest.read_text())
    valid = [a for a in assets if Path(a["path"]).exists()]
    log.info("[Step 2] Assets on disk: %d/%d", len(valid), len(assets))
    return valid


def _image_parts(sampled: list) -> list:
    parts = []
    for i, p in enumerate(sampled):
        b64 = _encode_image(p)
        parts.append({"type":"image_url","image_url":{"url":f"data:image/png;base64,{b64}"}})
        log.info("[Step 2]   page %d/%d: %s (%dKB)",
                 i+1, len(sampled), Path(p).name, Path(p).stat().st_size//1024)
    return parts


# ── Asset injection — the key fix ──────────────────────────────────────────────

def _inject_assets_into_skeleton(skeleton_html: str, assets: list) -> str:
    """
    Replace ALL logo/image references in the skeleton with real base64 data URIs.

    The AI may produce any of these patterns for a logo:
      - [LOGO] sentinel text
      - <img src="[LOGO]"> or <img src="LOGO">
      - <img src="file:///..."> with the actual path
      - <img src="logo.png"> or any placeholder filename
      - An empty <img> tag

    We handle all of them, plus we scan for existing <img> tags whose src
    isn't already a data: URI and replace them.
    """
    if not assets:
        log.info("[Step 2] No assets to inject")
        return skeleton_html

    # Build base64 data URIs for each asset
    asset_uris = []
    for a in assets:
        p = Path(a["path"])
        if not p.exists():
            log.warning("[Step 2] Asset not found: %s", p)
            continue
        ext = a.get("ext", "jpeg").lower()
        mime = {
            "jpeg": "image/jpeg", "jpg": "image/jpeg",
            "png":  "image/png",  "gif": "image/gif",
            "webp": "image/webp",
        }.get(ext, "image/jpeg")
        b64  = base64.b64encode(p.read_bytes()).decode()
        uri  = f"data:{mime};base64,{b64}"
        asset_uris.append({
            **a,
            "uri":  uri,
            "mime": mime,
            "is_logo": a.get("width", 0) < 300 and a.get("height", 0) < 300,
        })
        log.info("[Step 2]   Encoded asset: %s (%dx%d, %dKB b64)",
                 p.name, a.get("width",0), a.get("height",0), len(b64)//1024)

    if not asset_uris:
        return skeleton_html

    logo_uri  = asset_uris[0]["uri"]
    logo_mime = asset_uris[0]["mime"]
    logo_w    = assets[0].get("width", 80)
    logo_h    = assets[0].get("height", 80)

    # Sensible display size for logo
    display_h = min(logo_h, 80)
    display_w = int(logo_w * display_h / logo_h) if logo_h else 80

    logo_img_tag = (
        f'<img src="{logo_uri}" '
        f'style="width:{display_w}px;height:{display_h}px;object-fit:contain;" '
        f'alt="logo" />'
    )

    html = skeleton_html

    # ── Strategy 1: replace [LOGO] text sentinel ───────────────────────────
    if "[LOGO]" in html:
        log.info("[Step 2] Replacing [LOGO] sentinel")
        html = html.replace("[LOGO]", logo_img_tag)

    # ── Strategy 2: replace <img> tags with non-data src ──────────────────
    # Find all <img> tags whose src is NOT already a data: URI
    def replace_img(m):
        tag = m.group(0)
        src_match = re.search(r'src=["\']([^"\']*)["\']', tag)
        if not src_match:
            # No src at all — inject logo
            return logo_img_tag
        src = src_match.group(1)
        if src.startswith("data:"):
            return tag   # already a data URI, leave it
        if "file://" in src:
            # AI used a file:// path — replace with the matching asset or logo
            matched_uri = _match_asset_by_path(src, asset_uris) or logo_uri
            return re.sub(r'src=["\'][^"\']*["\']', f'src="{matched_uri}"', tag)
        # Generic placeholder filename (logo.png, image.jpg, etc.)
        return logo_img_tag

    html = re.sub(r'<img\b[^>]*>', replace_img, html, flags=re.IGNORECASE)

    # ── Strategy 3: inject logo if no <img> tag exists at all ─────────────
    if "<img" not in html.lower():
        log.warning("[Step 2] No <img> tag in skeleton — injecting logo into header")
        # Try to find the header div and prepend the logo
        header_match = re.search(
            r'(<(?:div|header)[^>]*(?:header|logo)[^>]*>)',
            html, flags=re.IGNORECASE
        )
        if header_match:
            insert_pos = header_match.end()
            html = html[:insert_pos] + "\n  " + logo_img_tag + "\n" + html[insert_pos:]
        else:
            # Fallback: insert right after <body>
            html = re.sub(
                r'(<body[^>]*>)',
                r'\1\n<div style="padding:10px">' + logo_img_tag + '</div>',
                html, flags=re.IGNORECASE
            )

    log.info("[Step 2] Asset injection complete — %d asset(s) processed", len(asset_uris))
    return html


def _match_asset_by_path(file_url: str, asset_uris: list) -> str | None:
    """Try to match a file:// URL to one of our extracted assets by filename."""
    fname = Path(file_url.replace("file://", "")).name.lower()
    for a in asset_uris:
        if Path(a["path"]).name.lower() == fname:
            return a["uri"]
    return None


# ── Skeleton sanitizer ─────────────────────────────────────────────────────────

_THINKING_MARKERS = [
    "the user wants", "let me ", "i need to", "looking at",
    "looking closer", "let's refine", "wait,", "-> ",
    "the currency", "must be kept",
]

def _sanitize_skeleton(html: str) -> tuple:
    warnings = []
    lower = html.lower()
    body_start = lower.find("<body")
    if body_start == -1: body_start = 0
    body_content = lower[body_start:]

    contaminated = [m for m in _THINKING_MARKERS if m in body_content]
    if contaminated:
        warnings.append(f"Model reasoning leaked into skeleton: {contaminated[:3]}")
        log.warning("[Step 2] ⚠ Skeleton contamination: %s", contaminated[:3])

    if not html.strip().lower().startswith(("<!doctype", "<html")):
        raise ValueError(
            "Skeleton does not start with <!DOCTYPE html> — "
            "model returned reasoning text instead of HTML. Re-analyze."
        )
    if contaminated:
        raise ValueError(
            f"AI reasoning text leaked into skeleton ({contaminated[0]!r}). "
            "Re-analyze the template."
        )
    return html, warnings


# ── Pass A: styles ─────────────────────────────────────────────────────────────

STYLE_PROMPT = """Analyze these PDF page images and extract the visual style.
Output ONLY a valid JSON object. No markdown, no explanation, no thinking.
Use exactly this structure:
{
  "styles": {
    "primary_color": "#hex", "secondary_color": "#hex",
    "background_color": "#hex", "text_color": "#hex",
    "font_family": "Arial, sans-serif",
    "font_size_body": "11pt", "font_size_heading": "16pt",
    "font_size_subheading": "13pt", "line_height": "1.5",
    "page_margin": "20mm", "table_border_color": "#hex",
    "table_header_bg": "#hex", "table_header_text": "#hex"
  },
  "layout": {
    "has_header": true, "header_bg_color": null,
    "header_text_color": "#hex", "has_footer": true,
    "footer_bg_color": null, "columns": 1, "page_size": "A4"
  },
  "has_logo": true, "logo_position": "top-left", "has_images": false
}
"""

_STYLE_DEFAULTS = {
    "styles": {
        "primary_color": "#000000", "secondary_color": "#B0C4DE",
        "background_color": "#FFFFFF", "text_color": "#000000",
        "font_family": "Arial, Helvetica, sans-serif",
        "font_size_body": "11pt", "font_size_heading": "16pt",
        "font_size_subheading": "13pt", "line_height": "1.5",
        "page_margin": "20mm", "table_border_color": "#AAAAAA",
        "table_header_bg": "#000000", "table_header_text": "#FFFFFF",
    },
    "layout": {
        "has_header": True, "header_bg_color": None,
        "header_text_color": "#000000", "has_footer": True,
        "footer_bg_color": None, "columns": 1, "page_size": "A4",
    },
    "has_logo": True, "logo_position": "top-left", "has_images": False,
}


def _extract_json_from_anywhere(raw: str) -> dict:
    thought_content = ""
    m = re.search(r'<(?:thought|thinking)[^>]*>(.*?)</(?:thought|thinking)>',
                  raw, flags=re.DOTALL|re.IGNORECASE)
    if m: thought_content = m.group(1)
    m2 = re.search(r'<(?:thought|thinking)[^>]*>(.*)', raw, flags=re.DOTALL|re.IGNORECASE)
    if m2 and len(m2.group(1)) > len(thought_content): thought_content = m2.group(1)

    stripped = _strip_thinking(raw)

    def _try(text):
        text = text.strip()
        if not text: raise ValueError("empty")
        if "```" in text:
            for part in text.split("```"):
                p = part.strip()
                if p.startswith("json"): p = p[4:].strip()
                if p.startswith("{"): text = p; break
        s, e = text.find("{"), text.rfind("}")
        if s != -1 and e != -1:
            c = re.sub(r',(\s*[}\]])', r'\1', text[s:e+1])
            return json.loads(c)
        raise ValueError("no JSON")

    for src in [stripped, thought_content, raw]:
        try: return _try(src)
        except Exception: pass

    for src in [stripped, thought_content, raw]:
        s = src.find("{")
        if s == -1: continue
        frag = src[s:]
        for i in range(len(frag)-1, -1, -1):
            if frag[i] == "}":
                try:
                    return json.loads(re.sub(r',(\s*[}\]])', r'\1', frag[:i+1]))
                except Exception: continue
    raise ValueError("No parseable JSON found")


def _pass_a(client, img_parts, model) -> dict:
    log.info("[Step 2] Pass A — styles (max_tokens=8192)")
    t0 = time.time()
    raw = stream_completion(
        client, model,
        messages=[{"role":"user","content": img_parts + [{"type":"text","text":STYLE_PROMPT}]}],
        max_tokens=8192,
        label="Step2-PassA",
    )
    log.info("[Step 2] Pass A done %.1fs", time.time()-t0)
    log.debug("[Step 2] Pass A raw (first 300): %s", raw[:300])
    try:
        result = _extract_json_from_anywhere(raw)
        log.info("[Step 2] Pass A OK — primary_color=%s",
                 result.get("styles", {}).get("primary_color", "?"))
        return result
    except Exception as e:
        log.warning("[Step 2] Pass A JSON parse failed (%s) — using safe defaults", e)
        return _STYLE_DEFAULTS


# ── Pass B: skeleton ───────────────────────────────────────────────────────────

SKELETON_PROMPT = """You are extracting the EXACT structure of a document as an HTML skeleton.

ABSOLUTE RULES:
1. NEVER change the currency (FCFA, XAF, €, etc.) — copy it exactly.
2. NEVER invent text — every label, column header, company name, address, legal text
   must be copied VERBATIM from the document images.
3. NEVER change the table structure — same columns, same headers word-for-word.
4. For the logo/images: write [LOGO] exactly where the logo appears.
   Do NOT write a file path. Do NOT write base64. Just write the text [LOGO].
   We will replace [LOGO] with the real image automatically after you finish.
5. Replace ONLY variable data with descriptive named tokens in DOUBLE curly braces.
   Examples: {{INVOICE_NUMBER}}, {{CLIENT_NAME}}, {{QUANTITY}}, {{TOTAL_XAF}}
   FORBIDDEN generic names: {{PLACEHOLDER}}, {{VALUE}}, {{TEXT}}, {{DATA}}
6. Keep ALL static text exactly: company name, slogan, column headers,
   row labels (Total HT, Payable au compte, etc.), footer address/phone/email,
   currency codes, registration numbers.
7. Use inline CSS only. Match colors and fonts from the style reference exactly.
8. Output ONLY the HTML starting with <!DOCTYPE html>. No explanation."""


def _pass_b(client, img_parts, styles, assets, model) -> str:
    log.info("[Step 2] Pass B — HTML skeleton")

    # Tell the model about assets but instruct it to use [LOGO] sentinel only
    asset_hint = ""
    if assets:
        log.info("[Step 2]   %d asset(s) detected — instructing model to use [LOGO] sentinel", len(assets))
        asset_hint = (
            f"\n\nASSETS DETECTED: {len(assets)} image(s) extracted from this PDF "
            f"(logo: {assets[0]['width']}x{assets[0]['height']}px). "
            "Write [LOGO] exactly where the logo appears — do not use file paths or base64."
        )

    prompt = (
        f"STYLE REFERENCE:\n```json\n{json.dumps(styles.get('styles',{}), indent=2)}\n```"
        f"{asset_hint}\n\n"
        + SKELETON_PROMPT
    )

    t0 = time.time()
    raw = stream_completion(
        client, model,
        messages=[{"role":"user","content": img_parts + [{"type":"text","text":prompt}]}],
        max_tokens=8192,
        label="Step2-PassB",
    )
    elapsed = time.time()-t0
    log.info("[Step 2] Pass B done %.1fs — %d chars", elapsed, len(raw))
    return _strip_fences(raw)


# ── Main entry point ───────────────────────────────────────────────────────────

def analyze_pages(image_paths: list, output_dir: str, model_name: str) -> dict:
    """
    Two-pass analysis:
      Pass A → styles JSON
      Pass B → HTML skeleton with {{PLACEHOLDER}} slots and [LOGO] sentinel
      Post-process → inject real base64 assets, replacing [LOGO] and all <img> tags
    """
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    log.info("[Step 2] Two-pass analysis — model=%s  pages=%d", model_name, len(image_paths))

    client    = _make_client()
    sampled   = _sample_pages(image_paths)
    assets    = _load_asset_paths(Path(image_paths[0]).parent)
    img_parts = _image_parts(sampled)

    # Pass A
    try:
        styles = _pass_a(client, img_parts, model_name)
    except Exception as e:
        log.error("[Step 2] Pass A failed: %s", e)
        raise

    # Pass B
    try:
        skeleton_raw = _pass_b(client, img_parts, styles, assets, model_name)
    except Exception as e:
        log.error("[Step 2] Pass B failed: %s", e)
        raise

    # Sanitize
    try:
        skeleton_html, _ = _sanitize_skeleton(skeleton_raw)
    except ValueError as e:
        log.error("[Step 2] Skeleton contamination: %s", e)
        raise

    # ── POST-PROCESS: inject real base64 assets ─────────────────────────────
    if assets:
        log.info("[Step 2] Injecting %d asset(s) as base64 data URIs", len(assets))
        skeleton_html = _inject_assets_into_skeleton(skeleton_html, assets)
    else:
        log.info("[Step 2] No assets to inject")

    # Report placeholders
    ph = re.findall(r'\{\{([A-Z0-9_]+)\}\}', skeleton_html)
    log.info("[Step 2] Placeholders: %s", sorted(set(ph)))

    # Check logo injection worked
    if "[LOGO]" in skeleton_html:
        log.warning("[Step 2] [LOGO] sentinel still in skeleton after injection — no asset matched")

    has_img = "<img" in skeleton_html.lower()
    log.info("[Step 2] Has <img> tag after injection: %s", has_img)

    # Persist
    template = {
        **styles,
        "total_pages": len(image_paths),
        "embedded_assets": [
            {"index": i, "path": a["path"], "page": a["page"],
             "width": a["width"], "height": a["height"], "ext": a.get("ext","png")}
            for i, a in enumerate(assets)
        ],
    }

    tpl_path      = output / "template.json"
    skeleton_path = output / "skeleton.html"

    tpl_path.write_text(json.dumps(template, indent=2, ensure_ascii=False), encoding="utf-8")
    skeleton_path.write_text(skeleton_html, encoding="utf-8")

    log.info("[Step 2] ✅ template.json → %s (%dKB)",
             tpl_path, tpl_path.stat().st_size//1024)
    log.info("[Step 2] ✅ skeleton.html → %s (%dKB)",
             skeleton_path, skeleton_path.stat().st_size//1024)

    return template
