"""Pengingat jatuh tempo untuk peminjaman fisik aktif.

Pemeriksaan memakai tanggal lokal sesuai TIME_ZONE Django. Pengingat
dikelompokkan per transaksi pinjam dan periode, lalu dicatat ke outbox
yang sama dengan notifikasi transaksi.
"""

from django.utils import timezone

from apps.circulation.models import Loan

from .dispatch import enqueue_telegram_notification
from .formatting import format_reminder

LOAN_ACTIVE = "ACTIVE"

REMINDER_H3 = "H3"
REMINDER_H1 = "H1"
REMINDER_OVERDUE = "OVERDUE"

REMINDER_CONFIG = {
    REMINDER_H3: {
        "notification_type": "LOAN_DUE_SOON",
        "event_key": "due_3",
    },
    REMINDER_H1: {
        "notification_type": "LOAN_DUE_SOON",
        "event_key": "due_1",
    },
    REMINDER_OVERDUE: {
        "notification_type": "LOAN_OVERDUE",
        "event_key": "overdue",
    },
}


def _local_date(value):
    if timezone.is_naive(value):
        value = timezone.make_aware(value)
    return timezone.localtime(value).date()


def _bucket_for(days_left):
    if days_left == 3:
        return REMINDER_H3
    if days_left == 1:
        return REMINDER_H1
    if days_left < 0:
        return REMINDER_OVERDUE
    return None


def _event_key(bucket, entries):
    dates = sorted({due_date.isoformat() for _, due_date, _ in entries})
    base = REMINDER_CONFIG[bucket]["event_key"]
    return f"{base}:{','.join(dates)}"


def collect_due_reminders(now=None):
    """Kumpulkan ringkasan pengingat yang memenuhi syarat.

    Hanya item yang belum dikembalikan pada pinjaman ACTIVE yang
    dipertimbangkan. Beberapa buku dalam satu transaksi digabung.
    """
    moment = now or timezone.now()
    today = timezone.localdate(moment)
    loans = (
        Loan.objects.filter(status=LOAN_ACTIVE)
        .select_related("user")
        .prefetch_related("items__book_copy__book")
        .order_by("pk")
    )
    reminders = []
    for loan in loans:
        groups = {}
        for item in loan.items.all():
            if item.returned_at is not None:
                continue
            due_at = item.due_at or loan.due_at
            if due_at is None:
                continue
            due_date = _local_date(due_at)
            days_left = (due_date - today).days
            bucket = _bucket_for(days_left)
            if bucket is None:
                continue
            groups.setdefault(bucket, []).append(
                (item, due_date, days_left)
            )

        for bucket, entries in groups.items():
            entries.sort(
                key=lambda entry: (
                    entry[1],
                    entry[0].book_copy.book.title,
                    entry[0].book_copy.copy_code,
                )
            )
            reminders.append(
                {
                    "loan": loan,
                    "bucket": bucket,
                    "entries": entries,
                    "event_key": _event_key(bucket, entries),
                }
            )
    return reminders


def enqueue_due_reminders(now=None):
    """Catat pengingat yang memenuhi syarat ke outbox Telegram."""
    reminders = collect_due_reminders(now=now)
    stats = {
        "reminders": len(reminders),
        "books": sum(len(item["entries"]) for item in reminders),
        "queued": 0,
    }
    for reminder in reminders:
        bucket = reminder["bucket"]
        config = REMINDER_CONFIG[bucket]
        telegram_message = format_reminder(
            bucket,
            reminder["loan"].user,
            reminder["entries"],
        )
        outbox = enqueue_telegram_notification(
            user=reminder["loan"].user,
            notification_type=config["notification_type"],
            title=telegram_message.title,
            message=telegram_message.message,
            reference_type="loan",
            reference_id=reminder["loan"].pk,
            event_key=reminder["event_key"],
        )
        if outbox is not None:
            stats["queued"] += 1
    return stats
