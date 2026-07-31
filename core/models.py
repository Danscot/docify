from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import uuid


class Template(models.Model):
    STATUS_CHOICES = [
        ("pending",   "Pending"),
        ("analyzing", "Analyzing"),
        ("ready",     "Ready"),
        ("failed",    "Failed"),
    ]

    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace     = models.ForeignKey(
        "accounts.Workspace", on_delete=models.CASCADE,
        related_name="templates", null=True, blank=True,
    )
    created_by    = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="templates",
    )
    name          = models.CharField(max_length=200)
    source_pdf    = models.FileField(upload_to="uploads/sources/")
    template_dir  = models.CharField(max_length=500, blank=True)
    status        = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    total_pages   = models.PositiveIntegerField(null=True, blank=True)
    primary_color = models.CharField(max_length=30, blank=True)
    thumbnail     = models.ImageField(upload_to="uploads/thumbs/", null=True, blank=True)
    error_message = models.TextField(blank=True)
    created_at    = models.DateTimeField(default=timezone.now)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    @property
    def template_json_path(self):
        if self.template_dir:
            from pathlib import Path
            return Path(self.template_dir) / "template.json"
        return None

    @property
    def is_ready(self):
        return self.status == "ready"


class GeneratedDocument(models.Model):
    STATUS_CHOICES = [
        ("pending",    "Pending"),
        ("generating", "Generating"),
        ("done",       "Done"),
        ("failed",     "Failed"),
    ]

    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace   = models.ForeignKey(
        "accounts.Workspace", on_delete=models.CASCADE,
        related_name="documents", null=True, blank=True,
    )
    created_by  = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="documents",
    )
    template    = models.ForeignKey(Template, on_delete=models.CASCADE, related_name="documents")
    title       = models.CharField(max_length=200)
    content     = models.TextField()
    html_file   = models.FileField(upload_to="output/html/", null=True, blank=True)
    pdf_file    = models.FileField(upload_to="output/pdf/",  null=True, blank=True)
    status      = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    error_message = models.TextField(blank=True)
    created_at  = models.DateTimeField(default=timezone.now)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title
