from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("konfigurasi/", views.policy_view, name="policy"),
]
