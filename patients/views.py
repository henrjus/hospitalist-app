# patients/views.py
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.template import loader, TemplateDoesNotExist
from django.db.models import BooleanField, Case, When, Value, Max
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.utils.timezone import localtime

from .models import Notification


@login_required
def notifications_list(request):
    """
    Show the current user's notifications that are visible now (visible_at <= now).
    Supports ?show=unread (otherwise 'all'). Paginates 25 per page.
    Orders: unread first, then newest.
    Also supports ?partial=1 to return only the inner list HTML (for AJAX refresh).
    """
    now = timezone.now()

    # --- Filter toggle ---
    show = (request.GET.get("show") or "all").lower()
    if show not in {"all", "unread"}:
        show = "all"

    qs = (
        Notification.objects
        .filter(recipient=request.user, visible_at__lte=now)
        .annotate(
            is_unread=Case(
                When(read_at__isnull=True, then=Value(True)),
                default=Value(False),
                output_field=BooleanField(),
            )
        )
        # If your template shows patient info, this helps avoid extra queries:
        .select_related("patient")
    )

    if show == "unread":
        qs = qs.filter(read_at__isnull=True)

    qs = qs.order_by("-is_unread", "-visible_at", "-created_at")

    # ---- Pagination ----
    page = request.GET.get("page", 1)
    paginator = Paginator(qs, 25)
    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    # ---- Context (define BEFORE any rendering) ----
    context = {
        "notifications": list(page_obj.object_list),
        "page_obj": page_obj,
        "paginator": paginator,
        "current_filter": show,  # used by the template to highlight the toggle
    }

    # --- Partial render for AJAX refresh ---
    if request.GET.get("partial") == "1":
        try:
            tmpl = loader.get_template("patients/partials/notifications_list_inner.html")
            return HttpResponse(tmpl.render(context, request))
        except TemplateDoesNotExist:
            pass  # fall through to full-page render

    # ---- Full-page render (existing behavior) ----
    try:
        tmpl = loader.get_template("patients/notifications_list.html")
        return HttpResponse(tmpl.render(context, request))
    except TemplateDoesNotExist:
        lines = []
        for n in context["notifications"]:
            status = "UNREAD" if n.read_at is None else "READ"
            when = timezone.localtime(n.visible_at).strftime("%Y-%m-%d %H:%M")
            lines.append(f"[{status} {n.level}] {when} — {n.message}")
        return HttpResponse("\n".join(lines) or "No notifications.", content_type="text/plain")


@login_required
@require_POST
def notification_mark_read(request, pk: int):
    n = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if n.read_at is None:
        n.mark_read()
    return redirect("patients:notifications_list")


@login_required
@require_POST
def notification_mark_unread(request, pk: int):
    n = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if n.read_at is not None:
        n.mark_unread()
    return redirect("patients:notifications_list")


@login_required
@require_POST
def notification_ack(request, pk: int):
    """
    Acknowledge (dismiss/confirm) a notification.
    Only the recipient can acknowledge it.
    Idempotent: safe to call multiple times.
    """
    n = get_object_or_404(Notification, pk=pk, recipient=request.user)

    if n.acknowledged_at is None:
        n.acknowledged_at = timezone.now()
        n.acknowledged_by = request.user
        n.save(update_fields=["acknowledged_at", "acknowledged_by"])

    return JsonResponse({
        "ok": True,
        "acknowledged_at": n.acknowledged_at.isoformat(),
        "acknowledged_by": request.user.get_username(),
    })


@login_required
@require_POST
def notifications_mark_all_read(request):
    """
    Mark all of the current user's *visible* notifications as read.
    (visible_at <= now, read_at IS NULL)
    """
    now = timezone.now()
    (Notification.objects
        .filter(recipient=request.user, visible_at__lte=now, read_at__isnull=True)
        .update(read_at=now))
    return redirect("patients:notifications_list")


# === API: lightweight status for polling (legacy badge) ===
@login_required
def notification_status(request):
    """
    Returns a tiny JSON payload for polling the badge/toast:
      - unread_count: unread + already-visible items (legacy semantics)
      - latest_ts: most recent visibility time (to detect new since last check)
      - latest_message: the message of that latest notification (for toast)
      - server_now: server time (helps client-side drift)
    """
    now = timezone.now()

    # Only consider notifications that are visible now
    visible_qs = Notification.objects.filter(recipient=request.user, visible_at__lte=now)

    # Unread = visible and read_at is null (legacy meaning)
    unread_count = visible_qs.filter(read_at__isnull=True).count()

    # Prefer latest by visible_at (fallback to created_at)
    latest_obj = (
        visible_qs.order_by("-visible_at", "-created_at")
        .values("visible_at", "message")
        .first()
    )

    return JsonResponse({
        "unread_count": unread_count,
        "latest_ts": latest_obj["visible_at"].isoformat() if latest_obj else None,
        "latest_message": latest_obj["message"] if latest_obj else None,
        "server_now": now.isoformat(),
    })


# === API (NEW): unified global JSON feed for toasts/modals ===
@login_required
def notifications_feed(request):
    """
    JSON feed of the current user's *visible & unacknowledged* notifications.
    Optional ?since_id=<int> returns only notifications with id > since_id.

    Returns:
      {
        "items": [
          {"id","level","category","message","created_at","visible_at"}
        ],
        "unread_count": <int>,   # count of visible & UNACKNOWLEDGED items (new semantics)
        "latest_id": <int|null>  # newest notification id for THIS USER
      }
    """
    now = timezone.now()
    since_id = request.GET.get("since_id")

    qs = (
        Notification.objects
        .filter(recipient=request.user, visible_at__lte=now, acknowledged_at__isnull=True)
        .order_by("-id")
    )

    if since_id and str(since_id).isdigit():
        qs = qs.filter(id__gt=int(since_id))

    # Cap payload and return oldest→newest for natural reading
    items = list(qs[:20].values("id", "level", "category", "message", "created_at", "visible_at"))
    items.reverse()

    # Make datetimes ISO strings
    for it in items:
        ca = it.get("created_at")
        va = it.get("visible_at")
        it["created_at"] = localtime(ca).isoformat() if ca else None
        it["visible_at"] = localtime(va).isoformat() if va else None

    # Count of visible & unacknowledged (badge semantics for unified client)
    unack_count = (
        Notification.objects
        .filter(recipient=request.user, visible_at__lte=now, acknowledged_at__isnull=True)
        .count()
    )

    # Latest id for THIS USER (not global)
    latest_id = (
        Notification.objects
        .filter(recipient=request.user)
        .aggregate(max_id=Max("id"))
        .get("max_id")
    )

    return JsonResponse(
        {"items": items, "unread_count": unack_count, "latest_id": latest_id},
        status=200,
    )
