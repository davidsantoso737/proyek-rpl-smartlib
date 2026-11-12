import logging
import os
import secrets
import socket
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .connection import start_link_token, verify_contact
from .linking import (
    STATUS_ALREADY_CLAIMED,
    STATUS_ALREADY_LINKED,
    STATUS_CLAIMED,
    STATUS_CONFLICT,
    claim_link_code,
)
from .models import TelegramBotState
from .services import TelegramError

logger = logging.getLogger(__name__)

START_WITHOUT_CODE = (
    "Pengajuan penautan dibuat dari halaman Profil SmartLib dan harus "
    "disetujui pustakawan terlebih dahulu. Setelah disetujui, buka "
    "tombol Konfirmasi Telegram di halaman Profil untuk membuka bot ini."
)
CODE_INVALID = (
    "Tautan konfirmasi tidak valid atau sudah kedaluwarsa. Buat tautan "
    "baru dari halaman Profil SmartLib."
)
CODE_PHONE_CHANGED = (
    "Nomor HP di profil SmartLib berubah setelah pengajuan disetujui. "
    "Ajukan penautan ulang dari halaman Profil SmartLib."
)
CONTACT_PROMPT = (
    "Tautan dikenali. Bagikan nomor HP yang terdaftar di SmartLib "
    "dengan tombol di bawah ini untuk membuktikan bahwa akun Telegram "
    "ini milik Anda."
)
CONTACT_ACCEPTED = (
    "Verifikasi berhasil. Akun Telegram ini kini aktif menerima "
    "notifikasi SmartLib."
)
CONTACT_INVALID = (
    "Kontak tidak valid. Gunakan tombol Bagikan nomor HP sehingga "
    "bot menerima kontak Telegram Anda sendiri."
)
CONTACT_MISMATCH = (
    "Nomor HP pada kontak tidak cocok dengan nomor di profil SmartLib. "
    "Periksa nomor profil atau bagikan kontak yang benar."
)
CONTACT_TOO_MANY = (
    "Terlalu banyak percobaan. Permintaan penautan dibatalkan. Buat "
    "tautan baru dari halaman Profil SmartLib."
)
CONTACT_EXPIRED = (
    "Tautan sudah kedaluwarsa. Buat tautan baru dari halaman Profil "
    "SmartLib."
)
CONTACT_ALREADY_ACTIVE = (
    "Akun SmartLib ini sudah memiliki tautan Telegram aktif."
)
CONTACT_CONFLICT = (
    "Akun Telegram ini sudah terhubung ke akun SmartLib lain. Putuskan "
    "tautan dari akun tersebut terlebih dahulu."
)
CONTACT_NO_PHONE = (
    "Profil SmartLib belum memiliki nomor HP yang valid. Lengkapi "
    "profil terlebih dahulu."
)
CONTACT_INACTIVE = (
    "Akun SmartLib tidak aktif. Hubungi pustakawan untuk bantuan."
)
CONTACT_NO_SESSION = (
    "Tidak ada penautan yang menunggu konfirmasi kontak. Buka tautan "
    "baru dari halaman Profil SmartLib."
)
CONTACT_PHONE_CHANGED = (
    "Nomor HP profil berubah sebelum konfirmasi selesai. Ajukan "
    "penautan ulang dari halaman Profil SmartLib."
)
CODE_CLAIMED = (
    "Kode diterima. Buka halaman Profil SmartLib dan pilih Konfirmasi "
    "tautan untuk menyelesaikan proses."
)
CODE_ALREADY_CLAIMED = (
    "Kode ini sudah diterima. Selesaikan konfirmasi di halaman Profil "
    "SmartLib."
)
CODE_ALREADY_LINKED = (
    "Akun SmartLib untuk kode ini sudah memiliki tautan Telegram. "
    "Putuskan tautan lama dari halaman Profil sebelum menautkan ulang."
)
CODE_CONFLICT = (
    "Akun Telegram ini sudah terhubung ke akun SmartLib lain. Putuskan "
    "tautan dari akun tersebut terlebih dahulu."
)


