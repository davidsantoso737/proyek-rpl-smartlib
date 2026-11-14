"""Penautan nomor WhatsApp dengan persetujuan pustakawan dan OTP.

Alur status:
1. Pengguna mengajukan nomor -> ``pending``.
2. Pustakawan menyetujui -> ``approved`` dan OTP dikirim via Fonnte.
3. Pengguna memasukkan OTP -> ``active``.
4. Penolakan -> ``rejected``; pembatalan atau penggantian -> ``revoked``.

Persetujuan dan verifikasi OTP adalah dua tahap terpisah. Nomor lama
yang masih ``active`` tidak diputus sampai nomor baru berhasil
diverifikasi.
"""

import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.notifications.models import Notification

from .models import WhatsAppConnection
from .preferences import should_send_whatsapp
from .services import (
    WhatsAppError,
    mask_phone,
    normalize_phone,
    send_text_message,
)

logger = logging.getLogger(__name__)

OTP_TTL = timedelta(minutes=10)
OTP_LENGTH = 6
MAX_OTP_ATTEMPTS = 5
OTP_RESEND_COOLDOWN = timedelta(seconds=60)


@dataclass(frozen=True)
class RequestResult:
    status: str
    connection: WhatsAppConnection | None = None


@dataclass(frozen=True)
class DecisionResult:
    status: str
    connection: WhatsAppConnection | None = None
    otp_sent: bool = False


@dataclass(frozen=True)
class OtpResult:
    status: str
    connection: WhatsAppConnection | None = None
    attempts_left: int = 0
    retry_after: int = 0


def _current_user(user):
    return get_user_model().objects.select_for_update().get(pk=user.pk)


def _active_row(user):
    return (
        WhatsAppConnection.objects.filter(
            user=user, status=WhatsAppConnection.Status.ACTIVE
        )
        .order_by("-verified_at", "-created_at")
        .first()
    )


def _latest_by_status(user, status):
    return (
        WhatsAppConnection.objects.filter(user=user, status=status)
        .order_by("-created_at", "-pk")
        .first()
    )


def _phone_in_use(phone, *, exclude_pk=None, include_pending=True):
    statuses = [WhatsAppConnection.Status.ACTIVE]
    if include_pending:
        statuses.extend(
            [
                WhatsAppConnection.Status.PENDING,
                WhatsAppConnection.Status.APPROVED,
            ]
        )
    queryset = WhatsAppConnection.objects.filter(
        phone=phone, status__in=statuses
    )
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    return queryset.exists()


def _revoke_row(row, now, note=""):
    row.status = WhatsAppConnection.Status.REVOKED
    row.active_phone = None
    row.revoked_at = now
    row.otp_hash = None
    row.otp_expires_at = None
    row.otp_attempts = 0
    if note:
        row.rejection_note = note
    row.save(
        update_fields=[
            "status",
            "active_phone",
            "revoked_at",
            "otp_hash",
            "otp_expires_at",
            "otp_attempts",
            "rejection_note",
            "updated_at",
        ]
    )


def _clear_otp(row):
    row.otp_hash = None
    row.otp_expires_at = None
    row.otp_attempts = 0


