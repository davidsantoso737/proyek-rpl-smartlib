from django.conf import settings
from django.db import models


class TelegramAccount(models.Model):
    class Category(models.TextChoices):
        LOAN = "LOAN", "Peminjaman berhasil"
        RETURN = "RETURN", "Pengembalian berhasil"
        FINE = "FINE", "Informasi atau perubahan denda"
        DUE_SOON = "DUE_SOON", "Pengingat jatuh tempo"
        OVERDUE = "OVERDUE", "Informasi keterlambatan"

    CATEGORY_FIELDS = {
        Category.LOAN: "notify_loans",
        Category.RETURN: "notify_returns",
        Category.FINE: "notify_fines",
        Category.DUE_SOON: "notify_due_soon",
        Category.OVERDUE: "notify_overdue",
    }

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="telegram_account",
    )
    telegram_user_id = models.BigIntegerField(unique=True)
    chat_id = models.BigIntegerField(unique=True)
    username = models.CharField(max_length=64, blank=True, default="")
    first_name = models.CharField(max_length=255, blank=True, default="")
    notifications_enabled = models.BooleanField(default=True)
    notify_loans = models.BooleanField(default=True)
    notify_returns = models.BooleanField(default=True)
    notify_fines = models.BooleanField(default=True)
    notify_due_soon = models.BooleanField(default=True)
    notify_overdue = models.BooleanField(default=True)
    linked_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "telegram_accounts"

    def __str__(self):
        return f"Telegram untuk pengguna {self.user_id}"

    def allows_category(self, category):
        if not self.notifications_enabled:
            return False
        field_name = self.CATEGORY_FIELDS.get(category)
        if not field_name:
            return False
        return bool(getattr(self, field_name))


class TelegramLinkToken(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="telegram_link_tokens",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    claimed_at = models.DateTimeField(null=True, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    telegram_user_id = models.BigIntegerField(null=True, blank=True)
    chat_id = models.BigIntegerField(null=True, blank=True)
    telegram_username = models.CharField(
        max_length=64, blank=True, default=""
    )
    telegram_first_name = models.CharField(
        max_length=255, blank=True, default=""
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "telegram_link_tokens"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "expires_at"]),
        ]

    def __str__(self):
        return f"Token Telegram pengguna {self.user_id}"


class TelegramBotState(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    last_update_id = models.BigIntegerField(default=0)
    lease_owner = models.CharField(max_length=128, blank=True, default="")
    lease_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "telegram_bot_state"

    def __str__(self):
        return "Status bot Telegram"


class TelegramOutbox(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Menunggu"
        SENDING = "SENDING", "Sedang dikirim"
        RETRY = "RETRY", "Menunggu percobaan ulang"
        SENT = "SENT", "Terkirim"
        FAILED = "FAILED", "Gagal permanen"
        UNCERTAIN = "UNCERTAIN", "Status pengiriman tidak pasti"
        SKIPPED = "SKIPPED", "Tidak dikirim"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="telegram_outbox",
    )
    notification_type = models.CharField(max_length=50)
    title = models.CharField(max_length=255)
    message = models.TextField()
    reference_type = models.CharField(
        max_length=50, null=True, blank=True
    )
    reference_id = models.BigIntegerField(null=True, blank=True)
    event_key = models.CharField(max_length=100)
    dedup_key = models.CharField(max_length=64, unique=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField()
    last_error = models.TextField(blank=True, default="")
    locked_until = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    telegram_message_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "telegram_outbox"
        ordering = ["created_at", "pk"]
        indexes = [
            models.Index(
                fields=["status", "next_attempt_at"],
                name="telegram_outbox_due_idx",
            ),
        ]

    def __str__(self):
        return (
            f"{self.notification_type} untuk pengguna "
            f"{self.user_id} ({self.status})"
        )
