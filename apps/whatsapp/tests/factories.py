from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.whatsapp.models import WhatsAppConnection
from apps.whatsapp.services import normalize_phone

User = get_user_model()


def make_user(
    username="siswa1",
    role=User.Role.SISWA,
    phone="081234567890",
    **extra,
):
    defaults = {
        "full_name": f"{username} Full",
        "phone": phone,
    }
    if role == User.Role.SISWA:
        defaults.setdefault("nis", f"NIS-{username}")
    if role == User.Role.GURU:
        defaults.setdefault("nip", f"NIP-{username}")
    defaults.update(extra)
    return User.objects.create_user(
        username=username,
        password="secret123",
        role=role,
        **defaults,
    )


def make_connection(
    user,
    *,
    phone="081234567890",
    status=WhatsAppConnection.Status.ACTIVE,
):
    normalized = normalize_phone(phone) or phone
    now = timezone.now()
    is_active = status == WhatsAppConnection.Status.ACTIVE
    return WhatsAppConnection.objects.create(
        user=user,
        phone=normalized,
        active_phone=normalized if is_active else None,
        status=status,
        requested_at=now,
        verified_at=now if is_active else None,
    )
