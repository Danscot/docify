import uuid
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse
from django.utils.text import slugify
from django.db import transaction

from .models import UserProfile, Workspace, WorkspaceMember, Invitation
from .forms  import (SignupForm, LoginForm, ProfileForm, WorkspaceSettingsForm,
                      InviteForm, ChangePasswordForm)


# ── Auth ───────────────────────────────────────────────────────────────────────

def signup(request):
    if request.user.is_authenticated:
        return redirect("core:dashboard")

    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            with transaction.atomic():
                user = User.objects.create_user(
                    username=data["email"],
                    email=data["email"],
                    password=data["password"],
                    first_name=data["first_name"],
                    last_name=data["last_name"],
                )
                # Signal auto-creates profile + personal workspace.
                # If team type, create an additional team workspace.
                if data["account_type"] == "team":
                    team_name = data["team_name"]
                    slug = _unique_slug(team_name)
                    ws = Workspace.objects.create(
                        name=team_name,
                        slug=slug,
                        type=Workspace.TYPE_TEAM,
                    )
                    WorkspaceMember.objects.create(
                        workspace=ws, user=user,
                        role=WorkspaceMember.ROLE_OWNER,
                    )
                    request.session["active_workspace"] = str(ws.pk)

            login(request, user)
            messages.success(request, f"Welcome to Docify, {user.first_name}!")
            return redirect("core:dashboard")
    else:
        form = SignupForm()

    return render(request, "accounts/signup.html", {"form": form})


def user_login(request):
    if request.user.is_authenticated:
        return redirect("core:dashboard")

    if request.method == "POST":
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirect(request.GET.get("next", "core:dashboard"))
        messages.error(request, "Invalid email or password.")
    else:
        form = LoginForm()

    return render(request, "accounts/login.html", {"form": form})


@login_required
def user_logout(request):
    logout(request)
    return redirect("accounts:login")


# ── Profile ────────────────────────────────────────────────────────────────────

@login_required
def profile(request):
    profile_obj, _ = UserProfile.objects.get_or_create(user=request.user)

    if request.method == "POST":
        form = ProfileForm(request.POST, request.FILES, instance=profile_obj)
        if form.is_valid():
            request.user.first_name = form.cleaned_data["first_name"]
            request.user.last_name  = form.cleaned_data["last_name"]
            request.user.email      = form.cleaned_data["email"]
            request.user.username   = form.cleaned_data["email"]
            request.user.save()
            form.save()
            messages.success(request, "Profile updated.")
            return redirect("accounts:profile")
    else:
        form = ProfileForm(instance=profile_obj, initial={
            "first_name": request.user.first_name,
            "last_name":  request.user.last_name,
            "email":      request.user.email,
        })

    pw_form = ChangePasswordForm(request.user)
    return render(request, "accounts/profile.html", {
        "form":    form,
        "pw_form": pw_form,
        "profile": profile_obj,
    })


@login_required
def change_password(request):
    if request.method == "POST":
        form = ChangePasswordForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, "Password updated successfully.")
        else:
            for error in form.errors.values():
                messages.error(request, error.as_text())
    return redirect("accounts:profile")


# ── Workspaces ─────────────────────────────────────────────────────────────────

@login_required
def workspace_list(request):
    memberships = request.user.workspace_memberships.select_related("workspace").all()
    return render(request, "accounts/workspace_list.html", {"memberships": memberships})


