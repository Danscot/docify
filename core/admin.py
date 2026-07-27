from django.contrib import admin
from .models import Template, GeneratedDocument


@admin.register(Template)
class TemplateAdmin(admin.ModelAdmin):
    list_display  = ["name", "status", "total_pages", "primary_color", "created_at"]
    list_filter   = ["status"]
    search_fields = ["name"]
    readonly_fields = ["id", "created_at", "updated_at", "template_dir"]


@admin.register(GeneratedDocument)
class GeneratedDocumentAdmin(admin.ModelAdmin):
    list_display  = ["title", "template", "status", "created_at"]
    list_filter   = ["status", "template"]
    search_fields = ["title", "content"]
    readonly_fields = ["id", "created_at", "updated_at"]
