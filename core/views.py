import json
import os
from pathlib import Path
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, FileResponse, Http404
from django.contrib import messages
from django.conf import settings
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required

from .models import Template, GeneratedDocument
from .forms import TemplateUploadForm, DocumentCreateForm, DocumentRefineForm
from . import pipeline


# ── Dashboard ──────────────────────────────────────────────────────────────────

@login_required
def dashboard(request):
    ws = request.workspace
    qs_t = Template.objects.filter(workspace=ws) if ws else Template.objects.none()
    qs_d = GeneratedDocument.objects.filter(workspace=ws) if ws else GeneratedDocument.objects.none()
    ctx = {
        "template_count":   qs_t.count(),
        "document_count":   qs_d.count(),
        "done_count":       qs_d.filter(status="done").count(),
        "recent_templates": qs_t.order_by("-created_at")[:4],
        "recent_documents": qs_d.order_by("-created_at")[:4],
    }
    return render(request, "core/dashboard.html", ctx)


# ── Templates ──────────────────────────────────────────────────────────────────

@login_required
def template_list(request):
    ws = request.workspace
    templates = Template.objects.filter(workspace=ws) if ws else Template.objects.none()
    return render(request, "core/template_list.html", {"templates": templates})


@login_required
def template_upload(request):
    if request.method == "POST":
        form = TemplateUploadForm(request.POST, request.FILES)
        if form.is_valid():
            tmpl = form.save(commit=False)
            tmpl.workspace  = request.workspace
            tmpl.created_by = request.user
            tmpl.save()
            pipeline.async_analyze(str(tmpl.pk))
            messages.success(request, f"'{tmpl.name}' is being analyzed — this may take a minute.")
            return redirect("core:template_detail", pk=tmpl.pk)
    else:
        form = TemplateUploadForm()
    return render(request, "core/template_upload.html", {"form": form})


@login_required
def template_detail(request, pk):
    tmpl = get_object_or_404(Template, pk=pk)
    documents = tmpl.documents.order_by("-created_at")
    return render(request, "core/template_detail.html", {"tmpl": tmpl, "documents": documents})


@require_POST
@login_required
def template_delete(request, pk):
    tmpl = get_object_or_404(Template, pk=pk)
    name = tmpl.name
    tmpl.delete()
    messages.success(request, f"Template '{name}' deleted.")
    return redirect("core:template_list")


@login_required
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

@login_required
def document_list(request):
    ws = request.workspace
    documents = (GeneratedDocument.objects
                 .filter(workspace=ws)
                 .select_related("template") if ws else GeneratedDocument.objects.none())
    return render(request, "core/document_list.html", {"documents": documents})


@login_required
def document_create(request):
    import re

    # Pre-select template if given via query string
    initial   = {}
    tmpl_id   = request.GET.get("template")
    preselect = None

    if tmpl_id:
        try:
            preselect = Template.objects.get(pk=tmpl_id, status="ready")
            initial["template"] = preselect
        except Template.DoesNotExist:
            pass

    if request.method == "POST":
        form = DocumentCreateForm(request.POST, workspace=request.workspace)
        if form.is_valid():
            tmpl = form.cleaned_data["template"]

            # The JS always assembles content into the hidden 'content' field
            # regardless of which mode the user chose. We just read it directly.
            content = request.POST.get("content", "").strip()

            # Fallback: if the hidden field is empty but field_* values exist
            # (e.g. JS didn't run), build content from individual fields
            if not content and tmpl.template_dir:
                skeleton_path = Path(tmpl.template_dir) / "skeleton.html"
                if skeleton_path.exists():
                    html         = skeleton_path.read_text(encoding="utf-8")
                    placeholders = sorted(set(re.findall(r'\{\{([A-Z0-9_]+)\}\}', html)))
                    lines = []
                    for ph in placeholders:
                        val = request.POST.get(f"field_{ph}", "").strip()
                        if val:
                            lines.append(f"{ph}: {val}")
                    extra = request.POST.get("extra_instructions", "").strip()
                    if extra:
                        lines.append(f"\n[EXTRA INSTRUCTIONS]\n{extra}")
                    content = "\n".join(lines)

            if not content:
                messages.error(request, "Please provide content for the document.")
                return render(request, "core/document_create.html", {
                    "form":          form,
                    "placeholders":  placeholders,
                    "defaults":      {},
                    "defaults_json": "{}",
                    "preselect":     tmpl,
                })

            doc = form.save(commit=False)
            doc.content     = content
            doc.workspace   = request.workspace
            doc.created_by  = request.user
            doc.mode        = request.POST.get("mode", "fill")
            doc.save()
            pipeline.async_generate(str(doc.pk))
            messages.success(request, f"Generating '{doc.title}'…")
            return redirect("core:document_detail", pk=doc.pk)
    else:
        form = DocumentCreateForm(initial=initial, workspace=request.workspace)

    # Load placeholders + defaults for the pre-selected template (if any)
    placeholders = []
    defaults     = {}
    if preselect and preselect.template_dir:
        skeleton_path = Path(preselect.template_dir) / "skeleton.html"
        if skeleton_path.exists():
            html         = skeleton_path.read_text(encoding="utf-8")
            placeholders = sorted(set(re.findall(r'\{\{([A-Z0-9_]+)\}\}', html)))

            # Pull defaults from last successful document for this template
            last_doc = (
                preselect.documents
                .filter(status="done")
                .order_by("-created_at")
                .first()
            )
            if last_doc and last_doc.content:
                ph_set = set(placeholders)
                for line in last_doc.content.splitlines():
                    line = line.strip()
                    if line.startswith("[EXTRA"):
                        break
                    m = re.match(r'([A-Z][A-Z0-9_]*)\s*:\s*(.*)', line)
                    if m and m.group(1) in ph_set:
                        defaults[m.group(1)] = m.group(2).strip()

    return render(request, "core/document_create.html", {
        "form":          form,
        "placeholders":  placeholders,
        "defaults":      defaults,
        "defaults_json": json.dumps(defaults, ensure_ascii=False),
        "preselect":     preselect,
    })