@login_required
def workspace_create(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        ws_type = request.POST.get("type", "team")
        if not name:
            messages.error(request, "Please enter a workspace name.")
        else:
            ws = Workspace.objects.create(
                name=name,
                slug=_unique_slug(name),
                type=ws_type,
            )
            WorkspaceMember.objects.create(
                workspace=ws, user=request.user,
                role=WorkspaceMember.ROLE_OWNER,
            )
            request.session["active_workspace"] = str(ws.pk)
            messages.success(request, f"Workspace '{name}' created.")
            return redirect("accounts:workspace_settings", pk=ws.pk)
    return render(request, "accounts/workspace_create.html")


@login_required
def workspace_switch(request, pk):
    ws = get_object_or_404(Workspace, pk=pk, members__user=request.user)
    request.session["active_workspace"] = str(ws.pk)
    messages.success(request, f"Switched to '{ws.name}'.")
    return redirect(request.GET.get("next", "core:dashboard"))


@login_required
def workspace_settings(request, pk):
    ws = get_object_or_404(Workspace, pk=pk)
    if not ws.can_manage(request.user):
        messages.error(request, "You don't have permission to manage this workspace.")
        return redirect("core:dashboard")

    form = WorkspaceSettingsForm(instance=ws)
    if request.method == "POST":
        form = WorkspaceSettingsForm(request.POST, request.FILES, instance=ws)
        if form.is_valid():
            form.save()
            messages.success(request, "Workspace settings saved.")
            return redirect("accounts:workspace_settings", pk=ws.pk)

    members     = ws.members.select_related("user").all()
    invite_form = InviteForm(workspace=ws)
    invitations = ws.invitations.filter(status="pending").order_by("-created_at")

    return render(request, "accounts/workspace_settings.html", {
        "ws":           ws,
        "form":         form,
        "members":      members,
        "invite_form":  invite_form,
        "invitations":  invitations,
        "can_manage":   True,
    })


@login_required
def member_remove(request, pk, user_id):
    ws = get_object_or_404(Workspace, pk=pk)
    if not ws.can_manage(request.user):
        messages.error(request, "Permission denied.")
        return redirect("core:dashboard")

    member = get_object_or_404(WorkspaceMember, workspace=ws, user_id=user_id)
    if member.is_owner:
        messages.error(request, "Cannot remove the workspace owner.")
    else:
        member.delete()
        messages.success(request, f"{member.user.email} removed from workspace.")
    return redirect("accounts:workspace_settings", pk=pk)


@login_required
def member_role_change(request, pk, user_id):
    ws = get_object_or_404(Workspace, pk=pk)
    if not ws.can_manage(request.user):
        messages.error(request, "Permission denied.")
        return redirect("core:dashboard")

    member  = get_object_or_404(WorkspaceMember, workspace=ws, user_id=user_id)
    new_role = request.POST.get("role")
    if new_role in (WorkspaceMember.ROLE_ADMIN, WorkspaceMember.ROLE_MEMBER):
        member.role = new_role
        member.save()
        messages.success(request, f"Role updated for {member.user.email}.")
    return redirect("accounts:workspace_settings", pk=pk)


# ── Invitations ────────────────────────────────────────────────────────────────

@login_required
def invite_send(request, pk):
    """
    Send an invitation to join a workspace.
    If the email belongs to an existing Docify user, add them directly
    (no email link needed) and notify them via a pending notification.
    If not, create a pending Invitation they accept via email link.
    """
    ws = get_object_or_404(Workspace, pk=pk)
    if not ws.can_manage(request.user):
        messages.error(request, "Permission denied.")
        return redirect("core:dashboard")

    form = InviteForm(workspace=ws, data=request.POST)
    if form.is_valid():
        email = form.cleaned_data["email"]
        role  = form.cleaned_data["role"]

        # Check if this email belongs to an existing platform user
        existing_user = User.objects.filter(email__iexact=email).first()

        if existing_user:
            # Add them directly — no email link needed
            _, created = WorkspaceMember.objects.get_or_create(
                workspace=ws,
                user=existing_user,
                defaults={"role": role},
            )
            if created:
                # Create an accepted invitation record for audit trail + notification
                Invitation.objects.create(
                    workspace=ws,
                    invited_by=request.user,
                    email=email,
                    role=role,
                    status=Invitation.STATUS_PENDING,   # pending = waiting for their ack
                )
                messages.success(
                    request,
                    f"{existing_user.get_full_name() or email} is already on Docify — "
                    f"they've been added to '{ws.name}' and will see it on their next login."
                )
            else:
                messages.warning(request, f"{email} is already a member of this workspace.")
        else:
            # External user — create pending invite with token
            inv = Invitation.objects.create(
                workspace=ws,
                invited_by=request.user,
                email=email,
                role=role,
            )
            invite_url = request.build_absolute_uri(
                f"/accounts/invite/accept/{inv.token}/"
            )
            messages.success(
                request,
                f"Invitation sent to {email}. "
                f"Share this link with them: {invite_url}"
            )
    else:
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)

    return redirect("accounts:workspace_settings", pk=pk)


