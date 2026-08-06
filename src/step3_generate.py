"""
Step 3 — Two distinct generation pipelines:

PIPELINE A — Template Fill
  Source: skeleton.html with {{PLACEHOLDER}} tokens
  Input:  KEY: value lines from the dynamic form
  Method: direct string substitution — AI only maps values to slots
  Use:    invoices, forms, any structured repeated document

PIPELINE B — Style Clone
  Source: template.json style tokens + logo from skeleton (if any)
  Input:  raw text content (report, notes, etc.)
  Method: AI receives style DNA + user text → produces fresh branded HTML
  Use:    reports, letters, any new document that should look like the brand
  NEVER touches skeleton.html — that is Template Fill only
"""
import json
import logging
import re
import time
from pathlib import Path

import httpx
from src._streaming import stream_completion

log = logging.getLogger("docify.step3")

STREAM_TIMEOUT = 300


def _make_client():
    from openai import OpenAI
    from django.conf import settings
    return OpenAI(
        api_key=settings.GEMMA_API_KEY,
        base_url=settings.GEMMA_BASE_URL,
        http_client=httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=STREAM_TIMEOUT,
                                  write=30.0, pool=5.0)
        ),
    )


def _strip_thinking(raw: str) -> str:
    import re as _re
    text = _re.sub(r'<thought[^>]*>.*?</thought[^>]*>', '', raw,  flags=_re.DOTALL|_re.IGNORECASE)
    text = _re.sub(r'<thinking[^>]*>.*?</thinking[^>]*>', '', text, flags=_re.DOTALL|_re.IGNORECASE)
    text = _re.sub(r'<thought[^>]*>.*',  '', text, flags=_re.DOTALL|_re.IGNORECASE)
    text = _re.sub(r'<thinking[^>]*>.*', '', text, flags=_re.DOTALL|_re.IGNORECASE)
    return text.strip()


def _strip_fences(text: str, lang: str = "") -> str:
    text = _strip_thinking(text).strip()
    if text.startswith("```"):
        lines = text.split("\n")
        inner = lines[1:]
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        text = "\n".join(inner).strip()
        if lang and text.startswith(lang):
            text = text[len(lang):].strip()
    return text


def _find_skeleton(template_dir: str):
    if not template_dir:
        return None
    p = Path(template_dir) / "skeleton.html"
    return p if p.exists() else None


def _extract_placeholders(html: str) -> list:
    return sorted(set(re.findall(r'\{\{([A-Z0-9_]+)\}\}', html)))


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE A — Template Fill
# ══════════════════════════════════════════════════════════════════════════════

