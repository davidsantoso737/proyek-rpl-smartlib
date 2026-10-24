"""Statistik koleksi terpusat (read-only).

Satu sumber definisi metrik untuk dashboard pustakawan dan AI Assistant:
judul (``Book``), e-book (``Ebook``), eksemplar fisik (``BookCopy``), dan
peminjaman aktif (``Loan``). Semua angka memakai agregasi database dan
daftar dibatasi ``detail_limit`` agar tidak memuat seluruh tabel.
"""

from django.db.models import Count, Q
from django.utils import timezone

from apps.circulation.models import Loan, LoanItem
from apps.circulation.services import LOAN_ACTIVE, STATUS_DIPINJAM

from .availability import STATUS_TERSEDIA
from .models import Book, BookCopy, Category, Ebook

STATUS_RUSAK = "RUSAK"
STATUS_HILANG = "HILANG"

# Urutan tampilan status; status lain (bila ada) tetap dilaporkan di akhir.
COPY_STATUS_ORDER = (
    (STATUS_TERSEDIA, "Tersedia"),
    (STATUS_DIPINJAM, "Dipinjam"),
    (STATUS_RUSAK, "Rusak"),
    (STATUS_HILANG, "Hilang"),
)
COPY_STATUS_LABELS = dict(COPY_STATUS_ORDER)


def _title_row(book):
    return {
        "judul": book.title,
        "kategori": book.category.name,
        "eksemplar": book.total_copies,
        "tersedia": book.available_copies,
        "dipinjam": book.borrowed_copies,
        "rusak": book.damaged_copies,
        "hilang": book.lost_copies,
    }


def _annotated_titles():
    titles = Book.objects.filter(is_active=True).select_related("category")
    return titles.annotate(
        total_copies=Count("copies", distinct=True),
        available_copies=Count(
            "copies",
            filter=Q(copies__status=STATUS_TERSEDIA),
            distinct=True,
        ),
        borrowed_copies=Count(
            "copies",
            filter=Q(copies__status=STATUS_DIPINJAM),
            distinct=True,
        ),
        damaged_copies=Count(
            "copies",
            filter=Q(copies__status=STATUS_RUSAK),
            distinct=True,
        ),
        lost_copies=Count(
            "copies",
            filter=Q(copies__status=STATUS_HILANG),
            distinct=True,
        ),
    )


def _unavailable_reason(row):
    if row.total_copies == 0:
        return "belum ada eksemplar tercatat"
    parts = []
    if row.borrowed_copies:
        parts.append(f"{row.borrowed_copies} sedang dipinjam")
    if row.damaged_copies:
        parts.append(f"{row.damaged_copies} rusak")
    if row.lost_copies:
        parts.append(f"{row.lost_copies} hilang")
    if not parts:
        return "tidak ada eksemplar tersedia"
    return ", ".join(parts)


def _category_breakdown():
    books = {
        row["category__name"]: row["total"]
        for row in Book.objects.filter(is_active=True)
        .values("category__name")
        .annotate(total=Count("pk"))
    }
    ebooks = {
        row["category__name"]: row["total"]
        for row in Ebook.objects.filter(is_active=True)
        .values("category__name")
        .annotate(total=Count("pk"))
    }
    rows = []
    for name in sorted(set(books) | set(ebooks)):
        rows.append(
            {
                "kategori": name,
                "buku": books.get(name, 0),
                "ebook": ebooks.get(name, 0),
            }
        )
    rows.sort(key=lambda row: (-(row["buku"] + row["ebook"]), row["kategori"]))
    return rows