@login_required
def document_detail(request, pk):
    doc = get_object_or_404(GeneratedDocument, pk=pk)
    refine_form = DocumentRefineForm()
    return render(request, "core/document_detail.html", {"doc": doc, "refine_form": refine_form})


@require_POST
@login_required
def document_delete(request, pk):
    doc = get_object_or_404(GeneratedDocument, pk=pk)
    title = doc.title
    doc.delete()
    messages.success(request, f"Document '{title}' deleted.")
    return redirect("core:document_list")


@login_required
def document_status(request, pk):
    doc = get_object_or_404(GeneratedDocument, pk=pk)
    return JsonResponse({
        "status":   doc.status,
        "pdf_url":  doc.pdf_file.url if doc.pdf_file else None,
        "html_url": doc.html_file.url if doc.html_file else None,
        "error":    doc.error_message,
    })


@require_POST
@login_required
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


@login_required
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

@login_required
@login_required
def log_viewer(request):
    """Tail the last N lines of docify.log — staff/superuser only."""
    if not request.user.is_staff:
        messages.error(request, "Access denied — admin only.")
        return redirect("core:dashboard")
    from django.conf import settings as s
    log_path = s.BASE_DIR / "docify.log"
    lines = []
    n = int(request.GET.get("n", 200))
    if log_path.exists():
        with open(log_path, "r") as f:
            all_lines = f.readlines()
            lines = all_lines[-n:]
    return render(request, "core/log_viewer.html", {
        "lines":    lines,
        "log_path": log_path,
        "n":        n,
    })


# ── Template placeholder preview ───────────────────────────────────────────────

@login_required
def template_placeholders(request, pk):
    """
    Return the placeholders detected in this template's skeleton as JSON,
    plus default values extracted from the most recently generated document
    for this template (so repeat invoices pre-fill with the last-used values).
    """
    import re
    tmpl = get_object_or_404(Template, pk=pk)
    placeholders    = []
    skeleton_exists = False
    warning         = None
    defaults        = {}

    if tmpl.template_dir:
        skeleton = Path(tmpl.template_dir) / "skeleton.html"
        if skeleton.exists():
            skeleton_exists = True
            html      = skeleton.read_text(encoding="utf-8")
            all_found = sorted(set(re.findall(r'\{\{([A-Z0-9_]+)\}\}', html)))

            GENERIC_TOKENS = {"PLACEHOLDER", "FIELD_NAME", "VALUE", "TEXT", "CONTENT",
                               "VARIABLE", "DATA", "INPUT", "EXAMPLE"}
            placeholders = [p for p in all_found if p not in GENERIC_TOKENS]

            if not placeholders and all_found:
                warning      = "The skeleton only contains generic placeholder names. Re-analyze this template for better results."
                placeholders = all_found
            elif not all_found:
                warning = "No placeholders found in skeleton. Re-analyze this template."

    # Pull default values from the last successfully generated document
    if placeholders:
        last_doc = (
            tmpl.documents
            .filter(status="done")
            .order_by("-created_at")
            .first()
        )
        if last_doc and last_doc.content:
            ph_set = set(placeholders)
            for line in last_doc.content.splitlines():
                line = line.strip()
                if line.startswith("[EXTRA"):
                    break
                m = re.match(r'([A-Z][A-Z0-9_]*)\s*:\s*(.*)', line)
                if m and m.group(1) in ph_set:
                    defaults[m.group(1)] = m.group(2).strip()

    return JsonResponse({
        "placeholders":    placeholders,
        "skeleton_exists": skeleton_exists,
        "warning":         warning,
        "defaults":        defaults,
        "has_defaults":    bool(defaults),
    })
