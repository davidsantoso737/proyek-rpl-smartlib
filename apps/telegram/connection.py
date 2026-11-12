"""Penautan Telegram dengan verifikasi nomor HP dan persetujuan pustakawan.

Alur utama (otomatis):
1. Akun dibuat atau nomor HP diisi/diubah -> pengajuan otomatis berstatus
   ``pending`` (menunggu persetujuan pustakawan).
2. Pustakawan menyetujui -> status ``awaiting_contact`` (menunggu
   konfirmasi identitas Telegram). Persetujuan ini belum membuktikan
   kepemilikan akun Telegram.
3. Pengguna membuat tautan sekali pakai dari halaman Profil, membuka bot,
   lalu membagikan kontaknya sendiri. Bot memvalidasi
   ``contact.user_id == message.from.id`` dan kecocokan nomor HP.
4. Status menjadi ``active`` dan notifikasi Telegram boleh dikirim.

Modul ini tidak mengubah TelegramAccount/TelegramOutbox lama. Resolusi
penerima notifikasi memakai koneksi berstatus ``active`` dengan
TelegramAccount lama sebagai fallback kompatibilitas.
"""

import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.notifications.models import Notification

from .linking import hash_link_code
from .models import TelegramAccount, TelegramConnection

TOKEN_TTL = timedelta(minutes=10)
TOKEN_BYTES = 32
TOKEN_MAX_LENGTH = 128
MAX_CONTACT_ATTEMPTS = 5
ACTIVE_SLOT = 1
SESSION_CONNECTION_TOKEN = "telegram_connection_token"

STATE_NONE = "none"
STATE_PENDING = "pending"
STATE_APPROVED = "approved"
STATE_AWAITING = "awaiting"
STATE_EXPIRED = "expired"
STATE_ACTIVE = "active"
STATE_REJECTED = "rejected"
STATE_REVOKED = "revoked"
STATE_LEGACY_ACTIVE = "legacy_active"


@dataclass(frozen=True)
class TokenResult:
    status: str
    code: str = ""
    connection: TelegramConnection | None = None


@dataclass(frozen=True)
class RequestResult:
    status: str
    connection: TelegramConnection | None = None


@dataclass(frozen=True)
class StartResult:
    status: str
    connection: TelegramConnection | None = None


@dataclass(frozen=True)
class ContactResult:
    status: str
    connection: TelegramConnection | None = None
    attempts_left: int = 0


@dataclass(frozen=True)
class DecisionResult:
    status: str
    connection: TelegramConnection | None = None


def normalize_phone(value):
    """Normalisasi nomor Indonesia ke bentuk 62xxxxxxxxxx.

    Menerima 08..., 62..., +62..., dengan spasi/tanda hubung/titik.
    Mengembalikan string kosong bila nomor tidak masuk akal.
    """
    raw = "" if value is None else str(value).strip()
    if not raw:
        return ""
    digits = re.sub(r"[^0-9]", "", raw)
    if not digits:
        return ""
    if digits.startswith("62"):
        normalized = digits
    elif digits.startswith("0"):
        normalized = "62" + digits[1:]
    elif digits.startswith("8"):
        normalized = "62" + digits
    else:
        return ""
    if not normalized.startswith("628"):
        return ""
    if not 10 <= len(normalized) <= 15:
        return ""
    return normalized


def mask_phone(value):
    normalized = normalize_phone(value)
    if not normalized:
        return ""
    local = "0" + normalized[2:]
    if len(local) <= 6:
        return local
    return f"{local[:4]}{'*' * (len(local) - 6)}{local[-2:]}"


def _active_slot_row(user, *, lock=False):
    queryset = TelegramConnection.objects.filter(
        user=user, active_slot=ACTIVE_SLOT
    )
    if lock:
        queryset = queryset.select_for_update()
    return queryset.first()


def _clear_identity(row):
    row.telegram_user_id = None
    row.chat_id = None
    row.telegram_username = ""
    row.telegram_first_name = ""
    row.token_hash = None
    row.token_expires_at = None


def _revoke_row(row, now, *, note=""):
    _clear_identity(row)
    row.status = TelegramConnection.Status.REVOKED
    row.active_slot = None
    row.revoked_at = now
    if note:
        row.rejection_note = note
    row.save(
        update_fields=[
            "status",
            "active_slot",
            "telegram_user_id",
            "chat_id",
            "telegram_username",
            "telegram_first_name",
            "token_hash",
            "token_expires_at",
            "revoked_at",
            "rejection_note",
            "updated_at",
        ]
    )


