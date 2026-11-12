"""Pembuatan pengajuan penautan Telegram secara otomatis.

Pengajuan dibuat ketika akun baru memiliki nomor HP atau ketika nomor
HP berubah. Sinyal ini hanya memanggil layanan penautan; kegagalannya
tidak boleh menggagalkan pembuatan/penyimpanan akun.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .connection import normalize_phone, request_connection

logger = logging.getLogger(__name__)

User = get_user_model()

PHONE_FIELD = "phone"


@receiver(pre_save, sender=User)
def capture_previous_phone(sender, instance, **kwargs):
    """Simpan nomor HP lama untuk mendeteksi perubahan nomor."""
    if not instance.pk:
        instance._telegram_previous_phone = None
        return
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and PHONE_FIELD not in update_fields:
        instance._telegram_previous_phone = None
        return
    instance._telegram_previous_phone = (
        sender.objects.filter(pk=instance.pk)
        .values_list(PHONE_FIELD, flat=True)
        .first()
    )


@receiver(post_save, sender=User)
def auto_request_connection(sender, instance, created, raw=False, **kwargs):
    """Buat pengajuan otomatis saat nomor HP baru/berubah."""
    if raw:
        return
    if getattr(settings, "TELEGRAM_LEGACY_LINKING_ENABLED", False):
        # Mode tautan lama masih aktif; jangan buat pengajuan alur baru.
        return
    if not getattr(instance, "is_active", False):
        return
    update_fields = kwargs.get("update_fields")
    if not created and update_fields is not None:
        if PHONE_FIELD not in update_fields:
            return

    previous = (
        None
        if created
        else getattr(instance, "_telegram_previous_phone", None)
    )
    current = normalize_phone(getattr(instance, PHONE_FIELD, ""))
    if not current:
        return
    if not created and normalize_phone(previous) == current:
        return

    try:
        request_connection(instance, notify=True)
    except Exception:
        logger.exception(
            "Gagal membuat pengajuan penautan Telegram untuk user_id=%s",
            instance.pk,
        )
