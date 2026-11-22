from django.core.management.base import BaseCommand

from apps.circulation.services import expire_pickup_loans


class Command(BaseCommand):
    help = (
        "Batalkan pengajuan APPROVED yang QR Pickup-nya sudah "
        "kedaluwarsa. Idempoten dan aman dijalankan berkala."
    )

    def handle(self, *args, **options):
        cancelled = expire_pickup_loans()
        self.stdout.write(
            self.style.SUCCESS(
                f"{cancelled} pengajuan kedaluwarsa dibatalkan."
            )
        )
