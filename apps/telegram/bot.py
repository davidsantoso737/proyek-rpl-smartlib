import logging
import os
import secrets
import socket
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

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
    "Untuk menautkan akun, buka halaman Profil SmartLib dan buat kode "
    "tautan. Kirim kode itu ke bot ini dengan format /start KODE."
)
CODE_INVALID = (
    "Kode tautan tidak valid atau sudah kedaluwarsa. Buat kode baru "
    "dari halaman Profil SmartLib."
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


def _reply(client, chat_id, text):
    try:
        client.send_message(chat_id, text)
    except TelegramError as exc:
        logger.warning(
            "Gagal membalas chat Telegram: %s", type(exc).__name__
        )


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

    result = claim_link_code(
        code,
        telegram_user_id=telegram_user_id,
        chat_id=chat_id,
        username=str(sender.get("username") or ""),
        first_name=str(sender.get("first_name") or ""),
    )
    if result.status == STATUS_CLAIMED:
        _reply(client, chat_id, CODE_CLAIMED)
    elif result.status == STATUS_ALREADY_CLAIMED:
        _reply(client, chat_id, CODE_ALREADY_CLAIMED)
    elif result.status == STATUS_ALREADY_LINKED:
        _reply(client, chat_id, CODE_ALREADY_LINKED)
    elif result.status == STATUS_CONFLICT:
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
