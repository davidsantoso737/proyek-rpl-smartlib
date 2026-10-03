from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.notification_list_view, name="list"),
    path("<int:pk>/baca/", views.notification_read_view, name="read"),
    path(
        "tandai-semua/",
        views.notification_read_all_view,
        name="read_all",
    ),
]
