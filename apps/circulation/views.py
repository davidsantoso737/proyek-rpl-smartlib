from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.authorization import (
    active_login_required,
    role_required,
)

from .models import Loan
from .services import (
    LoanRequestError,
    cart_add,
    cart_clear,
    cart_copies,
    cart_remove,
    get_cart_ids,
    request_loan,
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
