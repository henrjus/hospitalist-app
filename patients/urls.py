# patients/urls.py
from django.urls import path
from . import views

app_name = "patients"

urlpatterns = [
    path("notifications/", views.notifications_list, name="notifications_list"),
    path("notifications/<int:pk>/read/", views.notification_mark_read, name="notification_mark_read"),
    path("notifications/<int:pk>/unread/", views.notification_mark_unread, name="notification_mark_unread"),
    path("notifications/mark-all-read/", views.notifications_mark_all_read, name="notifications_mark_all_read"),
]