def request_connection(user, phone):
    """Buat pengajuan nomor WhatsApp baru berstatus PENDING."""
    if user is None or not getattr(user, "pk", None):
        return RequestResult("invalid")
    if not getattr(user, "is_active", False):
        return RequestResult("inactive")
    normalized = normalize_phone(phone)
    if not normalized:
        return RequestResult("invalid_phone")

    now = timezone.now()
    with transaction.atomic():
        _current_user(user)
        active = _active_row(user)
        if (
            active is not None
            and active.phone == normalized
            and active.status == WhatsAppConnection.Status.ACTIVE
        ):
            return RequestResult("already_active", connection=active)

        if _phone_in_use(normalized, exclude_pk=None):
            other = (
                WhatsAppConnection.objects.filter(phone=normalized)
                .exclude(user=user)
                .filter(
                    status__in=[
                        WhatsAppConnection.Status.ACTIVE,
                        WhatsAppConnection.Status.PENDING,
                        WhatsAppConnection.Status.APPROVED,
                    ]
                )
                .first()
            )
            if other is not None:
                return RequestResult("phone_in_use")

            pending_same = (
                WhatsAppConnection.objects.filter(
                    user=user,
                    phone=normalized,
                    status=WhatsAppConnection.Status.PENDING,
                )
                .order_by("-created_at")
                .first()
            )
            if pending_same is not None:
                return RequestResult(
                    "already_pending", connection=pending_same
                )
            approved_same = (
                WhatsAppConnection.objects.filter(
                    user=user,
                    phone=normalized,
                    status=WhatsAppConnection.Status.APPROVED,
                )
                .order_by("-created_at")
                .first()
            )
            if approved_same is not None:
                return RequestResult(
                    "awaiting_verification", connection=approved_same
                )

        for status in (
            WhatsAppConnection.Status.PENDING,
            WhatsAppConnection.Status.APPROVED,
        ):
            current = _latest_by_status(user, status)
            if current is not None:
                _revoke_row(
                    current,
                    now,
                    note="Diganti pengajuan nomor WhatsApp yang lebih baru.",
                )

        try:
            row = WhatsAppConnection.objects.create(
                user=user,
                phone=normalized,
                status=WhatsAppConnection.Status.PENDING,
                requested_at=now,
            )
        except IntegrityError:
            logger.warning(
                "Pengajuan WhatsApp gagal dibuat user_id=%s", user.pk
            )
            return RequestResult("error")

    notify_librarians(row)
    return RequestResult("created", connection=row)


def _issue_otp(row, now):
    code = f"{secrets.randbelow(10 ** OTP_LENGTH):0{OTP_LENGTH}d}"
    row.otp_hash = make_password(code)
    row.otp_expires_at = now + OTP_TTL
    row.otp_attempts = 0
    row.otp_sent_at = now
    row.otp_last_error = ""
    row.save(
        update_fields=[
            "otp_hash",
            "otp_expires_at",
            "otp_attempts",
            "otp_sent_at",
            "otp_last_error",
            "updated_at",
        ]
    )
    return code


def _otp_message(code):
    return (
        "Kode verifikasi WhatsApp SmartLib Anda: "
        f"{code}\n\n"
        "Kode berlaku 10 menit dan hanya dapat dipakai satu kali. "
        "Jangan bagikan kode ini kepada siapa pun."
    )


def send_connection_otp(connection, *, force=False):
    """Buat dan kirim OTP baru untuk pengajuan yang sudah disetujui."""
    now = timezone.now()
    with transaction.atomic():
        row = (
            WhatsAppConnection.objects.select_for_update()
            .select_related("user")
            .filter(pk=connection.pk)
            .first()
        )
        if row is None:
            return OtpResult("missing")
        if row.status != WhatsAppConnection.Status.APPROVED:
            return OtpResult("not_approved", connection=row)
        if not row.user.is_active:
            return OtpResult("inactive", connection=row)
        if not force and row.otp_sent_at is not None:
            elapsed = now - row.otp_sent_at
            if elapsed < OTP_RESEND_COOLDOWN:
                seconds = int(
                    (OTP_RESEND_COOLDOWN - elapsed).total_seconds()
                )
                return OtpResult(
                    "cooldown",
                    connection=row,
                    retry_after=max(seconds, 1),
                )
        code = _issue_otp(row, now)

    try:
        send_text_message(row.phone, _otp_message(code))
    except WhatsAppError as exc:
        error = str(exc) or "Pengiriman OTP gagal."
        WhatsAppConnection.objects.filter(pk=row.pk).update(
            otp_last_error=error[:1000],
            updated_at=timezone.now(),
        )
        logger.warning(
            "OTP WhatsApp gagal dikirim connection_id=%s kind=%s",
            row.pk,
            type(exc).__name__,
        )
        row.otp_last_error = error[:1000]
        return OtpResult("send_failed", connection=row)

    WhatsAppConnection.objects.filter(pk=row.pk).update(
        otp_last_error="", updated_at=timezone.now()
    )
    row.otp_last_error = ""
    return OtpResult("ok", connection=row)


