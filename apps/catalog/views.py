from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Count, Prefetch, Q
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.authorization import active_login_required, role_required

from .availability import STATUS_TERSEDIA, count_available_copies
from .forms import (
    EBOOK_EXTENSIONS,
    EBOOK_MAX_SIZE,
    AuthorForm,
    BookCopyForm,
    BookForm,
    CategoryForm,
    EbookForm,
    generate_copy_code,
)
from .models import Author, Book, BookAuthor, BookCopy, Category, Ebook, EbookAuthor

User = get_user_model()

PAGE_SIZE = 12
COPY_PAGE_SIZE = 4


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
    page_obj = Paginator(book.copies.order_by("copy_code"), COPY_PAGE_SIZE).get_page(
        request.GET.get("page")
    )
    params = request.GET.copy()
    params.pop("page", None)
    return render(
        request,
        "catalog/book_detail.html",
        {
            "book": book,
            "authors": [rel.author for rel in book.bookauthor_set.all()],
            "page_obj": page_obj,
            "available": count_available_copies(book),
            "total": book.total_copies,
            "querystring": params.urlencode(),
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
    from apps.circulation.services import get_active_ebook_loan

    ebook = get_object_or_404(_ebook_queryset(), pk=pk)
    loan_info = None
    if request.user.is_authenticated and request.user.is_active:
        from apps.circulation.models import EbookLoan
        from apps.circulation.services import EBOOK_PENDING

        active = get_active_ebook_loan(request.user, ebook)
        pending = (
            EbookLoan.objects.filter(
                user=request.user, ebook=ebook, status=EBOOK_PENDING
            ).first()
            if active is None
            else None
        )
        loan_info = {"active": active, "pending": pending}
    return render(
        request,
        "catalog/ebook_detail.html",
        {
            "ebook": ebook,
            "authors": [
                rel.author for rel in ebook.ebookauthor_set.all()
            ],
            "file_size_display": _human_file_size(ebook.file_size),
            "loan_info": loan_info,
        },
    )


# --- Kelola koleksi (FR-82 – FR-85, BR-10 – BR-13). Hanya Pustakawan.
# Tanpa verifikasi TU (BR-09 di luar scope sistem).
# Tanpa hapus buku/eksemplar: riwayat dilindungi PROTECT + kebutuhan
# pelaporan; nonaktifkan buku bila perlu. ---


@role_required(User.Role.PUSTAKAWAN)
def manage_book_list_view(request):
    """Daftar buku untuk pengelolaan + pencarian (FR-82)."""
    q = (request.GET.get("q") or "").strip()
    books = Book.objects.select_related("category").order_by("title")
    if q:
        books = books.filter(
            Q(title__icontains=q) | Q(isbn__icontains=q)
        )
    paginator = Paginator(books, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(
        request,
        "catalog/manage_book_list.html",
        {
            "page_obj": page_obj,
            "q": q,
            "querystring": params.urlencode(),
            "result_count": paginator.count,
        },
    )


@role_required(User.Role.PUSTAKAWAN)
def manage_book_form_view(request, pk=None):
    """Tambah/ubah buku beserta penulisnya (FR-82 – FR-84)."""
    book = get_object_or_404(Book, pk=pk) if pk else None
    if request.method == "POST":
        form = BookForm(request.POST, instance=book)
        if form.is_valid():
            saved = form.save()
            messages.success(
                request, f"Buku {saved.title} berhasil disimpan."
            )
            return redirect("catalog:manage_book_copies", pk=saved.pk)
    else:
        form = BookForm(instance=book)
    return render(
        request,
        "catalog/manage_book_form.html",
        {"form": form, "book": book},
    )


@role_required(User.Role.PUSTAKAWAN)
@require_POST
def manage_book_toggle_view(request, pk):
    """Aktif/nonaktifkan buku di katalog (bukan hapus)."""
    book = get_object_or_404(Book, pk=pk)
    book.is_active = not book.is_active
    book.save(update_fields=["is_active", "updated_at"])
    messages.success(
        request,
        f"Buku {book.title} kini "
        f"{'aktif' if book.is_active else 'nonaktif'}.",
    )
    return redirect("catalog:manage_books")


@role_required(User.Role.PUSTAKAWAN)
def manage_book_copies_view(request, pk):
    """Eksemplar per judul + statusnya (FR-85, FR-86)."""
    book = get_object_or_404(
        Book.objects.select_related("category"), pk=pk
    )
    copies = book.copies.order_by("copy_code")
    return render(
        request,
        "catalog/manage_copies.html",
        {"book": book, "copies": copies},
    )


def _copy_bound_to_open_loan(copy):
    from apps.circulation.models import LoanItem
    from apps.circulation.services import OPEN_LOAN_STATUSES

    return LoanItem.objects.filter(
        book_copy=copy,
        loan__status__in=OPEN_LOAN_STATUSES,
    ).exists()


def _create_copies_bulk(request, form, book):
    """Buat 1..N eksemplar sekaligus; semua-atau-tidak-sama-sekali."""
    from django.db import transaction

    cleaned = form.cleaned_data
    jumlah = cleaned.get("jumlah") or 1
    manual = (cleaned.get("copy_code") or "").strip()
    try:
        with transaction.atomic():
            created = []
            for _ in range(jumlah):
                code = (
                    manual
                    if jumlah == 1 and manual
                    else generate_copy_code(book)
                )
                created.append(
                    BookCopy.objects.create(
                        book=book,
                        copy_code=code,
                        status=cleaned["status"],
                        acquired_at=cleaned["acquired_at"],
                        condition=cleaned["condition"],
                        location=cleaned["location"],
                    )
                )
    except IntegrityError:
        form.add_error(
            "copy_code",
            "Kode eksemplar sudah dipakai. Gunakan kode lain.",
        )
        return render(
            request,
            "catalog/manage_copy_form.html",
            {"form": form, "book": book, "copy": None},
        )
    if jumlah == 1:
        messages.success(
            request,
            f"Eksemplar {created[0].copy_code} berhasil disimpan.",
        )
    else:
        messages.success(
            request,
            f"{len(created)} eksemplar berhasil ditambahkan "
            f"({created[0].copy_code} s/d {created[-1].copy_code}).",
        )
    return redirect("catalog:manage_book_copies", pk=book.pk)


@role_required(User.Role.PUSTAKAWAN)
def manage_copy_form_view(request, book_pk=None, pk=None):
    """Tambah/ubah eksemplar + kode unik (FR-85, BR-81 – BR-84).

    Mode tambah mendukung bulk: jumlah > 1 membuat N eksemplar
    sekaligus dengan kode unik per eksemplar.
    """
    if pk:
        copy = get_object_or_404(
            BookCopy.objects.select_related("book"), pk=pk
        )
        book = copy.book
    else:
        copy = None
        book = get_object_or_404(Book, pk=book_pk)
    original_status = copy.status if copy else None
    if request.method == "POST":
        form = BookCopyForm(request.POST, instance=copy)
        if form.is_valid():
            if copy is None:
                return _create_copies_bulk(request, form, book)
            new_copy = form.save(commit=False)
            new_copy.book = book
            if not (form.cleaned_data.get("copy_code") or "").strip():
                new_copy.copy_code = generate_copy_code(book)
            if (
                new_copy.status != original_status
                and _copy_bound_to_open_loan(copy)
            ):
                form.add_error(
                    "status",
                    "Eksemplar terikat peminjaman aktif sehingga "
                    "statusnya tidak dapat diubah.",
                )
            else:
                try:
                    new_copy.save()
                except IntegrityError:
                    form.add_error(
                        "copy_code",
                        "Kode eksemplar sudah dipakai. Gunakan kode lain.",
                    )
                else:
                    messages.success(
                        request,
                        f"Eksemplar {new_copy.copy_code} berhasil disimpan.",
                    )
                    return redirect(
                        "catalog:manage_book_copies", pk=book.pk
                    )
    else:
        form = BookCopyForm(instance=copy)
    return render(
        request,
        "catalog/manage_copy_form.html",
        {"form": form, "book": book, "copy": copy},
    )


@role_required(User.Role.PUSTAKAWAN)
def manage_categories_view(request):
    """Daftar + tambah kategori (FR-84)."""
    if request.method == "POST":
        form = CategoryForm(request.POST)
        if form.is_valid():
            category = form.save()
            messages.success(
                request, f"Kategori {category.name} ditambahkan."
            )
            return redirect("catalog:manage_categories")
    else:
        form = CategoryForm()
    categories = Category.objects.order_by("name")
    return render(
        request,
        "catalog/manage_categories.html",
        {"categories": categories, "form": form},
    )


@role_required(User.Role.PUSTAKAWAN)
@require_POST
def manage_category_delete_view(request, pk):
    """Hapus kategori bila tidak dipakai (PROTECT dijaga)."""
    category = get_object_or_404(Category, pk=pk)
    try:
        category.delete()
    except (ProtectedError, IntegrityError):
        messages.error(
            request,
            f"Kategori {category.name} dipakai koleksi sehingga "
            "tidak dapat dihapus.",
        )
    else:
        messages.success(
            request, f"Kategori {category.name} dihapus."
        )
    return redirect("catalog:manage_categories")


@role_required(User.Role.PUSTAKAWAN)
def manage_authors_view(request):
    """Daftar + tambah penulis (FR-84)."""
    if request.method == "POST":
        form = AuthorForm(request.POST)
        if form.is_valid():
            author = form.save()
            messages.success(
                request, f"Penulis {author.name} ditambahkan."
            )
            return redirect("catalog:manage_authors")
    else:
        form = AuthorForm()
    authors = Author.objects.order_by("name")
    return render(
        request,
        "catalog/manage_authors.html",
        {"authors": authors, "form": form},
    )


@role_required(User.Role.PUSTAKAWAN)
@require_POST
def manage_author_delete_view(request, pk):
    """Hapus penulis bila tidak dipakai.

    BookAuthor/EbookAuthor memakai CASCADE sehingga pengecekan eksplisit
    diperlukan agar hapus tidak diam-diam melepas kepenulisan (NFR-16).
    """
    from .models import EbookAuthor

    author = get_object_or_404(Author, pk=pk)
    if BookAuthor.objects.filter(author=author).exists() or (
        EbookAuthor.objects.filter(author=author).exists()
    ):
        messages.error(
            request,
            f"Penulis {author.name} dipakai koleksi sehingga "
            "tidak dapat dihapus.",
        )
        return redirect("catalog:manage_authors")
    try:
        author.delete()
    except (ProtectedError, IntegrityError):
        messages.error(
            request,
            f"Penulis {author.name} tidak dapat dihapus.",
        )
    else:
        messages.success(request, f"Penulis {author.name} dihapus.")
    return redirect("catalog:manage_authors")


def _ebook_storage_root():
    from pathlib import Path

    from django.conf import settings as dj_settings

    root = Path(dj_settings.EBOOK_STORAGE_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _validate_ebook_upload(uploaded):
    """Validasi tipe/ukuran/integritas file e-book (NFR-27)."""
    if uploaded is None or uploaded.size == 0:
        raise ValueError("File e-book tidak boleh kosong.")
    from pathlib import Path

    ext = Path(uploaded.name).suffix.lower().lstrip(".")
    if ext not in EBOOK_EXTENSIONS:
        raise ValueError(
            f"Tipe file .{ext or '?'} tidak diizinkan. "
            "Gunakan: pdf, epub."
        )
    if uploaded.size > EBOOK_MAX_SIZE:
        raise ValueError("Ukuran file melebihi 50 MB.")
    return ext


def _store_ebook_upload(uploaded, ext):
    import uuid

    name = f"{uuid.uuid4().hex}.{ext}"
    target = _ebook_storage_root() / name
    try:
        with open(target, "wb") as dest:
            for chunk in uploaded.chunks():
                dest.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise ValueError("File gagal diproses dan tidak disimpan.")
    return name


@role_required(User.Role.PUSTAKAWAN)
def manage_ebook_list_view(request):
    """Daftar e-book untuk pengelolaan (FR-82)."""
    q = (request.GET.get("q") or "").strip()
    ebooks = Ebook.objects.select_related("category").order_by("title")
    if q:
        ebooks = ebooks.filter(
            Q(title__icontains=q) | Q(eisbn__icontains=q)
        )
    paginator = Paginator(ebooks, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(
        request,
        "catalog/manage_ebook_list.html",
        {
            "page_obj": page_obj,
            "q": q,
            "querystring": params.urlencode(),
            "result_count": paginator.count,
        },
    )


@role_required(User.Role.PUSTAKAWAN)
def manage_ebook_form_view(request, pk=None):
    """Tambah/ubah e-book + file privat (FR-82 – FR-84, NFR-26 – NFR-28).

    Tanpa hapus: riwayat EbookLoan dilindungi PROTECT; nonaktifkan bila
    perlu. File baru menggantikan lama hanya setelah baris tersimpan.
    """
    ebook = get_object_or_404(Ebook, pk=pk) if pk else None
    if request.method == "POST":
        form = EbookForm(request.POST, request.FILES, instance=ebook)
        uploaded = request.FILES.get("file")
        if ebook is None and uploaded is None:
            form.add_error("file", "File e-book wajib diunggah.")
        elif uploaded is not None:
            try:
                _validate_ebook_upload(uploaded)
            except ValueError as exc:
                form.add_error("file", str(exc))
        if form.is_valid():
            if ebook is None:
                ext = _validate_ebook_upload(uploaded)
                try:
                    stored = _store_ebook_upload(uploaded, ext)
                except ValueError as exc:
                    form.add_error("file", str(exc))
                    return render(
                        request,
                        "catalog/manage_ebook_form.html",
                        {"form": form, "ebook": ebook},
                    )
                try:
                    ebook = Ebook(
                        category=form.cleaned_data["category"],
                        eisbn=form.cleaned_data["eisbn"],
                        title=form.cleaned_data["title"],
                        description=(
                            form.cleaned_data["description"] or None
                        ),
                        publisher=form.cleaned_data["publisher"] or None,
                        publication_year=form.cleaned_data[
                            "publication_year"
                        ],
                        edition=form.cleaned_data["edition"] or None,
                        cover_url=form.cleaned_data["cover_url"] or None,
                        file_path=stored,
                        file_name=uploaded.name,
                        file_type=ext,
                        file_size=uploaded.size,
                        version=form.cleaned_data["version"],
                        is_active=form.cleaned_data["is_active"],
                    )
                    ebook.save()
                    form.instance = ebook
                    form.save()
                except Exception:
                    (_ebook_storage_root() / stored).unlink(
                        missing_ok=True
                    )
                    raise
                messages.success(
                    request, f"E-book {ebook.title} berhasil ditambahkan."
                )
                return redirect("catalog:manage_ebooks")
            old_path = ebook.file_path
            saved = form.save()
            if uploaded is not None:
                ext = _validate_ebook_upload(uploaded)
                try:
                    stored = _store_ebook_upload(uploaded, ext)
                except ValueError as exc:
                    form.add_error("file", str(exc))
                    return render(
                        request,
                        "catalog/manage_ebook_form.html",
                        {"form": form, "ebook": saved},
                    )
                saved.file_path = stored
                saved.file_name = uploaded.name
                saved.file_type = ext
                saved.file_size = uploaded.size
                try:
                    saved.save(
                        update_fields=[
                            "file_path", "file_name", "file_type",
                            "file_size", "updated_at",
                        ]
                    )
                except Exception:
                    (_ebook_storage_root() / stored).unlink(
                        missing_ok=True
                    )
                    raise
                (_ebook_storage_root() / old_path).unlink(missing_ok=True)
            messages.success(
                request, f"E-book {saved.title} berhasil disimpan."
            )
            return redirect("catalog:manage_ebooks")
    else:
        form = EbookForm(instance=ebook)
    return render(
        request,
        "catalog/manage_ebook_form.html",
        {"form": form, "ebook": ebook},
    )


@role_required(User.Role.PUSTAKAWAN)
@require_POST
def manage_ebook_toggle_view(request, pk):
    """Aktif/nonaktifkan e-book di katalog (bukan hapus)."""
    ebook = get_object_or_404(Ebook, pk=pk)
    ebook.is_active = not ebook.is_active
    ebook.save(update_fields=["is_active", "updated_at"])
    messages.success(
        request,
        f"E-book {ebook.title} kini "
        f"{'aktif' if ebook.is_active else 'nonaktif'}.",
    )
    return redirect("catalog:manage_ebooks")
