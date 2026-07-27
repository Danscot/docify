from django.urls import path
from . import views

app_name = "core"

urlpatterns = [
    # Dashboard
    path("", views.dashboard, name="dashboard"),

    # Templates
    path("templates/",                        views.template_list,    name="template_list"),
    path("templates/upload/",                 views.template_upload,  name="template_upload"),
    path("templates/<uuid:pk>/",              views.template_detail,  name="template_detail"),
    path("templates/<uuid:pk>/delete/",       views.template_delete,  name="template_delete"),
    path("templates/<uuid:pk>/status/",        views.template_status,       name="template_status"),
    path("templates/<uuid:pk>/placeholders/", views.template_placeholders,  name="template_placeholders"),

    # Documents
    path("documents/",                        views.document_list,    name="document_list"),
    path("documents/create/",                 views.document_create,  name="document_create"),
    path("documents/<uuid:pk>/",              views.document_detail,  name="document_detail"),
    path("documents/<uuid:pk>/delete/",       views.document_delete,  name="document_delete"),
    path("documents/<uuid:pk>/status/",       views.document_status,  name="document_status"),
    path("documents/<uuid:pk>/refine/",       views.document_refine,  name="document_refine"),
    path("documents/<uuid:pk>/download/pdf/", views.document_download_pdf, name="document_download_pdf"),

    # Debug
    path("logs/", views.log_viewer, name="log_viewer"),
]
