import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.telegram.bot import (
    acquire_bot_lease,
    get_last_update_id,
    make_lease_owner,
    process_updates,
    release_bot_lease,
    renew_bot_lease,
)
from apps.telegram.services import (
    TelegramAPIError,
    TelegramError,
    TelegramNetworkError,
    get_client,
)


class Command(BaseCommand):
    help = (
        "Jalankan long polling Telegram. Hanya satu poller yang boleh "
        "aktif pada satu waktu."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Ambil satu batch update lalu berhenti.",
        )
        parser.add_argument(
            "--poll-timeout",
            type=int,
            default=25,
            help="Batas long polling getUpdates dalam detik (0-50).",
        )
        parser.add_argument(
            "--lease-seconds",
            type=int,
            default=90,
            help="Durasi lease poller dalam detik.",
        )

    def handle(self, *args, **options):
        if not getattr(settings, "TELEGRAM_BOT_TOKEN", ""):
            raise CommandError("TELEGRAM_BOT_TOKEN belum diatur.")

        once = options["once"]
        poll_timeout = options["poll_timeout"]
        if not 0 <= poll_timeout <= 50:
            raise CommandError(
                "Poll timeout harus antara 0 dan 50 detik."
            )
        effective_timeout = 0 if once else poll_timeout
        request_margin = int(
            getattr(settings, "TELEGRAM_TIMEOUT_SECONDS", 10)
        )
        lease_seconds = max(
            options["lease_seconds"],
            effective_timeout + request_margin + 30,
        )

        owner = make_lease_owner()
        if not acquire_bot_lease(owner, lease_seconds):
            raise CommandError(
                "Poller Telegram lain sedang aktif. Hentikan poller itu "
                "sebelum menjalankan yang baru."
            )

        self.stdout.write(
            "Poller Telegram aktif. Tekan Ctrl+C untuk berhenti."
        )
        try:
            try:
                client = get_client()
            except TelegramError as exc:
                raise CommandError(str(exc)) from None
            while True:
                if not renew_bot_lease(owner, lease_seconds):
                    raise CommandError(
                        "Lease poller Telegram hilang. Poller dihentikan."
                    )
                try:
                    updates = client.get_updates(
                        offset=get_last_update_id() + 1,
                        timeout=effective_timeout,
                        allowed_updates=["message"],
                    )
                except TelegramAPIError as exc:
                    if exc.code == 409:
                        raise CommandError(
                            "Telegram melaporkan konflik polling. "
                            "Pastikan webhook tidak aktif dan tidak ada "
                            "poller lain."
                        ) from None
                    if exc.code == 401:
                        raise CommandError(
                            "Token bot Telegram ditolak."
                        ) from None
                    if exc.code == 429 and not once:
                        wait = max(exc.retry_after or 5, 1)
                        time.sleep(wait)
                        continue
                    raise CommandError(str(exc)) from None
                except TelegramNetworkError as exc:
                    if once:
                        raise CommandError(str(exc)) from None
                    self.stderr.write(
                        self.style.WARNING(
                            "Telegram tidak tersedia, mencoba lagi."
                        )
                    )
                    time.sleep(2)
                    continue
                except TelegramError as exc:
                    raise CommandError(str(exc)) from None

                process_updates(updates, client=client)
                if once:
                    break
        except KeyboardInterrupt:
            self.stdout.write("Poller Telegram dihentikan.")
        finally:
            release_bot_lease(owner)
