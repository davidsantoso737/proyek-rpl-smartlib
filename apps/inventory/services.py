"""Layanan stock opname (FR-82 – FR-89, BR-74 – BR-76, NFR-16).

Keputusan teknis: cakupan opname adalah SELURUH koleksi eksemplar
(model existing tidak memiliki kolom scope). Perbandingan expected
vs found dihitung dari seluruh BookCopy; tanpa koreksi/penghapusan
otomatis (out of scope).
"""

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import BookCopy

from .models import StockOpname, StockOpnameItem

OPNAME_RUNNING = "BERJALAN"
OPNAME_DONE = "SELESAI"
RESULT_FOUND = "DITEMUKAN"


class OpnameError(Exception):
    """Kegagalan alur opname (pesan siap tampil)."""


def start_opname(user, notes=""):
    """Mulai sesi opname; hanya satu sesi BERJALAN dalam satu waktu."""
    if StockOpname.objects.filter(status=OPNAME_RUNNING).exists():
        raise OpnameError(
            "Masih ada opname yang berjalan. Selesaikan dahulu "
            "sebelum memulai yang baru."
        )
    return StockOpname.objects.create(
        started_by=user,
        started_at=timezone.now(),
        status=OPNAME_RUNNING,
        notes=(notes or "").strip() or None,
    )


def scan_copy(opname_id, code):
    """Pindai QR Identitas -> tandai DITEMUKAN (FR-87, BR-75)."""
    code = (code or "").strip()
    if not code:
        raise OpnameError("Pindai QR Identitas atau ketik kode eksemplar.")
    with transaction.atomic():
        try:
            opname = StockOpname.objects.select_for_update().get(
                pk=opname_id
            )
        except (StockOpname.DoesNotExist, TypeError, ValueError):
            raise OpnameError("Sesi opname tidak ditemukan.")
        if opname.status != OPNAME_RUNNING:
            raise OpnameError("Sesi opname ini sudah selesai.")
        try:
            copy = BookCopy.objects.select_related("book").get(
                copy_code=code
            )
        except BookCopy.DoesNotExist:
            raise OpnameError(f"Kode {code} tidak terdaftar pada sistem.")
        if StockOpnameItem.objects.filter(
            stock_opname=opname, book_copy=copy
        ).exists():
            raise OpnameError(
                f"Eksemplar {copy.copy_code} sudah dipindai pada sesi ini."
            )
        return StockOpnameItem.objects.create(
            stock_opname=opname,
            book_copy=copy,
            scanned_at=timezone.now(),
            result=RESULT_FOUND,
        )


def summarize(opname):
    """Bandingkan expected vs found (FR-88)."""
    found_ids = set(
        StockOpnameItem.objects.filter(stock_opname=opname).values_list(
            "book_copy_id", flat=True
        )
    )
    expected = list(
        BookCopy.objects.select_related("book").order_by("copy_code")
    )
    found = [copy for copy in expected if copy.pk in found_ids]
    missing = [copy for copy in expected if copy.pk not in found_ids]
    return {
        "expected_count": len(expected),
        "found": found,
        "missing": missing,
    }


def complete_opname(opname_id):
    """Selesaikan sesi; hasil hanya perbandingan, tanpa koreksi otomatis."""
    with transaction.atomic():
        try:
            opname = StockOpname.objects.select_for_update().get(
                pk=opname_id
            )
        except (StockOpname.DoesNotExist, TypeError, ValueError):
            raise OpnameError("Sesi opname tidak ditemukan.")
        if opname.status != OPNAME_RUNNING:
            raise OpnameError("Sesi opname ini sudah selesai.")
        opname.status = OPNAME_DONE
        opname.completed_at = timezone.now()
        opname.save(update_fields=["status", "completed_at"])
    return opname
