"""Mesin laporan administratif (FR-90 – FR-95, BR-79 – BR-80, NFR-21).

Hanya menyajikan data yang tersimpan (tanpa metrik baru): setiap jenis
laporan mendefinisikan kolom + builder baris dari queryset yang
difilter. Tanpa model sendiri (tanpa migration).
"""

from datetime import datetime, time, timedelta

from django.db.models import Count, Q
from django.utils import timezone

from apps.catalog.models import Book, Category
from apps.circulation.models import EbookLoan, Loan, LoanItem
from apps.circulation.services import STATUS_TERSEDIA

MAX_ROWS = 2000


def _fmt(dt):
    if not dt:
        return "-"
    local = timezone.localtime(dt) if timezone.is_aware(dt) else dt
    return local.strftime("%d %b %Y %H:%M")


def _fmt_date(dt):
    if not dt:
        return "-"
    local = timezone.localtime(dt) if timezone.is_aware(dt) else dt
    return local.strftime("%d %b %Y")


def _filter_local_dates(qs, field, filters):
    """Filter rentang tanggal lokal tanpa CONVERT_TZ MySQL.

    Batas hari dihitung di Python dari TIME_ZONE aktif, lalu dibandingkan
    sebagai instant UTC. Ini tetap benar meski tabel zona waktu MySQL
    belum dimuat.
    """
    tz = timezone.get_current_timezone()
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    if date_from:
        start = timezone.make_aware(
            datetime.combine(date_from, time.min), tz
        )
        qs = qs.filter(**{f"{field}__gte": start})
    if date_to:
        end = timezone.make_aware(
            datetime.combine(date_to + timedelta(days=1), time.min), tz
        )
        qs = qs.filter(**{f"{field}__lt": end})
    return qs


def _loan_rows(filters):
    qs = (
        Loan.objects.select_related("user")
        .prefetch_related("items__book_copy__book")
        .order_by("-requested_at")
    )
    if filters.get("status"):
        qs = qs.filter(status=filters["status"])
    qs = _filter_local_dates(qs, "requested_at", filters)
    if filters.get("q"):
        q = filters["q"]
        qs = qs.filter(
            Q(user__username__icontains=q) | Q(user__full_name__icontains=q)
        )
    qs = qs[:MAX_ROWS]
    return [
        [
            loan.pk,
            loan.user.username,
            loan.user.get_role_display(),
            loan.items.count(),
            loan.status,
            _fmt(loan.requested_at),
            _fmt(loan.borrowed_at),
            _fmt(loan.due_at),
            _fmt(loan.completed_at),
        ]
        for loan in qs
    ]


def _return_rows(filters):
    qs = (
        LoanItem.objects.filter(returned_at__isnull=False)
        .select_related("loan__user", "book_copy__book")
        .order_by("-returned_at")
    )
    qs = _filter_local_dates(qs, "returned_at", filters)
    if filters.get("q"):
        q = filters["q"]
        qs = qs.filter(
            Q(book_copy__copy_code__icontains=q)
            | Q(loan__user__username__icontains=q)
        )
    qs = qs[:MAX_ROWS]
    rows = [
        [
            _fmt(item.returned_at),
            item.book_copy.copy_code,
            item.book_copy.book.title,
            item.loan.user.username,
            item.late_day,
            str(item.fine_amount),
            item.fine_status,
            item.return_status,
        ]
        for item in qs
    ]
    return rows


def _collection_rows(filters):
    qs = Book.objects.select_related("category").order_by("title")
    if filters.get("category"):
        qs = qs.filter(category_id=filters["category"])
    if filters.get("q"):
        qs = qs.filter(title__icontains=filters["q"])
    books = list(qs[:MAX_ROWS])
    ids = [book.pk for book in books]
    from apps.catalog.models import BookCopy

    counts = {
        row["book"]: (row["total"], row["available"])
        for row in BookCopy.objects.filter(book_id__in=ids)
        .values("book")
        .annotate(
            total=Count("pk"),
            available=Count(
                "pk", filter=Q(status=STATUS_TERSEDIA)
            ),
        )
    }
    return [
        [
            book.title,
            book.isbn,
            book.category.name,
            counts.get(book.pk, (0, 0))[0],
            counts.get(book.pk, (0, 0))[1],
            "Ya" if book.is_active else "Tidak",
        ]
        for book in books
    ]


def _member_rows(filters):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    qs = User.objects.order_by("role", "username")
    if filters.get("role"):
        qs = qs.filter(role=filters["role"])
    if filters.get("active") in ("yes", "no"):
        qs = qs.filter(is_active=(filters["active"] == "yes"))
    if filters.get("q"):
        q = filters["q"]
        qs = qs.filter(
            Q(username__icontains=q) | Q(full_name__icontains=q)
        )
    return [
        [
            user.username,
            user.full_name,
            user.get_role_display(),
            user.nis or user.nip or "-",
            "Aktif" if user.is_active else "Nonaktif",
        ]
        for user in qs[:MAX_ROWS]
    ]


def _ebook_loan_rows(filters):
    qs = (
        EbookLoan.objects.select_related("user", "ebook")
        .order_by("-requested_at")
    )
    if filters.get("status"):
        qs = qs.filter(status=filters["status"])
    qs = _filter_local_dates(qs, "requested_at", filters)
    if filters.get("q"):
        q = filters["q"]
        qs = qs.filter(
            Q(user__username__icontains=q) | Q(ebook__title__icontains=q)
        )
    return [
        [
            loan.pk,
            loan.user.username,
            loan.ebook.title,
            loan.status,
            _fmt(loan.started_at),
            _fmt(loan.expires_at),
        ]
        for loan in qs[:MAX_ROWS]
    ]


REPORTS = {
    "peminjaman": {
        "title": "Laporan Peminjaman Buku Fisik",
        "columns": [
            "ID", "Peminjam", "Role", "Item", "Status",
            "Diajukan", "Diserahkan", "Jatuh tempo", "Selesai",
        ],
        "statuses": [
            "PENDING_APPROVAL", "APPROVED", "ACTIVE",
            "COMPLETED", "REJECTED",
        ],
        "has_q": True,
        "has_dates": True,
        "build": _loan_rows,
    },
    "pengembalian": {
        "title": "Laporan Pengembalian & Denda",
        "columns": [
            "Kembali", "Kode", "Judul", "Peminjam",
            "Terlambat (hari)", "Denda (Rp)", "Status denda", "Status",
        ],
        "statuses": [],
        "has_q": True,
        "has_dates": True,
        "build": _return_rows,
    },
    "koleksi": {
        "title": "Laporan Koleksi Buku Fisik",
        "columns": [
            "Judul", "ISBN", "Kategori", "Eksemplar",
            "Tersedia", "Aktif",
        ],
        "statuses": [],
        "has_q": True,
        "has_dates": False,
        "has_category": True,
        "build": _collection_rows,
    },
    "anggota": {
        "title": "Laporan Anggota",
        "columns": ["Username", "Nama", "Role", "NIS/NIP", "Status"],
        "statuses": [],
        "has_q": True,
        "has_dates": False,
        "has_role": True,
        "has_active": True,
        "build": _member_rows,
    },
    "ebook": {
        "title": "Laporan Peminjaman E-Book",
        "columns": [
            "ID", "Peminjam", "Judul", "Status", "Mulai", "Kedaluarsa",
        ],
        "statuses": ["PENDING_APPROVAL", "APPROVED", "REJECTED"],
        "has_q": True,
        "has_dates": True,
        "build": _ebook_loan_rows,
    },
}


def category_choices():
    return [(str(cat.pk), cat.name) for cat in Category.objects.order_by("name")]
