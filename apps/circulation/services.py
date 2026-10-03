"""Layanan peminjaman buku fisik: pengajuan + approval + QR Pickup.

Critical rule pengajuan (FR-13 – FR-16): pengajuan BUKAN active loan
sampai handover selesai. Status eksemplar TIDAK diubah saat pengajuan
maupun approval; konflik dicegah dengan memeriksa item pada loan yang
masih terbuka (NFR-13, NFR-16, NFR-19, NFR-23).

QR Pickup (BR-17 – BR-20, BR-37 – BR-42, NFR-05 – NFR-07, NFR-17):
token opaque acak (tanpa data sensitif), berekspirasi dari konfigurasi
pickup_qr_validity (menit), hanya valid pada state APPROVED, sekali
pakai (pickup_used_at diisi saat handover oleh checkpoint berikutnya).
"""

import io
import secrets
from datetime import timedelta

import qrcode
from django.db import transaction
from django.utils import timezone

from apps.catalog.availability import STATUS_TERSEDIA
from apps.catalog.models import BookCopy
from apps.core.models import LibrarySetting
from apps.notifications.models import Notification

from .models import Loan, LoanItem

LOAN_PENDING = "PENDING_APPROVAL"
LOAN_APPROVED = "APPROVED"
LOAN_REJECTED = "REJECTED"
LOAN_ACTIVE = "ACTIVE"

# Status eksemplar saat dipinjam aktif (BR-13, BR-31).
STATUS_DIPINJAM = "DIPINJAM"

# Status loan yang masih "membawa" eksemplar sehingga eksemplar di
# dalamnya tidak boleh diminta lagi. ACTIVE ikut terbuka karena
# eksemplar baru bebas setelah pengembalian (checkpoint berikut).
OPEN_LOAN_STATUSES = (LOAN_PENDING, LOAN_APPROVED, LOAN_ACTIVE)

ITEM_RETURN_PENDING = "BELUM_KEMBALI"
FINE_UNPAID = "BELUM_BAYAR"

CART_SESSION_KEY = "loan_cart"


class LoanRequestError(Exception):
    """Kegagalan validasi/bisnis pengajuan (pesan siap tampil, NFR-33)."""


class ApprovalError(LoanRequestError):
    """Kegagalan approval/reject oleh pustakawan."""


class PickupTokenError(LoanRequestError):
    """Token QR Pickup tidak valid/kedaluarsa/sudah dipakai/salah state."""


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


def _current_library_setting():
    setting = LibrarySetting.objects.order_by("-effective_from").first()
    if setting is None:
        raise LoanRequestError(
            "Konfigurasi perpustakaan belum tersedia. "
            "Hubungi pustakawan sebelum mengajukan peminjaman."
        )
    return setting


def _current_fine_rate():
    return _current_library_setting().fine_rate_per_day


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


def _notify(user, notif_type, title, message, loan):
    Notification.objects.create(
        user=user,
        type=notif_type,
        title=title,
        message=message,
        reference_type="loan",
        reference_id=loan.pk,
    )


def available_substitutes(book, exclude_loan_pk=None):
    """Eksemplar TERSEDIA dari judul yang sama untuk substitusi approval."""
    candidates = BookCopy.objects.filter(
        book=book, status=STATUS_TERSEDIA
    ).order_by("copy_code")
    if exclude_loan_pk is None:
        taken = LoanItem.objects.filter(
            loan__status__in=OPEN_LOAN_STATUSES
        ).values("book_copy_id")
    else:
        taken = LoanItem.objects.filter(
            loan__status__in=OPEN_LOAN_STATUSES
        ).exclude(loan_id=exclude_loan_pk).values("book_copy_id")
    return candidates.exclude(pk__in=taken)


