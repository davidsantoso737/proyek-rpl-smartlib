from django.conf import settings

from .models import TelegramAccount

CATEGORY_BY_NOTIFICATION = {
    "LOAN_APPROVED": (TelegramAccount.Category.LOAN,),
    "LOAN_REJECTED": (TelegramAccount.Category.LOAN,),
    "LOAN_ACTIVE": (TelegramAccount.Category.LOAN,),
    "EBOOK_APPROVED": (TelegramAccount.Category.LOAN,),
    "EBOOK_REJECTED": (TelegramAccount.Category.LOAN,),
    "LOAN_RETURNED": (TelegramAccount.Category.RETURN,),
    "LOAN_DUE_SOON": (TelegramAccount.Category.DUE_SOON,),
    "LOAN_OVERDUE": (TelegramAccount.Category.OVERDUE,),
}


def categories_for_notification(notification_type, message=""):
    categories = list(CATEGORY_BY_NOTIFICATION.get(notification_type, ()))
    if (
        notification_type == "LOAN_RETURNED"
        and "denda" in (message or "").casefold()
    ):
        categories.append(TelegramAccount.Category.FINE)
    return tuple(categories)


def should_send_telegram(account, notification_type, message=""):
    if account is None or not account.notifications_enabled:
        return False
    if not getattr(account, "user", None) or not account.user.is_active:
        return False
    if not getattr(settings, "TELEGRAM_NOTIFICATIONS_ENABLED", False):
        return False
    categories = categories_for_notification(
        notification_type, message=message
    )
    return any(account.allows_category(category) for category in categories)
