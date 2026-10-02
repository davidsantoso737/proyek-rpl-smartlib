from django.conf import settings
from django.db import models


class LibrarySetting(models.Model):
    # Satuan: HARI
    physical_loan_duration = models.PositiveIntegerField(
        help_text="Durasi peminjaman fisik dalam HARI."
    )
    # Satuan: HARI
    ebook_access_duration = models.PositiveIntegerField(
        help_text="Durasi akses ebook dalam HARI."
    )
    # Satuan: MENIT. Ini konfigurasi durasi saja (mis. 30 = 30 menit).
    # Waktu kedaluwarsa aktual per transaksi disimpan di
    # Loan.pickup_exp_at (DateTimeField).
    pickup_qr_validity = models.PositiveIntegerField(
        help_text="Masa berlaku QR pickup dalam MENIT."
    )
    fine_rate_per_day = models.DecimalField(max_digits=12, decimal_places=2)
    effective_from = models.DateTimeField()
    effective_until = models.DateTimeField(null=True, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="updated_settings",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "library_settings"
