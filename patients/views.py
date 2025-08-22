# patients/views.py
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404, redirect
from django.http import HttpResponse
from django.utils import timezone
from django.template import loader, TemplateDoesNotExist
from django.db.models import BooleanField, Case, When, Value
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

from .models import Notification


@login_required
def notifications_list(request):
    """
    Show the current user's notifications that are visible now (visible_at <= now).
    Supports ?show=unread (otherwise 'all'). Paginates 25 per page.
    Orders: unread first, then newest.
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

    context = {
        "notifications": list(page_obj.object_list),
        "page_obj": page_obj,
        "paginator": paginator,
        "current_filter": show,  # <-- used by the template to highlight the toggle
    }

    # Render (template exists; fallback kept just in case)
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
