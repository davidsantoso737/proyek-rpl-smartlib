from django.core.management.base import BaseCommand, CommandError

from apps.telegram.services import TelegramError, send_text_message


class Command(BaseCommand):
    help = (
        "Kirim satu pesan uji ke Chat ID yang diberikan operator. "
        "Tidak mengirim ke pengguna lain."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--chat-id",
            type=int,
            required=True,
            help="Chat ID tujuan yang diberikan operator.",
        )
        parser.add_argument(
            "--text",
            default="Tes koneksi Telegram SmartLib.",
            help="Isi pesan uji.",
        )

    def handle(self, *args, **options):
        chat_id = options["chat_id"]
        if chat_id <= 0:
            raise CommandError(
                "Chat ID harus angka positif untuk chat pribadi."
            )
        try:
            sent = send_text_message(chat_id, options["text"])
        except TelegramError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(
            self.style.SUCCESS(
                "Pesan uji terkirim ke Chat ID "
                f"{sent.chat_id} (message_id {sent.message_id})."
            )
        )