def _create_pending_row(user, phone, now):
    return TelegramConnection.objects.create(
        user=user,
        status=TelegramConnection.Status.PENDING,
        active_slot=ACTIVE_SLOT,
        requested_phone=phone,
        requested_at=now,
    )


def request_connection(user, *, force=False, notify=True):
    """Buat pengajuan penautan otomatis untuk nomor HP saat ini.

    Idempoten: bila sudah ada pengajuan/persetujuan untuk nomor yang
    sama, tidak ada baris baru. Bila nomor berubah, koneksi lama
    diputus lebih dulu lalu pengajuan baru dibuat tanpa menghapus
    riwayat.
    """
    if user is None or not getattr(user, "pk", None):
        return RequestResult("invalid")
    if not getattr(user, "is_active", False):
        return RequestResult("inactive")
    phone = normalize_phone(getattr(user, "phone", ""))
    if not phone:
        return RequestResult("no_phone")

    now = timezone.now()
    old_chat_id = None
    row = None
    with transaction.atomic():
        current = _active_slot_row(user, lock=True)
        if current is not None:
            current_phone = normalize_phone(
                current.requested_phone or current.verified_phone
            )
            if current.status == TelegramConnection.Status.ACTIVE:
                if current_phone == phone and not force:
                    return RequestResult("already_active", connection=current)
                old_chat_id = current.chat_id
                _revoke_row(
                    current,
                    now,
                    note="Nomor HP berubah; koneksi Telegram lama diputus.",
                )
            elif current.status == TelegramConnection.Status.PENDING:
                if current_phone == phone and not force:
                    return RequestResult("already_pending", connection=current)
                _revoke_row(
                    current,
                    now,
                    note="Diganti pengajuan penautan yang lebih baru.",
                )
            elif current.status == (
                TelegramConnection.Status.AWAITING_CONTACT
            ):
                if current_phone == phone and not force:
                    return RequestResult(
                        "awaiting_confirmation", connection=current
                    )
                _revoke_row(
                    current,
                    now,
                    note="Diganti pengajuan penautan yang lebih baru.",
                )
            else:
                _revoke_row(current, now)

        for _ in range(5):
            try:
                with transaction.atomic():
                    row = _create_pending_row(user, phone, now)
            except IntegrityError:
                current = _active_slot_row(user)
                if current is not None:
                    if current.status == (
                        TelegramConnection.Status.PENDING
                    ):
                        return RequestResult(
                            "already_pending", connection=current
                        )
                    return RequestResult(
                        "already_pending", connection=current
                    )
                continue
            break
        else:
            return RequestResult("error")

    if old_chat_id:
        _send_best_effort(
            old_chat_id,
            "Nomor HP SmartLib berubah. Koneksi Telegram lama diputus; "
            "ajukan penautan ulang dari halaman Profil.",
        )
    if notify:
        notify_librarians(row)
    return RequestResult("created", connection=row)


def create_connection_token(user):
    """Buat tautan konfirmasi sekali pakai untuk pengajuan yang sudah
    disetujui pustakawan (status ``awaiting_contact``)."""
    if not getattr(user, "is_active", False):
        return TokenResult("inactive")
    phone = normalize_phone(getattr(user, "phone", ""))
    if not phone:
        return TokenResult("no_phone")

    now = timezone.now()
    with transaction.atomic():
        current = _active_slot_row(user, lock=True)
        if current is None:
            return TokenResult("not_approved")
        if current.status == TelegramConnection.Status.ACTIVE:
            return TokenResult("already_active", connection=current)
        if current.status == TelegramConnection.Status.PENDING:
            return TokenResult("pending_admin", connection=current)
        if current.status != TelegramConnection.Status.AWAITING_CONTACT:
            return TokenResult("not_approved", connection=current)
        if current.approved_at is None:
            return TokenResult("not_approved", connection=current)
        approved_phone = normalize_phone(
            current.requested_phone
        ) or normalize_phone(getattr(current.user, "phone", ""))
        if approved_phone != phone:
            _revoke_row(
                current,
                now,
                note="Nomor HP berubah setelah persetujuan; ajukan ulang.",
            )
            return TokenResult("phone_changed", connection=current)

        for _ in range(5):
            code = secrets.token_urlsafe(TOKEN_BYTES)
            token_hash = hash_link_code(code)
            if (
                TelegramConnection.objects.filter(token_hash=token_hash)
                .exclude(pk=current.pk)
                .exists()
            ):
                continue
            current.token_hash = token_hash
            current.token_expires_at = now + TOKEN_TTL
            current.attempt_count = 0
            current.save(
                update_fields=[
                    "token_hash",
                    "token_expires_at",
                    "attempt_count",
                    "updated_at",
                ]
            )
            return TokenResult("ok", code=code, connection=current)
    return TokenResult("error")


