from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("konfigurasi/", views.policy_view, name="policy"),
    path(
        "konfigurasi/ai/simpan/",
        views.ai_config_save_view,
        name="ai_config_save",
    ),
    path(
        "konfigurasi/ai/uji/",
        views.ai_config_test_view,
        name="ai_config_test",
    ),
    path(
        "konfigurasi/ai/models/",
        views.ai_config_models_view,
        name="ai_config_models",
    ),
    path(
        "konfigurasi/ai/reset/",
        views.ai_config_reset_view,
        name="ai_config_reset",
    ),
]
