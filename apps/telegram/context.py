from django.conf import settings

from .connection import (
    SESSION_CONNECTION_TOKEN,
    STATE_ACTIVE,
    STATE_AWAITING,
    get_connection_context,
    normalize_phone,
)
from .forms import (
    TelegramConnectionPreferencesForm,
    TelegramPreferencesForm,
)
from .linking import code_matches_token, get_pending_link_token
from .models import TelegramAccount

SESSION_LINK_CODE = "telegram_link_code"


def build_profile_context(request, telegram_form=None):
    user = request.user
    account = TelegramAccount.objects.filter(user=user).first()
    token = get_pending_link_token(user)
    code = request.session.get(SESSION_LINK_CODE)
    if token is None or token.claimed_at is not None:
        request.session.pop(SESSION_LINK_CODE, None)
        code = None
    elif code and not code_matches_token(code, token):
        request.session.pop(SESSION_LINK_CODE, None)
        code = None

    connection_token = request.session.get(SESSION_CONNECTION_TOKEN)
    connection_context = get_connection_context(user, connection_token)
    if connection_context["state"] != STATE_AWAITING:
        request.session.pop(SESSION_CONNECTION_TOKEN, None)
        connection_token = None

    connection = connection_context["connection"]
    if (
        connection_context["state"] == STATE_ACTIVE
        and connection is not None
        and telegram_form is None
    ):
        telegram_form = TelegramConnectionPreferencesForm(
            instance=connection
        )
    elif account is not None and telegram_form is None:
        telegram_form = TelegramPreferencesForm(instance=account)

    connection_form = None
    if connection_context["state"] == STATE_ACTIVE:
        connection_form = telegram_form

    return {
        "telegram_account": account,
        "telegram_link": token,
        "telegram_link_ready": bool(code and token),
        "telegram_form": telegram_form,
        "telegram_bot_username": getattr(
            settings, "TELEGRAM_BOT_USERNAME", ""
        ),
        "telegram_system_enabled": getattr(
            settings, "TELEGRAM_NOTIFICATIONS_ENABLED", False
        ),
        "telegram_connection_state": connection_context["state"],
        "telegram_connection": connection,
        "telegram_connection_form": connection_form,
        "telegram_legacy_account": connection_context["legacy_account"],
        "telegram_masked_phone": connection_context["masked_phone"],
        "telegram_link_ready_now": connection_context["link_ready"],
        "telegram_rejection_note": connection_context["rejection_note"],
        "telegram_connection_token": connection_token,
        "telegram_has_phone": bool(normalize_phone(user.phone)),
        "telegram_current_phone": normalize_phone(user.phone),
        "telegram_legacy_linking": bool(
            getattr(settings, "TELEGRAM_LEGACY_LINKING_ENABLED", False)
        ),
    }
