from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.authorization import role_required

from .models import StockOpname
from .services import (
    OPNAME_DONE,
    OPNAME_RUNNING,
    OpnameError,
    complete_opname,
    scan_copy,
    start_opname,
    summarize,
)

User = get_user_model()


@role_required(User.Role.PUSTAKAWAN)
def opname_list_view(request):
    """Riwayat sesi opname + tombol mulai (FR-82)."""
    opnames = StockOpname.objects.select_related("started_by").order_by(
        "-started_at"
    )[:20]
    running = StockOpname.objects.filter(status=OPNAME_RUNNING).first()
    return render(
        request,
        "inventory/opname_list.html",
        {"opnames": opnames, "running": running},
    )


@role_required(User.Role.PUSTAKAWAN)
@require_POST
def opname_start_view(request):
    try:
        opname = start_opname(request.user, request.POST.get("notes", ""))
    except OpnameError as exc:
        messages.error(request, str(exc))
        return redirect("inventory:opname_list")
    return redirect("inventory:opname_detail", pk=opname.pk)


@role_required(User.Role.PUSTAKAWAN)
def opname_detail_view(request, pk):
    """Sesi berjalan: scan QR, tandai, bandingkan (FR-87 – FR-89)."""
    opname = get_object_or_404(
        StockOpname.objects.select_related("started_by"), pk=pk
    )
    error = ""
    if request.method == "POST" and opname.status == OPNAME_RUNNING:
        try:
            item = scan_copy(pk, request.POST.get("code", ""))
        except OpnameError as exc:
            error = str(exc)
        else:
            messages.success(
                request,
                f"{item.book_copy.copy_code} tercatat DITEMUKAN.",
            )
            return redirect("inventory:opname_detail", pk=pk)
    summary = summarize(opname)
    return render(
        request,
        "inventory/opname_detail.html",
        {
            "opname": opname,
            "error": error,
            "code": request.POST.get("code", "") if error else "",
            "is_running": opname.status == OPNAME_RUNNING,
            "is_done": opname.status == OPNAME_DONE,
            **summary,
        },
    )


@role_required(User.Role.PUSTAKAWAN)
@require_POST
def opname_complete_view(request, pk):
    try:
        complete_opname(pk)
    except OpnameError as exc:
        messages.error(request, str(exc))
        return redirect("inventory:opname_detail", pk=pk)
    messages.success(request, "Stock opname diselesaikan.")
    return redirect("inventory:opname_detail", pk=pk)