def _parse_mapping_json(raw: str, placeholders: list) -> dict:
    """Robust 4-strategy JSON parser for placeholder mappings."""
    text = raw.strip()
    if "```" in text:
        for part in text.split("```"):
            p = part.strip()
            if p.startswith("json"): p = p[4:].strip()
            if p.startswith("{"): text = p; break
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end+1]

    # Strategy 1: strict
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: repair single quotes + trailing commas
    repaired = re.sub(r"'([^']*)'", lambda m: '"' + m.group(1).replace('"', '\\"') + '"', text)
    repaired = re.sub(r',(\s*[}\]])', r'\1', repaired)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Strategy 3: unquoted keys
    repaired2 = re.sub(r'([{,]\s*)([A-Z][A-Z0-9_]*)(\s*:)', r'\1"\2"\3', repaired)
    try:
        return json.loads(repaired2)
    except json.JSONDecodeError:
        pass

    # Strategy 4: line-by-line
    result = {}
    ph_set = set(placeholders)
    for line in raw.splitlines():
        line = line.strip().rstrip(',')
        m = re.match(r'"?([A-Z][A-Z0-9_]*)"?\s*:\s*"?(.*?)"?\s*$', line)
        if m and m.group(1) in ph_set:
            result[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    if result:
        log.info("[Step 3] JSON parsed via line strategy (%d keys)", len(result))
        return result

    log.error("[Step 3] All JSON parse strategies failed. Raw:\n%s", raw[:400])
    return {}


def _parse_direct_content(content: str, placeholders: list) -> tuple:
    """
    Detect if content is pre-structured KEY: value lines from the dynamic form.
    Returns (mapping_dict, is_direct_bool).
    """
    ph_set  = set(placeholders)
    lines   = [l.strip() for l in content.strip().splitlines() if l.strip()]
    mapping = {}
    for line in lines:
        if line.startswith("[EXTRA"):
            break
        m = re.match(r'([A-Z][A-Z0-9_]*)\s*:\s*(.*)', line)
        if m and m.group(1) in ph_set:
            mapping[m.group(1)] = m.group(2).strip()
    is_direct = len(mapping) >= max(1, len(placeholders) // 2)
    return mapping, is_direct


def _extract_extra_instructions(content: str) -> str:
    marker = "[EXTRA INSTRUCTIONS]"
    idx = content.find(marker)
    return content[idx + len(marker):].strip() if idx != -1 else ""


def _map_via_ai(client, placeholders: list, content: str, model_name: str) -> dict:
    """Ask AI to map free-text content to placeholder slots (JSON output)."""
    log.info("[Step 3] AI mapping %d slots", len(placeholders))
    example = "{" + ", ".join(f'"{k}": "..."' for k in placeholders[:3]) + "}"
    prompt = (
        "Fill in the document template slots below using the provided content.\n\n"
        "SLOTS:\n" + "\n".join(f"  {p}" for p in placeholders) +
        "\n\nCONTENT:\n" + content +
        "\n\nRULES:\n"
        "1. Output a single JSON object only — no markdown, no explanation.\n"
        "2. Keys = slot names. Values = strings in double quotes.\n"
        "3. If no matching value exists, use \"\".\n"
        "4. Never change currency symbols or units.\n"
        f"\nFormat: {example}"
    )
    t0 = time.time()
    raw = stream_completion(
        client, model_name,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2048,
        label="Step3-Mapping",
    )
    log.info("[Step 3] AI mapping done %.1fs", time.time()-t0)
    mapping = _parse_mapping_json(raw, placeholders)
    for p in placeholders:
        if p not in mapping:
            mapping[p] = ""
    return mapping


def _fill_skeleton(skeleton_html: str, mapping: dict) -> str:
    result = skeleton_html
    for key, value in mapping.items():
        result = result.replace("{{" + key + "}}", str(value))
    remaining = _extract_placeholders(result)
    if remaining:
        log.warning("[Step 3] Unfilled placeholders: %s", remaining)
        for p in remaining:
            result = result.replace(
                "{{" + p + "}}",
                f'<span style="background:#fee2e2;color:#991b1b;padding:1px 4px;'
                f'border-radius:3px;font-size:0.85em">[{p}]</span>'
            )
    return result


def _run_template_fill(client, skeleton_path, content: str,
                       output: Path, model_name: str) -> str:
    """Pipeline A: fill skeleton placeholders from structured content."""
    log.info("[Step 3] PIPELINE A — Template Fill")
    skeleton_html = skeleton_path.read_text(encoding="utf-8")
    placeholders  = _extract_placeholders(skeleton_html)
    log.info("[Step 3] Skeleton: %dKB, %d placeholders: %s",
             skeleton_path.stat().st_size // 1024, len(placeholders), placeholders)

    if not placeholders:
        log.warning("[Step 3] Skeleton has no placeholders — returning as-is")
        html = skeleton_html
    else:
        # Try direct parse first (structured form submission)
        direct_mapping, is_direct = _parse_direct_content(content, placeholders)

        if is_direct:
            log.info("[Step 3] Direct mapping — %d/%d fields, skipping AI",
                     len(direct_mapping), len(placeholders))
            for p in placeholders:
                if p not in direct_mapping:
                    direct_mapping[p] = ""
            # Handle extra instructions if present
            extra = _extract_extra_instructions(content)
            if extra and any(not v for v in direct_mapping.values()):
                log.info("[Step 3] Extra instructions for unfilled slots: %s", extra[:80])
                extra_mapping = _map_via_ai(
                    client,
                    [p for p in placeholders if not direct_mapping.get(p)],
                    extra, model_name
                )
                for k, v in extra_mapping.items():
                    if not direct_mapping.get(k):
                        direct_mapping[k] = v
            mapping = direct_mapping
        else:
            log.info("[Step 3] Free-text content — using AI mapping")
            mapping = _map_via_ai(client, placeholders, content, model_name)

        log.info("[Step 3] Final mapping:")
        for k, v in mapping.items():
            log.info("[Step 3]   {{%s}} = %s", k, str(v)[:60])

        html = _fill_skeleton(skeleton_html, mapping)

    html_path = output / "output.html"
    html_path.write_text(html, encoding="utf-8")
    log.info("[Step 3] ✅ Template Fill → %s (%dKB)",
             html_path, html_path.stat().st_size // 1024)
    return str(html_path)


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE B — Style Clone
# ══════════════════════════════════════════════════════════════════════════════

def _extract_logo_from_skeleton(skeleton_path) -> str | None:
    """
    Pull the base64 logo data URI from the skeleton so Style Clone
    can include it in the new document's header without re-extracting.
    """
    if not skeleton_path or not skeleton_path.exists():
        return None
    html = skeleton_path.read_text(encoding="utf-8")
    # Find first data: URI in an <img> tag
    m = re.search(r'<img[^>]+src="(data:image/[^"]+)"', html, re.IGNORECASE)
    if m:
        uri = m.group(1)
        log.info("[Step 3] Extracted logo data URI from skeleton (%dKB)", len(uri)//1024)
        return uri
    return None


def _extract_header_footer_from_skeleton(skeleton_path) -> dict:
    """
    Pull the static header and footer HTML blocks from the skeleton.
    These contain the logo, company name, slogan, service bullets, and
    footer address — exactly the brand identity the new document needs.
    Returns {"header": "...", "footer": "..."} or empty strings.
    """
    result = {"header": "", "footer": "", "styles": ""}
    if not skeleton_path or not skeleton_path.exists():
        return result

    html = skeleton_path.read_text(encoding="utf-8")

    # Extract <style> block
    sm = re.search(r'<style[^>]*>(.*?)</style>', html, re.DOTALL|re.IGNORECASE)
    if sm:
        result["styles"] = sm.group(1).strip()

    # Heuristic: header is the first major div/header block before the meta-block
    # Footer is the last div/footer before </body>
    # We use the actual class/id names the skeleton uses
    header_m = re.search(
        r'(<(?:div|header)[^>]*(?:class|id)="[^"]*header[^"]*"[^>]*>.*?</(?:div|header)>)',
        html, re.DOTALL|re.IGNORECASE
    )
    if header_m:
        result["header"] = header_m.group(1)
        log.info("[Step 3] Extracted header block (%d chars)", len(result["header"]))

    footer_m = re.search(
        r'(<(?:div|footer)[^>]*(?:class|id)="[^"]*footer[^"]*"[^>]*>.*?</(?:div|footer)>)',
        html, re.DOTALL|re.IGNORECASE
    )
    if footer_m:
        result["footer"] = footer_m.group(1)
        log.info("[Step 3] Extracted footer block (%d chars)", len(result["footer"]))

    return result


def _run_style_clone(client, template: dict, skeleton_path,
                     content: str, output: Path, model_name: str) -> str:
    """
    Pipeline B: produce a brand-new document using the brand visual style.

    EMPTY PDF FIX — logo base64 was going into the prompt, exhausting the
    model context window before it could generate any real HTML output.

    Logo strategy:
      1. Extract logo data URI from the skeleton (kept entirely off-prompt)
      2. Give the AI a short sentinel string DOCIFY_LOGO_PLACEHOLDER
      3. After AI returns HTML, replace the sentinel with the real base64 URI
    This keeps the prompt lean and lets the model focus on formatting content.
    """
    log.info("[Step 3] PIPELINE B — Style Clone")
    log.info("[Step 3] Content: %d chars", len(content))

    styles = template.get("styles", {})
    LOGO_SENTINEL = "DOCIFY_LOGO_PLACEHOLDER"

    # Extract brand elements — logo URI stays off-prompt
    brand    = _extract_header_footer_from_skeleton(skeleton_path)
    logo_uri = _extract_logo_from_skeleton(skeleton_path)
    has_logo = bool(logo_uri)
    log.info("[Step 3] Logo found: %s (%dKB)", has_logo,
             len(logo_uri) // 1024 if logo_uri else 0)

    # Compact style reference — text only
    style_ref = json.dumps({
        "colors": {
            "primary":           styles.get("primary_color",    "#000"),
            "secondary":         styles.get("secondary_color",  "#eee"),
            "background":        styles.get("background_color", "#fff"),
            "text":              styles.get("text_color",       "#000"),
            "table_header_bg":   styles.get("table_header_bg",  "#000"),
            "table_header_text": styles.get("table_header_text","#fff"),
        },
        "typography": {
            "font_family":          styles.get("font_family",          "Arial, sans-serif"),
            "font_size_body":       styles.get("font_size_body",       "11pt"),
            "font_size_heading":    styles.get("font_size_heading",    "16pt"),
            "font_size_subheading": styles.get("font_size_subheading", "13pt"),
            "line_height":          styles.get("line_height",          "1.5"),
        },
        "layout": {
            "page_margin": styles.get("page_margin", "20mm"),
            "has_header":  template.get("layout", {}).get("has_header", True),
            "has_footer":  template.get("layout", {}).get("has_footer", True),
        },
    }, indent=2)

    # Logo instruction uses sentinel — never raw base64
    logo_block = ""
    if has_logo:
        logo_block = (
            f'\n\nLOGO: Place this img tag where the logo belongs (top-left of header):\n'
            f'<img src="{LOGO_SENTINEL}" style="max-height:80px;width:auto;" alt="logo" />'
        )

    # Header/footer: strip any existing base64 from extracted blocks
    header_block = ""
    if brand.get("header"):
        clean = re.sub(r'src="data:[^"]*"', f'src="{LOGO_SENTINEL}"', brand["header"])
        header_block = "\n\nHEADER HTML (copy verbatim — do not modify):\n" + clean

    footer_block = ""
    if brand.get("footer"):
        footer_block = "\n\nFOOTER HTML (copy verbatim — do not modify):\n" + brand["footer"]

    # CSS block — reuse brand styles
    css_block = ""
    if brand.get("styles"):
        css_block = f"\n\nBRAND CSS (use as visual base):\n<style>\n{brand['styles']}\n</style>"

    prompt = (
        "You are a professional document designer.\n"
        "Format the user's text content as a polished PDF-ready HTML document "
        "that matches the brand style below. "
        "This is NOT about replicating an existing document — it is about applying "
        "the brand's visual identity (colors, fonts, logo, header, footer) to NEW content.\n\n"
        f"BRAND STYLE:\n```json\n{style_ref}\n```"
        f"{css_block}"
        f"{logo_block}"
        f"{header_block}"
        f"{footer_block}\n\n"
        "USER CONTENT TO FORMAT:\n"
        "─────────────────────────────────────────\n"
        f"{content}\n"
        "─────────────────────────────────────────\n\n"
        "REQUIREMENTS:\n"
        "1. Output a complete HTML file starting with <!DOCTYPE html>.\n"
        "2. ALL CSS inside a single <style> tag — no external stylesheets, no Google Fonts.\n"
        "3. Apply brand colors, fonts, and spacing throughout the document.\n"
        f"4. The logo img tag with src=\"{LOGO_SENTINEL}\" must appear exactly as written.\n"
        "5. If HEADER HTML is given, paste it verbatim inside the document body.\n"
        "6. If FOOTER HTML is given, paste it verbatim at the bottom.\n"
        "7. Format the content sensibly: headings, paragraphs, tables, bullet lists.\n"
        "8. Do NOT invent or add content beyond what the user provided.\n"
        "9. Output ONLY the HTML. No markdown fences, no comments, no explanation.\n"
    )

    log.info("[Step 3] Prompt: %d chars (logo off-prompt: %s)", len(prompt), has_logo)

    raw = stream_completion(
        client, model_name,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=16000,
        label="Step3-StyleClone",
    )
    log.info("[Step 3] Stream done: %d chars", len(raw))

    html = _strip_fences(raw, "html")

    # Validate — catch empty responses early
    if len(html.strip()) < 200:
        log.error("[Step 3] Output too short (%d chars) — model may have failed", len(html))
        raise ValueError(
            f"Style Clone produced nearly empty HTML ({len(html)} chars). "
            "Check logs for streaming errors or reduce content length."
        )

    if not html.lower().lstrip().startswith(("<!doctype", "<html")):
        log.warning("[Step 3] Output may not be valid HTML. First 300 chars:\n%s", html[:300])

    # Post-process: inject real logo base64 (replaces sentinel)
    if has_logo:
        if LOGO_SENTINEL in html:
            html = html.replace(LOGO_SENTINEL, logo_uri)
            log.info("[Step 3] ✅ Logo injected (%dKB)", len(logo_uri) // 1024)
        else:
            # Model ignored the sentinel — inject logo into header manually
            log.warning("[Step 3] Logo sentinel missing from output — injecting into header")
            logo_tag = (
                f'<img src="{logo_uri}" '
                f'style="max-height:80px;width:auto;display:block;" alt="logo" />'
            )
            # Try to find the header div
            header_match = re.search(
                r'(<(?:div|header)\b[^>]*(?:header|logo)[^>]*>)',
                html, re.IGNORECASE
            )
            if header_match:
                html = html[:header_match.end()] + "\n" + logo_tag + html[header_match.end():]
            else:
                # Fallback: right after <body>
                html = re.sub(
                    r'(<body\b[^>]*>)',
                    r'\1\n<div style="padding:10px 20px">' + logo_tag + '</div>',
                    html, flags=re.IGNORECASE
                )
            log.info("[Step 3] Logo injected via fallback")

    html_path = output / "output.html"
    html_path.write_text(html, encoding="utf-8")
    log.info("[Step 3] ✅ Style Clone output: %s (%dKB)",
             html_path, html_path.stat().st_size // 1024)
    return str(html_path)




def generate_html(template: dict, content: str, output_dir: str,
                  model_name: str, mode: str = "auto") -> str:
    """
    Generate a document HTML.

    mode: "fill"  → Template Fill (use skeleton placeholders)
          "style" → Style Clone   (brand new document in template style)
          "auto"  → detect from content structure and skeleton presence
    """
    log.info("[Step 3] generate_html — mode=%s", mode)
    output  = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    client  = _make_client()

    template_dir  = template.get("_template_dir", "")
    skeleton_path = _find_skeleton(template_dir)

    # Auto-detect mode
    if mode == "auto":
        if skeleton_path:
            placeholders = _extract_placeholders(skeleton_path.read_text(encoding="utf-8"))
            _, is_direct = _parse_direct_content(content, placeholders) if placeholders else ({}, False)
            if placeholders and is_direct:
                mode = "fill"
                log.info("[Step 3] Auto-detected: Template Fill (direct KEY:value content)")
            elif placeholders and re.search(r'^[A-Z][A-Z0-9_]+\s*:', content, re.MULTILINE):
                mode = "fill"
                log.info("[Step 3] Auto-detected: Template Fill (KEY: pattern found)")
            else:
                mode = "style"
                log.info("[Step 3] Auto-detected: Style Clone (free-text content)")
        else:
            mode = "style"
            log.info("[Step 3] Auto-detected: Style Clone (no skeleton)")

    if mode == "fill":
        if not skeleton_path:
            log.warning("[Step 3] Fill mode requested but no skeleton — falling back to Style Clone")
            mode = "style"
        else:
            return _run_template_fill(client, skeleton_path, content, output, model_name)

    if mode == "style":
        return _run_style_clone(client, template, skeleton_path, content, output, model_name)

    raise ValueError(f"Unknown mode: {mode}")


def refine_html(html_path: str, feedback: str, model_name: str) -> str:
    """Apply targeted style/layout changes to an existing HTML file."""
    log.info("[Step 3] Refining: %s", html_path)
    original = Path(html_path).read_text(encoding="utf-8")

    prompt = (
        f"HTML document:\n\n```html\n{original}\n```\n\n"
        f"Apply ONLY these changes: {feedback}\n\n"
        "Rules:\n"
        "- Do NOT change any text content or values.\n"
        "- Do NOT change any <img src> attributes.\n"
        "- Do NOT change currency symbols or numeric values.\n"
        "- Output ONLY the complete updated HTML. No markdown, no explanation.\n"
    )

    t0 = time.time()
    client = _make_client()
    raw = stream_completion(
        client, model_name,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=16000,
        label="Step3-Refine",
    )
    log.info("[Step 3] Refine done %.1fs", time.time()-t0)
    html = _strip_fences(raw, "html")
    Path(html_path).write_text(html, encoding="utf-8")
    log.info("[Step 3] ✅ Refined → %s", html_path)
    return html_path
