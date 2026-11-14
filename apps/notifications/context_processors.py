def unread_notifications(request):
    """Badge belum dibaca dan pratinjau panel header."""
    from .services import recent_for_panel, unread_count

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"unread_notifications": 0, "notification_preview": []}
    try:
        return {
            "unread_notifications": unread_count(user),
            "notification_preview": recent_for_panel(user),
        }
    except Exception:
        return {"unread_notifications": 0, "notification_preview": []}
