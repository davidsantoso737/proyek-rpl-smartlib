from django.urls import path

from . import views

app_name = "materials"

urlpatterns = [
    path("", views.material_list_view, name="list"),
    path("saya/", views.my_materials_view, name="my"),
    path("unggah/", views.material_upload_view, name="upload"),
    path(
        "<int:pk>/minta-hapus/",
        views.material_deletion_request_view,
        name="deletion_request",
    ),
    path(
        "<int:pk>/unduh/",
        views.material_download_view,
        name="download",
    ),
    path("verifikasi/", views.material_review_list_view, name="review_list"),
    path(
        "verifikasi/<int:pk>/",
        views.material_review_detail_view,
        name="review_detail",
    ),
    path(
        "verifikasi/<int:pk>/putuskan/",
        views.material_verify_view,
        name="verify",
    ),
    path(
        "penghapusan/<int:pk>/putuskan/",
        views.deletion_review_view,
        name="deletion_review",
    ),
]
