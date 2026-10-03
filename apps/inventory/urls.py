from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("", views.opname_list_view, name="opname_list"),
    path("mulai/", views.opname_start_view, name="opname_start"),
    path("<int:pk>/", views.opname_detail_view, name="opname_detail"),
    path(
        "<int:pk>/selesaikan/",
        views.opname_complete_view,
        name="opname_complete",
    ),
]
