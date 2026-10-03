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
]
