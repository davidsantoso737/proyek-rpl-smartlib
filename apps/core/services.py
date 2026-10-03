"""Layanan kebijakan perpustakaan berversi (FR-50 – FR-53E, BR-27, NFR-39 – NFR-40).

Technical decision: TIDAK ada perubahan schema. Model LibrarySetting
sudah mendukung historical effective state via pasangan
(effective_from, effective_until). Setiap perubahan membuat baris
BARU dan menutup baris aktif, sehingga:
- transaksi baru memakai konfigurasi aktif;
- denda/jatuh tempo/expiry historis tidak berubah (NFR-22),
  karena snapshot sudah tersimpan di tiap transaksi.
"""

from django.db import models, transaction
from django.utils import timezone

from .models import LibrarySetting


class PolicyError(Exception):
    """Validasi perubahan kebijakan (pesan siap tampil)."""


def get_active_setting(at=None):
    """Konfigurasi yang berlaku pada suatu waktu (default: sekarang)."""
    moment = at or timezone.now()
    return (
        LibrarySetting.objects.filter(effective_from__lte=moment)
        .filter(
            models.Q(effective_until__isnull=True)
            | models.Q(effective_until__gt=moment)
        )
        .order_by("-effective_from")
        .first()
    )


def update_policy(user, physical_loan_duration, ebook_access_duration,
                  pickup_qr_validity, fine_rate_per_day):
    """Buat versi kebijakan baru dan tutup versi aktif (atomik)."""
    values = {
        "physical_loan_duration": physical_loan_duration,
        "ebook_access_duration": ebook_access_duration,
        "pickup_qr_validity": pickup_qr_validity,
        "fine_rate_per_day": fine_rate_per_day,
    }
    for name, value in values.items():
        if value is None or value <= 0:
            raise PolicyError(
                "Seluruh nilai kebijakan harus lebih dari nol."
            )
    with transaction.atomic():
        now = timezone.now()
        current = get_active_setting(now)
        if current is not None:
            current.effective_until = now
            current.save(update_fields=["effective_until", "updated_at"])
        return LibrarySetting.objects.create(
            effective_from=now,
            updated_by=user,
            **values,
        )
