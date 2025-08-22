# patients/context_processors.py
from django.utils import timezone
from .models import Notification

def notifications_badge(request):
    """
    Provide unread notification count for the current user.
    Only counts notifications visible now (visible_at <= now) and unread.
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {"notifications_unread_count": 0}

    now = timezone.now()
    count = (
        Notification.objects
        .filter(recipient=user, visible_at__lte=now, read_at__isnull=True)
        .count()
    )
    return {"notifications_unread_count": count}
