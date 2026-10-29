from django.conf import settings
from django.db import models


class AIConfiguration(models.Model):
    """Konfigurasi AI Assistant yang dapat diubah pustakawan lewat UI.

    Hanya satu baris (pk=1) yang dipakai. API key disimpan terenkripsi di
    kolom ``api_key_encrypted``; nilai aslinya tidak pernah disimpan di
    model ini dan tidak pernah dikirim ke template.
    """

    PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"
    PROVIDER_CHOICES = [
        (
            PROVIDER_OPENAI_COMPATIBLE,
            "Endpoint kompatibel OpenAI (chat/completions)",
        ),
    ]

    provider = models.CharField(
        max_length=40,
        choices=PROVIDER_CHOICES,
        default=PROVIDER_OPENAI_COMPATIBLE,
    )
    base_url = models.CharField(max_length=255)
    model = models.CharField(max_length=120)
    api_key_encrypted = models.TextField()
    is_enabled = models.BooleanField(default=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="updated_ai_configurations",
    )
    last_validated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ai_configuration"
        verbose_name = "Konfigurasi AI"
        verbose_name_plural = "Konfigurasi AI"

    def __str__(self):
        return f"{self.provider} / {self.model}"