def _identity_conflict(telegram_user_id, chat_id, *, exclude_pk=None):
    queryset = TelegramConnection.objects.filter(
        Q(telegram_user_id=telegram_user_id) | Q(chat_id=chat_id)
    )
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    if queryset.exists():
        return True
    return TelegramAccount.objects.filter(
        Q(telegram_user_id=telegram_user_id) | Q(chat_id=chat_id)
    ).exists()


def start_link_token(
    code,
    *,
    telegram_user_id,
    chat_id,
    username="",
    first_name="",
):
    """Validasi token dari /start dan ikat identitas Telegram ke baris."""
    normalized = str(code or "").strip()
    if (
        not normalized
        or len(normalized) > TOKEN_MAX_LENGTH
        or not isinstance(telegram_user_id, int)
        or isinstance(telegram_user_id, bool)
        or telegram_user_id <= 0
        or not isinstance(chat_id, int)
        or isinstance(chat_id, bool)
        or chat_id <= 0
        or chat_id != telegram_user_id
    ):
        return StartResult("invalid")

    now = timezone.now()
    with transaction.atomic():
        row = (
            TelegramConnection.objects.select_for_update()
            .select_related("user")
            .filter(
                token_hash=hash_link_code(normalized),
                status=TelegramConnection.Status.AWAITING_CONTACT,
            )
            .first()
        )
        if row is None:
            return StartResult("invalid")
        if row.approved_at is None:
            return StartResult("invalid")
        if row.token_expires_at is None or row.token_expires_at <= now:
            row.token_hash = None
            row.token_expires_at = None
            row.save(
                update_fields=[
                    "token_hash",
                    "token_expires_at",
                    "updated_at",
                ]
            )
            return StartResult("expired")
        if not row.user.is_active:
            return StartResult("inactive")

        expected = normalize_phone(row.requested_phone) or normalize_phone(
            getattr(row.user, "phone", "")
        )
        if not expected:
            return StartResult("no_phone")
        if expected != normalize_phone(getattr(row.user, "phone", "")):
            _revoke_row(
                row,
                now,
                note="Nomor HP berubah setelah persetujuan; ajukan ulang.",
            )
            return StartResult("phone_changed")

        if row.telegram_user_id is not None:
            if (
                row.telegram_user_id != telegram_user_id
                or row.chat_id != chat_id
            ):
                return StartResult("conflict", connection=row)
        else:
            if _identity_conflict(
                telegram_user_id, chat_id, exclude_pk=row.pk
            ):
                return StartResult("conflict", connection=row)
            row.telegram_user_id = telegram_user_id
            row.chat_id = chat_id

        row.telegram_username = str(username or "")[:64]
        row.telegram_first_name = str(first_name or "")[:255]
        row.save(
            update_fields=[
                "telegram_user_id",
                "chat_id",
                "telegram_username",
                "telegram_first_name",
                "updated_at",
            ]
        )
        return StartResult("ok", connection=row)


