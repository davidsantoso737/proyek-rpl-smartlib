"""Layanan pengajuan peminjaman buku fisik (FR-13 – FR-16).

Critical rule: pengajuan BUKAN active loan sampai handover selesai.
Konsekuensinya status eksemplar TIDAK diubah saat pengajuan; konflik
dicegah dengan memeriksa item pada loan yang masih terbuka.
Seluruh pembuatan Loan + LoanItem berjalan dalam satu transaksi
atomik (NFR-13, NFR-16, NFR-19, NFR-23).
"""

from django.db import transaction
from django.utils import timezone

from apps.catalog.availability import STATUS_TERSEDIA
from apps.catalog.models import BookCopy
from apps.core.models import LibrarySetting

from .models import Loan, LoanItem

LOAN_PENDING = "PENDING_APPROVAL"

# Status loan yang masih "membawa" eksemplar sehingga eksemplar di
# dalamnya tidak boleh diminta lagi. Diperluas oleh checkpoint
# approval/handover (mis. APPROVED, ACTIVE) tanpa mengubah service ini.
OPEN_LOAN_STATUSES = (LOAN_PENDING,)

ITEM_RETURN_PENDING = "BELUM_KEMBALI"
FINE_UNPAID = "BELUM_BAYAR"

CART_SESSION_KEY = "loan_cart"


class LoanRequestError(Exception):
    """Kegagalan validasi/bisnis pengajuan (pesan siap tampil, NFR-33)."""


def get_cart_ids(session):
    try:
        return [int(pk) for pk in session.get(CART_SESSION_KEY, [])]
    except (TypeError, ValueError):
        return []


def _save_cart(session, copy_ids):
    session[CART_SESSION_KEY] = list(dict.fromkeys(int(pk) for pk in copy_ids))
    session.modified = True


def _validate_copy_for_cart(copy_id):
    try:
        copy = BookCopy.objects.select_related("book").get(pk=copy_id)
    except (BookCopy.DoesNotExist, TypeError, ValueError):
        raise LoanRequestError("Eksemplar yang dipilih tidak ditemukan.")
    if not copy.book.is_active:
        raise LoanRequestError(
            f"Judul {copy.book.title} sedang tidak aktif."
        )
    if copy.status != STATUS_TERSEDIA:
        raise LoanRequestError(
            f"Eksemplar {copy.copy_code} sedang tidak tersedia."
        )
    if LoanItem.objects.filter(
        book_copy=copy, loan__status__in=OPEN_LOAN_STATUSES
    ).exists():
        raise LoanRequestError(
            f"Eksemplar {copy.copy_code} sudah ada pada pengajuan aktif."
        )
    return copy


def cart_add(session, copy_id):
    copy = _validate_copy_for_cart(copy_id)
    ids = get_cart_ids(session)
    if copy.pk in ids:
        raise LoanRequestError(
            f"Eksemplar {copy.copy_code} sudah ada di keranjang."
        )
    ids.append(copy.pk)
    _save_cart(session, ids)
    return copy


def cart_remove(session, copy_id):
    ids = [pk for pk in get_cart_ids(session) if pk != int(copy_id)]
    _save_cart(session, ids)


def cart_clear(session):
    session.pop(CART_SESSION_KEY, None)
    session.modified = True


def cart_copies(session):
    ids = get_cart_ids(session)
    if not ids:
        return []
    copies = BookCopy.objects.filter(pk__in=ids).select_related("book")
    by_id = {copy.pk: copy for copy in copies}
    return [by_id[pk] for pk in ids if pk in by_id]


def _current_fine_rate():
    setting = LibrarySetting.objects.order_by("-effective_from").first()
    if setting is None:
        raise LoanRequestError(
            "Konfigurasi denda belum tersedia. "
            "Hubungi pustakawan sebelum mengajukan peminjaman."
        )
    return setting.fine_rate_per_day


def request_loan(user, copy_ids):
    """Buat Loan PENDING_APPROVAL + LoanItems secara atomik (FR-16).

    Validasi (FR-13 – FR-15, BR-14 – BR-16, BR-25 – BR-29):
    user aktif, pilihan tidak kosong, eksemplar ada/aktif/tersedia,
    dan tidak terikat pengajuan aktif lain (NFR-19).
    """
    if not user.is_active:
        raise LoanRequestError(
            "Akun Anda tidak aktif sehingga tidak dapat mengajukan "
            "peminjaman."
        )
    try:
        ids = list(dict.fromkeys(int(pk) for pk in copy_ids))
    except (TypeError, ValueError):
        raise LoanRequestError("Pilihan eksemplar tidak valid.")
    if not ids:
        raise LoanRequestError(
            "Pilih minimal satu buku untuk diajukan."
        )

    with transaction.atomic():
        copies = list(
            BookCopy.objects.select_for_update()
            .filter(pk__in=ids)
            .select_related("book")
        )
        if len(copies) != len(ids):
            raise LoanRequestError(
                "Sebagian eksemplar yang dipilih tidak ditemukan."
            )
        by_id = {copy.pk: copy for copy in copies}
        for copy_id in ids:
            copy = by_id[copy_id]
            if not copy.book.is_active:
                raise LoanRequestError(
                    f"Judul {copy.book.title} sedang tidak aktif."
                )
            if copy.status != STATUS_TERSEDIA:
                raise LoanRequestError(
                    f"Eksemplar {copy.copy_code} sedang tidak tersedia."
                )
            if (
                LoanItem.objects.filter(
                    book_copy=copy, loan__status__in=OPEN_LOAN_STATUSES
                ).exists()
            ):
                raise LoanRequestError(
                    f"Eksemplar {copy.copy_code} sudah ada pada "
                    "pengajuan aktif."
                )

        fine_rate = _current_fine_rate()
        loan = Loan.objects.create(
            user=user,
            status=LOAN_PENDING,
            requested_at=timezone.now(),
        )
        LoanItem.objects.bulk_create(
            LoanItem(
                loan=loan,
                book_copy=by_id[copy_id],
                return_status=ITEM_RETURN_PENDING,
                fine_rate_per_day=fine_rate,
                late_day=0,
                fine_amount=0,
                fine_status=FINE_UNPAID,
            )
            for copy_id in ids
        )
    return loan
