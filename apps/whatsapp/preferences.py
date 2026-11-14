from django.conf import settings

from .models import WhatsAppConnection

CATEGORY_BY_NOTIFICATION = {
    "LOAN_APPROVED": (WhatsAppConnection.Category.LOAN,),
    "LOAN_REJECTED": (WhatsAppConnection.Category.LOAN,),
    "LOAN_ACTIVE": (WhatsAppConnection.Category.LOAN,),
    "EBOOK_APPROVED": (WhatsAppConnection.Category.LOAN,),
    "EBOOK_REJECTED": (WhatsAppConnection.Category.LOAN,),
    "LOAN_RETURNED": (WhatsAppConnection.Category.RETURN,),
    "LOAN_DUE_SOON": (WhatsAppConnection.Category.DUE_SOON,),
    "LOAN_OVERDUE": (WhatsAppConnection.Category.OVERDUE,),
}

SYSTEM_NOTIFICATION_TYPES = frozenset({"WHATSAPP_LINK_ACTIVE"})


def categories_for_notification(notification_type, message=""):
    categories = list(CATEGORY_BY_NOTIFICATION.get(notification_type, ()))
    if (
        notification_type == "LOAN_RETURNED"
        and "denda" in (message or "").casefold()
    ):
        categories.append(WhatsAppConnection.Category.FINE)
    return tuple(categories)


def should_send_whatsapp(account, notification_type, message=""):
    if account is None or not account.notifications_enabled:
        return False
    if not getattr(account, "user", None) or not account.user.is_active:
        return False
    if not getattr(
        settings, "WHATSAPP_NOTIFICATIONS_ENABLED", True
    ):
        return False
    if notification_type in SYSTEM_NOTIFICATION_TYPES:
        return True
    categories = categories_for_notification(
        notification_type, message=message
    )
    return any(account.allows_category(category) for category in categories)
