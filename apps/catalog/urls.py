from django.urls import path

from . import views

app_name = "catalog"

urlpatterns = [
    path("", views.catalog_list_view, name="catalog"),
    path("buku/<int:pk>/", views.book_detail_view, name="book_detail"),
    path("ebook/<int:pk>/", views.ebook_detail_view, name="ebook_detail"),
]
