"""Helper paginasi bersama untuk view daftar."""

from django.core.paginator import Paginator


def paginate_queryset(request, queryset, page_size):
    """Kembalikan page object dan querystring tanpa parameter page."""
    paginator = Paginator(queryset, page_size)
    page_obj = paginator.get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return page_obj, params.urlencode()
