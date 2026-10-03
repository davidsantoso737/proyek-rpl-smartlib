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
]
