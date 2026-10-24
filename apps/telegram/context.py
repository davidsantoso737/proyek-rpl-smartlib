from django.conf import settings

from .forms import TelegramPreferencesForm
from .linking import code_matches_token, get_pending_link_token
from .models import TelegramAccount

SESSION_LINK_CODE = "telegram_link_code"


def build_profile_context(request, telegram_form=None):
    account = TelegramAccount.objects.filter(user=request.user).first()
    token = get_pending_link_token(request.user)
    code = request.session.get(SESSION_LINK_CODE)
    if token is None or token.claimed_at is not None:
        request.session.pop(SESSION_LINK_CODE, None)
        code = None
    elif code and not code_matches_token(code, token):
        request.session.pop(SESSION_LINK_CODE, None)
        code = None

    if account is not None:
        if telegram_form is None:
            telegram_form = TelegramPreferencesForm(instance=account)
    else:
        telegram_form = None

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
    }
