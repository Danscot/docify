import os
from pathlib import Path
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, FileResponse, Http404
from django.contrib import messages
from django.conf import settings
from django.views.decorators.http import require_POST

from .models import Template, GeneratedDocument
from .forms import TemplateUploadForm, DocumentCreateForm, DocumentRefineForm
from . import pipeline


# ── Dashboard ──────────────────────────────────────────────────────────────────

def dashboard(request):
    ctx = {
        "template_count":  Template.objects.count(),
        "document_count":  GeneratedDocument.objects.count(),
        "done_count":      GeneratedDocument.objects.filter(status="done").count(),
        "recent_templates": Template.objects.order_by("-created_at")[:4],
        "recent_documents": GeneratedDocument.objects.order_by("-created_at")[:4],
    }
    return render(request, "core/dashboard.html", ctx)


# ── Templates ──────────────────────────────────────────────────────────────────

def template_list(request):
    templates = Template.objects.all()
    return render(request, "core/template_list.html", {"templates": templates})


def template_upload(request):
    if request.method == "POST":
        form = TemplateUploadForm(request.POST, request.FILES)
        if form.is_valid():
            tmpl = form.save()
            pipeline.async_analyze(str(tmpl.pk))
            messages.success(request, f"'{tmpl.name}' is being analyzed — this may take a minute.")
            return redirect("core:template_detail", pk=tmpl.pk)
    else:
        form = TemplateUploadForm()
    return render(request, "core/template_upload.html", {"form": form})


def template_detail(request, pk):
    tmpl = get_object_or_404(Template, pk=pk)
    documents = tmpl.documents.order_by("-created_at")
    return render(request, "core/template_detail.html", {"tmpl": tmpl, "documents": documents})


@require_POST
def template_delete(request, pk):
    tmpl = get_object_or_404(Template, pk=pk)
    name = tmpl.name
    tmpl.delete()
    messages.success(request, f"Template '{name}' deleted.")
    return redirect("core:template_list")


def template_status(request, pk):
    """Polling endpoint — returns JSON with current status."""
    tmpl = get_object_or_404(Template, pk=pk)
    return JsonResponse({
        "status":       tmpl.status,
        "total_pages":  tmpl.total_pages,
        "primary_color": tmpl.primary_color,
        "thumbnail":    tmpl.thumbnail.url if tmpl.thumbnail else None,
        "error":        tmpl.error_message,
    })


# ── Documents ──────────────────────────────────────────────────────────────────

def document_list(request):
    documents = GeneratedDocument.objects.select_related("template").all()
    return render(request, "core/document_list.html", {"documents": documents})


def document_create(request):
    # Pre-select template if given via query string
    initial = {}
    tmpl_id = request.GET.get("template")
    if tmpl_id:
        try:
            initial["template"] = Template.objects.get(pk=tmpl_id, status="ready")
        except Template.DoesNotExist:
            pass

    if request.method == "POST":
        form = DocumentCreateForm(request.POST)
        if form.is_valid():
            doc = form.save()
            pipeline.async_generate(str(doc.pk))
            messages.success(request, f"Generating '{doc.title}'…")
            return redirect("core:document_detail", pk=doc.pk)
    else:
        form = DocumentCreateForm(initial=initial)

    return render(request, "core/document_create.html", {"form": form})


def document_detail(request, pk):
    doc = get_object_or_404(GeneratedDocument, pk=pk)
    refine_form = DocumentRefineForm()
    return render(request, "core/document_detail.html", {"doc": doc, "refine_form": refine_form})


@require_POST
def document_delete(request, pk):
    doc = get_object_or_404(GeneratedDocument, pk=pk)
    title = doc.title
    doc.delete()
    messages.success(request, f"Document '{title}' deleted.")
    return redirect("core:document_list")


def document_status(request, pk):
    doc = get_object_or_404(GeneratedDocument, pk=pk)
    return JsonResponse({
        "status":   doc.status,
        "pdf_url":  doc.pdf_file.url if doc.pdf_file else None,
        "html_url": doc.html_file.url if doc.html_file else None,
        "error":    doc.error_message,
    })


@require_POST
def document_refine(request, pk):
    doc  = get_object_or_404(GeneratedDocument, pk=pk)
    form = DocumentRefineForm(request.POST)
    if form.is_valid():
        feedback = form.cleaned_data["feedback"]
        pipeline.async_refine(str(doc.pk), feedback)
        messages.success(request, "Applying refinements…")
    else:
        messages.error(request, "Please provide refinement instructions.")
    return redirect("core:document_detail", pk=pk)


def document_download_pdf(request, pk):
    doc = get_object_or_404(GeneratedDocument, pk=pk)
    if not doc.pdf_file:
        raise Http404("PDF not ready yet.")
    response = FileResponse(
        open(doc.pdf_file.path, "rb"),
        content_type="application/pdf",
    )
    response["Content-Disposition"] = f'attachment; filename="{doc.title}.pdf"'
    return response


# ── Live log viewer ────────────────────────────────────────────────────────────

def log_viewer(request):
    """Tail the last N lines of docify.log for live debugging."""
    from django.conf import settings as s
    log_path = s.BASE_DIR / "docify.log"
    lines = []
    n = int(request.GET.get("n", 200))
    if log_path.exists():
        with open(log_path, "r") as f:
            all_lines = f.readlines()
            lines = all_lines[-n:]
    return render(request, "core/log_viewer.html", {
        "lines": lines,
        "log_path": log_path,
        "n": n,
    })


# ── Template placeholder preview ───────────────────────────────────────────────

def template_placeholders(request, pk):
    """Return the placeholders detected in this template's skeleton as JSON."""
    import re
    tmpl = get_object_or_404(Template, pk=pk)
    placeholders = []
    skeleton_exists = False
    warning = None

    if tmpl.template_dir:
        skeleton = Path(tmpl.template_dir) / "skeleton.html"
        if skeleton.exists():
            skeleton_exists = True
            html = skeleton.read_text(encoding="utf-8")
            all_found = sorted(set(re.findall(r'\{\{([A-Z0-9_]+)\}\}', html)))

            # Filter out the generic example token the model sometimes copies verbatim
            GENERIC_TOKENS = {"PLACEHOLDER", "FIELD_NAME", "VALUE", "TEXT", "CONTENT",
                               "VARIABLE", "DATA", "INPUT", "EXAMPLE"}
            placeholders = [p for p in all_found if p not in GENERIC_TOKENS]

            if not placeholders and all_found:
                # All tokens were generic — skeleton is low quality
                warning = "The skeleton only contains generic placeholder names. Re-analyze this template for better results."
                placeholders = all_found  # show them anyway so user sees the issue
            elif not all_found:
                warning = "No placeholders found in skeleton. Re-analyze this template."

    return JsonResponse({
        "placeholders": placeholders,
        "skeleton_exists": skeleton_exists,
        "warning": warning,
    })
