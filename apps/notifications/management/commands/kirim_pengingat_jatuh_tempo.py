from django.core.management.base import BaseCommand

from apps.notifications.services import sync_due_overdue


class Command(BaseCommand):
    help = (
        "Buat notifikasi jatuh tempo (H-2) dan keterlambatan "
        "untuk peminjaman aktif. Idempoten; aman dijalankan berkala."
    )

    def handle(self, *args, **options):
        due, overdue = sync_due_overdue()
        self.stdout.write(
            self.style.SUCCESS(
                f"Notifikasi dibuat: {due} jatuh tempo, "
                f"{overdue} terlambat."
            )
        )
