from django.core.management.base import BaseCommand, CommandError

from apps.whatsapp.services import WhatsAppError, check_configuration


class Command(BaseCommand):
    help = (
        "Periksa konfigurasi Fonnte tanpa mengirim pesan ke pengguna."
    )

    def handle(self, *args, **options):
        try:
            config = check_configuration()
        except WhatsAppError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(
            self.style.SUCCESS(
                "Konfigurasi WhatsApp siap: "
                f"endpoint={config['api_url']} "
                f"timeout={config['timeout']}s "
                f"kode_negara={config['country_code']}."
            )
        )
