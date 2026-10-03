from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.authorization import (
    active_login_required,
    role_required,
)

from .models import Loan, LoanItem
from .services import (
    LOAN_APPROVED,
    LOAN_PENDING,
    ApprovalError,
    LoanRequestError,
    PickupTokenError,
    ReturnError,
    activate_handover,
    available_substitutes,
    approve_loan,
    cart_add,
    cart_clear,
    cart_copies,
    cart_remove,
    get_cart_ids,
    lookup_return,
    make_pickup_qr_png,
    make_qr_png,
    process_return,
    reject_loan,
    request_loan,
    verify_pickup_token,
)

User = get_user_model()

REQUESTER_ROLES = (User.Role.SISWA, User.Role.GURU)


@role_required(*REQUESTER_ROLES)
@require_POST
def cart_add_view(request, copy_id):
    """Masukkan satu eksemplar ke keranjang (FR-13)."""
    try:
        copy = cart_add(request.session, copy_id)
    except LoanRequestError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request, f"Eksemplar {copy.copy_code} masuk keranjang."
        )
    return redirect("circulation:cart")


@role_required(*REQUESTER_ROLES)
@require_POST
def cart_remove_view(request, copy_id):
    try:
        cart_remove(request.session, copy_id)
    except (TypeError, ValueError):
        pass
    return redirect("circulation:cart")


@role_required(*REQUESTER_ROLES)
def cart_detail_view(request):
    """Isi keranjang sebelum diajukan (FR-13)."""
    copies = cart_copies(request.session)
    return render(
        request,
        "circulation/cart_detail.html",
        {"copies": copies},
    )


@role_required(*REQUESTER_ROLES)
@require_POST
def loan_create_view(request):
    """Ajukan peminjaman multi-item -> PENDING_APPROVAL (FR-16)."""
    copy_ids = get_cart_ids(request.session)
    try:
        loan = request_loan(request.user, copy_ids)
    except LoanRequestError as exc:
        messages.error(request, str(exc))
        return redirect("circulation:cart")
    cart_clear(request.session)
    messages.success(
        request,
        "Pengajuan peminjaman berhasil dibuat dan menunggu "
        "persetujuan pustakawan.",
    )
    return redirect("circulation:loan_detail", pk=loan.pk)


@active_login_required
def loan_list_view(request):
    """Riwayat/status pengajuan milik sendiri."""
    loans = (
        Loan.objects.filter(user=request.user)
        .prefetch_related("items__book_copy__book")
        .order_by("-requested_at")
    )
    return render(
        request, "circulation/loan_list.html", {"loans": loans}
    )


@active_login_required
def loan_detail_view(request, pk):
    """Detail pengajuan milik sendiri (privasi antar-pengguna)."""
    loan = get_object_or_404(
        Loan.objects.prefetch_related(
            "items__book_copy__book"
        ).select_related("user"),
        pk=pk,
        user=request.user,
    )
    return render(
        request, "circulation/loan_detail.html", {"loan": loan}
    )


def _approval_loan_or_404(pk):
    return get_object_or_404(
        Loan.objects.prefetch_related(
            "items__book_copy__book"
        ).select_related("user"),
        pk=pk,
    )


@role_required(User.Role.PUSTAKAWAN)
def approval_list_view(request):
    """Daftar pengajuan PENDING_APPROVAL (FR-17). Hanya Pustakawan."""
    loans = (
        Loan.objects.filter(status=LOAN_PENDING)
        .select_related("user")
        .prefetch_related("items__book_copy__book")
        .order_by("requested_at")
    )
    return render(
        request, "circulation/approval_list.html", {"loans": loans}
    )


@role_required(User.Role.PUSTAKAWAN)
def approval_detail_view(request, pk):
    """Periksa pengajuan + tetapkan eksemplar (FR-17 – FR-18)."""
    loan = _approval_loan_or_404(pk)
    return _render_approval_detail(request, loan)


def _render_approval_detail(request, loan, substitutions=None):
    rows = []
    for item in loan.items.all():
        choices = list(available_substitutes(item.book_copy.book, exclude_loan_pk=loan.pk))
        current = item.book_copy
        if all(choice.pk != current.pk for choice in choices):
            choices = [current, *choices]
        selected = str(
            (substitutions or {}).get(item.pk, current.pk)
        )
        rows.append(
            {"item": item, "choices": choices, "selected": selected}
        )
    return render(
        request,
        "circulation/approval_detail.html",
        {"loan": loan, "rows": rows},
    )


@role_required(User.Role.PUSTAKAWAN)
@require_POST
def approval_approve_view(request, pk):
    """Setujui + hasilkan QR Pickup (FR-18 – FR-20)."""
    loan = _approval_loan_or_404(pk)
    substitutions = {
        int(key.split("_", 1)[1]): value
        for key, value in request.POST.items()
        if key.startswith("copy_") and value
    }
    try:
        approve_loan(loan.pk, request.user, substitutions)
    except (ApprovalError, ValueError) as exc:
        messages.error(request, str(exc))
        return _render_approval_detail(request, loan, substitutions)
    messages.success(
        request,
        f"Pengajuan #{loan.pk} disetujui. QR Pickup dibuat dan "
        "dapat dilihat peminjam pada detail pengajuannya.",
    )
    return redirect("circulation:approval_detail", pk=loan.pk)


