"""
Step 2 — Two-pass vision analysis: styles + HTML skeleton.

ANTI-HALLUCINATION DESIGN:
- Pass A: extract style tokens (colors, fonts) — fast, <1K tokens output
- Pass B: extract literal HTML skeleton with {{PLACEHOLDERS}} for variable fields
  The prompt uses concrete negative examples to prevent the most common failures:
  currency substitution, company name invention, column header changes.
"""
import base64
import json
import logging
import re
import time
from pathlib import Path

import httpx

log = logging.getLogger("docify.step2")

MAX_PAGES      = 6
API_CONNECT_TO = 10
API_READ_TO    = 180


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
    """
    Remove <thought>...</thought> and <thinking>...</thinking> blocks.
    Gemini with extended thinking emits these before the actual output.
    They contain the model's chain-of-thought which breaks JSON parsing.
    """
    import re as _re
    text = _re.sub(r'<thought[^>]*>.*?</thought[^>]*>', '', raw,  flags=_re.DOTALL | _re.IGNORECASE)
    text = _re.sub(r'<thinking[^>]*>.*?</thinking[^>]*>', '', text, flags=_re.DOTALL | _re.IGNORECASE)
    # Handle truncated/unclosed thinking blocks at end of response
    text = _re.sub(r'<thought[^>]*>.*',  '', text, flags=_re.DOTALL | _re.IGNORECASE)
    text = _re.sub(r'<thinking[^>]*>.*', '', text, flags=_re.DOTALL | _re.IGNORECASE)
    result = text.strip()
    if len(result) < len(raw.strip()):
        log.info("[Step 2] Stripped %d chars of model thinking",
                 len(raw.strip()) - len(result))
    return result


def _extract_json(raw: str) -> dict:
    # Strip thinking blocks FIRST — they cause unterminated string JSON errors
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
        import re as _re
        # Remove trailing commas before } or ] and retry
        text = _re.sub(r',(\s*[}\]])', r'\1', text)
        return json.loads(text)


def _strip_fences(raw: str) -> str:
    # Strip thinking blocks before fence detection
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
    manifest = pages_dir.parent / "assets" / "manifest.json"
    if not manifest.exists(): return []
    assets = json.loads(manifest.read_text())
    valid = [a for a in assets if Path(a["path"]).exists()]
    log.info("[Step 2] Assets on disk: %d", len(valid))
    return valid


