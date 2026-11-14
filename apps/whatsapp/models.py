from django.conf import settings
from django.db import models


class WhatsAppConnection(models.Model):
    """Riwayat penautan nomor WhatsApp milik satu pengguna.

    Satu pengguna dapat memiliki beberapa baris riwayat. Baris yang
    berstatus ``active`` menyimpan nomornya di ``active_phone`` yang
    unik, sehingga satu nomor tidak dapat aktif pada dua akun.
    """

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

    class Status(models.TextChoices):
        PENDING = "pending", "Menunggu persetujuan pustakawan"
        APPROVED = "approved", "Menunggu verifikasi OTP"
        ACTIVE = "active", "Aktif"
        REJECTED = "rejected", "Ditolak"
        REVOKED = "revoked", "Diputus"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="whatsapp_connections",
    )
    phone = models.CharField(max_length=20, db_index=True)
    active_phone = models.CharField(
        max_length=20, null=True, blank=True, unique=True
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    requested_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_connection_approvals",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_note = models.TextField(blank=True, default="")
    rejected_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    otp_hash = models.CharField(max_length=128, null=True, blank=True)
    otp_expires_at = models.DateTimeField(null=True, blank=True)
    otp_attempts = models.PositiveSmallIntegerField(default=0)
    otp_sent_at = models.DateTimeField(null=True, blank=True)
    otp_last_error = models.TextField(blank=True, default="")
    notifications_enabled = models.BooleanField(default=True)
    notify_loans = models.BooleanField(default=True)
    notify_returns = models.BooleanField(default=True)
    notify_fines = models.BooleanField(default=True)
    notify_due_soon = models.BooleanField(default=True)
    notify_overdue = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "whatsapp_connections"
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(
                fields=["status", "requested_at"],
                name="whatsapp_conn_status_idx",
            ),
            models.Index(
                fields=["user", "status"],
                name="whatsapp_conn_user_idx",
            ),
            models.Index(
                fields=["phone", "status"],
                name="whatsapp_conn_phone_idx",
            ),
        ]

    def __str__(self):
        return f"WhatsApp pengguna {self.user_id} ({self.status})"

    def allows_category(self, category):
        if not self.notifications_enabled:
            return False
        field_name = self.CATEGORY_FIELDS.get(category)
        if not field_name:
            return False
        return bool(getattr(self, field_name))


class WhatsAppOutbox(models.Model):
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
        related_name="whatsapp_outbox",
    )
    notification_type = models.CharField(max_length=50)
    title = models.CharField(max_length=255)
    message = models.TextField()
    reference_type = models.CharField(max_length=50, null=True, blank=True)
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
    recipient_phone = models.CharField(
        max_length=20, blank=True, default=""
    )
    provider_message_id = models.CharField(
        max_length=128, blank=True, default=""
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "whatsapp_outbox"
        ordering = ["created_at", "pk"]
        indexes = [
            models.Index(
                fields=["status", "next_attempt_at"],
                name="whatsapp_outbox_due_idx",
            ),
        ]

    def __str__(self):
        return (
            f"{self.notification_type} untuk pengguna "
            f"{self.user_id} ({self.status})"
        )
