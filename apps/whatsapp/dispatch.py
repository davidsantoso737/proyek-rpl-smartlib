"""Outbox notifikasi WhatsApp untuk kejadian transaksi.

Pencatatan outbox dilakukan di dalam transaksi bisnis tanpa memanggil
Fonnte. Pengiriman berjalan terpisah lewat management command.
"""

import hashlib
import logging
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, transaction
from django.db.models import Count
from django.utils import timezone

from .connection import (
    resolve_recipient,
    resolve_recipient_for_user_id,
)
from .models import WhatsAppOutbox
from .preferences import should_send_whatsapp
from .services import (
    WhatsAppAPIError,
    WhatsAppConfigError,
    WhatsAppNetworkError,
    WhatsAppResponseError,
    WhatsAppTimeoutError,
    WhatsAppValidationError,
    get_client,
)

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
RETRY_BASE_SECONDS = 60
RETRY_MAX_SECONDS = 3600
STALE_SEND_SECONDS = 120


def build_dedup_key(
    *, user_id, notification_type, reference_type, reference_id, event_key
):
    raw = "|".join(
        [
            str(user_id),
            str(notification_type),
            str(reference_type or ""),
            str(reference_id if reference_id is not None else ""),
            str(event_key),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def enqueue_whatsapp_notification(
    *,
    user,
    notification_type,
    title,
    message,
    reference_type="",
    reference_id=None,
    event_key="default",
):
    """Catat satu kejadian ke outbox bila penerima mengizinkannya.

    Fungsi ini tidak menghubungi Fonnte dan aman dipanggil di dalam
    transaksi bisnis. Kejadian yang sama tidak dicatat dua kali.
    """
    if user is None or not getattr(user, "pk", None):
        return None
    account = resolve_recipient(user)
    if not should_send_whatsapp(account, notification_type, message):
        return None

    dedup_key = build_dedup_key(
        user_id=user.pk,
        notification_type=notification_type,
        reference_type=reference_type,
        reference_id=reference_id,
        event_key=event_key,
    )
    try:
        with transaction.atomic():
            outbox, _ = WhatsAppOutbox.objects.get_or_create(
                dedup_key=dedup_key,
                defaults={
                    "user": user,
                    "notification_type": notification_type,
                    "title": (title or "").strip()[:255],
                    "message": (message or "").strip(),
                    "reference_type": reference_type or None,
                    "reference_id": reference_id,
                    "event_key": event_key,
                    "next_attempt_at": timezone.now(),
                },
            )
    except DatabaseError:
        logger.warning(
            "Outbox WhatsApp gagal dicatat type=%s", notification_type
        )
        return None
    return outbox


def _claim(outbox, now):
    locked_until = now + timedelta(
        seconds=int(getattr(settings, "FONNTE_TIMEOUT_SECONDS", 15))
        + STALE_SEND_SECONDS
    )
    claimed = WhatsAppOutbox.objects.filter(
        pk=outbox.pk,
        status=outbox.status,
    ).update(
        status=WhatsAppOutbox.Status.SENDING,
        attempts=outbox.attempts + 1,
        locked_until=locked_until,
        updated_at=now,
    )
    if not claimed:
        return None
    outbox.refresh_from_db()
    return outbox


def _mark_stale_sending(now):
    return WhatsAppOutbox.objects.filter(
        status=WhatsAppOutbox.Status.SENDING,
        locked_until__lt=now,
    ).update(
        status=WhatsAppOutbox.Status.UNCERTAIN,
        last_error=(
            "Proses pengiriman berhenti sebelum status tercatat. "
            "Status pengiriman tidak pasti."
        ),
        locked_until=None,
        updated_at=now,
    )


def _finish(outbox, status, now, *, error="", message_id=None):
    fields = {
        "status": status,
        "last_error": error,
        "locked_until": None,
        "updated_at": now,
    }
    if status == WhatsAppOutbox.Status.SENT:
        fields["sent_at"] = now
        fields["provider_message_id"] = message_id or ""
    WhatsAppOutbox.objects.filter(pk=outbox.pk).update(**fields)


def _schedule_retry(outbox, now, error, retry_after=None):
    if outbox.attempts >= MAX_ATTEMPTS:
        _finish(
            outbox,
            WhatsAppOutbox.Status.FAILED,
            now,
            error=f"{error} Batas percobaan tercapai.",
        )
        return WhatsAppOutbox.Status.FAILED
    delay = retry_after or min(
        RETRY_MAX_SECONDS,
        RETRY_BASE_SECONDS * (2 ** max(outbox.attempts - 1, 0)),
    )
    WhatsAppOutbox.objects.filter(pk=outbox.pk).update(
        status=WhatsAppOutbox.Status.RETRY,
        next_attempt_at=now + timedelta(seconds=delay),
        last_error=error,
        locked_until=None,
        updated_at=now,
    )
    return WhatsAppOutbox.Status.RETRY


def _deliver(outbox, client, now=None):
    moment = now or timezone.now()
    account = resolve_recipient_for_user_id(outbox.user_id)
    if account is None or not account.active_phone:
        _finish(
            outbox,
            WhatsAppOutbox.Status.SKIPPED,
            moment,
            error="Penerima tidak memiliki nomor WhatsApp aktif.",
        )
        return WhatsAppOutbox.Status.SKIPPED
    if not should_send_whatsapp(
        account, outbox.notification_type, outbox.message
    ):
        _finish(
            outbox,
            WhatsAppOutbox.Status.SKIPPED,
            moment,
            error="Penerima tidak lagi mengizinkan notifikasi ini.",
        )
        return WhatsAppOutbox.Status.SKIPPED

    text = f"{outbox.title}\n\n{outbox.message}".strip()
    try:
        sent = client.send_message(account.active_phone, text)
    except WhatsAppTimeoutError:
        _finish(
            outbox,
            WhatsAppOutbox.Status.UNCERTAIN,
            moment,
            error=(
                "Batas waktu Fonnte tercapai. Status pengiriman tidak "
                "pasti; tinjau sebelum mencoba lagi."
            ),
        )
        return WhatsAppOutbox.Status.UNCERTAIN
    except WhatsAppResponseError:
        _finish(
            outbox,
            WhatsAppOutbox.Status.UNCERTAIN,
            moment,
            error=(
                "Respons Fonnte tidak valid. Status pengiriman tidak "
                "pasti; tinjau sebelum mencoba lagi."
            ),
        )
        return WhatsAppOutbox.Status.UNCERTAIN
    except WhatsAppAPIError as exc:
        if exc.code is not None and exc.code >= 500:
            return _schedule_retry(
                outbox, moment, "Fonnte mengalami gangguan sementara."
            )
        if exc.code == 429:
            return _schedule_retry(
                outbox,
                moment,
                "Fonnte membatasi laju pengiriman.",
                retry_after=5,
            )
        if exc.code in {401, 403}:
            _finish(
                outbox,
                WhatsAppOutbox.Status.FAILED,
                moment,
                error="Token atau akses Fonnte ditolak.",
            )
            return WhatsAppOutbox.Status.FAILED
        _finish(
            outbox,
            WhatsAppOutbox.Status.FAILED,
            moment,
            error="Permintaan Fonnte ditolak.",
        )
        return WhatsAppOutbox.Status.FAILED
    except WhatsAppValidationError:
        _finish(
            outbox,
            WhatsAppOutbox.Status.FAILED,
            moment,
            error="Data pengiriman WhatsApp tidak valid.",
        )
        return WhatsAppOutbox.Status.FAILED
    except WhatsAppConfigError:
        _finish(
            outbox,
            WhatsAppOutbox.Status.FAILED,
            moment,
            error="Konfigurasi Fonnte tidak valid.",
        )
        return WhatsAppOutbox.Status.FAILED
    except WhatsAppNetworkError:
        return _schedule_retry(
            outbox,
            moment,
            "Fonnte tidak dapat dihubungi.",
        )
    except Exception:
        logger.warning(
            "Outbox WhatsApp gagal tak terduga type=%s",
            outbox.notification_type,
        )
        return _schedule_retry(
            outbox,
            moment,
            "Kegagalan pengiriman tidak terduga.",
        )

    _finish(
        outbox,
        WhatsAppOutbox.Status.SENT,
        moment,
        message_id=sent.provider_message_id,
    )
    WhatsAppOutbox.objects.filter(pk=outbox.pk).update(
        recipient_phone=sent.phone
    )
    return WhatsAppOutbox.Status.SENT


def process_due(limit=50, *, now=None, client=None):
    """Kirim outbox yang jatuh tempo tanpa pengiriman ganda."""
    if limit <= 0:
        return {
            "claimed": 0,
            "sent": 0,
            "retry": 0,
            "failed": 0,
            "uncertain": 0,
            "skipped": 0,
            "stale": 0,
        }
    moment = now or timezone.now()
    if not getattr(settings, "WHATSAPP_NOTIFICATIONS_ENABLED", True):
        raise WhatsAppConfigError(
            "Pengiriman WhatsApp sedang dinonaktifkan."
        )
    sender = client or get_client()
    stats = {
        "claimed": 0,
        "sent": 0,
        "retry": 0,
        "failed": 0,
        "uncertain": 0,
        "skipped": 0,
        "stale": _mark_stale_sending(moment),
    }
    candidates = list(
        WhatsAppOutbox.objects.filter(
            status__in=[
                WhatsAppOutbox.Status.PENDING,
                WhatsAppOutbox.Status.RETRY,
            ],
            next_attempt_at__lte=moment,
        ).order_by("next_attempt_at", "created_at")[:limit]
    )
    for candidate in candidates:
        outbox = _claim(candidate, moment)
        if outbox is None:
            continue
        stats["claimed"] += 1
        status = _deliver(outbox, sender, moment)
        if status == WhatsAppOutbox.Status.SENT:
            stats["sent"] += 1
        elif status == WhatsAppOutbox.Status.RETRY:
            stats["retry"] += 1
        elif status == WhatsAppOutbox.Status.FAILED:
            stats["failed"] += 1
        elif status == WhatsAppOutbox.Status.UNCERTAIN:
            stats["uncertain"] += 1
        elif status == WhatsAppOutbox.Status.SKIPPED:
            stats["skipped"] += 1
    return stats


def retry_failed(*, ids=None, now=None):
    """Kembalikan kegagalan ke antrean dengan hitungan percobaan baru."""
    moment = now or timezone.now()
    queryset = WhatsAppOutbox.objects.filter(
        status__in=[
            WhatsAppOutbox.Status.FAILED,
            WhatsAppOutbox.Status.UNCERTAIN,
        ]
    )
    if ids:
        queryset = queryset.filter(pk__in=ids)
    return queryset.update(
        status=WhatsAppOutbox.Status.PENDING,
        attempts=0,
        next_attempt_at=moment,
        last_error="",
        locked_until=None,
        sent_at=None,
        recipient_phone="",
        provider_message_id="",
        updated_at=moment,
    )


def review_summary(limit=20):
    counts = {
        row["status"]: row["total"]
        for row in WhatsAppOutbox.objects.values("status").annotate(
            total=Count("pk")
        )
    }
    problems = list(
        WhatsAppOutbox.objects.filter(
            status__in=[
                WhatsAppOutbox.Status.FAILED,
                WhatsAppOutbox.Status.UNCERTAIN,
            ]
        )
        .select_related("user")
        .order_by("-updated_at")[:limit]
    )
    return counts, problems