def resend_otp(user):
    row = _latest_by_status(user, WhatsAppConnection.Status.APPROVED)
    if row is None:
        return OtpResult("none")
    return send_connection_otp(row)


def approve_connection(connection_id, librarian):
    """Setujui pengajuan lalu kirim OTP. Aktivasi menunggu verifikasi."""
    now = timezone.now()
    with transaction.atomic():
        row = (
            WhatsAppConnection.objects.select_for_update()
            .select_related("user")
            .filter(pk=connection_id)
            .first()
        )
        if row is None:
            return DecisionResult("missing")
        if row.status != WhatsAppConnection.Status.PENDING:
            return DecisionResult("not_pending", connection=row)
        if not row.user.is_active:
            return DecisionResult("inactive", connection=row)
        if _phone_in_use(
            row.phone,
            exclude_pk=row.pk,
            include_pending=False,
        ):
            return DecisionResult("phone_in_use", connection=row)

        row.status = WhatsAppConnection.Status.APPROVED
        row.approved_by = librarian
        row.approved_at = now
        _clear_otp(row)
        row.otp_last_error = ""
        row.save(
            update_fields=[
                "status",
                "approved_by",
                "approved_at",
                "otp_hash",
                "otp_expires_at",
                "otp_attempts",
                "otp_last_error",
                "updated_at",
            ]
        )

    notify_approved(row)
    otp = send_connection_otp(row, force=True)
    return DecisionResult(
        "ok" if otp.status == "ok" else "otp_failed",
        connection=row,
        otp_sent=otp.status == "ok",
    )


def reject_connection(connection_id, librarian, note=""):
    now = timezone.now()
    with transaction.atomic():
        row = (
            WhatsAppConnection.objects.select_for_update()
            .select_related("user")
            .filter(pk=connection_id)
            .first()
        )
        if row is None:
            return DecisionResult("missing")
        if row.status != WhatsAppConnection.Status.PENDING:
            return DecisionResult("not_pending", connection=row)
        row.status = WhatsAppConnection.Status.REJECTED
        row.active_phone = None
        row.approved_by = librarian
        row.approved_at = now
        row.rejection_note = str(note or "").strip()[:1000]
        row.rejected_at = now
        _clear_otp(row)
        row.save(
            update_fields=[
                "status",
                "active_phone",
                "approved_by",
                "approved_at",
                "rejection_note",
                "rejected_at",
                "otp_hash",
                "otp_expires_at",
                "otp_attempts",
                "updated_at",
            ]
        )
    notify_rejected(row)
    return DecisionResult("ok", connection=row)


def _register_otp_failure(row, now):
    row.otp_attempts += 1
    attempts_left = max(MAX_OTP_ATTEMPTS - row.otp_attempts, 0)
    if row.otp_attempts >= MAX_OTP_ATTEMPTS:
        _revoke_row(
            row,
            now,
            note="Batas percobaan verifikasi OTP tercapai.",
        )
        return True, 0
    row.save(update_fields=["otp_attempts", "updated_at"])
    return False, attempts_left