@role_required(User.Role.PUSTAKAWAN)
@require_POST
def approval_reject_view(request, pk):
    """Tolak pengajuan dengan alasan (FR-17)."""
    loan = _approval_loan_or_404(pk)
    try:
        reject_loan(loan.pk, request.user, request.POST.get("reason"))
    except ApprovalError as exc:
        messages.error(request, str(exc))
        return _render_approval_detail(request, loan)
    messages.success(request, f"Pengajuan #{loan.pk} ditolak.")
    return redirect("circulation:approval_list")


@active_login_required
def pickup_qr_image_view(request, pk):
    """PNG QR Pickup untuk pemilik pengajuan / pustakawan (FR-20).

    Hanya pada state APPROVED dengan token aktif; selain itu 404 agar
    QR tidak bocor pada state yang salah.
    """
    loan = get_object_or_404(Loan, pk=pk, pickup_token__isnull=False)
    if request.user.role != User.Role.PUSTAKAWAN and loan.user_id != request.user.pk:
        raise Http404("Tidak ditemukan.")
    if loan.status != LOAN_APPROVED or not loan.pickup_token:
        raise Http404("Tidak ditemukan.")
    return HttpResponse(
        make_pickup_qr_png(loan.pickup_token), content_type="image/png"
    )


def _handover_loan_preview(token):
    loan = verify_pickup_token((token or "").strip())
    return (
        Loan.objects.prefetch_related("items__book_copy__book")
        .select_related("user")
        .get(pk=loan.pk)
    )


@role_required(User.Role.PUSTAKAWAN)
def handover_view(request):
    """Halaman penyerahan: scan/input token lalu tampilkan item (FR-21 – FR-23).

    Hanya Pustakawan. Kamera memakai BarcodeDetector bawaan browser
    (tanpa dependensi); input manual selalu tersedia sebagai fallback.
    """
    context = {"token": "", "loan": None, "error": ""}
    if request.method == "POST":
        token = (request.POST.get("token") or "").strip()
        context["token"] = token
        try:
            context["loan"] = _handover_loan_preview(token)
        except PickupTokenError as exc:
            context["error"] = str(exc)
    return render(request, "circulation/handover.html", context)


@role_required(User.Role.PUSTAKAWAN)
@require_POST
def handover_confirm_view(request):
    """Konfirmasi penyerahan buku -> peminjaman aktif (FR-24 – FR-28)."""
    token = (request.POST.get("token") or "").strip()
    try:
        loan = activate_handover(token, request.user)
    except PickupTokenError as exc:
        messages.error(request, str(exc))
        return redirect("circulation:handover")
    return render(request, "circulation/handover_success.html", {"loan": loan})


@role_required(User.Role.PUSTAKAWAN)
def copy_identity_qr_view(request, pk):
    """PNG QR Identitas eksemplar (payload = copy_code).

    BERBEDA dari QR Pickup: identitas eksemplar, bukan token transaksi.
    """
    from apps.catalog.models import BookCopy

    copy = get_object_or_404(BookCopy, pk=pk)
    return HttpResponse(
        make_qr_png(copy.copy_code), content_type="image/png"
    )


@role_required(User.Role.PUSTAKAWAN)
def return_view(request):
    """Halaman pengembalian: lookup QR Identitas -> info peminjam (FR-30 – FR-40)."""
    context = {"code": "", "found": None, "error": ""}
    if request.method == "POST":
        code = (request.POST.get("code") or "").strip()
        context["code"] = code
        try:
            copy, item, loan = lookup_return(code)
        except ReturnError as exc:
            context["error"] = str(exc)
        else:
            context["found"] = {"copy": copy, "item": item, "loan": loan}
    return render(request, "circulation/return.html", context)


@role_required(User.Role.PUSTAKAWAN)
@require_POST
def return_confirm_view(request):
    """Konfirmasi pengembalian + denda (FR-41 – FR-49)."""
    code = (request.POST.get("code") or "").strip()
    condition = (request.POST.get("condition") or "BAIK").strip()
    lost = request.POST.get("lost") == "on"
    try:
        item = process_return(code, request.user, condition, lost)
    except ReturnError as exc:
        messages.error(request, str(exc))
        return redirect("circulation:return")
    return render(request, "circulation/return_success.html", {"item": item})


@role_required(User.Role.PUSTAKAWAN)
def return_history_view(request):
    """Riwayat pengembalian untuk administrasi (FR-47, FR-80)."""
    items = (
        LoanItem.objects.filter(returned_at__isnull=False)
        .select_related("loan__user", "book_copy__book", "processed_by")
        .order_by("-returned_at")
    )
    paginator = Paginator(items, 15)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(
        request, "circulation/return_history.html", {"page_obj": page_obj}
    )
