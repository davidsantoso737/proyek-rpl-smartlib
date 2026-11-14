from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.authorization import active_login_required, role_required
from apps.accounts.forms import ProfileForm

from .connection import (
    approve_connection,
    cancel_pending_request,
    reject_connection,
    request_connection,
    resend_otp,
    unlink_active,
    verify_otp,
)
from .context import build_profile_context
from .forms import WhatsAppOtpForm, WhatsAppPhoneForm, WhatsAppPreferencesForm
from .models import WhatsAppConnection


def _profile_redirect():
    return redirect("accounts:profile")


def _render_profile(request, **context):
    return render(
        request,
        "accounts/profile.html",
        {
            "form": ProfileForm(instance=request.user),
            **build_profile_context(request, **context),
        },
    )


@active_login_required
@require_POST
def request_connection_view(request):
    form = WhatsAppPhoneForm(request.POST)
    if not form.is_valid():
        return _render_profile(request, phone_form=form)

    result = request_connection(request.user, form.cleaned_data["phone"])
    if result.status == "created":
        messages.success(
            request,
            "Pengajuan penautan WhatsApp dikirim. Pustakawan akan "
            "memeriksa nomor Anda sebelum kode OTP dikirim.",
        )
    elif result.status == "already_pending":
        messages.info(
            request,
            "Pengajuan nomor ini masih menunggu persetujuan pustakawan.",
        )
    elif result.status == "awaiting_verification":
        messages.info(
            request,
            "Nomor ini sudah disetujui. Masukkan kode OTP yang dikirim "
            "ke WhatsApp Anda.",
        )
    elif result.status == "already_active":
        messages.info(
            request, "Nomor WhatsApp ini sudah terhubung ke akun Anda."
        )
    elif result.status == "phone_in_use":
        messages.error(
            request,
            "Nomor WhatsApp ini sudah dipakai akun lain atau sedang "
            "dalam proses penautan.",
        )
    elif result.status == "inactive":
        messages.error(request, "Akun Anda tidak aktif.")
    else:
        messages.error(
            request,
            "Pengajuan penautan tidak dapat dibuat saat ini. Coba lagi "
            "beberapa saat lagi.",
        )
    return _profile_redirect()


@active_login_required
@require_POST
def cancel_request_view(request):
    result = cancel_pending_request(request.user)
    if result.status == "ok":
        messages.success(request, "Pengajuan penautan WhatsApp dibatalkan.")
    else:
        messages.info(
            request, "Tidak ada pengajuan penautan yang bisa dibatalkan."
        )
    return _profile_redirect()


@active_login_required
@require_POST
def unlink_view(request):
    result = unlink_active(request.user)
    if result.status == "ok":
        messages.success(request, "Tautan WhatsApp diputus.")
    else:
        messages.info(request, "Tidak ada tautan WhatsApp aktif.")
    return _profile_redirect()


@active_login_required
@require_POST
def verify_otp_view(request):
    form = WhatsAppOtpForm(request.POST)
    if not form.is_valid():
        return _render_profile(request, otp_form=form)

    result = verify_otp(request.user, form.cleaned_data["code"])
    if result.status == "ok":
        messages.success(request, "WhatsApp berhasil dihubungkan.")
    elif result.status == "invalid":
        messages.error(
            request,
            "Kode OTP salah. Sisa percobaan: "
            f"{result.attempts_left}. Periksa kembali pesan WhatsApp Anda.",
        )
    elif result.status == "expired":
        messages.warning(
            request,
            "Kode OTP sudah kedaluwarsa. Minta kode baru untuk "
            "melanjutkan.",
        )
    elif result.status == "too_many":
        messages.error(
            request,
            "Terlalu banyak percobaan. Pengajuan penautan dibatalkan. "
            "Ajukan penautan ulang dari halaman Profil.",
        )
    elif result.status == "phone_in_use":
        messages.error(
            request,
            "Nomor WhatsApp ini sudah aktif pada akun lain. Ajukan "
            "nomor lain atau hubungi pustakawan.",
        )
    elif result.status == "no_otp":
        messages.info(
            request,
            "Kode OTP belum tersedia. Minta kode baru untuk melanjutkan.",
        )
    elif result.status == "inactive":
        messages.error(request, "Akun Anda tidak aktif.")
    else:
        messages.info(
            request,
            "Tidak ada pengajuan yang menunggu verifikasi OTP.",
        )
    return _profile_redirect()