def verify_contact(
    *,
    telegram_user_id,
    chat_id,
    contact_user_id,
    phone_number,
    username="",
    first_name="",
):
    """Cocokkan kontak Telegram dengan nomor HP yang sudah disetujui."""
    if (
        not isinstance(telegram_user_id, int)
        or isinstance(telegram_user_id, bool)
        or telegram_user_id <= 0
        or not isinstance(chat_id, int)
        or isinstance(chat_id, bool)
        or chat_id <= 0
        or not isinstance(contact_user_id, int)
        or isinstance(contact_user_id, bool)
        or contact_user_id <= 0
    ):
        return ContactResult("invalid")

    now = timezone.now()
    activated = None
    with transaction.atomic():
        row = (
            TelegramConnection.objects.select_for_update()
            .select_related("user")
            .filter(
                telegram_user_id=telegram_user_id,
                status=TelegramConnection.Status.AWAITING_CONTACT,
            )
            .first()
        )
        if row is None:
            return ContactResult("no_session")
        if row.approved_at is None:
            return ContactResult("no_session")
        if chat_id != telegram_user_id or row.chat_id != chat_id:
            return ContactResult("invalid")
        if row.token_expires_at is None or row.token_expires_at <= now:
            row.token_hash = None
            row.token_expires_at = None
            row.save(
                update_fields=[
                    "token_hash",
                    "token_expires_at",
                    "updated_at",
                ]
            )
            return ContactResult("expired")
        if not row.user.is_active:
            return ContactResult("inactive")

        if contact_user_id != telegram_user_id:
            exceeded = _register_attempt(row, now)
            return ContactResult(
                "too_many" if exceeded else "contact_mismatch",
                connection=row,
                attempts_left=max(
                    MAX_CONTACT_ATTEMPTS - row.attempt_count, 0
                ),
            )

        expected = normalize_phone(row.requested_phone) or normalize_phone(
            getattr(row.user, "phone", "")
        )
        current_phone = normalize_phone(getattr(row.user, "phone", ""))
        if not expected:
            _revoke_row(row, now, note="Profil belum memiliki nomor HP.")
            return ContactResult("no_phone", connection=row)
        if expected != current_phone:
            _revoke_row(
                row,
                now,
                note="Nomor HP berubah; ajukan penautan ulang.",
            )
            return ContactResult("phone_changed", connection=row)

        provided = normalize_phone(phone_number)
        if not provided or provided != expected:
            exceeded = _register_attempt(row, now)
            return ContactResult(
                "too_many" if exceeded else "phone_mismatch",
                connection=row,
                attempts_left=max(
                    MAX_CONTACT_ATTEMPTS - row.attempt_count, 0
                ),
            )

        if _identity_conflict(
            telegram_user_id, chat_id, exclude_pk=row.pk
        ):
            _revoke_row(
                row, now, note="Akun Telegram sudah dipakai akun lain."
            )
            return ContactResult("conflict", connection=row)

        row.status = TelegramConnection.Status.ACTIVE
        row.active_slot = ACTIVE_SLOT
        row.verified_phone = provided
        row.verified_at = now
        row.token_hash = None
        row.token_expires_at = None
        row.attempt_count = 0
        row.telegram_username = str(username or "")[:64]
        row.telegram_first_name = str(first_name or "")[:255]
        row.save(
            update_fields=[
                "status",
                "active_slot",
                "verified_phone",
                "verified_at",
                "token_hash",
                "token_expires_at",
                "attempt_count",
                "telegram_username",
                "telegram_first_name",
                "updated_at",
            ]
        )
        activated = row

    if activated is not None:
        notify_activated(activated)
    return ContactResult("ok", connection=activated)


def _register_attempt(row, now):
    row.attempt_count += 1
    if row.attempt_count >= MAX_CONTACT_ATTEMPTS:
        _revoke_row(
            row,
            now,
            note="Batas percobaan verifikasi kontak tercapai.",
        )
        return True
    row.save(update_fields=["attempt_count", "updated_at"])
    return False


def cancel_pending_connection(user):
    """Batalkan tautan yang belum selesai tanpa menghapus riwayat."""
    now = timezone.now()
    with transaction.atomic():
        row = _active_slot_row(user, lock=True)
        if row is None:
            return DecisionResult("not_pending", connection=None)
        if row.status == TelegramConnection.Status.ACTIVE:
            return DecisionResult("not_pending", connection=row)
        _revoke_row(row, now, note="Dibatalkan oleh pengguna.")
        return DecisionResult("ok", connection=row)


def revoke_active_connection(user):
    """Putuskan koneksi aktif; kembalikan chat_id untuk pemberitahuan."""
    now = timezone.now()
    with transaction.atomic():
        row = (
            TelegramConnection.objects.select_for_update()
            .filter(
                user=user, status=TelegramConnection.Status.ACTIVE
            )
            .order_by("-created_at")
            .first()
        )
        if row is None:
            return None, None
        chat_id = row.chat_id
        _revoke_row(row, now, note="Diputus oleh pengguna.")
        return chat_id, row


