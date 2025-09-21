# patients/urls.py
from django.urls import path
from . import views

app_name = "patients"

urlpatterns = [
    path("notifications/", views.notifications_list, name="notifications_list"),
    path("notifications/<int:pk>/read/", views.notification_mark_read, name="notification_mark_read"),
    path("notifications/<int:pk>/unread/", views.notification_mark_unread, name="notification_mark_unread"),
    path("notifications/<int:pk>/ack/", views.notification_ack, name="notification_ack"),
    path("notifications/mark-all-read/", views.notifications_mark_all_read, name="notifications_mark_all_read"),

    # Real status endpoint
    path("api/notifications/status/", views.notification_status, name="notification-status"),

    # JSON feed for global notifications (unacked only)
    path("api/notifications/", views.notifications_feed, name="notifications-feed"),
]
