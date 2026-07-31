"""
Step 3 — Fill the HTML skeleton with user content.

Step3's job:
  1. Read skeleton.html (produced by step2)
  2. Ask the model to map user content → placeholder values (JSON)
  3. Substitute values into the skeleton via plain string replace
  4. Never let the model touch structure/labels/currency/layout
"""
import json
import logging
import re
import time
from pathlib import Path

import httpx

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
    """Remove <thought> / <thinking> model reasoning blocks before parsing."""
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
    p = Path(template_dir) / "skeleton.html"
    return p if p.exists() else None


def _extract_placeholders(html: str) -> list:
    return sorted(set(re.findall(r'\{\{([A-Z0-9_]+)\}\}', html)))


# ── Robust JSON parser — 4 fallback strategies ─────────────────────────────────

def _parse_mapping_json(raw: str, placeholders: list) -> dict:
    """
    Extract a {KEY: value} mapping from a model response.
    Tries four strategies before giving up:
      1. Strict json.loads after fence-stripping
      2. Single-quote repair + trailing-comma removal
      3. Unquoted key normalization
      4. Line-by-line KEY: value extraction
    Never raises — returns {} on total failure so unfilled slots show red.
    """
    # ── Strategy 1: strip fences, extract { … }, strict parse ─────────────────
    text = raw.strip()
    if "```" in text:
        for part in text.split("```"):
            p = part.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                text = p
                break

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]

    try:
        result = json.loads(text)
        log.debug("[Step 3] JSON parsed (strategy 1 — strict)")
        return result
    except json.JSONDecodeError as e1:
        log.warning("[Step 3] Strategy 1 failed: %s", e1)

    # ── Strategy 2: repair single quotes + trailing commas ────────────────────
    repaired = text
    # single quotes used as string delimiters → double quotes
    repaired = re.sub(r"'([^']*)'", lambda m: '"' + m.group(1).replace('"', '\\"') + '"', repaired)
    # trailing commas before } or ]
    repaired = re.sub(r',(\s*[}\]])', r'\1', repaired)

    try:
        result = json.loads(repaired)
        log.info("[Step 3] JSON parsed (strategy 2 — quote/comma repair)")
        return result
    except json.JSONDecodeError as e2:
        log.warning("[Step 3] Strategy 2 failed: %s", e2)

    # ── Strategy 3: unquoted keys + Python literals ───────────────────────────
    repaired2 = repaired
    repaired2 = re.sub(r'([{,]\s*)([A-Z][A-Z0-9_]*)(\s*:)', r'\1"\2"\3', repaired2)
    repaired2 = (repaired2
                 .replace(": True",  ": true")
                 .replace(": False", ": false")
                 .replace(": None",  ": null"))

    try:
        result = json.loads(repaired2)
        log.info("[Step 3] JSON parsed (strategy 3 — key normalization)")
        return result
    except json.JSONDecodeError as e3:
        log.warning("[Step 3] Strategy 3 failed: %s", e3)

    # ── Strategy 4: line-by-line KEY: "value" extraction ─────────────────────
    result = {}
    ph_set = set(placeholders)
    for line in raw.splitlines():
        line = line.strip().rstrip(',')
        # Match:  "KEY": "value"  |  KEY: "value"  |  KEY: value
        m = re.match(r'"?([A-Z][A-Z0-9_]*)"?\s*:\s*"?(.*?)"?\s*$', line)
        if m:
            key = m.group(1)
            val = m.group(2).strip().strip('"').strip("'")
            if key in ph_set:
                result[key] = val
                log.debug("[Step 3]   line-parsed: %s → %s", key, val[:50])

    if result:
        log.info("[Step 3] JSON parsed (strategy 4 — line parsing, %d/%d keys)",
                 len(result), len(placeholders))
        return result

    # ── Total failure ─────────────────────────────────────────────────────────
    log.error("[Step 3] All 4 parse strategies failed.\nRaw response:\n%s", raw[:800])
    return {}


# ── Mapping call ───────────────────────────────────────────────────────────────

