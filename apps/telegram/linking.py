import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from .models import TelegramAccount, TelegramLinkToken

LINK_CODE_TTL = timedelta(minutes=10)
LINK_CODE_BYTES = 24
LINK_CODE_MAX_LENGTH = 64

STATUS_CLAIMED = "claimed"
STATUS_ALREADY_CLAIMED = "already_claimed"
STATUS_ALREADY_LINKED = "already_linked"
STATUS_CONFLICT = "conflict"
STATUS_EXPIRED = "expired"
STATUS_INVALID = "invalid"
STATUS_UNCLAIMED = "unclaimed"
STATUS_MISSING = "missing"
STATUS_SUCCESS = "success"


@dataclass(frozen=True)
class LinkClaimResult:
    status: str
    token: TelegramLinkToken | None = None


@dataclass(frozen=True)
class LinkConfirmResult:
    status: str
    account: TelegramAccount | None = None


def hash_link_code(code):
    normalized = str(code or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def code_matches_token(code, token):
    if not code or token is None:
        return False
    return hmac.compare_digest(hash_link_code(code), token.token_hash)


def create_link_token(user):
    TelegramLinkToken.objects.filter(
        user=user, consumed_at__isnull=True
    ).delete()
    expires_at = timezone.now() + LINK_CODE_TTL
    for _ in range(5):
        code = secrets.token_urlsafe(LINK_CODE_BYTES)
        try:
            token = TelegramLinkToken.objects.create(
                user=user,
                token_hash=hash_link_code(code),
                expires_at=expires_at,
            )
        except IntegrityError:
            continue
        return code, token
    raise IntegrityError("Gagal membuat kode tautan Telegram yang unik.")


def get_pending_link_token(user):
    return (
        TelegramLinkToken.objects.filter(
            user=user,
            consumed_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .order_by("-created_at")
        .first()
    )


def claim_link_code(
    code,
    *,
    telegram_user_id,
    chat_id,
    username="",
    first_name="",
):
    normalized = str(code or "").strip()
    if (
        not normalized
        or len(normalized) > LINK_CODE_MAX_LENGTH
        or not isinstance(telegram_user_id, int)
        or isinstance(telegram_user_id, bool)
        or telegram_user_id <= 0
        or not isinstance(chat_id, int)
        or isinstance(chat_id, bool)
        or chat_id <= 0
    ):
        return LinkClaimResult(STATUS_INVALID)

    now = timezone.now()
    token_hash = hash_link_code(normalized)
    with transaction.atomic():
        token = (
            TelegramLinkToken.objects.select_for_update()
            .filter(
                token_hash=token_hash,
                consumed_at__isnull=True,
            )
            .first()
        )
        if token is None:
            return LinkClaimResult(STATUS_INVALID)
        if token.expires_at <= now:
            return LinkClaimResult(STATUS_EXPIRED, token)
        if token.claimed_at is not None:
            if (
                token.telegram_user_id == telegram_user_id
                and token.chat_id == chat_id
            ):
                return LinkClaimResult(STATUS_ALREADY_CLAIMED, token)
            return LinkClaimResult(STATUS_INVALID, token)
        if TelegramAccount.objects.filter(user=token.user).exists():
            return LinkClaimResult(STATUS_ALREADY_LINKED, token)
        conflict = (
            TelegramAccount.objects.filter(
                Q(telegram_user_id=telegram_user_id) | Q(chat_id=chat_id)
            )
            .exclude(user=token.user)
            .exists()
        )
        if conflict:
            return LinkClaimResult(STATUS_CONFLICT, token)

        token.telegram_user_id = telegram_user_id
        token.chat_id = chat_id
        token.telegram_username = (username or "")[:64]
        token.telegram_first_name = (first_name or "")[:255]
        token.claimed_at = now
        token.save(
            update_fields=[
                "telegram_user_id",
                "chat_id",
                "telegram_username",
                "telegram_first_name",
                "claimed_at",
            ]
        )
        return LinkClaimResult(STATUS_CLAIMED, token)


def confirm_link_token(user):
    now = timezone.now()
    with transaction.atomic():
        token = (
            TelegramLinkToken.objects.select_for_update()
            .filter(user=user, consumed_at__isnull=True)
            .order_by("-created_at")
            .first()
        )
        if token is None:
            return LinkConfirmResult(STATUS_MISSING)
        if token.expires_at <= now:
            return LinkConfirmResult(STATUS_EXPIRED, None)
        if token.claimed_at is None:
            return LinkConfirmResult(STATUS_UNCLAIMED, None)
        if TelegramAccount.objects.filter(user=user).exists():
            return LinkConfirmResult(STATUS_ALREADY_LINKED, None)
        conflict = (
            TelegramAccount.objects.filter(
                Q(telegram_user_id=token.telegram_user_id)
                | Q(chat_id=token.chat_id)
            )
            .exclude(user=user)
            .exists()
        )
        if conflict:
            return LinkConfirmResult(STATUS_CONFLICT, None)

        try:
            account = TelegramAccount.objects.create(
                user=user,
                telegram_user_id=token.telegram_user_id,
                chat_id=token.chat_id,
                username=token.telegram_username,
                first_name=token.telegram_first_name,
            )
        except IntegrityError:
            return LinkConfirmResult(STATUS_CONFLICT, None)

        token.consumed_at = now
        token.save(update_fields=["consumed_at"])
        TelegramLinkToken.objects.filter(
            user=user, consumed_at__isnull=True
        ).exclude(pk=token.pk).delete()
        return LinkConfirmResult(STATUS_SUCCESS, account)


def cancel_pending_link(user):
    TelegramLinkToken.objects.filter(
        user=user, consumed_at__isnull=True
    ).delete()


def unlink_account(user):
    TelegramAccount.objects.filter(user=user).delete()
    cancel_pending_link(user)