def approve_connection(connection_id, librarian):
    """Setujui pengajuan. Aktivasi tetap menunggu konfirmasi Telegram."""
    now = timezone.now()
    row = None
    with transaction.atomic():
        row = (
            TelegramConnection.objects.select_for_update()
            .select_related("user")
            .filter(pk=connection_id)
            .first()
        )
        if row is None:
            return DecisionResult("missing")
        if row.status != TelegramConnection.Status.PENDING:
            return DecisionResult("not_pending", connection=row)
        if not row.user.is_active:
            return DecisionResult("inactive", connection=row)

        current_phone = normalize_phone(row.user.phone)
        if not current_phone:
            return DecisionResult("no_phone", connection=row)
        if (
            row.requested_phone
            and normalize_phone(row.requested_phone) != current_phone
        ):
            return DecisionResult("phone_changed", connection=row)

        already_verified = bool(
            row.verified_at
            and row.telegram_user_id
            and row.chat_id
        )
        if already_verified and _identity_conflict(
            row.telegram_user_id,
            row.chat_id,
            exclude_pk=row.pk,
        ):
            return DecisionResult("conflict", connection=row)

        row.approved_by = librarian
        row.approved_at = now
        row.active_slot = ACTIVE_SLOT
        if not row.requested_phone:
            row.requested_phone = current_phone
        if already_verified:
            row.status = TelegramConnection.Status.ACTIVE
        else:
            row.status = TelegramConnection.Status.AWAITING_CONTACT
            row.token_hash = None
            row.token_expires_at = None
            row.attempt_count = 0
        row.save(
            update_fields=[
                "approved_by",
                "approved_at",
                "active_slot",
                "requested_phone",
                "status",
                "token_hash",
                "token_expires_at",
                "attempt_count",
                "updated_at",
            ]
        )
    if row.status == TelegramConnection.Status.ACTIVE:
        notify_activated(row)
    else:
        notify_approved(row)
    return DecisionResult("ok", connection=row)


def reject_connection(connection_id, librarian, note=""):
    now = timezone.now()
    chat_id = None
    row = None
    with transaction.atomic():
        row = (
            TelegramConnection.objects.select_for_update()
            .select_related("user")
            .filter(pk=connection_id)
            .first()
        )
        if row is None:
            return DecisionResult("missing")
        if row.status != TelegramConnection.Status.PENDING:
            return DecisionResult("not_pending", connection=row)
        chat_id = row.chat_id
        row.status = TelegramConnection.Status.REJECTED
        row.active_slot = None
        row.approved_by = librarian
        row.approved_at = now
        row.rejection_note = str(note or "").strip()[:1000]
        row.rejected_at = now
        _clear_identity(row)
        row.save(
            update_fields=[
                "status",
                "active_slot",
                "approved_by",
                "approved_at",
                "rejection_note",
                "rejected_at",
                "telegram_user_id",
                "chat_id",
                "telegram_username",
                "telegram_first_name",
                "token_hash",
                "token_expires_at",
                "updated_at",
            ]
        )
    notify_rejected(row, chat_id)
    return DecisionResult("ok", connection=row)


def _send_best_effort(chat_id, text):
    if not chat_id:
        return
    try:
        from .services import send_text_message

        send_text_message(chat_id, text, require_enabled=False)
    except Exception:
        # Pemberitahuan penautan tidak boleh menggagalkan alur utama.
        return


def _notify_user_in_app(user, notification_type, title, message, connection):
    Notification.objects.get_or_create(
        user=user,
        type=notification_type,
        reference_type="telegram_connection",
        reference_id=connection.pk,
        defaults={"title": title, "message": message},
    )


def notify_librarians(connection):
    User = get_user_model()
    masked = mask_phone(connection.requested_phone)
    message = (
        f"{connection.user.full_name} "
        f"({connection.user.get_role_display()}) mengajukan penautan "
        f"Telegram dengan nomor HP {masked or 'yang terdaftar'}. "
        "Buka Verifikasi Telegram untuk menyetujui atau menolak."
    )
    existing = Notification.objects.filter(
        type="TELEGRAM_LINK_REQUEST",
        reference_type="telegram_connection",
        reference_id=connection.pk,
    ).exists()
    if existing:
        return
    notifications = [
        Notification(
            user=librarian,
            type="TELEGRAM_LINK_REQUEST",
            title="Permintaan penautan Telegram",
            message=message,
            reference_type="telegram_connection",
            reference_id=connection.pk,
        )
        for librarian in User.objects.filter(
            role="PUSTAKAWAN", is_active=True
        )
    ]
    if notifications:
        Notification.objects.bulk_create(notifications)


