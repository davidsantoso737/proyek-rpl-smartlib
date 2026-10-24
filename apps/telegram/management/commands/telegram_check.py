from django.core.management.base import BaseCommand, CommandError

from apps.telegram.services import TelegramError, check_connection


class Command(BaseCommand):
    help = (
        "Periksa koneksi bot Telegram dengan getMe. "
        "Tidak mengirim pesan ke pengguna."
    )

    def handle(self, *args, **options):
        try:
            bot = check_connection()
        except TelegramError as exc:
            raise CommandError(str(exc)) from None
        username = f"@{bot.username}" if bot.username else "(tanpa username)"
        self.stdout.write(
            self.style.SUCCESS(
                f"Koneksi Telegram berhasil: {username} (ID {bot.id})."
            )
        )