def approve_loan(loan_id, approver, substitutions=None):
    """Setujui pengajuan + hasilkan QR Pickup (FR-17 – FR-20, BR-17 – BR-20).

    substitutions: {loan_item_id: book_copy_id} untuk menetapkan
    eksemplar pengganti yang tersedia. Token unik, expiry dari
    pickup_qr_validity (menit). Atomik + notifikasi peminjam.
    """
    substitutions = substitutions or {}
    with transaction.atomic():
        try:
            loan = Loan.objects.select_for_update().get(pk=loan_id)
        except (Loan.DoesNotExist, TypeError, ValueError):
            raise ApprovalError("Pengajuan tidak ditemukan.")
        if loan.status != LOAN_PENDING:
            raise ApprovalError(
                "Hanya pengajuan PENDING_APPROVAL yang dapat disetujui."
            )
        setting = _current_library_setting()
        items = list(
            LoanItem.objects.select_for_update()
            .filter(loan=loan)
            .select_related("book_copy__book")
        )
        if not items:
            raise ApprovalError("Pengajuan tidak memiliki item.")
        for item in items:
            raw = substitutions.get(item.pk, item.book_copy_id)
            try:
                wanted_id = int(raw)
            except (TypeError, ValueError):
                raise ApprovalError("Pilihan eksemplar tidak valid.")
            try:
                copy = BookCopy.objects.select_for_update().get(
                    pk=wanted_id
                )
            except BookCopy.DoesNotExist:
                raise ApprovalError("Eksemplar pengganti tidak ditemukan.")
            if copy.book_id != item.book_copy.book_id:
                raise ApprovalError(
                    "Eksemplar pengganti harus dari judul yang sama."
                )
            if copy.status != STATUS_TERSEDIA:
                raise ApprovalError(
                    f"Eksemplar {copy.copy_code} sedang tidak tersedia."
                )
            if (
                LoanItem.objects.filter(
                    book_copy=copy, loan__status__in=OPEN_LOAN_STATUSES
                )
                .exclude(loan=loan)
                .exists()
            ):
                raise ApprovalError(
                    f"Eksemplar {copy.copy_code} sudah terikat "
                    "pengajuan aktif lain."
                )
            if copy.pk != item.book_copy_id:
                item.book_copy = copy
                item.save(update_fields=["book_copy"])

        token = secrets.token_urlsafe(32)
        while Loan.objects.filter(pickup_token=token).exists():
            token = secrets.token_urlsafe(32)
        now = timezone.now()
        loan.status = LOAN_APPROVED
        loan.approved_at = now
        loan.approved_by = approver
        loan.pickup_token = token
        loan.pickup_exp_at = now + timedelta(
            minutes=setting.pickup_qr_validity
        )
        loan.save(
            update_fields=[
                "status",
                "approved_at",
                "approved_by",
                "pickup_token",
                "pickup_exp_at",
                "updated_at",
            ]
        )
        _notify(
            loan.user,
            "LOAN_APPROVED",
            "Pengajuan disetujui",
            f"Pengajuan #{loan.pk} disetujui. Tunjukkan QR Pickup "
            "saat mengambil buku di perpustakaan.",
            loan,
        )
    return loan


def reject_loan(loan_id, approver, reason):
    """Tolak pengajuan dengan alasan wajib (FR-17)."""
    reason = (reason or "").strip()
    if not reason:
        raise ApprovalError("Alasan penolakan wajib diisi.")
    with transaction.atomic():
        try:
            loan = Loan.objects.select_for_update().get(pk=loan_id)
        except (Loan.DoesNotExist, TypeError, ValueError):
            raise ApprovalError("Pengajuan tidak ditemukan.")
        if loan.status != LOAN_PENDING:
            raise ApprovalError(
                "Hanya pengajuan PENDING_APPROVAL yang dapat ditolak."
            )
        loan.status = LOAN_REJECTED
        loan.rejected_at = timezone.now()
        loan.rejection_reason = reason
        loan.save(
            update_fields=[
                "status",
                "rejected_at",
                "rejection_reason",
                "updated_at",
            ]
        )
        _notify(
            loan.user,
            "LOAN_REJECTED",
            "Pengajuan ditolak",
            f"Pengajuan #{loan.pk} ditolak: {reason}",
            loan,
        )
    return loan