def _map_content_to_placeholders(client, skeleton_html: str, placeholders: list,
                                  user_content: str, model_name: str) -> dict:
    """Ask the model for a JSON mapping, parse it robustly."""
    log.info("[Step 3] Mapping %d placeholder(s): %s", len(placeholders), placeholders)

    # Use the actual placeholder names as the example keys
    example_keys = placeholders[:3] if len(placeholders) >= 3 else placeholders
    example      = "{" + ", ".join(f'"{k}": "..."' for k in example_keys) + "}"

    prompt = (
        "Fill in the document template slots below using the provided content.\n\n"
        "SLOTS TO FILL:\n"
        + "\n".join(f"  {p}" for p in placeholders)
        + "\n\nCONTENT:\n"
        + user_content
        + "\n\nRULES:\n"
        "1. Output a single JSON object only — no markdown, no explanation.\n"
        "2. Keys = slot names above. Values = strings in double quotes.\n"
        "3. If no matching value exists in the content, use \"\".\n"
        "4. Never invent values. Never change currency symbols or units.\n"
        "5. No trailing commas. No single quotes.\n\n"
        f"Format: {example}"
    )

    log.info("[Step 3] Calling API...")
    t0 = time.time()
    resp = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2048,
    )
    log.info("[Step 3] Mapping API done in %.1fs", time.time() - t0)

    raw = resp.choices[0].message.content or ""
    log.info("[Step 3] Raw mapping (%d chars): %s", len(raw), raw[:400])

    mapping = _parse_mapping_json(raw, placeholders)

    # Ensure every placeholder has at least an empty string (never KeyError downstream)
    for p in placeholders:
        if p not in mapping:
            mapping[p] = ""
            log.warning("[Step 3] Placeholder not in mapping: %s → ''", p)

    log.info("[Step 3] Final mapping — %d/%d placeholders filled",
             sum(1 for v in mapping.values() if v), len(placeholders))
    for k, v in mapping.items():
        log.info("[Step 3]   {{%s}} → %s", k, str(v)[:60])

    return mapping


# ── Direct content parser ─────────────────────────────────────────────────────

