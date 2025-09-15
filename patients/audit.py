# patients/audit.py
from __future__ import annotations

from typing import Optional
from django.contrib.auth.signals import user_logged_in, user_login_failed, user_logged_out
from django.dispatch import receiver

from .models import AuditLog


def _get_ip(request) -> Optional[str]:
    if request is None:
        return None
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        # First IP in list is original client
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _get_user_agent(request) -> str:
    if request is None:
        return ""
    return request.META.get("HTTP_USER_AGENT", "")


@receiver(user_logged_in)
def audit_login_success(sender, request, user, **kwargs):
    AuditLog.objects.create(
        event=AuditLog.Event.LOGIN_SUCCESS,
        changed_by=user,
        username=getattr(user, "username", "") or (user.get_username() if user else ""),
        ip_address=_get_ip(request),
        user_agent=_get_user_agent(request),
    )


@receiver(user_login_failed)
def audit_login_failed(sender, credentials, request, **kwargs):
    username = ""
    try:
        username = (credentials or {}).get("username", "")  # credentials may be None
    except Exception:
        username = ""
    AuditLog.objects.create(
        event=AuditLog.Event.LOGIN_FAILED,
        changed_by=None,
        username=username or "",
        ip_address=_get_ip(request),
        user_agent=_get_user_agent(request),
    )


@receiver(user_logged_out)
def audit_logout(sender, request, user, **kwargs):
    # user may be None depending on context
    uname = ""
    if user is not None:
        try:
            uname = user.get_username()
        except Exception:
            uname = getattr(user, "username", "") or ""
    AuditLog.objects.create(
        event=AuditLog.Event.LOGOUT,
        changed_by=(user if getattr(user, "is_authenticated", False) else None),
        username=uname,
        ip_address=_get_ip(request),
        user_agent=_get_user_agent(request),
    )
