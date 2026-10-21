from django.urls import path

from . import views

app_name = "telegram"

urlpatterns = [
    path("tautan/mulai/", views.link_start_view, name="link_start"),
    path("tautan/buka/", views.link_open_view, name="link_open"),
    path("tautan/batalkan/", views.link_cancel_view, name="link_cancel"),
    path("tautan/konfirmasi/", views.link_confirm_view, name="link_confirm"),
    path("putuskan/", views.unlink_view, name="unlink"),
    path("preferensi/", views.preferences_view, name="preferences"),
]