def _ensure_pickup_usable(loan):
    """Aturan validitas QR Pickup (BR-39 – BR-42, NFR-17).

    Dipakai verifikasi biasa maupun handover terkunci.
    """
    if loan.status != LOAN_APPROVED:
        raise PickupTokenError(
            "QR Pickup hanya berlaku pada pengajuan yang disetujui "
            "dan belum diserahkan."
        )
    if loan.pickup_used_at is not None:
        raise PickupTokenError("QR Pickup sudah digunakan.")
    if loan.pickup_exp_at is None or loan.pickup_exp_at <= timezone.now():
        raise PickupTokenError("QR Pickup sudah kedaluarsa.")


def verify_pickup_token(token):
    """Validasi token QR Pickup untuk handover (BR-39 – BR-42, NFR-17).

    Urutan penolakan: tidak dikenal -> salah state -> sudah dipakai ->
    kedaluarsa. Dipakai checkpoint handover; diuji di sini.
    """
    if not token:
        raise PickupTokenError("QR Pickup tidak dikenal.")
    try:
        loan = Loan.objects.select_related("user").get(pickup_token=token)
    except (Loan.DoesNotExist, ValueError):
        raise PickupTokenError("QR Pickup tidak dikenal.")
    _ensure_pickup_usable(loan)
    return loan


def activate_handover(token, librarian):
    """Aktifkan peminjaman saat buku diserahkan (FR-21 – FR-28, BR-19 – BR-24).

    Satu batas transaksi untuk loan + items + copies + token (NFR-16):
    borrowed_at = waktu aktual konfirmasi (BUKAN requested/approved),
    due_at dari durasi yang berlaku saat aktivasi (BR-24), eksemplar ->
    DIPINJAM (BR-13, FR-28), token ditandai sekali pakai (BR-40).
    """
    token = (token or "").strip()
    if not token:
        raise PickupTokenError("QR Pickup tidak dikenal.")
    with transaction.atomic():
        try:
            loan = (
                Loan.objects.select_for_update()
                .select_related("user")
                .get(pickup_token=token)
            )
        except (Loan.DoesNotExist, ValueError):
            raise PickupTokenError("QR Pickup tidak dikenal.")
        _ensure_pickup_usable(loan)
        setting = _current_library_setting()
        items = list(
            LoanItem.objects.select_for_update()
            .filter(loan=loan)
            .select_related("book_copy__book")
        )
        if not items:
            raise PickupTokenError(
                "Pengajuan tidak memiliki item untuk diserahkan."
            )
        for item in items:
            copy = BookCopy.objects.select_for_update().get(
                pk=item.book_copy_id
            )
            if copy.status != STATUS_TERSEDIA:
                raise PickupTokenError(
                    f"Eksemplar {copy.copy_code} sedang tidak tersedia "
                    "sehingga serah terima dibatalkan."
                )

        now = timezone.now()
        due_at = now + timedelta(days=setting.physical_loan_duration)
        loan.status = LOAN_ACTIVE
        loan.borrowed_at = now
        loan.due_at = due_at
        loan.pickup_used_at = now
        loan.save(
            update_fields=[
                "status",
                "borrowed_at",
                "due_at",
                "pickup_used_at",
                "updated_at",
            ]
        )
        for item in items:
            item.borrowed_at = now
            item.due_at = due_at
            item.save(update_fields=["borrowed_at", "due_at", "updated_at"])
        BookCopy.objects.filter(
            pk__in=[item.book_copy_id for item in items]
        ).update(status=STATUS_DIPINJAM)
        _notify(
            loan.user,
            "LOAN_ACTIVE",
            "Peminjaman aktif",
            f"Pengajuan #{loan.pk} aktif. Jatuh tempo "
            f"{due_at.strftime('%d %b %Y %H:%M')}.",
            loan,
        )
    return loan


def make_pickup_qr_png(token):
    """Render QR Pickup (payload = token opaque, tanpa data sensitif)."""
    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(token)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
