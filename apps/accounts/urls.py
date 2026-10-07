from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path(
        "dashboard/siswa/",
        views.siswa_dashboard_view,
        name="dashboard_siswa",
    ),
    path(
        "dashboard/guru/",
        views.guru_dashboard_view,
        name="dashboard_guru",
    ),
    path(
        "dashboard/pustakawan/",
        views.pustakawan_dashboard_view,
        name="dashboard_pustakawan",
    ),
    path("profile/", views.profile_view, name="profile"),
    path(
        "password/change/",
        views.password_change_view,
        name="password_change",
    ),
    path("pengguna/", views.user_list_view, name="user_list"),
    path(
        "pengguna/tambah/<str:role>/",
        views.user_create_view,
        name="user_create",
    ),
    path(
        "pengguna/<int:pk>/ubah/",
        views.user_update_view,
        name="user_update",
    ),
    path(
        "pengguna/<int:pk>/status/",
        views.user_toggle_active_view,
        name="user_toggle_active",
    ),
    path(
        "pengguna/<int:pk>/reset-password/",
        views.user_reset_password_view,
        name="user_reset_password",
    ),
    path(
        "pengguna/import/<str:kind>/",
        views.import_upload_view,
        name="import_upload",
    ),
    path(
        "pengguna/import/<str:kind>/template/",
        views.import_template_view,
        name="import_template",
    ),
    path(
        "pengguna/import/<str:kind>/konfirmasi/",
        views.import_confirm_view,
        name="import_confirm",
    ),
]
