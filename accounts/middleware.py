"""
WorkspaceMiddleware — attaches the active workspace to every request
so views and templates can use request.workspace directly.

Sets two attributes:
  request.workspace           — the active Workspace object (or None)
  request.workspace_can_manage — bool: True if user can manage the workspace
"""

EXEMPT_PREFIXES = (
    "/accounts/login/",
    "/accounts/signup/",
    "/accounts/invite/accept/",
    "/admin/",
    "/static/",
    "/media/",
)


class WorkspaceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Always initialize to safe defaults first
        request.workspace             = None
        request.workspace_can_manage  = False

        # Skip workspace resolution for admin and auth paths
        # (avoids interfering with Django's context copying in admin views)
        path = request.path
        if any(path.startswith(p) for p in EXEMPT_PREFIXES):
            return self.get_response(request)

        if request.user.is_authenticated:
            ws = self._resolve_workspace(request)
            if ws:
                request.workspace            = ws
                request.workspace_can_manage = ws.can_manage(request.user)

        return self.get_response(request)

    def _resolve_workspace(self, request):
        from .models import Workspace

        # 1. Session-stored preference
        wid = request.session.get("active_workspace")
        if wid:
            try:
                return Workspace.objects.get(pk=wid, members__user=request.user)
            except Workspace.DoesNotExist:
                del request.session["active_workspace"]

        # 2. First workspace the user belongs to
        ws = (Workspace.objects
              .filter(members__user=request.user)
              .order_by("-members__joined_at")
              .first())
        if ws:
            request.session["active_workspace"] = str(ws.pk)
        return ws
