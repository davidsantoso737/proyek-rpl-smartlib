from django.urls import path

from . import views

app_name = "whatsapp"

urlpatterns = [
    path("ajukan/", views.request_connection_view, name="request"),
    path("batalkan/", views.cancel_request_view, name="cancel_request"),
    path("putuskan/", views.unlink_view, name="unlink"),
    path("verifikasi-otp/", views.verify_otp_view, name="verify_otp"),
    path("kirim-ulang-otp/", views.resend_otp_view, name="resend_otp"),
    path("preferensi/", views.preferences_view, name="preferences"),
    path(
        "verifikasi/",
        views.verification_list_view,
        name="verification_list",
    ),
    path(
        "verifikasi/<int:pk>/",
        views.verification_detail_view,
        name="verification_detail",
    ),
    path(
        "verifikasi/<int:pk>/setujui/",
        views.verification_approve_view,
        name="verification_approve",
    ),
    path(
        "verifikasi/<int:pk>/tolak/",
        views.verification_reject_view,
        name="verification_reject",
    ),
]
