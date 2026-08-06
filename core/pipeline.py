"""
Django pipeline wrappers — runs steps 1-4 in background threads.
"""
import json
import logging
import os
import sys
import threading
from pathlib import Path

log = logging.getLogger("docify.pipeline")

# Ensure src/ is importable from anywhere
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _bootstrap_thread():
    """
    Must be called at the top of every thread function.
    Ensures Django is configured and env vars are set — threads don't
    inherit the parent's Django setup state reliably.
    """
    # Set the settings module so django.setup() knows what to load
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "docify.settings")

    import django
    django.setup()

    # Now safe to import settings
    from django.conf import settings as s

    # Mirror key values into os.environ for any code that reads env directly
    os.environ["GEMMA_API_KEY"]  = s.GEMMA_API_KEY  or ""
    os.environ["GEMMA_BASE_URL"] = s.GEMMA_BASE_URL or ""
    os.environ["GEMMA_MODEL"]    = s.GEMMA_MODEL    or ""

    # Make sure output dirs exist
    for d in [s.UPLOADS_DIR, s.TEMPLATES_DIR, s.OUTPUT_DIR]:
        Path(d).mkdir(parents=True, exist_ok=True)


def _model():
    from django.conf import settings
    return settings.GEMMA_MODEL


# ── Step runners ───────────────────────────────────────────────────────────────

def run_analyze(template_id: str):
    """Steps 1+2: PDF → images → template.json."""
    _bootstrap_thread()
    from core.models import Template
    from django.conf import settings

    log.info("=== [analyze] START template_id=%s ===", template_id)

    try:
        tmpl = Template.objects.get(pk=template_id)
    except Template.DoesNotExist:
        log.error("[analyze] Template %s not found in DB", template_id)
        return

    tmpl.status = "analyzing"
    tmpl.save(update_fields=["status", "updated_at"])
    log.info("[analyze] Status → analyzing")

    try:
        from src.step1_ingest  import pdf_to_images
        from src.step2_analyze import analyze_pages

        pdf_path = Path(tmpl.source_pdf.path)
        imgs_dir = Path(settings.UPLOADS_DIR)  / str(tmpl.pk)
        tpl_dir  = Path(settings.TEMPLATES_DIR) / str(tmpl.pk)

        log.info("[analyze] PDF: %s  (%.1f KB)", pdf_path, pdf_path.stat().st_size / 1024)
        log.info("[analyze] Images dir: %s", imgs_dir)
        log.info("[analyze] Template dir: %s", tpl_dir)

        # ── Step 1 ──
        log.info("[analyze] ── Step 1: PDF → images ──")
        image_paths = pdf_to_images(str(pdf_path), str(imgs_dir), dpi=150)
        log.info("[analyze] Step 1 done — %d page image(s)", len(image_paths))

        # ── Step 2 ──
        log.info("[analyze] ── Step 2: Vision analysis ──")
        analyze_pages(image_paths, str(tpl_dir), model_name=_model())
        log.info("[analyze] Step 2 done")

        # Read back the generated template.json for metadata
        tpl_json = tpl_dir / "template.json"
        if tpl_json.exists():
            data = json.loads(tpl_json.read_text(encoding="utf-8"))
            tmpl.total_pages   = data.get("total_pages")
            tmpl.primary_color = data.get("styles", {}).get("primary_color", "")
            log.info("[analyze] Metadata: %d pages, color=%s",
                     tmpl.total_pages or 0, tmpl.primary_color)
        else:
            log.warning("[analyze] template.json was not created!")

        # Save page-1 thumbnail
        if image_paths:
            from django.core.files import File
            thumb = Path(image_paths[0])
            if thumb.exists():
                with open(thumb, "rb") as f:
                    tmpl.thumbnail.save(f"{tmpl.pk}_thumb.png", File(f), save=False)
                log.info("[analyze] Thumbnail saved")

        tmpl.template_dir  = str(tpl_dir)
        tmpl.status        = "ready"
        tmpl.error_message = ""
        tmpl.save()
        log.info("=== [analyze] DONE — status=ready ===")

    except Exception as exc:
        log.exception("[analyze] FAILED: %s", exc)
        try:
            tmpl.status        = "failed"
            tmpl.error_message = str(exc)[:2000]
            tmpl.save(update_fields=["status", "error_message", "updated_at"])
        except Exception as save_err:
            log.error("[analyze] Could not save failure state: %s", save_err)


