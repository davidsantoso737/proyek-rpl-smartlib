def unread_notifications(request):
    """Badge jumlah belum dibaca untuk navigasi."""
    from .services import unread_count

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"unread_notifications": 0}
    try:
        return {"unread_notifications": unread_count(user)}
    except Exception:
        return {"unread_notifications": 0}
