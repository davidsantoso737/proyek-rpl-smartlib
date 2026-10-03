from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.authorization import (
    active_login_required,
    role_required,
)

from .models import EbookLoan, Loan, LoanItem
from .services import (
    EBOOK_PENDING,
    LOAN_APPROVED,
    LOAN_PENDING,
    ApprovalError,
    EbookLoanError,
    LoanRequestError,
    PickupTokenError,
    ReturnError,
    activate_handover,
    approve_ebook_loan,
    available_substitutes,
    approve_loan,
    cart_add,
    cart_clear,
    cart_copies,
    cart_remove,
    get_active_ebook_loan,
    get_cart_ids,
    lookup_return,
    make_pickup_qr_png,
    make_qr_png,
    process_return,
    reject_ebook_loan,
    reject_loan,
    request_ebook_loan,
    request_loan,
    resolve_ebook_file,
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


# --- E-book (FR-54 – FR-63) ---

EBOOK_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "epub": "application/epub+zip",
}


@role_required(*REQUESTER_ROLES)
@require_POST
def ebook_request_view(request, pk):
    """Ajukan peminjaman e-book (FR-55)."""
    try:
        loan = request_ebook_loan(request.user, pk)
    except EbookLoanError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"Pengajuan {loan.ebook.title} dibuat dan menunggu "
            "persetujuan pustakawan.",
        )
    return redirect("catalog:ebook_detail", pk=pk)


@active_login_required
def my_ebooks_view(request):
    """E-book saya: akses aktif, menunggu, dan riwayat (FR-63)."""
    from django.utils import timezone

    loans = (
        EbookLoan.objects.filter(user=request.user)
        .select_related("ebook")
        .order_by("-requested_at")
    )
    now = timezone.now()
    for loan in loans:
        loan.is_usable = (
            loan.status == "APPROVED"
            and loan.started_at is not None
            and loan.started_at <= now
            and loan.expires_at is not None
            and loan.expires_at > now
        )
    return render(request, "circulation/ebook_my.html", {"loans": loans})


def _usable_ebook_loan_or_403(request, pk):
    """Syarat reader: terautentikasi + pemilik + APPROVED + belum kedaluarsa
    (NFR-24 – NFR-26). Gagal -> 403 dengan pesan yang jelas."""
    loan = get_object_or_404(
        EbookLoan.objects.select_related("ebook", "user"), pk=pk
    )
    if loan.user_id != request.user.pk:
        raise PermissionDenied("Akses e-book ini bukan milik Anda.")
    if loan.status != "APPROVED":
        raise PermissionDenied(
            "Peminjaman e-book ini belum disetujui atau sudah ditutup. "
            "Ajukan peminjaman kembali bila diperlukan."
        )
    active = get_active_ebook_loan(request.user, loan.ebook)
    if active is None or active.pk != loan.pk:
        raise PermissionDenied(
            "Masa akses e-book telah berakhir. "
            "Ajukan peminjaman kembali untuk membaca lagi."
        )
    return loan


@active_login_required
def ebook_reader_view(request, pk):
    """Reader terproteksi (FR-57)."""
    loan = _usable_ebook_loan_or_403(request, pk)
    return render(request, "circulation/ebook_reader.html", {"loan": loan})


@active_login_required
def ebook_file_view(request, pk):
    """Stream file dari storage privat setelah otorisasi penuh (NFR-26).

    Tidak ada URL publik untuk file ini: hanya rute ini yang
    menyentuh storage, selalu sesudah pemeriksaan di atas.
    """
    loan = _usable_ebook_loan_or_403(request, pk)
    try:
        path = resolve_ebook_file(loan.ebook)
    except EbookLoanError as exc:
        raise Http404(str(exc))
    content_type = EBOOK_CONTENT_TYPES.get(
        (loan.ebook.file_type or "").lower(), "application/octet-stream"
    )
    return FileResponse(
        open(path, "rb"),
        content_type=content_type,
        filename=loan.ebook.file_name,
    )


@role_required(User.Role.PUSTAKAWAN)
def ebook_approval_list_view(request):
    """Pengajuan e-book menunggu persetujuan (FR-56)."""
    loans = (
        EbookLoan.objects.filter(status=EBOOK_PENDING)
        .select_related("user", "ebook")
        .order_by("requested_at")
    )
    return render(
        request, "circulation/ebook_approval_list.html", {"loans": loans}
    )


@role_required(User.Role.PUSTAKAWAN)
def ebook_approval_detail_view(request, pk):
    loan = get_object_or_404(
        EbookLoan.objects.select_related("user", "ebook"), pk=pk
    )
    return render(
        request, "circulation/ebook_approval_detail.html", {"loan": loan}
    )


@role_required(User.Role.PUSTAKAWAN)
@require_POST
def ebook_approve_view(request, pk):
    try:
        loan = approve_ebook_loan(pk, request.user)
    except EbookLoanError as exc:
        messages.error(request, str(exc))
        return redirect("circulation:ebook_approval_detail", pk=pk)
    messages.success(
        request, f"{loan.ebook.title} disetujui; akses dimulai sekarang."
    )
    return redirect("circulation:ebook_approval_list")


@role_required(User.Role.PUSTAKAWAN)
@require_POST
def ebook_reject_view(request, pk):
    try:
        reject_ebook_loan(pk, request.user, request.POST.get("reason"))
    except EbookLoanError as exc:
        messages.error(request, str(exc))
        return redirect("circulation:ebook_approval_detail", pk=pk)
    messages.success(request, "Pengajuan e-book ditolak.")
    return redirect("circulation:ebook_approval_list")