def verify_otp(user, code):
    raw = str(code or "").strip()
    if (
        len(raw) != OTP_LENGTH
        or not raw.isdigit()
    ):
        return OtpResult("invalid_format")

    now = timezone.now()
    activated = None
    with transaction.atomic():
        row = (
            WhatsAppConnection.objects.select_for_update()
            .select_related("user")
            .filter(
                user=user,
                status=WhatsAppConnection.Status.APPROVED,
            )
            .order_by("-created_at", "-pk")
            .first()
        )
        if row is None:
            return OtpResult("none")
        if not row.user.is_active:
            return OtpResult("inactive", connection=row)
        if not row.otp_hash:
            return OtpResult("no_otp", connection=row)
        if (
            row.otp_expires_at is None
            or row.otp_expires_at <= now
        ):
            _clear_otp(row)
            row.save(
                update_fields=[
                    "otp_hash",
                    "otp_expires_at",
                    "otp_attempts",
                    "updated_at",
                ]
            )
            return OtpResult("expired", connection=row)
        if row.otp_attempts >= MAX_OTP_ATTEMPTS:
            _revoke_row(
                row,
                now,
                note="Batas percobaan verifikasi OTP tercapai.",
            )
            return OtpResult("too_many", connection=row)

        if not check_password(raw, row.otp_hash):
            exceeded, attempts_left = _register_otp_failure(row, now)
            return OtpResult(
                "too_many" if exceeded else "invalid",
                connection=row,
                attempts_left=attempts_left,
            )

        conflict = (
            WhatsAppConnection.objects.filter(
                phone=row.phone,
                status=WhatsAppConnection.Status.ACTIVE,
            )
            .exclude(pk=row.pk)
            .exists()
        )
        if conflict:
            _revoke_row(
                row,
                now,
                note="Nomor WhatsApp sudah aktif pada akun lain.",
            )
            return OtpResult("phone_in_use", connection=row)

        WhatsAppConnection.objects.filter(
            user=user,
            status=WhatsAppConnection.Status.ACTIVE,
        ).exclude(pk=row.pk).update(
            status=WhatsAppConnection.Status.REVOKED,
            active_phone=None,
            revoked_at=now,
            otp_hash=None,
            otp_expires_at=None,
            otp_attempts=0,
            updated_at=now,
        )

        row.status = WhatsAppConnection.Status.ACTIVE
        row.active_phone = row.phone
        row.verified_at = now
        _clear_otp(row)
        try:
            row.save(
                update_fields=[
                    "status",
                    "active_phone",
                    "verified_at",
                    "otp_hash",
                    "otp_expires_at",
                    "otp_attempts",
                    "updated_at",
                ]
            )
        except IntegrityError:
            logger.warning(
                "Nomor WhatsApp bentrok saat verifikasi connection_id=%s",
                row.pk,
            )
            return OtpResult("phone_in_use")
        activated = row

    notify_activated(activated)
    return OtpResult("ok", connection=activated)


def cancel_pending_request(user):
    now = timezone.now()
    with transaction.atomic():
        _current_user(user)
        row = None
        for status in (
            WhatsAppConnection.Status.APPROVED,
            WhatsAppConnection.Status.PENDING,
        ):
            row = _latest_by_status(user, status)
            if row is not None:
                _revoke_row(
                    row,
                    now,
                    note="Dibatalkan oleh pengguna.",
                )
                return DecisionResult("ok", connection=row)
    return DecisionResult("not_pending")


def unlink_active(user):
    now = timezone.now()
    with transaction.atomic():
        _current_user(user)
        row = _active_row(user)
        if row is None:
            return DecisionResult("not_active")
        _revoke_row(row, now, note="Diputus oleh pengguna.")
    return DecisionResult("ok", connection=row)


def resolve_recipient_for_user_id(user_id):
    return (
        WhatsAppConnection.objects.select_related("user")
        .filter(
            user_id=user_id,
            status=WhatsAppConnection.Status.ACTIVE,
            active_phone__isnull=False,
        )
        .exclude(active_phone="")
        .order_by("-verified_at", "-created_at")
        .first()
    )


def resolve_recipient(user):
    if user is None or not getattr(user, "pk", None):
        return None
    return resolve_recipient_for_user_id(user.pk)


def _notify_user_in_app(user, notification_type, title, message, connection):
    Notification.objects.get_or_create(
        user=user,
        type=notification_type,
        reference_type="whatsapp_connection",
        reference_id=connection.pk,
        defaults={"title": title, "message": message},
    )


