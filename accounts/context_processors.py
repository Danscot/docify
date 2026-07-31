"""
Injects invitation count into every template context so the badge
in the user dropdown always shows the correct number without a
separate query in every view.
"""
from .models import Invitation


def pending_invitations(request):
    if not request.user.is_authenticated:
        return {"pending_inv_count": 0}
    count = Invitation.objects.filter(
        email__iexact=request.user.email,
        status=Invitation.STATUS_PENDING,
    ).count()
    return {"pending_inv_count": count}
