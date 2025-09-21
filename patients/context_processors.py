# patients/context_processors.py
from django.utils import timezone
from .models import Notification

def notifications_badge(request):
    """
    Provide counts for notifications:
      - notifications_unread_count: unread (read_at IS NULL) among notifications visible now.
      - notifications_badge_count: unacknowledged (acknowledged_at IS NULL) among notifications visible now.
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {
            "notifications_unread_count": 0,   # for page UI (e.g., "Mark all as read")
            "notifications_badge_count": 0,    # for header badge (unacknowledged)
        }

    now = timezone.now()

    # Page UI: unread by read_at
    unread = (
        Notification.objects
        .filter(recipient=user, visible_at__lte=now, read_at__isnull=True)
        .count()
    )

    # Header badge: unacknowledged by acknowledged_at
    unack = (
        Notification.objects
        .filter(recipient=user, visible_at__lte=now, acknowledged_at__isnull=True)
        .count()
    )

    return {
        "notifications_unread_count": unread,
        "notifications_badge_count": unack,
    }
