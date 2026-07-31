"""
Auto-create a UserProfile and a personal Workspace when a new User is created.
"""
import re
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.text import slugify

from .models import UserProfile, Workspace, WorkspaceMember


def _unique_slug(base: str) -> str:
    slug = slugify(base)[:80]
    candidate = slug
    n = 1
    while Workspace.objects.filter(slug=candidate).exists():
        candidate = f"{slug}-{n}"
        n += 1
    return candidate


@receiver(post_save, sender=User)
def create_user_profile_and_workspace(sender, instance, created, **kwargs):
    if not created:
        return

    # 1. Profile
    UserProfile.objects.get_or_create(user=instance)

    # 2. Personal workspace named after the user
    name = instance.get_full_name() or instance.email.split("@")[0]
    ws   = Workspace.objects.create(
        name=f"{name}'s workspace",
        slug=_unique_slug(name),
        type=Workspace.TYPE_PERSONAL,
    )
    WorkspaceMember.objects.create(
        workspace=ws,
        user=instance,
        role=WorkspaceMember.ROLE_OWNER,
    )
