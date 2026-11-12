from django.apps import AppConfig


class TelegramConfig(AppConfig):
    name = "apps.telegram"
    verbose_name = "Telegram"

    def ready(self):
        # Daftarkan sinyal pembuatan pengajuan otomatis.
        from . import signals  # noqa: F401
