"""
Management command to test the full pipeline from the command line.

Usage:
  # Test just step 1 (PDF → images):
  python manage.py test_pipeline --pdf /path/to/file.pdf --step 1

  # Test steps 1+2 (analyze):
  python manage.py test_pipeline --pdf /path/to/file.pdf --step 2

  # Test steps 3+4 from an existing template:
  python manage.py test_pipeline --template-id <uuid> --content "My content here" --step 4

  # Full pipeline end-to-end:
  python manage.py test_pipeline --pdf /path/to/file.pdf --content "My content" --step 4
"""
import json
import os
import sys
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run and debug the Docify pipeline from the command line"

    def add_arguments(self, parser):
        parser.add_argument("--pdf",         type=str, help="Path to source PDF")
        parser.add_argument("--template-id", type=str, help="Existing Template UUID (skip steps 1+2)")
        parser.add_argument("--content",     type=str, default="# Test Document\n\nThis is a test of the Docify pipeline.", help="Content to generate")
        parser.add_argument("--step",        type=int, default=4, choices=[1,2,3,4], help="Run up to this step")
        parser.add_argument("--dpi",         type=int, default=150, help="DPI for PDF→image conversion")
        parser.add_argument("--output-dir",  type=str, default="/tmp/docify_test", help="Where to write output")

    def handle(self, *args, **options):
        # Set env
        os.environ["DJANGO_SETTINGS_MODULE"] = "docify.settings"
        os.environ["GEMMA_API_KEY"]  = settings.GEMMA_API_KEY  or ""
        os.environ["GEMMA_BASE_URL"] = settings.GEMMA_BASE_URL or ""

        # Add project root to path
        root = Path(__file__).resolve().parents[4]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))

        model     = settings.GEMMA_MODEL
        out_dir   = Path(options["output_dir"])
        step      = options["step"]
        pdf_path  = options.get("pdf")
        content   = options["content"]

        self.stdout.write(self.style.SUCCESS(f"\n{'='*60}"))
        self.stdout.write(self.style.SUCCESS(f"Docify Pipeline Test  (up to step {step})"))
        self.stdout.write(self.style.SUCCESS(f"Model: {model}"))
        self.stdout.write(self.style.SUCCESS(f"{'='*60}\n"))

        image_paths  = []
        template_data = None

        # ── Step 1 ────────────────────────────────────────────────────────
        if step >= 1 and pdf_path and not options.get("template_id"):
            self.stdout.write(self.style.WARNING("\n── Step 1: PDF → images ──"))
            t0 = time.time()
            from src.step1_ingest import pdf_to_images
            imgs_dir    = out_dir / "pages"
            image_paths = pdf_to_images(pdf_path, str(imgs_dir), dpi=options["dpi"])
            self.stdout.write(self.style.SUCCESS(
                f"✅ Step 1 done in {time.time()-t0:.1f}s — {len(image_paths)} pages"
            ))
            for p in image_paths:
                size_kb = Path(p).stat().st_size // 1024
                self.stdout.write(f"   {Path(p).name}  ({size_kb} KB)")

        # ── Step 2 ────────────────────────────────────────────────────────
        if step >= 2 and image_paths and not options.get("template_id"):
            self.stdout.write(self.style.WARNING("\n── Step 2: Vision analysis ──"))
            t0 = time.time()
            from src.step2_analyze import analyze_pages
            tpl_dir      = out_dir / "template"
            template_data = analyze_pages(image_paths, str(tpl_dir), model_name=model)
            elapsed = time.time() - t0
            self.stdout.write(self.style.SUCCESS(f"✅ Step 2 done in {elapsed:.1f}s"))
            self.stdout.write(f"   Pages:   {template_data.get('total_pages')}")
            self.stdout.write(f"   Color:   {template_data.get('styles',{}).get('primary_color','?')}")
            self.stdout.write(f"   Assets:  {len(template_data.get('embedded_assets',[]))}")
            self.stdout.write(f"   JSON:    {tpl_dir}/template.json")

        # Load from existing template if --template-id given
        if options.get("template_id") and step >= 3:
            from core.models import Template
            try:
                tmpl = Template.objects.get(pk=options["template_id"])
                tpl_json = tmpl.template_json_path
                if not tpl_json or not tpl_json.exists():
                    self.stderr.write(f"template.json not found for {tmpl.pk}")
                    return
                template_data = json.loads(tpl_json.read_text())
                self.stdout.write(self.style.SUCCESS(f"Loaded template: {tmpl.name}"))
            except Template.DoesNotExist:
                self.stderr.write(f"Template {options['template_id']} not found")
                return

        # ── Step 3 ────────────────────────────────────────────────────────
        if step >= 3 and template_data:
            self.stdout.write(self.style.WARNING("\n── Step 3: Generate HTML ──"))
            t0 = time.time()
            from src.step3_generate import generate_html
            html_dir  = out_dir / "output"
            html_path = generate_html(template_data, content, str(html_dir), model)
            size_kb   = Path(html_path).stat().st_size // 1024
            self.stdout.write(self.style.SUCCESS(
                f"✅ Step 3 done in {time.time()-t0:.1f}s — {size_kb} KB"
            ))
            self.stdout.write(f"   HTML: {html_path}")

            # ── Step 4 ────────────────────────────────────────────────────
            if step >= 4:
                self.stdout.write(self.style.WARNING("\n── Step 4: Render PDF ──"))
                t0 = time.time()
                from src.step4_render import render_pdf
                pdf_out  = str(out_dir / "output.pdf")
                pdf_path_out = render_pdf(html_path, pdf_out)
                size_kb  = Path(pdf_path_out).stat().st_size // 1024
                self.stdout.write(self.style.SUCCESS(
                    f"✅ Step 4 done in {time.time()-t0:.1f}s — {size_kb} KB"
                ))
                self.stdout.write(f"   PDF:  {pdf_path_out}")

        self.stdout.write(self.style.SUCCESS(f"\n{'='*60}"))
        self.stdout.write(self.style.SUCCESS("Pipeline test complete!"))
        self.stdout.write(self.style.SUCCESS(f"{'='*60}\n"))