def collection_statistics(detail_limit=5, include_operational=False):
    """Snapshot statistik koleksi. Daftar dibatasi ``detail_limit``.

    ``include_operational`` hanya untuk role yang berwenang melihat detail
    operasional (pustakawan). Identitas peminjam tidak pernah dimasukkan.
    """
    detail_limit = max(1, min(int(detail_limit), 20))

    status_rows = list(
        BookCopy.objects.values("status").annotate(total=Count("pk"))
    )
    status_counts = {row["status"]: row["total"] for row in status_rows}
    copies_total = sum(status_counts.values())

    titles_total = Book.objects.count()
    active_titles = Book.objects.filter(is_active=True)
    titles_active = active_titles.count()
    ebooks_total = Ebook.objects.count()
    ebooks_active = Ebook.objects.filter(is_active=True).count()

    title_counts = active_titles.aggregate(
        with_available=Count(
            "pk", filter=Q(copies__status=STATUS_TERSEDIA), distinct=True
        ),
        with_borrowed=Count(
            "pk", filter=Q(copies__status=STATUS_DIPINJAM), distinct=True
        ),
        with_damaged=Count(
            "pk", filter=Q(copies__status=STATUS_RUSAK), distinct=True
        ),
        with_lost=Count(
            "pk", filter=Q(copies__status=STATUS_HILANG), distinct=True
        ),
    )
    titles_with_available = title_counts["with_available"] or 0
    titles_without_available = max(titles_active - titles_with_available, 0)

    all_titles = [
        _title_row(book)
        for book in _annotated_titles().order_by("title")[:detail_limit]
    ]
    available_titles = [
        _title_row(book)
        for book in _annotated_titles()
        .filter(available_copies__gt=0)
        .order_by("title")[:detail_limit]
    ]
    unavailable_rows = list(
        _annotated_titles()
        .filter(available_copies=0)
        .order_by("title")[:detail_limit]
    )
    unavailable_titles = []
    for book in unavailable_rows:
        row = _title_row(book)
        row["alasan"] = _unavailable_reason(book)
        unavailable_titles.append(row)

    borrowed_titles = [
        _title_row(book)
        for book in _annotated_titles()
        .filter(borrowed_copies__gt=0)
        .order_by("title")[:detail_limit]
    ]
    damaged_titles = [
        _title_row(book)
        for book in _annotated_titles()
        .filter(damaged_copies__gt=0)
        .order_by("title")[:detail_limit]
    ]
    lost_titles = [
        _title_row(book)
        for book in _annotated_titles()
        .filter(lost_copies__gt=0)
        .order_by("title")[:detail_limit]
    ]
    ebook_rows = [
        {"judul": ebook.title, "kategori": ebook.category.name}
        for ebook in Ebook.objects.filter(is_active=True)
        .select_related("category")
        .order_by("title")[:detail_limit]
    ]

    now = timezone.now()
    overdue_items = LoanItem.objects.filter(
        loan__status=LOAN_ACTIVE,
        returned_at__isnull=True,
        due_at__lt=now,
    ).count()
    category_breakdown = _category_breakdown()

    statistics = {
        "titles_total": titles_total,
        "titles_active": titles_active,
        "titles_with_available": titles_with_available,
        "titles_without_available": titles_without_available,
        "ebooks_total": ebooks_total,
        "ebooks_active": ebooks_active,
        "categories_total": Category.objects.count(),
        "category_breakdown": category_breakdown,
        "copies_total": copies_total,
        "copy_status_counts": status_counts,
        "copies_available": status_counts.get(STATUS_TERSEDIA, 0),
        "copies_borrowed": status_counts.get(STATUS_DIPINJAM, 0),
        "copies_damaged": status_counts.get(STATUS_RUSAK, 0),
        "copies_lost": status_counts.get(STATUS_HILANG, 0),
        "titles_borrowed": title_counts["with_borrowed"] or 0,
        "titles_damaged": title_counts["with_damaged"] or 0,
        "titles_lost": title_counts["with_lost"] or 0,
        "active_loans": Loan.objects.filter(status=LOAN_ACTIVE).count(),
        "overdue_items": overdue_items,
        "all_titles": all_titles,
        "available_titles": available_titles,
        "unavailable_titles": unavailable_titles,
        "borrowed_titles": borrowed_titles,
        "damaged_titles": damaged_titles,
        "lost_titles": lost_titles,
        "ebook_titles": ebook_rows,
    }
    if include_operational:
        statistics["operational_items"] = [
            {
                "judul": item.book_copy.book.title,
                "kode_eksemplar": item.book_copy.copy_code,
                "jatuh_tempo": (
                    item.due_at.isoformat() if item.due_at else None
                ),
            }
            for item in LoanItem.objects.filter(
                loan__status=LOAN_ACTIVE, returned_at__isnull=True
            )
            .select_related("book_copy__book")
            .order_by("due_at")[:detail_limit]
        ]
    return statistics
