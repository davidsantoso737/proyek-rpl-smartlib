from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.authorization import active_login_required

from .models import Notification
from .services import mark_all_read, mark_as_read, reference_url

User = get_user_model()


@active_login_required
def notification_list_view(request):
    """Daftar notifikasi milik sendiri: belum dibaca dulu (FR-96)."""
    notifications = (
        Notification.objects.filter(user=request.user)
        .order_by("is_read", "-created_at")
    )
    page_obj = Paginator(notifications, 15).get_page(request.GET.get("page"))
    entries = [
        {"notif": notif, "url": reference_url(notif, request.user)}
        for notif in page_obj.object_list
    ]
    return render(
        request,
        "notifications/notification_list.html",
        {"page_obj": page_obj, "entries": entries},
    )


@active_login_required
@require_POST
def notification_read_view(request, pk):
    """Tandai dibaca lalu ikut tautan referensi bila ada."""
    notif = mark_as_read(request.user, pk)
    if notif is None:
        messages.error(request, "Notifikasi tidak ditemukan.")
        return redirect("notifications:list")
    url = reference_url(notif, request.user)
    return redirect(url or "notifications:list")


@active_login_required
@require_POST
def notification_read_all_view(request):
    count = mark_all_read(request.user)
    messages.success(request, f"{count} notifikasi ditandai dibaca.")
    return redirect("notifications:list")