@login_required
def invite_cancel(request, pk, token):
    ws  = get_object_or_404(Workspace, pk=pk)
    inv = get_object_or_404(Invitation, token=token, workspace=ws)
    if ws.can_manage(request.user):
        inv.status = Invitation.STATUS_EXPIRED
        inv.save()
        messages.success(request, f"Invitation to {inv.email} cancelled.")
    return redirect("accounts:workspace_settings", pk=pk)


@login_required
def my_invitations(request):
    """
    Show all pending invitations for the current user's email.
    Lets users accept or decline workspace invitations directly in the UI.
    """
    pending = Invitation.objects.filter(
        email__iexact=request.user.email,
        status=Invitation.STATUS_PENDING,
    ).select_related("workspace", "invited_by").order_by("-created_at")

    return render(request, "accounts/my_invitations.html", {
        "pending_invitations": pending,
    })


@login_required
def invitation_respond(request, token):
    """Accept or decline an invitation from the My Invitations page."""
    inv = get_object_or_404(
        Invitation,
        token=token,
        email__iexact=request.user.email,
    )

    if not inv.is_valid:
        messages.error(request, "This invitation has expired or is no longer valid.")
        return redirect("accounts:my_invitations")

    action = request.POST.get("action")

    if action == "accept":
        _accept_invitation(request.user, inv)
        request.session["active_workspace"] = str(inv.workspace.pk)
        messages.success(request, f"You joined '{inv.workspace.name}'! Switching to it now.")
        return redirect("core:dashboard")

    elif action == "decline":
        inv.status = Invitation.STATUS_EXPIRED
        inv.save()
        messages.info(request, f"Invitation to '{inv.workspace.name}' declined.")
        return redirect("accounts:my_invitations")


    return redirect("accounts:my_invitations")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _unique_slug(name: str) -> str:
    from django.utils.text import slugify
    slug = slugify(name)[:80]
    candidate, n = slug, 1
    while Workspace.objects.filter(slug=candidate).exists():
        candidate = f"{slug}-{n}"
        n += 1
    return candidate


def _accept_invitation(user, inv):
    WorkspaceMember.objects.get_or_create(
        workspace=inv.workspace,
        user=user,
        defaults={"role": inv.role},
    )
    inv.status = Invitation.STATUS_ACCEPTED
    inv.save()


def invite_accept(request, token):
    """Public endpoint — no login required so external users can sign up first."""
    inv = get_object_or_404(Invitation, token=token)

    if not inv.is_valid:
        return render(request, "accounts/invite_invalid.html", {"inv": inv})

    if request.user.is_authenticated:
        if request.user.email.lower() != inv.email.lower():
            messages.error(request, f"This invitation was sent to {inv.email}.")
            return redirect("core:dashboard")
        _accept_invitation(request.user, inv)
        messages.success(request, f"You joined '{inv.workspace.name}'!")
        request.session["active_workspace"] = str(inv.workspace.pk)
        return redirect("core:dashboard")

    # Store token in session, redirect to signup/login
    request.session["pending_invite"] = str(inv.token)
    return render(request, "accounts/invite_accept.html", {"inv": inv})


# ── User lookup API (for invite form) ──────────────────────────────────────────

@login_required
def user_lookup(request):
    """
    Returns whether an email belongs to an existing Docify user.
    Used by the invite form to show instant feedback.
    """
    email = request.GET.get("email", "").strip().lower()
    if not email or "@" not in email:
        return JsonResponse({"found": False})

    user = User.objects.filter(email__iexact=email).first()
    if user:
        name = user.get_full_name() or user.email
        initials = "".join(p[0].upper() for p in name.split()[:2]) if user.get_full_name() else email[0].upper()
        return JsonResponse({"found": True, "name": name, "initials": initials})

    return JsonResponse({"found": False})
