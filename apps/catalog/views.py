from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, render

from apps.accounts.authorization import active_login_required

from .availability import STATUS_TERSEDIA, count_available_copies
from .models import Book, BookAuthor, Category, Ebook, EbookAuthor

PAGE_SIZE = 12


def _book_queryset():
    return (
        Book.objects.filter(is_active=True)
        .select_related("category")
        .prefetch_related(
            Prefetch(
                "bookauthor_set",
                queryset=BookAuthor.objects.select_related("author"),
            )
        )
        .annotate(
            total_copies=Count("copies", distinct=True),
            available_copies=Count(
                "copies",
                filter=Q(copies__status=STATUS_TERSEDIA),
                distinct=True,
            ),
        )
        .order_by("title")
    )


def _ebook_queryset():
    return (
        Ebook.objects.filter(is_active=True)
        .select_related("category")
        .prefetch_related(
            Prefetch(
                "ebookauthor_set",
                queryset=EbookAuthor.objects.select_related("author"),
            )
        )
        .order_by("title")
    )


def _apply_search(books, ebooks, q):
    """Pencarian minimal FR-09: judul, penulis, kategori, penerbit, ISBN."""
    if not q:
        return books, ebooks
    books = books.filter(
        Q(title__icontains=q)
        | Q(bookauthor__author__name__icontains=q)
        | Q(category__name__icontains=q)
        | Q(publisher__icontains=q)
        | Q(isbn__icontains=q)
    ).distinct()
    ebooks = ebooks.filter(
        Q(title__icontains=q)
        | Q(ebookauthor__author__name__icontains=q)
        | Q(category__name__icontains=q)
        | Q(publisher__icontains=q)
        | Q(eisbn__icontains=q)
    ).distinct()
    return books, ebooks


def _author_names(relations, attr):
    return [getattr(rel, attr).name for rel in relations.all()]


@active_login_required
def catalog_list_view(request):
    """Daftar katalog buku fisik + e-book (FR-08 – FR-12)."""
    q = (request.GET.get("q") or "").strip()
    category_id = request.GET.get("category") or ""
    kind = request.GET.get("type") or ""

    books = _book_queryset()
    ebooks = _ebook_queryset()

    if category_id.isdigit():
        books = books.filter(category_id=int(category_id))
        ebooks = ebooks.filter(category_id=int(category_id))
    else:
        category_id = ""

    books, ebooks = _apply_search(books, ebooks, q)

    items = []
    if kind in ("", "book"):
        items.extend(
            {
                "kind": "book",
                "obj": book,
                "title": book.title,
                "authors": _author_names(
                    book.bookauthor_set, "author"
                ),
                "available": count_available_copies(book),
                "total": book.total_copies,
            }
            for book in books
        )
    if kind in ("", "ebook"):
        items.extend(
            {
                "kind": "ebook",
                "obj": ebook,
                "title": ebook.title,
                "authors": _author_names(
                    ebook.ebookauthor_set, "author"
                ),
            }
            for ebook in ebooks
        )
    if kind not in ("", "book", "ebook"):
        kind = ""
    items.sort(key=lambda item: item["title"].lower())

    paginator = Paginator(items, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))

    params = request.GET.copy()
    params.pop("page", None)
    querystring = params.urlencode()

    return render(
        request,
        "catalog/catalog_list.html",
        {
            "page_obj": page_obj,
            "q": q,
            "category_id": category_id,
            "kind": kind,
            "categories": Category.objects.order_by("name"),
            "querystring": querystring,
            "result_count": paginator.count,
        },
    )


@active_login_required
def book_detail_view(request, pk):
    """Detail koleksi fisik + ketersediaan aktual eksemplar (FR-10, FR-11)."""
    book = get_object_or_404(_book_queryset(), pk=pk)
    copies = book.copies.order_by("copy_code")
    return render(
        request,
        "catalog/book_detail.html",
        {
            "book": book,
            "authors": [rel.author for rel in book.bookauthor_set.all()],
            "copies": copies,
            "available": count_available_copies(book),
            "total": book.total_copies,
        },
    )


def _human_file_size(size):
    try:
        size = int(size)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


@active_login_required
def ebook_detail_view(request, pk):
    """Detail e-book (FR-10, FR-12: dibedakan dari koleksi fisik)."""
    ebook = get_object_or_404(_ebook_queryset(), pk=pk)
    return render(
        request,
        "catalog/ebook_detail.html",
        {
            "ebook": ebook,
            "authors": [
                rel.author for rel in ebook.ebookauthor_set.all()
            ],
            "file_size_display": _human_file_size(ebook.file_size),
        },
    )