def notify_approved(connection):
    message = (
        "Pengajuan penautan Telegram disetujui. Buka halaman Profil "
        "SmartLib dan pilih Konfirmasi Telegram untuk membuktikan "
        "kepemilikan akun Telegram Anda."
    )
    _notify_user_in_app(
        connection.user,
        "TELEGRAM_LINK_APPROVED",
        "Pengajuan Telegram disetujui",
        message,
        connection,
    )
    _send_best_effort(connection.chat_id, message)


def notify_activated(connection):
    message = (
        "Penautan Telegram aktif. Akun ini sekarang menerima "
        "notifikasi perpustakaan."
    )
    _notify_user_in_app(
        connection.user,
        "TELEGRAM_LINK_ACTIVE",
        "Telegram berhasil dihubungkan",
        message,
        connection,
    )
    _send_best_effort(connection.chat_id, message)


def notify_rejected(connection, chat_id):
    note = connection.rejection_note
    message = "Permintaan penautan Telegram ditolak oleh pustakawan."
    if note:
        message = f"{message} Catatan: {note}"
    _notify_user_in_app(
        connection.user,
        "TELEGRAM_LINK_REJECTED",
        "Permintaan penautan Telegram ditolak",
        message,
        connection,
    )
    _send_best_effort(chat_id, message)


def build_deep_link(token):
    username = (getattr(settings, "TELEGRAM_BOT_USERNAME", "") or "").strip()
    if not username or not token:
        return ""
    return f"https://t.me/{quote(username)}?start={quote(token)}"


def resolve_recipient(user):
    """Pilih koneksi aktif baru, lalu akun lama sebagai fallback.

    Hanya baris berstatus active yang boleh dipakai untuk pengiriman;
    koneksi pending/awaiting/rejected/revoked tidak boleh menerima
    notifikasi.
    """
    return resolve_recipient_for_user_id(user.pk)


def resolve_recipient_for_user_id(user_id):
    active = (
        TelegramConnection.objects.select_related("user")
        .filter(
            user_id=user_id,
            status=TelegramConnection.Status.ACTIVE,
            chat_id__isnull=False,
        )
        .order_by("-approved_at", "-created_at")
        .first()
    )
    if active is not None:
        return active
    return (
        TelegramAccount.objects.select_related("user")
        .filter(user_id=user_id)
        .first()
    )


def get_connection_context(user, session_token=None):
    """Data tampilan profil tanpa membocorkan chat id atau token."""
    rows = TelegramConnection.objects.filter(user=user)
    active = rows.filter(status=TelegramConnection.Status.ACTIVE).first()
    pending = rows.filter(
        status=TelegramConnection.Status.PENDING,
        active_slot=ACTIVE_SLOT,
    ).first()
    awaiting = rows.filter(
        status=TelegramConnection.Status.AWAITING_CONTACT,
        active_slot=ACTIVE_SLOT,
    ).first()
    last = rows.first()
    legacy_account = TelegramAccount.objects.filter(user=user).first()

    now = timezone.now()
    state = STATE_NONE
    connection = None
    link_ready = False
    if active is not None:
        state = STATE_ACTIVE
        connection = active
    elif pending is not None:
        state = STATE_PENDING
        connection = pending
    elif awaiting is not None:
        connection = awaiting
        if awaiting.token_hash is None:
            state = STATE_APPROVED
        elif (
            awaiting.token_expires_at is None
            or awaiting.token_expires_at <= now
        ):
            state = STATE_EXPIRED
        else:
            state = STATE_AWAITING
            link_ready = bool(
                session_token
                and awaiting.token_hash
                and hash_link_code(session_token) == awaiting.token_hash
            )
    elif legacy_account is not None:
        state = STATE_LEGACY_ACTIVE
        connection = None
    elif last is not None and last.status == TelegramConnection.Status.REJECTED:
        state = STATE_REJECTED
        connection = last
    elif last is not None and last.status == TelegramConnection.Status.REVOKED:
        state = STATE_REVOKED
        connection = last

    phone = ""
    if connection is not None:
        phone = (
            connection.verified_phone
            or connection.requested_phone
            or ""
        )
    if not phone:
        phone = getattr(user, "phone", "") or ""

    return {
        "state": state,
        "connection": connection,
        "legacy_account": legacy_account,
        "masked_phone": mask_phone(phone),
        "link_ready": link_ready,
        "rejection_note": (
            connection.rejection_note if connection is not None else ""
        ),
        "requested_at": (
            connection.requested_at if connection is not None else None
        ),
        "approved_at": (
            connection.approved_at if connection is not None else None
        ),
    }
