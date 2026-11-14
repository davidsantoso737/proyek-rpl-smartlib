from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.whatsapp.dispatch import (
    process_due,
    retry_failed,
    review_summary,
)
from apps.whatsapp.models import WhatsAppOutbox
from apps.whatsapp.services import WhatsAppError


class Command(BaseCommand):
    help = (
        "Kirim notifikasi WhatsApp dari outbox, atau tinjau dan ulangi "
        "pengiriman yang gagal."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Jumlah maksimum outbox yang diproses (1-500).",
        )
        parser.add_argument(
            "--review",
            action="store_true",
            help="Tampilkan ringkasan dan kegagalan tanpa mengirim.",
        )
        parser.add_argument(
            "--retry-failed",
            action="store_true",
            help="Kembalikan semua kegagalan ke antrean.",
        )
        parser.add_argument(
            "--retry-id",
            type=int,
            action="append",
            default=[],
            help="Kembalikan satu outbox gagal berdasarkan ID.",
        )

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 500:
            raise CommandError("Limit harus antara 1 dan 500.")

        if (
            options["review"]
            or options["retry_failed"]
            or options["retry_id"]
        ):
            self._review_and_retry(options)
            return

        if not getattr(
            settings, "WHATSAPP_NOTIFICATIONS_ENABLED", True
        ):
            raise CommandError(
                "Pengiriman WhatsApp sedang dinonaktifkan. "
                "Set WHATSAPP_NOTIFICATIONS_ENABLED=true untuk mengaktifkan."
            )
        if not getattr(settings, "FONNTE_API_TOKEN", ""):
            raise CommandError("FONNTE_API_TOKEN belum diatur.")
        try:
            stats = process_due(limit=options["limit"])
        except WhatsAppError as exc:
            raise CommandError(str(exc)) from None

        self.stdout.write(
            "Outbox diproses: "
            f"{stats['claimed']} diklaim, "
            f"{stats['sent']} terkirim, "
            f"{stats['retry']} menunggu ulang, "
            f"{stats['failed']} gagal, "
            f"{stats['uncertain']} tidak pasti, "
            f"{stats['skipped']} dilewati, "
            f"{stats['stale']} basi ditandai."
        )

    def _review_and_retry(self, options):
        retry_ids = options["retry_id"]
        if options["retry_failed"] or retry_ids:
            retried = retry_failed(ids=retry_ids or None)
            self.stdout.write(
                f"{retried} outbox dikembalikan ke antrean."
            )

        counts, problems = review_summary()
        ordered = [
            WhatsAppOutbox.Status.PENDING,
            WhatsAppOutbox.Status.RETRY,
            WhatsAppOutbox.Status.SENDING,
            WhatsAppOutbox.Status.SENT,
            WhatsAppOutbox.Status.FAILED,
            WhatsAppOutbox.Status.UNCERTAIN,
            WhatsAppOutbox.Status.SKIPPED,
        ]
        self.stdout.write("Ringkasan outbox WhatsApp:")
        for status in ordered:
            self.stdout.write(
                f"  {status.label}: {counts.get(status.value, 0)}"
            )
        if not problems:
            self.stdout.write("Tidak ada pengiriman gagal atau tidak pasti.")
            return
        self.stdout.write("Perlu ditinjau:")
        for outbox in problems:
            self.stdout.write(
                f"  #{outbox.pk} {outbox.notification_type} "
                f"[{outbox.status}] pengguna={outbox.user.username} "
                f"percobaan={outbox.attempts} "
                f"alasan={outbox.last_error or '-'}"
            )