def notify_librarians(connection):
    User = get_user_model()
    masked = mask_phone(connection.phone)
    message = (
        f"{connection.user.full_name} "
        f"({connection.user.get_role_display()}) mengajukan penautan "
        f"WhatsApp dengan nomor {masked or 'yang terdaftar'}. "
        "Buka Verifikasi WhatsApp untuk menyetujui atau menolak."
    )
    existing = Notification.objects.filter(
        type="WHATSAPP_LINK_REQUEST",
        reference_type="whatsapp_connection",
        reference_id=connection.pk,
    ).exists()
    if existing:
        return
    notifications = [
        Notification(
            user=librarian,
            type="WHATSAPP_LINK_REQUEST",
            title="Permintaan penautan WhatsApp",
            message=message,
            reference_type="whatsapp_connection",
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
        "Pengajuan penautan WhatsApp disetujui. Masukkan kode OTP yang "
        "dikirim ke nomor Anda pada halaman Profil SmartLib untuk "
        "mengaktifkan notifikasi."
    )
    _notify_user_in_app(
        connection.user,
        "WHATSAPP_LINK_APPROVED",
        "Pengajuan WhatsApp disetujui",
        message,
        connection,
    )


def notify_rejected(connection):
    note = connection.rejection_note
    message = "Permintaan penautan WhatsApp ditolak oleh pustakawan."
    if note:
        message = f"{message} Catatan: {note}"
    _notify_user_in_app(
        connection.user,
        "WHATSAPP_LINK_REJECTED",
        "Permintaan penautan WhatsApp ditolak",
        message,
        connection,
    )


def notify_activated(connection):
    message = (
        "Penautan WhatsApp aktif. Nomor ini sekarang menerima "
        "notifikasi perpustakaan."
    )
    _notify_user_in_app(
        connection.user,
        "WHATSAPP_LINK_ACTIVE",
        "WhatsApp berhasil dihubungkan",
        message,
        connection,
    )
    try:
        from .dispatch import enqueue_whatsapp_notification

        enqueue_whatsapp_notification(
            user=connection.user,
            notification_type="WHATSAPP_LINK_ACTIVE",
            title="WhatsApp berhasil dihubungkan",
            message=message,
            reference_type="whatsapp_connection",
            reference_id=connection.pk,
            event_key="activated",
        )
    except Exception:
        logger.warning(
            "Konfirmasi aktivasi WhatsApp gagal dicatat ke outbox.",
            exc_info=True,
        )


def get_connection_context(user):
    rows = WhatsAppConnection.objects.filter(user=user)
    active = rows.filter(
        status=WhatsAppConnection.Status.ACTIVE
    ).order_by("-verified_at", "-created_at").first()
    pending = rows.filter(
        status=WhatsAppConnection.Status.PENDING
    ).order_by("-created_at", "-pk").first()
    approved = rows.filter(
        status=WhatsAppConnection.Status.APPROVED
    ).order_by("-created_at", "-pk").first()
    last = rows.first()

    now = timezone.now()
    state = "none"
    if pending is not None:
        state = "pending"
    elif approved is not None:
        state = "approved"
    elif active is not None:
        state = "active"
    elif last is not None and last.status == WhatsAppConnection.Status.REJECTED:
        state = "rejected"
    elif last is not None and last.status == WhatsAppConnection.Status.REVOKED:
        state = "revoked"

    otp_expired = bool(
        approved is not None
        and (
            approved.otp_expires_at is None
            or approved.otp_expires_at <= now
        )
    )
    approved_phone = ""
    if approved is not None:
        approved_phone = mask_phone(approved.phone)
    pending_phone = ""
    if pending is not None:
        pending_phone = mask_phone(pending.phone)
    active_phone = ""
    if active is not None:
        active_phone = mask_phone(active.active_phone or active.phone)

    last_rejection_note = ""
    if last is not None and last.status == WhatsAppConnection.Status.REJECTED:
        last_rejection_note = last.rejection_note

    return {
        "state": state,
        "active": active,
        "pending": pending,
        "approved": approved,
        "last": last,
        "active_masked_phone": active_phone,
        "pending_masked_phone": pending_phone,
        "approved_masked_phone": approved_phone,
        "otp_expired": otp_expired,
        "rejection_note": last_rejection_note,
        "system_enabled": bool(
            getattr(settings, "WHATSAPP_NOTIFICATIONS_ENABLED", True)
        ),
    }


def should_send(account, notification_type, message=""):
    return should_send_whatsapp(account, notification_type, message)
