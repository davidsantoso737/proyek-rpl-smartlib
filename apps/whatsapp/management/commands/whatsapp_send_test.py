from django.core.management.base import BaseCommand, CommandError

from apps.whatsapp.services import (
    WhatsAppError,
    mask_phone,
    send_text_message,
)


class Command(BaseCommand):
    help = (
        "Kirim satu pesan uji ke nomor WhatsApp yang diberikan operator. "
        "Tidak mengirim ke pengguna lain."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--phone",
            required=True,
            help="Nomor tujuan yang diberikan operator, misalnya 08xxxxxxxxxx.",
        )
        parser.add_argument(
            "--text",
            default="Tes koneksi WhatsApp SmartLib.",
            help="Isi pesan uji.",
        )

    def handle(self, *args, **options):
        try:
            sent = send_text_message(options["phone"], options["text"])
        except WhatsAppError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(
            self.style.SUCCESS(
                "Pesan uji terkirim ke "
                f"{mask_phone(sent.phone)} "
                f"(ID pesan {sent.provider_message_id or '-'})."
            )
        )
