from django.urls import path

from . import views

app_name = "ai"

urlpatterns = [
    path("asisten/", views.assistant_view, name="assistant"),
    path("asisten/tanya/", views.assistant_ask_view, name="assistant_ask"),
]
