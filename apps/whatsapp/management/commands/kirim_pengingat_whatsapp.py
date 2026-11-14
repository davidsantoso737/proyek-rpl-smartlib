from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.whatsapp.dispatch import process_due
from apps.whatsapp.reminders import enqueue_due_reminders
from apps.whatsapp.services import WhatsAppError


class Command(BaseCommand):
    help = (
        "Buat dan kirim pengingat WhatsApp H-3, H-1, dan keterlambatan "
        "untuk peminjaman fisik yang masih aktif."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Jumlah maksimum outbox yang dikirim (1-500).",
        )
        parser.add_argument(
            "--no-send",
            action="store_true",
            help="Hanya catat pengingat ke outbox tanpa mengirim.",
        )

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 500:
            raise CommandError("Limit harus antara 1 dan 500.")
        if not getattr(
            settings, "WHATSAPP_NOTIFICATIONS_ENABLED", True
        ):
            raise CommandError(
                "Pengiriman WhatsApp sedang dinonaktifkan. "
                "Set WHATSAPP_NOTIFICATIONS_ENABLED=true untuk mengaktifkan."
            )

        summary = enqueue_due_reminders()
        if options["no_send"]:
            self.stdout.write(
                f"Pengingat dicatat: {summary['reminders']} ringkasan, "
                f"{summary['books']} buku, {summary['queued']} di antrean."
            )
            return

        if not getattr(settings, "FONNTE_API_TOKEN", ""):
            raise CommandError("FONNTE_API_TOKEN belum diatur.")
        try:
            stats = process_due(limit=options["limit"])
        except WhatsAppError as exc:
            raise CommandError(str(exc)) from None

        self.stdout.write(
            self.style.SUCCESS(
                f"Pengingat dicatat: {summary['reminders']} ringkasan, "
                f"{summary['books']} buku. Pengiriman: "
                f"{stats['sent']} terkirim, "
                f"{stats['retry']} menunggu ulang, "
                f"{stats['failed']} gagal, "
                f"{stats['uncertain']} tidak pasti, "
                f"{stats['skipped']} dilewati."
            )
        )