def _reply(client, chat_id, text, *, reply_markup=None):
    try:
        client.send_message(chat_id, text, reply_markup=reply_markup)
    except TelegramError as exc:
        logger.warning(
            "Gagal membalas chat Telegram: %s", type(exc).__name__
        )


def _contact_keyboard():
    return {
        "keyboard": [
            [{"text": "Bagikan nomor HP", "request_contact": True}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def _remove_keyboard():
    return {"remove_keyboard": True}


def _handle_contact(message, *, client, chat_id, telegram_user_id, sender):
    contact = message.get("contact")
    if not isinstance(contact, dict):
        return False
    try:
        contact_user_id = int(contact.get("user_id"))
    except (TypeError, ValueError):
        contact_user_id = 0
    result = verify_contact(
        telegram_user_id=telegram_user_id,
        chat_id=chat_id,
        contact_user_id=contact_user_id,
        phone_number=str(contact.get("phone_number") or ""),
        username=str(sender.get("username") or ""),
        first_name=str(sender.get("first_name") or ""),
    )
    if result.status == "ok":
        _reply(
            client,
            chat_id,
            CONTACT_ACCEPTED,
            reply_markup=_remove_keyboard(),
        )
    elif result.status == "invalid":
        _reply(client, chat_id, CONTACT_INVALID)
    elif result.status in ("phone_mismatch", "contact_mismatch"):
        text = CONTACT_MISMATCH
        if result.attempts_left:
            text = f"{text} Sisa percobaan: {result.attempts_left}."
        _reply(client, chat_id, text)
    elif result.status == "too_many":
        _reply(
            client,
            chat_id,
            CONTACT_TOO_MANY,
            reply_markup=_remove_keyboard(),
        )
    elif result.status == "expired":
        _reply(
            client,
            chat_id,
            CONTACT_EXPIRED,
            reply_markup=_remove_keyboard(),
        )
    elif result.status == "already_active":
        _reply(
            client,
            chat_id,
            CONTACT_ALREADY_ACTIVE,
            reply_markup=_remove_keyboard(),
        )
    elif result.status == "conflict":
        _reply(
            client,
            chat_id,
            CONTACT_CONFLICT,
            reply_markup=_remove_keyboard(),
        )
    elif result.status == "no_phone":
        _reply(
            client,
            chat_id,
            CONTACT_NO_PHONE,
            reply_markup=_remove_keyboard(),
        )
    elif result.status == "phone_changed":
        _reply(
            client,
            chat_id,
            CONTACT_PHONE_CHANGED,
            reply_markup=_remove_keyboard(),
        )
    elif result.status == "inactive":
        _reply(client, chat_id, CONTACT_INACTIVE)
    else:
        _reply(client, chat_id, CONTACT_NO_SESSION)
    return True


def handle_update(update, *, client):
    if not isinstance(update, dict):
        return False
    message = update.get("message")
    if not isinstance(message, dict):
        return False
    chat = message.get("chat")
    sender = message.get("from")
    if not isinstance(chat, dict) or not isinstance(sender, dict):
        return False
    if chat.get("type") != "private" or sender.get("is_bot"):
        return False
    try:
        chat_id = int(chat["id"])
        telegram_user_id = int(sender["id"])
    except (KeyError, TypeError, ValueError):
        return False
    if chat_id <= 0 or telegram_user_id <= 0:
        return False
    if chat_id != telegram_user_id:
        return False

    if isinstance(message.get("contact"), dict):
        return _handle_contact(
            message,
            client=client,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            sender=sender,
        )

    text = str(message.get("text") or "").strip()
    if not text:
        return False
    command, _, argument = text.partition(" ")
    command = command.partition("@")[0].lower()
    if command != "/start":
        _reply(client, chat_id, START_WITHOUT_CODE)
        return True
    code = argument.strip()
    if not code:
        _reply(client, chat_id, START_WITHOUT_CODE)
        return True

    result = start_link_token(
        code,
        telegram_user_id=telegram_user_id,
        chat_id=chat_id,
        username=str(sender.get("username") or ""),
        first_name=str(sender.get("first_name") or ""),
    )
    if result.status == "ok":
        _reply(
            client,
            chat_id,
            CONTACT_PROMPT,
            reply_markup=_contact_keyboard(),
        )
        return True
    if result.status == "expired":
        _reply(client, chat_id, CONTACT_EXPIRED)
        return True
    if result.status == "already_active":
        _reply(client, chat_id, CODE_ALREADY_LINKED)
        return True
    if result.status == "conflict":
        _reply(client, chat_id, CODE_CONFLICT)
        return True
    if result.status == "phone_changed":
        _reply(client, chat_id, CODE_PHONE_CHANGED)
        return True
    if result.status == "no_phone":
        _reply(client, chat_id, CONTACT_NO_PHONE)
        return True
    if result.status == "inactive":
        _reply(client, chat_id, CONTACT_INACTIVE)
        return True
    if result.status != "invalid":
        _reply(client, chat_id, CODE_INVALID)
        return True

    # Kompatibilitas kode tautan lama. Alur baru selalu dicoba lebih dulu.
    legacy = claim_link_code(
        code,
        telegram_user_id=telegram_user_id,
        chat_id=chat_id,
        username=str(sender.get("username") or ""),
        first_name=str(sender.get("first_name") or ""),
    )
    if legacy.status == STATUS_CLAIMED:
        _reply(client, chat_id, CODE_CLAIMED)
    elif legacy.status == STATUS_ALREADY_CLAIMED:
        _reply(client, chat_id, CODE_ALREADY_CLAIMED)
    elif legacy.status == STATUS_ALREADY_LINKED:
        _reply(client, chat_id, CODE_ALREADY_LINKED)
    elif legacy.status == STATUS_CONFLICT:
        _reply(client, chat_id, CODE_CONFLICT)
    else:
        _reply(client, chat_id, CODE_INVALID)
    return True


def make_lease_owner():
    return (
        f"{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(4)}"
    )


def acquire_bot_lease(owner, seconds):
    now = timezone.now()
    with transaction.atomic():
        TelegramBotState.objects.get_or_create(pk=1)
        state = TelegramBotState.objects.select_for_update().get(pk=1)
        if (
            state.lease_until
            and state.lease_until > now
            and state.lease_owner != owner
        ):
            return False
        state.lease_owner = owner
        state.lease_until = now + timedelta(seconds=max(seconds, 1))
        state.save(
            update_fields=[
                "lease_owner",
                "lease_until",
                "updated_at",
            ]
        )
        return True


def renew_bot_lease(owner, seconds):
    now = timezone.now()
    with transaction.atomic():
        state = (
            TelegramBotState.objects.select_for_update()
            .filter(pk=1)
            .first()
        )
        if state is None or state.lease_owner != owner:
            return False
        state.lease_until = now + timedelta(seconds=max(seconds, 1))
        state.save(
            update_fields=["lease_until", "updated_at"]
        )
        return True


def release_bot_lease(owner):
    with transaction.atomic():
        state = (
            TelegramBotState.objects.select_for_update()
            .filter(pk=1)
            .first()
        )
        if state is None or state.lease_owner != owner:
            return
        state.lease_owner = ""
        state.lease_until = None
        state.save(
            update_fields=[
                "lease_owner",
                "lease_until",
                "updated_at",
            ]
        )


def get_last_update_id():
    value = (
        TelegramBotState.objects.filter(pk=1)
        .values_list("last_update_id", flat=True)
        .first()
    )
    return value or 0


def advance_update_id(update_id):
    if (
        not isinstance(update_id, int)
        or isinstance(update_id, bool)
        or update_id <= 0
    ):
        return
    with transaction.atomic():
        TelegramBotState.objects.get_or_create(pk=1)
        state = TelegramBotState.objects.select_for_update().get(pk=1)
        if update_id > state.last_update_id:
            state.last_update_id = update_id
            state.save(
                update_fields=["last_update_id", "updated_at"]
            )


def process_updates(updates, *, client):
    for update in updates:
        update_id = (
            update.get("update_id")
            if isinstance(update, dict)
            else None
        )
        try:
            handle_update(update, client=client)
        except Exception:
            logger.exception("Gagal memproses update Telegram.")
        advance_update_id(update_id)
