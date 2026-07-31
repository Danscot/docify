import uuid
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone as tz_util


class UserProfile(models.Model):
    """Extends Django's built-in User with Docify-specific fields."""
    user       = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    avatar     = models.ImageField(upload_to="avatars/", null=True, blank=True)
    timezone   = models.CharField(max_length=60, default="UTC")
    created_at = models.DateTimeField(default=tz_util.now)

    def __str__(self):
        return f"{self.user.email} profile"

    @property
    def display_name(self):
        return self.user.get_full_name() or self.user.email

    @property
    def initials(self):
        name = self.user.get_full_name()
        if name:
            parts = name.split()
            return "".join(p[0].upper() for p in parts[:2])
        return self.user.email[0].upper()

    def active_workspace(self, request):
        """Return the workspace currently selected in session."""
        wid = request.session.get("active_workspace")
        if wid:
            try:
                return Workspace.objects.get(pk=wid,
                    members__user=self.user)
            except Workspace.DoesNotExist:
                pass
        # Fall back to first owned workspace
        return Workspace.objects.filter(
            members__user=self.user,
            members__role="owner"
        ).first()


class Workspace(models.Model):
    """
    The central tenant unit. Every Template and Document belongs to a Workspace.
    A Personal workspace has exactly one member (the owner).
    A Team workspace can have many members.
    """
    TYPE_PERSONAL = "personal"
    TYPE_TEAM     = "team"
    TYPE_CHOICES  = [
        (TYPE_PERSONAL, "Personal"),
        (TYPE_TEAM,     "Team"),
    ]

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name       = models.CharField(max_length=100)
    slug       = models.SlugField(max_length=100, unique=True)
    type       = models.CharField(max_length=10, choices=TYPE_CHOICES, default=TYPE_PERSONAL)
    logo       = models.ImageField(upload_to="workspace_logos/", null=True, blank=True)
    created_at = models.DateTimeField(default=tz_util.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def is_team(self):
        return self.type == self.TYPE_TEAM

    @property
    def owner(self):
        m = self.members.filter(role=WorkspaceMember.ROLE_OWNER).first()
        return m.user if m else None

    def get_member(self, user):
        return self.members.filter(user=user).first()

    def has_member(self, user):
        return self.members.filter(user=user).exists()

    def can_manage(self, user):
        """Owner or admin can manage workspace settings and members."""
        return self.members.filter(
            user=user, role__in=[WorkspaceMember.ROLE_OWNER, WorkspaceMember.ROLE_ADMIN]
        ).exists()


class WorkspaceMember(models.Model):
    ROLE_OWNER  = "owner"
    ROLE_ADMIN  = "admin"
    ROLE_MEMBER = "member"
    ROLE_CHOICES = [
        (ROLE_OWNER,  "Owner"),
        (ROLE_ADMIN,  "Admin"),
        (ROLE_MEMBER, "Member"),
    ]

    workspace  = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="members")
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name="workspace_memberships")
    role       = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_MEMBER)
    joined_at  = models.DateTimeField(default=tz_util.now)

    class Meta:
        unique_together = ("workspace", "user")
        ordering        = ["joined_at"]

    def __str__(self):
        return f"{self.user.email} @ {self.workspace.name} ({self.role})"

    @property
    def is_owner(self):
        return self.role == self.ROLE_OWNER

    @property
    def can_manage(self):
        return self.role in (self.ROLE_OWNER, self.ROLE_ADMIN)


class Invitation(models.Model):
    STATUS_PENDING  = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_EXPIRED  = "expired"
    STATUS_CHOICES  = [
        (STATUS_PENDING,  "Pending"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_EXPIRED,  "Expired"),
    ]

    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace   = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="invitations")
    invited_by  = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sent_invitations")
    email       = models.EmailField()
    role        = models.CharField(max_length=10,
                                   choices=WorkspaceMember.ROLE_CHOICES,
                                   default=WorkspaceMember.ROLE_MEMBER)
    token       = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status      = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at  = models.DateTimeField(default=tz_util.now)
    expires_at  = models.DateTimeField()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Invite {self.email} → {self.workspace.name}"

    def save(self, *args, **kwargs):
        if not self.expires_at:
            from datetime import timedelta
            self.expires_at = tz_util.now() + timedelta(days=7)
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        return tz_util.now() > self.expires_at

    @property
    def is_valid(self):
        return self.status == self.STATUS_PENDING and not self.is_expired

    @property
    def invited_user_exists(self):
        """True if this email belongs to an existing Docify account."""
        from django.contrib.auth.models import User as AuthUser
        return AuthUser.objects.filter(email__iexact=self.email).exists()