def _parse_direct_content(content: str, placeholders: list) -> tuple[dict, bool]:
    """
    Check if content is pre-structured as "KEY: value" lines (sent by the
    dynamic form). If so, build the mapping directly without an AI call.
    Returns (mapping, is_direct).
    """
    import re as _re
    ph_set   = set(placeholders)
    lines    = [l.strip() for l in content.strip().splitlines() if l.strip()]
    mapping  = {}
    matched  = 0

    for line in lines:
        if line.startswith("[EXTRA"):   # stop at extra instructions block
            break
        m = _re.match(r'([A-Z][A-Z0-9_]*)\s*:\s*(.*)', line)
        if m and m.group(1) in ph_set:
            mapping[m.group(1)] = m.group(2).strip()
            matched += 1

    # Consider it "direct" if at least half the placeholders matched
    is_direct = matched >= max(1, len(placeholders) // 2)
    return mapping, is_direct


def _extract_extra_instructions(content: str) -> str:
    """Pull out the [EXTRA INSTRUCTIONS] block if present."""
    marker = "[EXTRA INSTRUCTIONS]"
    idx = content.find(marker)
    if idx == -1:
        return ""
    return content[idx + len(marker):].strip()


# ── Skeleton fill ──────────────────────────────────────────────────────────────

def _fill_skeleton(skeleton_html: str, mapping: dict) -> str:
    result = skeleton_html
    for key, value in mapping.items():
        result = result.replace("{{" + key + "}}", str(value))

    remaining = _extract_placeholders(result)
    if remaining:
        log.warning("[Step 3] Unfilled after substitution: %s", remaining)
        for p in remaining:
            result = result.replace(
                "{{" + p + "}}",
                f'<span style="background:#fee2e2;color:#991b1b;padding:1px 4px;'
                f'border-radius:3px;font-size:0.85em">[{p}]</span>'
            )
    return result


# ── Public entry points ────────────────────────────────────────────────────────

def generate_html(template: dict, content: str, output_dir: str, model_name: str) -> str:
    log.info("[Step 3] Starting generation — model=%s", model_name)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    client = _make_client()

    skeleton_path = _find_skeleton(template.get("_template_dir", ""))
    if skeleton_path:
        log.info("[Step 3] Skeleton found: %s (%dKB)",
                 skeleton_path, skeleton_path.stat().st_size // 1024)
        skeleton_html = skeleton_path.read_text(encoding="utf-8")
        placeholders  = _extract_placeholders(skeleton_html)
        log.info("[Step 3] Placeholders: %s", placeholders)

        if placeholders:
            # Fast path: content arrived as structured KEY: value lines from the
            # dynamic form — no AI call needed, build mapping directly
            direct_mapping, is_direct = _parse_direct_content(content, placeholders)

            if is_direct:
                log.info("[Step 3] Direct field mapping — skipping AI (%d/%d fields matched)",
                         len(direct_mapping), len(placeholders))
                # Fill any gaps with empty string
                for p in placeholders:
                    if p not in direct_mapping:
                        direct_mapping[p] = ""
                        log.warning("[Step 3] Field not provided: %s → ''", p)

                # Extra instructions → pass to AI for a light refinement pass only if present
                extra = _extract_extra_instructions(content)
                if extra:
                    log.info("[Step 3] Extra instructions detected — applying via AI: %s", extra[:100])
                    # Fill skeleton first, then ask AI to apply only the extra instructions
                    pre_filled = _fill_skeleton(skeleton_html, direct_mapping)
                    mapping_extra = _map_content_to_placeholders(
                        client,
                        pre_filled,
                        [p for p in placeholders if not direct_mapping.get(p)],
                        extra,
                        model_name,
                    )
                    # Merge: direct values take priority, extra fills only blank slots
                    for k, v in mapping_extra.items():
                        if not direct_mapping.get(k):
                            direct_mapping[k] = v

                mapping = direct_mapping
            else:
                log.info("[Step 3] Free-text content — using AI mapping")
                mapping = _map_content_to_placeholders(
                    client, skeleton_html, placeholders, content, model_name
                )

            html = _fill_skeleton(skeleton_html, mapping)
        else:
            log.warning("[Step 3] Skeleton has no placeholders — using as-is")
            html = skeleton_html

        html_path = output / "output.html"
        html_path.write_text(html, encoding="utf-8")
        log.info("[Step 3] ✅ HTML written (skeleton-fill) → %s (%dKB)",
                 html_path, html_path.stat().st_size // 1024)
        return str(html_path)

    log.warning("[Step 3] No skeleton.html — falling back to full-prompt generation")
    return _generate_html_fullprompt(client, template, content, output, model_name)


def _generate_html_fullprompt(client, template: dict, content: str,
                               output: Path, model_name: str) -> str:
    """Fallback for templates without a skeleton (analyzed before v4)."""
    log.info("[Step 3] Full-prompt fallback")
    assets = template.get("embedded_assets", [])
    asset_lines = []
    for i, a in enumerate(assets):
        role = "LOGO" if (i == 0 and template.get("has_logo")) else f"IMAGE_{i+1}"
        asset_lines.append(
            f'  [{role}]: <img src="file://{a["path"]}" '
            f'style="max-height:80px;width:auto;" alt="{role.lower()}" />'
        )
    asset_block = ("\nASSETS:\n" + "\n".join(asset_lines)) if asset_lines else ""
    template_clean = {k: v for k, v in template.items()
                      if k not in ("embedded_assets", "_template_dir")}

    prompt = (
        "Reproduce this document's EXACT visual design. "
        "Do NOT invent structure. Do NOT change currency or units.\n\n"
        f"TEMPLATE:\n```json\n{json.dumps(template_clean, indent=2)}\n```\n"
        f"{asset_block}\n\nCONTENT:\n{content}\n\n"
        "Output a complete HTML file (<!DOCTYPE html>), all CSS in <style>, "
        "no external resources, no markdown."
    )

    t0 = time.time()
    resp = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=16000,
    )
    log.info("[Step 3] Full-prompt done in %.1fs", time.time() - t0)

    html = _strip_fences(resp.choices[0].message.content or "", "html")
    html_path = output / "output.html"
    html_path.write_text(html, encoding="utf-8")
    log.info("[Step 3] ✅ HTML written (fullprompt) → %s", html_path)
    return str(html_path)


def refine_html(html_path: str, feedback: str, model_name: str) -> str:
    """Apply targeted style/layout changes to an existing HTML file."""
    log.info("[Step 3] Refining: %s  feedback: %s", html_path, feedback)
    original = Path(html_path).read_text(encoding="utf-8")

    prompt = (
        f"HTML document:\n\n```html\n{original}\n```\n\n"
        f"Apply ONLY: {feedback}\n\n"
        "Rules: do NOT change text content, labels, values, img src, or currency. "
        "Output only the complete updated HTML."
    )

    t0 = time.time()
    client = _make_client()
    resp = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=16000,
    )
    log.info("[Step 3] Refine done in %.1fs", time.time() - t0)

    html = _strip_fences(resp.choices[0].message.content or "", "html")
    Path(html_path).write_text(html, encoding="utf-8")
    log.info("[Step 3] ✅ Refined → %s", html_path)
    return html_path