@active_login_required
@require_POST
def resend_otp_view(request):
    result = resend_otp(request.user)
    if result.status == "ok":
        messages.success(request, "Kode OTP baru sudah dikirim ke WhatsApp.")
    elif result.status == "cooldown":
        messages.info(
            request,
            "Tunggu "
            f"{result.retry_after} detik sebelum meminta kode baru.",
        )
    elif result.status == "send_failed":
        messages.error(
            request,
            "Kode OTP belum dapat dikirim. Periksa koneksi WhatsApp Anda "
            "atau coba lagi beberapa saat lagi.",
        )
    elif result.status == "inactive":
        messages.error(request, "Akun Anda tidak aktif.")
    else:
        messages.info(
            request,
            "Tidak ada pengajuan yang menunggu verifikasi OTP.",
        )
    return _profile_redirect()


@active_login_required
@require_POST
def preferences_view(request):
    connection = (
        WhatsAppConnection.objects.filter(
            user=request.user,
            status=WhatsAppConnection.Status.ACTIVE,
        )
        .order_by("-verified_at", "-created_at")
        .first()
    )
    if connection is None:
        messages.info(request, "Tidak ada tautan WhatsApp aktif.")
        return _profile_redirect()

    form = WhatsAppPreferencesForm(request.POST, instance=connection)
    if form.is_valid():
        form.save()
        messages.success(request, "Preferensi WhatsApp disimpan.")
        return _profile_redirect()

    messages.error(
        request, "Preferensi tidak valid. Periksa kembali pilihan Anda."
    )
    return _render_profile(request, preferences_form=form)


@role_required("PUSTAKAWAN")
@require_GET
def verification_list_view(request):
    pending = (
        WhatsAppConnection.objects.filter(
            status=WhatsAppConnection.Status.PENDING
        )
        .select_related("user")
        .order_by("requested_at", "created_at")
    )
    recent = (
        WhatsAppConnection.objects.filter(
            status__in=[
                WhatsAppConnection.Status.ACTIVE,
                WhatsAppConnection.Status.APPROVED,
                WhatsAppConnection.Status.REJECTED,
                WhatsAppConnection.Status.REVOKED,
            ]
        )
        .select_related("user", "approved_by")
        .order_by("-updated_at")[:10]
    )
    return render(
        request,
        "whatsapp/verification_list.html",
        {
            "pending_connections": pending,
            "recent_connections": recent,
            "pending_count": pending.count(),
        },
    )


@role_required("PUSTAKAWAN")
@require_GET
def verification_detail_view(request, pk):
    connection = (
        WhatsAppConnection.objects.select_related("user", "approved_by")
        .filter(pk=pk)
        .first()
    )
    if connection is None:
        messages.error(request, "Pengajuan penautan tidak ditemukan.")
        return redirect("whatsapp:verification_list")
    history = (
        WhatsAppConnection.objects.filter(user=connection.user)
        .select_related("approved_by")
        .order_by("-created_at", "-pk")
    )
    return render(
        request,
        "whatsapp/verification_detail.html",
        {
            "connection": connection,
            "history": history,
        },
    )


@role_required("PUSTAKAWAN")
@require_POST
def verification_approve_view(request, pk):
    result = approve_connection(pk, request.user)
    if result.status == "ok":
        messages.success(
            request,
            "Penautan WhatsApp disetujui. Kode OTP sudah dikirim ke "
            "nomor pengguna.",
        )
    elif result.status == "otp_failed":
        messages.warning(
            request,
            "Penautan disetujui, tetapi kode OTP gagal dikirim. Pengguna "
            "dapat meminta kode baru dari halaman Profil.",
        )
    elif result.status == "missing":
        messages.error(request, "Permintaan penautan tidak ditemukan.")
    elif result.status == "not_pending":
        messages.info(request, "Permintaan ini sudah diproses sebelumnya.")
    elif result.status == "phone_in_use":
        messages.error(
            request,
            "Nomor WhatsApp pada pengajuan ini sudah aktif pada akun lain.",
        )
    elif result.status == "inactive":
        messages.error(request, "Akun pengguna tidak aktif.")
    else:
        messages.error(request, "Penautan WhatsApp tidak dapat disetujui.")
    return redirect("whatsapp:verification_list")


@role_required("PUSTAKAWAN")
@require_POST
def verification_reject_view(request, pk):
    note = str(request.POST.get("note") or "").strip()[:1000]
    result = reject_connection(pk, request.user, note)
    if result.status == "ok":
        messages.success(request, "Permintaan penautan ditolak.")
    elif result.status == "missing":
        messages.error(request, "Permintaan penautan tidak ditemukan.")
    elif result.status == "not_pending":
        messages.info(request, "Permintaan ini sudah diproses sebelumnya.")
    else:
        messages.error(request, "Permintaan tidak dapat ditolak.")
    return redirect("whatsapp:verification_list")
