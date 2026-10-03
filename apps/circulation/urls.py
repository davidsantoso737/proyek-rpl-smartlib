from django.urls import path

from . import views

app_name = "circulation"

urlpatterns = [
    path("keranjang/", views.cart_detail_view, name="cart"),
    path(
        "keranjang/tambah/<int:copy_id>/",
        views.cart_add_view,
        name="cart_add",
    ),
    path(
        "keranjang/hapus/<int:copy_id>/",
        views.cart_remove_view,
        name="cart_remove",
    ),
    path("ajukan/", views.loan_create_view, name="loan_create"),
    path("riwayat/", views.loan_list_view, name="loan_list"),
    path(
        "riwayat/<int:pk>/", views.loan_detail_view, name="loan_detail"
    ),
    path("qr/<int:pk>/", views.pickup_qr_image_view, name="pickup_qr"),
    path("persetujuan/", views.approval_list_view, name="approval_list"),
    path(
        "persetujuan/<int:pk>/",
        views.approval_detail_view,
        name="approval_detail",
    ),
    path(
        "persetujuan/<int:pk>/setujui/",
        views.approval_approve_view,
        name="approval_approve",
    ),
    path(
        "persetujuan/<int:pk>/tolak/",
        views.approval_reject_view,
        name="approval_reject",
    ),
    path("penyerahan/", views.handover_view, name="handover"),
    path(
        "penyerahan/konfirmasi/",
        views.handover_confirm_view,
        name="handover_confirm",
    ),
    path("pengembalian/", views.return_view, name="return"),
    path(
        "pengembalian/konfirmasi/",
        views.return_confirm_view,
        name="return_confirm",
    ),
    path(
        "pengembalian/riwayat/",
        views.return_history_view,
        name="return_history",
    ),
    path(
        "identitas/<int:pk>/",
        views.copy_identity_qr_view,
        name="copy_identity_qr",
    ),
    path(
        "ebook/minta/<int:pk>/",
        views.ebook_request_view,
        name="ebook_request",
    ),
    path("ebook/saya/", views.my_ebooks_view, name="my_ebooks"),
    path(
        "ebook/baca/<int:pk>/",
        views.ebook_reader_view,
        name="ebook_reader",
    ),
    path(
        "ebook/file/<int:pk>/",
        views.ebook_file_view,
        name="ebook_file",
    ),
    path(
        "ebook/persetujuan/",
        views.ebook_approval_list_view,
        name="ebook_approval_list",
    ),
    path(
        "ebook/persetujuan/<int:pk>/",
        views.ebook_approval_detail_view,
        name="ebook_approval_detail",
    ),
    path(
        "ebook/persetujuan/<int:pk>/setujui/",
        views.ebook_approve_view,
        name="ebook_approve",
    ),
    path(
        "ebook/persetujuan/<int:pk>/tolak/",
        views.ebook_reject_view,
        name="ebook_reject",
    ),
]
