from django.contrib import admin
from .models import UserProfile, Workspace, WorkspaceMember, Invitation


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "timezone", "created_at"]
    search_fields = ["user__email", "user__first_name"]


class WorkspaceMemberInline(admin.TabularInline):
    model = WorkspaceMember
    extra = 0


class InvitationInline(admin.TabularInline):
    model = Invitation
    extra = 0
    readonly_fields = ["token", "created_at", "expires_at"]


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display  = ["name", "type", "slug", "created_at"]
    list_filter   = ["type"]
    search_fields = ["name", "slug"]
    inlines       = [WorkspaceMemberInline, InvitationInline]


@admin.register(WorkspaceMember)
class WorkspaceMemberAdmin(admin.ModelAdmin):
    list_display  = ["user", "workspace", "role", "joined_at"]
    list_filter   = ["role"]
    search_fields = ["user__email", "workspace__name"]


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display  = ["email", "workspace", "role", "status", "created_at", "expires_at"]
    list_filter   = ["status", "role"]
    search_fields = ["email", "workspace__name"]
    readonly_fields = ["token"]