def _image_parts(sampled: list) -> list:
    parts = []
    for i, p in enumerate(sampled):
        b64 = _encode_image(p)
        parts.append({"type":"image_url","image_url":{"url":f"data:image/png;base64,{b64}"}})
        log.info("[Step 2]   page %d/%d: %s (%dKB)", i+1, len(sampled),
                 Path(p).name, Path(p).stat().st_size//1024)
    return parts


# ── Pass A ────────────────────────────────────────────────────────────────────

# Safe defaults used when the model returns truncated/unparseable JSON.
# Colors are cosmetic — wrong defaults are infinitely better than a crash.
_STYLE_DEFAULTS = {
    "styles": {
        "primary_color": "#000000",
        "secondary_color": "#B0C4DE",
        "background_color": "#FFFFFF",
        "text_color": "#000000",
        "font_family": "Arial, Helvetica, sans-serif",
        "font_size_body": "11pt",
        "font_size_heading": "16pt",
        "font_size_subheading": "13pt",
        "line_height": "1.5",
        "page_margin": "20mm",
        "table_border_color": "#AAAAAA",
        "table_header_bg": "#000000",
        "table_header_text": "#FFFFFF",
    },
    "layout": {
        "has_header": True,
        "header_bg_color": None,
        "header_text_color": "#000000",
        "has_footer": True,
        "footer_bg_color": None,
        "columns": 1,
        "page_size": "A4",
    },
    "has_logo": True,
    "logo_position": "top-left",
    "has_images": False,
}

STYLE_PROMPT = """
Analyze these PDF page images and extract the visual style.
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



def _extract_json_from_anywhere(raw: str) -> dict:
    """
    Try to extract JSON from a model response using every strategy:
    1. After stripping thinking blocks
    2. Inside the thinking block (model put it there before truncation)
    3. Largest parseable JSON fragment (handles token-truncated output)
    Raises ValueError only if nothing works.
    """
    import re as _re

    def _try_parse(text: str) -> dict:
        text = text.strip()
        if not text:
            raise ValueError("empty")
        # Strip ``` fences
        if "```" in text:
            for part in text.split("```"):
                p = part.strip()
                if p.startswith("json"): p = p[4:].strip()
                if p.startswith("{"): text = p; break
        # Find outermost {}
        s, e = text.find("{"), text.rfind("}")
        if s != -1 and e != -1:
            candidate = text[s:e+1]
            candidate = _re.sub(r',\s*([}\]])', r'\1', candidate)
            return json.loads(candidate)
        raise ValueError("no JSON object found")

    # Capture thought block content before stripping
    thought_content = ""
    m = _re.search(r'<(?:thought|thinking)[^>]*>(.*?)</(?:thought|thinking)>',
                   raw, flags=_re.DOTALL | _re.IGNORECASE)
    if m:
        thought_content = m.group(1)
    # Also grab unclosed block content
    m2 = _re.search(r'<(?:thought|thinking)[^>]*>(.*)', raw,
                    flags=_re.DOTALL | _re.IGNORECASE)
    if m2 and len(m2.group(1)) > len(thought_content):
        thought_content = m2.group(1)

    stripped = _strip_thinking(raw)

    # Strategy 1: clean output after thinking
    try:
        return _try_parse(stripped)
    except Exception:
        pass

    # Strategy 2: JSON was inside the thought block
    if thought_content:
        try:
            return _try_parse(thought_content)
        except Exception:
            pass

    # Strategy 3: token-truncated JSON — try largest parseable prefix
    for text in [stripped, thought_content, raw]:
        s = text.find("{")
        if s == -1:
            continue
        fragment = text[s:]
        for i in range(len(fragment) - 1, -1, -1):
            if fragment[i] == "}":
                try:
                    candidate = _re.sub(r',\s*([}\]])', r'\1', fragment[:i+1])
                    return json.loads(candidate)
                except Exception:
                    continue

    raise ValueError("No parseable JSON found in model response")


def _pass_a(client, img_parts, model) -> dict:
    log.info("[Step 2] Pass A — styles (max_tokens=8192 to survive thinking overhead)")
    t0 = time.time()
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": img_parts + [{"type": "text", "text": STYLE_PROMPT}]}],
        max_tokens=8192,   # was 1024 — thinking consumed all tokens leaving no room for JSON
    )
    log.info("[Step 2] Pass A done %.1fs", time.time() - t0)
    raw = resp.choices[0].message.content or ""
    log.debug("[Step 2] Pass A raw (first 300): %s", raw[:300])

    try:
        result = _extract_json_from_anywhere(raw)
        log.info("[Step 2] Pass A parsed OK — primary_color=%s",
                 result.get("styles", {}).get("primary_color", "?"))
        return result
    except Exception as e:
        log.warning("[Step 2] Pass A JSON parse failed (%s) — using safe defaults", e)
        return _STYLE_DEFAULTS


# ── Pass B ────────────────────────────────────────────────────────────────────

SKELETON_SYSTEM = """You are a document skeleton extractor. Your output must be a pixel-faithful
HTML reproduction of the source PDF with variable fields replaced by descriptive named tokens.

ABSOLUTE RULES — violating any of these is a critical failure:

1. NEVER change the currency. If you see "FCFA", "XAF", "€", "CFA" — copy it exactly.
   WRONG: changing FCFA to $ or € or any other currency.

2. NEVER invent text. Every label, column header, company name, address, legal text,
   registration number, phone, email must be copied VERBATIM from the document.
   WRONG: "Nom du Client" when the document says "Facturé à".
   WRONG: "Prestation de services" when the document says "Service des repas collectifs".
   WRONG: "Paris" or "France" when the document says "Douala" or "Cameroon".

3. NEVER change the table structure. Reproduce column headers word-for-word.

4. Replace ONLY variable data with descriptive named tokens using DOUBLE curly braces.
   Tokens MUST be descriptive — named after what they represent, in SCREAMING_SNAKE_CASE.

   CORRECT token examples:
     {{INVOICE_NUMBER}}   for an invoice/facture number
     {{INVOICE_DATE}}     for the date
     {{CLIENT_NAME}}      for the client's name
     {{CLIENT_ADDRESS}}   for the client's address
     {{QUANTITY}}         for a quantity/quantité
     {{UNIT_PRICE}}       for a unit price/prix unitaire
     {{LINE_TOTAL}}       for a line total
     {{GRAND_TOTAL}}      for the grand total
     {{AMOUNT_IN_WORDS}}  for the written-out amount
     {{BANK_ACCOUNT}}     for a bank account number
     {{PERIOD_START}}     for a start date/period
     {{PERIOD_END}}       for an end date/period

   WRONG — never use these generic tokens:
     {{PLACEHOLDER}}  ← TOO GENERIC, forbidden
     {{VALUE}}        ← TOO GENERIC, forbidden
     {{TEXT}}         ← TOO GENERIC, forbidden
     {{FIELD}}        ← TOO GENERIC, forbidden
     {{DATA}}         ← TOO GENERIC, forbidden

5. Keep ALL static text exactly as-is:
   - Company name, slogan, service bullets
   - Table column headers (verbatim from PDF)
   - Row labels: "Total HT", "Total TTC", "Payable au compte:", etc.
   - Footer: full address, phone, email, registration numbers, tax regime
   - Currency codes/symbols: FCFA, XAF, €, etc. — never change these

6. Use inline CSS only. Match colors and fonts exactly from the style reference.
7. Output ONLY the HTML starting with <!DOCTYPE html>. No explanation, no markdown."""


def _pass_b(client, img_parts, styles, assets, model) -> str:
    log.info("[Step 2] Pass B — HTML skeleton")

    asset_hint = ""
    if assets:
        lines = ["\nASSETS (use exact file:// paths, do not change):"]
        for i, a in enumerate(assets):
            role = "LOGO" if i == 0 and styles.get("has_logo") else f"IMAGE_{i+1}"
            lines.append(f'  [{role}]: <img src="file://{a["path"]}" style="max-height:80px;width:auto;" alt="{role}" />')
        asset_hint = "\n".join(lines)

    prompt = (
        f"STYLE REFERENCE:\n```json\n{json.dumps(styles.get('styles',{}), indent=2)}\n```"
        f"{asset_hint}\n\n"
        + SKELETON_SYSTEM
    )

    t0 = time.time()
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role":"user","content": img_parts + [{"type":"text","text":prompt}]}],
        max_tokens=8192,
    )
    elapsed = time.time()-t0
    log.info("[Step 2] Pass B done %.1fs", elapsed)

    finish = getattr(resp.choices[0], "finish_reason", None)
    if finish and finish != "stop":
        log.warning("[Step 2] Pass B finish_reason=%s — may be truncated!", finish)

    raw = resp.choices[0].message.content or ""
    log.info("[Step 2] Pass B: %d chars", len(raw))
    html = _strip_fences(raw)

    # Detect model reasoning contamination before saving anything
    html, warnings = _sanitize_skeleton(html)

    ph = re.findall(r'\{\{([A-Z0-9_]+)\}\}', html)
    log.info("[Step 2] Placeholders found: %s", sorted(set(ph)))

    return html


# ── Entry point ───────────────────────────────────────────────────────────────

def analyze_pages(image_paths: list, output_dir: str, model_name: str) -> dict:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    log.info("[Step 2] Two-pass analysis — model=%s  pages=%d", model_name, len(image_paths))

    client   = _make_client()
    sampled  = _sample_pages(image_paths)
    assets   = _load_asset_paths(Path(image_paths[0]).parent)
    img_parts = _image_parts(sampled)

    # Pass A
    try:
        styles = _pass_a(client, img_parts, model_name)
    except Exception as e:
        log.error("[Step 2] Pass A failed: %s", e); raise

    # Pass B
    try:
        skeleton_html = _pass_b(client, img_parts, styles, assets, model_name)
    except Exception as e:
        log.error("[Step 2] Pass B failed: %s", e); raise

    # Persist
    template = {
        **styles,
        "total_pages": len(image_paths),
        "embedded_assets": [
            {"index":i,"path":a["path"],"page":a["page"],
             "width":a["width"],"height":a["height"],"ext":a.get("ext","png")}
            for i, a in enumerate(assets)
        ],
    }

    (output / "template.json").write_text(
        json.dumps(template, indent=2, ensure_ascii=False), encoding="utf-8")
    (output / "skeleton.html").write_text(skeleton_html, encoding="utf-8")

    log.info("[Step 2] ✅ template.json + skeleton.html written to %s", output)
    return template


# ── Skeleton sanitizer ────────────────────────────────────────────────────────

# Phrases that indicate the model leaked its reasoning into the skeleton output
_THINKING_MARKERS = [
    "the user wants",
    "let me ",
    "i need to",
    "looking at",
    "looking closer",
    "let's refine",
    "wait,",
    "this is definitely",
    "-> ",          # the model uses -> to show its reasoning steps
    "the currency",
    "must be kept",
]

def _sanitize_skeleton(html: str) -> tuple[str, list]:
    """
    Detect contamination from model reasoning leaking into the skeleton.
    Returns (cleaned_html, list_of_warnings).
    If contamination is severe (reasoning text in <body>), raises ValueError.
    """
    warnings = []
    lower = html.lower()

    # Check for thinking markers in the visible body content (not in comments/style)
    body_start = lower.find("<body")
    if body_start == -1:
        body_start = 0
    body_content = lower[body_start:]

    contaminated = [m for m in _THINKING_MARKERS if m in body_content]
    if contaminated:
        warnings.append(f"Model reasoning leaked into skeleton: {contaminated[:3]}")
        log.warning("[Step 2] ⚠ Skeleton contamination detected: %s", contaminated[:3])

    # Check skeleton starts with <!DOCTYPE html> (not with reasoning text)
    if not html.strip().lower().startswith("<!doctype") and not html.strip().lower().startswith("<html"):
        raise ValueError(
            "Skeleton does not start with <!DOCTYPE html> — the model returned "
            "reasoning text instead of HTML. Re-analyze this template."
        )

    # If contaminated, reject the skeleton so pipeline fails clearly
    # rather than saving garbage to disk
    if contaminated:
        raise ValueError(
            f"AI reasoning text leaked into skeleton ({contaminated[0]!r} found in body). "
            "This happens when the model is in 'thinking' mode. "
            "Re-analyze the template — it will use a fresh model call."
        )

    return html, warnings
