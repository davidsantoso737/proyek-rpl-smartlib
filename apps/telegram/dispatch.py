"""Outbox notifikasi Telegram untuk kejadian transaksi.

Pencatatan outbox dilakukan di dalam transaksi bisnis tanpa memanggil
Telegram. Pengiriman berjalan terpisah lewat management command.
"""

import hashlib
import logging
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, transaction
from django.db.models import Count
from django.utils import timezone

from .models import TelegramAccount, TelegramOutbox
from .preferences import should_send_telegram
from .services import (
    TelegramAPIError,
    TelegramConfigError,
    TelegramNetworkError,
    TelegramResponseError,
    TelegramTimeoutError,
    TelegramValidationError,
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


def enqueue_telegram_notification(
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

    Fungsi ini tidak menghubungi Telegram dan aman dipanggil di dalam
    transaksi bisnis. Kejadian yang sama tidak dicatat dua kali.
    """
    if user is None or not getattr(user, "pk", None):
        return None
    account = (
        TelegramAccount.objects.select_related("user")
        .filter(user_id=user.pk)
        .first()
    )
    if not should_send_telegram(account, notification_type, message):
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
            outbox, _ = TelegramOutbox.objects.get_or_create(
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
            "Outbox Telegram gagal dicatat type=%s", notification_type
        )
        return None
    return outbox


def _claim(outbox, now):
    locked_until = now + timedelta(
        seconds=int(getattr(settings, "TELEGRAM_TIMEOUT_SECONDS", 10))
        + STALE_SEND_SECONDS
    )
    claimed = TelegramOutbox.objects.filter(
        pk=outbox.pk,
        status=outbox.status,
    ).update(
        status=TelegramOutbox.Status.SENDING,
        attempts=outbox.attempts + 1,
        locked_until=locked_until,
        updated_at=now,
    )
    if not claimed:
        return None
    outbox.refresh_from_db()
    return outbox


def _mark_stale_sending(now):
    cutoff = now - timedelta(seconds=STALE_SEND_SECONDS)
    return TelegramOutbox.objects.filter(
        status=TelegramOutbox.Status.SENDING,
        locked_until__lt=cutoff,
    ).update(
        status=TelegramOutbox.Status.UNCERTAIN,
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
    if status == TelegramOutbox.Status.SENT:
        fields["sent_at"] = now
        fields["telegram_message_id"] = message_id
    TelegramOutbox.objects.filter(pk=outbox.pk).update(**fields)


def _schedule_retry(outbox, now, error, retry_after=None):
    if outbox.attempts >= MAX_ATTEMPTS:
        _finish(
            outbox,
            TelegramOutbox.Status.FAILED,
            now,
            error=f"{error} Batas percobaan tercapai.",
        )
        return
    delay = retry_after or min(
        RETRY_MAX_SECONDS,
        RETRY_BASE_SECONDS * (2 ** max(outbox.attempts - 1, 0)),
    )
    TelegramOutbox.objects.filter(pk=outbox.pk).update(
        status=TelegramOutbox.Status.RETRY,
        next_attempt_at=now + timedelta(seconds=delay),
        last_error=error,
        locked_until=None,
        updated_at=now,
    )


def _deliver(outbox, client):
    account = (
        TelegramAccount.objects.select_related("user")
        .filter(user_id=outbox.user_id)
        .first()
    )
    if not should_send_telegram(
        account, outbox.notification_type, outbox.message
    ):
        _finish(
            outbox,
            TelegramOutbox.Status.SKIPPED,
            timezone.now(),
            error="Penerima tidak lagi mengizinkan notifikasi ini.",
        )
        return TelegramOutbox.Status.SKIPPED

    text = f"{outbox.title}\n\n{outbox.message}".strip()
    try:
        sent = client.send_message(account.chat_id, text)
    except TelegramTimeoutError:
        _finish(
            outbox,
            TelegramOutbox.Status.UNCERTAIN,
            timezone.now(),
            error=(
                "Batas waktu Telegram tercapai. Status pengiriman tidak "
                "pasti; tinjau sebelum mencoba lagi."
            ),
        )
        return TelegramOutbox.Status.UNCERTAIN
    except TelegramResponseError:
        _finish(
            outbox,
            TelegramOutbox.Status.UNCERTAIN,
            timezone.now(),
            error=(
                "Respons Telegram tidak valid. Status pengiriman tidak "
                "pasti; tinjau sebelum mencoba lagi."
            ),
        )
        return TelegramOutbox.Status.UNCERTAIN
    except TelegramAPIError as exc:
        now = timezone.now()
        if exc.code == 429:
            _schedule_retry(
                outbox,
                now,
                "Telegram membatasi laju pengiriman.",
                retry_after=max(exc.retry_after or 5, 1),
            )
            return TelegramOutbox.Status.RETRY
        if exc.code in (400, 401, 403):
            reasons = {
                400: "Permintaan Telegram ditolak.",
                401: "Token bot Telegram ditolak.",
                403: "Bot diblokir atau chat tidak tersedia.",
            }
            _finish(
                outbox,
                TelegramOutbox.Status.FAILED,
                now,
                error=reasons[exc.code],
            )
            return TelegramOutbox.Status.FAILED
        if exc.code is not None and exc.code >= 500:
            _schedule_retry(
                outbox, now, "Telegram mengalami gangguan sementara."
            )
            return TelegramOutbox.Status.RETRY
        _finish(
            outbox,
            TelegramOutbox.Status.FAILED,
            now,
            error="Permintaan Telegram ditolak.",
        )
        return TelegramOutbox.Status.FAILED
    except TelegramValidationError:
        _finish(
            outbox,
            TelegramOutbox.Status.FAILED,
            timezone.now(),
            error="Data pengiriman Telegram tidak valid.",
        )
        return TelegramOutbox.Status.FAILED
    except TelegramConfigError:
        _finish(
            outbox,
            TelegramOutbox.Status.FAILED,
            timezone.now(),
            error="Konfigurasi Telegram tidak valid.",
        )
        return TelegramOutbox.Status.FAILED
    except TelegramNetworkError:
        _schedule_retry(
            outbox,
            timezone.now(),
            "Telegram tidak dapat dihubungi.",
        )
        return TelegramOutbox.Status.RETRY
    except Exception:
        logger.warning(
            "Outbox Telegram gagal tak terduga type=%s",
            outbox.notification_type,
        )
        _schedule_retry(
            outbox,
            timezone.now(),
            "Kegagalan pengiriman tidak terduga.",
        )
        return TelegramOutbox.Status.RETRY

    _finish(
        outbox,
        TelegramOutbox.Status.SENT,
        timezone.now(),
        message_id=sent.message_id,
    )
    return TelegramOutbox.Status.SENT


def process_due(limit=50, *, now=None, client=None):
    """Kirim outbox yang jatuh tempo. Tidak pernah mengirim dua kali
    untuk dedup key yang sama."""
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
    if not getattr(settings, "TELEGRAM_NOTIFICATIONS_ENABLED", False):
        raise TelegramConfigError(
            "Pengiriman Telegram sedang dinonaktifkan."
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
        TelegramOutbox.objects.filter(
            status__in=[
                TelegramOutbox.Status.PENDING,
                TelegramOutbox.Status.RETRY,
            ],
            next_attempt_at__lte=moment,
        ).order_by("next_attempt_at", "created_at")[:limit]
    )
    for candidate in candidates:
        outbox = _claim(candidate, moment)
        if outbox is None:
            continue
        stats["claimed"] += 1
        status = _deliver(outbox, sender)
        if status == TelegramOutbox.Status.SENT:
            stats["sent"] += 1
        elif status == TelegramOutbox.Status.RETRY:
            stats["retry"] += 1
        elif status == TelegramOutbox.Status.FAILED:
            stats["failed"] += 1
        elif status == TelegramOutbox.Status.UNCERTAIN:
            stats["uncertain"] += 1
        elif status == TelegramOutbox.Status.SKIPPED:
            stats["skipped"] += 1
    return stats


def retry_failed(*, ids=None, now=None):
    """Kembalikan kegagalan ke antrean dengan hitungan percobaan baru."""
    moment = now or timezone.now()
    queryset = TelegramOutbox.objects.filter(
        status__in=[
            TelegramOutbox.Status.FAILED,
            TelegramOutbox.Status.UNCERTAIN,
        ]
    )
    if ids:
        queryset = queryset.filter(pk__in=ids)
    return queryset.update(
        status=TelegramOutbox.Status.PENDING,
        attempts=0,
        next_attempt_at=moment,
        last_error="",
        locked_until=None,
        sent_at=None,
        telegram_message_id=None,
        updated_at=moment,
    )


def review_summary(limit=20):
    counts = {
        row["status"]: row["total"]
        for row in TelegramOutbox.objects.values("status").annotate(
            total=Count("pk")
        )
    }
    problems = list(
        TelegramOutbox.objects.filter(
            status__in=[
                TelegramOutbox.Status.FAILED,
                TelegramOutbox.Status.UNCERTAIN,
            ]
        )
        .select_related("user")
        .order_by("-updated_at")[:limit]
    )
    return counts, problems
