from django.urls import path

from . import views

app_name = "catalog"

urlpatterns = [
    path("", views.catalog_list_view, name="catalog"),
    path("buku/<int:pk>/", views.book_detail_view, name="book_detail"),
    path("ebook/<int:pk>/", views.ebook_detail_view, name="ebook_detail"),
    path("kelola/", views.manage_book_list_view, name="manage_books"),
    path(
        "kelola/buku/tambah/",
        views.manage_book_form_view,
        name="manage_book_create",
    ),
    path(
        "kelola/buku/<int:pk>/ubah/",
        views.manage_book_form_view,
        name="manage_book_update",
    ),
    path(
        "kelola/buku/<int:pk>/status/",
        views.manage_book_toggle_view,
        name="manage_book_toggle",
    ),
    path(
        "kelola/buku/<int:pk>/eksemplar/",
        views.manage_book_copies_view,
        name="manage_book_copies",
    ),
    path(
        "kelola/buku/<int:book_pk>/eksemplar/tambah/",
        views.manage_copy_form_view,
        name="manage_copy_create",
    ),
    path(
        "kelola/eksemplar/<int:pk>/ubah/",
        views.manage_copy_form_view,
        name="manage_copy_update",
    ),
    path(
        "kelola/kategori/",
        views.manage_categories_view,
        name="manage_categories",
    ),
    path(
        "kelola/kategori/<int:pk>/hapus/",
        views.manage_category_delete_view,
        name="manage_category_delete",
    ),
    path(
        "kelola/penulis/",
        views.manage_authors_view,
        name="manage_authors",
    ),
    path(
        "kelola/penulis/<int:pk>/hapus/",
        views.manage_author_delete_view,
        name="manage_author_delete",
    ),
]