def run_generate(document_id: str):
    """Steps 3+4: template + content → HTML → PDF."""
    _bootstrap_thread()
    from core.models import GeneratedDocument
    from django.conf import settings

    log.info("=== [generate] START document_id=%s ===", document_id)

    try:
        doc = GeneratedDocument.objects.get(pk=document_id)
    except GeneratedDocument.DoesNotExist:
        log.error("[generate] Document %s not found in DB", document_id)
        return

    doc.status = "generating"
    doc.save(update_fields=["status", "updated_at"])
    log.info("[generate] Status → generating")

    try:
        from src.step3_generate import generate_html
        from src.step4_render   import render_pdf

        tmpl_json_path = doc.template.template_json_path
        if not tmpl_json_path or not tmpl_json_path.exists():
            raise FileNotFoundError(
                f"template.json not found at: {tmpl_json_path}\n"
                "Re-analyze the template first (delete it and re-upload)."
            )

        log.info("[generate] Template JSON: %s (%.1f KB)",
                 tmpl_json_path, tmpl_json_path.stat().st_size / 1024)

        template_data = json.loads(tmpl_json_path.read_text(encoding="utf-8"))
        # Inject the template directory so step3 can find skeleton.html
        template_data["_template_dir"] = str(tmpl_json_path.parent)
        out_dir = Path(settings.OUTPUT_DIR) / str(doc.pk)
        out_dir.mkdir(parents=True, exist_ok=True)

        # ── Step 3 ──
        log.info("[generate] ── Step 3: Generate HTML (mode=%s) ──", doc.mode)
        html_path = generate_html(template_data, doc.content, str(out_dir), _model(),
                                  mode=doc.mode)
        log.info("[generate] Step 3 done → %s", html_path)

        # ── Step 4 ──
        pdf_out = out_dir / f"{doc.pk}.pdf"
        log.info("[generate] ── Step 4: Render PDF → %s ──", pdf_out)
        pdf_path = render_pdf(html_path, str(pdf_out))
        log.info("[generate] Step 4 done → %s", pdf_path)

        # Paths relative to MEDIA_ROOT for Django FileField
        media_root = Path(settings.MEDIA_ROOT)
        doc.html_file     = str(Path(html_path).relative_to(media_root))
        doc.pdf_file      = str(Path(pdf_path).relative_to(media_root))
        doc.status        = "done"
        doc.error_message = ""
        doc.save()
        log.info("=== [generate] DONE — status=done ===")

    except Exception as exc:
        log.exception("[generate] FAILED: %s", exc)
        try:
            doc.status        = "failed"
            doc.error_message = str(exc)[:2000]
            doc.save(update_fields=["status", "error_message", "updated_at"])
        except Exception as save_err:
            log.error("[generate] Could not save failure state: %s", save_err)


def run_refine(document_id: str, feedback: str):
    """Re-generate HTML with feedback, re-render PDF."""
    _bootstrap_thread()
    from core.models import GeneratedDocument
    from django.conf import settings

    log.info("=== [refine] START document_id=%s ===", document_id)

    try:
        doc = GeneratedDocument.objects.get(pk=document_id)
    except GeneratedDocument.DoesNotExist:
        log.error("[refine] Document %s not found", document_id)
        return

    doc.status = "generating"
    doc.save(update_fields=["status", "updated_at"])

    try:
        from src.step3_generate import refine_html
        from src.step4_render   import render_pdf

        html_path = Path(settings.MEDIA_ROOT) / str(doc.html_file)
        if not html_path.exists():
            raise FileNotFoundError(f"HTML not found: {html_path}")

        log.info("[refine] ── Step 3: Refine HTML ──")
        updated = refine_html(str(html_path), feedback, _model())

        pdf_out = html_path.with_suffix(".pdf")
        log.info("[refine] ── Step 4: Re-render PDF → %s ──", pdf_out)
        pdf_path = render_pdf(updated, str(pdf_out))

        media_root    = Path(settings.MEDIA_ROOT)
        doc.pdf_file  = str(Path(pdf_path).relative_to(media_root))
        doc.status    = "done"
        doc.error_message = ""
        doc.save()
        log.info("=== [refine] DONE ===")

    except Exception as exc:
        log.exception("[refine] FAILED: %s", exc)
        try:
            doc.status        = "failed"
            doc.error_message = str(exc)[:2000]
            doc.save(update_fields=["status", "error_message", "updated_at"])
        except Exception as save_err:
            log.error("[refine] Could not save failure state: %s", save_err)


# ── Thread launchers ───────────────────────────────────────────────────────────

def _in_thread(fn, args):
    t = threading.Thread(target=fn, args=args, daemon=True)
    t.start()
    log.info("Launched thread: %s  args=%s", fn.__name__, args)
    return t

def async_analyze(template_id: str):
    _in_thread(run_analyze, (str(template_id),))

def async_generate(document_id: str):
    _in_thread(run_generate, (str(document_id),))

def async_refine(document_id: str, feedback: str):
    _in_thread(run_refine, (str(document_id), feedback))
