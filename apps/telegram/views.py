import logging

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.authorization import active_login_required, role_required
from apps.accounts.forms import ProfileForm

from .connection import (
    SESSION_CONNECTION_TOKEN,
    STATE_ACTIVE,
    STATE_AWAITING,
    approve_connection,
    build_deep_link,
    cancel_pending_connection,
    create_connection_token,
    get_connection_context,
    reject_connection,
    request_connection,
    revoke_active_connection,
)
from .context import SESSION_LINK_CODE, build_profile_context
from .forms import (
    TelegramConnectionPreferencesForm,
    TelegramPreferencesForm,
)
from .linking import (
    STATUS_ALREADY_LINKED,
    STATUS_CONFLICT,
    STATUS_EXPIRED,
    STATUS_MISSING,
    STATUS_SUCCESS,
    STATUS_UNCLAIMED,
    cancel_pending_link,
    code_matches_token,
    confirm_link_token,
    create_link_token,
    get_pending_link_token,
    unlink_account,
)
from .models import TelegramAccount, TelegramConnection
from .services import TelegramError, send_text_message

logger = logging.getLogger(__name__)


def _legacy_enabled():
    return bool(
        getattr(settings, "TELEGRAM_LEGACY_LINKING_ENABLED", False)
    )


def _profile_redirect():
    return redirect("accounts:profile")


def _send_best_effort(chat_id, text, *, account=None):
    if account is not None and not account.notifications_enabled:
        return
    try:
        send_text_message(chat_id, text, require_enabled=False)
    except TelegramError as exc:
        logger.warning(
            "Pesan Telegram gagal dikirim: %s",
            type(exc).__name__,
        )


@active_login_required
@require_POST
def link_start_view(request):
    """Mulai/melanjutkan penautan Telegram.

    Tanpa pengajuan aktif, view ini membuat pengajuan otomatis untuk
    diproses pustakawan. Bila pengajuan sudah disetujui, view ini
    membuat tautan konfirmasi sekali pakai untuk dibuka di bot.
    """
    if _legacy_enabled():
        return _legacy_link_start(request)

    result = create_connection_token(request.user)
    if result.status == "ok":
        request.session[SESSION_CONNECTION_TOKEN] = result.code
        messages.success(
            request,
            "Tautan konfirmasi dibuat. Buka bot dalam 10 menit, lalu "
            "bagikan nomor HP Anda.",
        )
        return _profile_redirect()

    if result.status == "phone_changed":
        request_connection(request.user)
        request.session.pop(SESSION_CONNECTION_TOKEN, None)
        messages.info(
            request,
            "Nomor HP berubah setelah persetujuan. Pengajuan penautan "
            "baru dikirim ke pustakawan.",
        )
        return _profile_redirect()

    if result.status == "pending_admin":
        messages.info(
            request,
            "Pengajuan penautan masih menunggu persetujuan pustakawan.",
        )
        return _profile_redirect()

    if result.status == "already_active":
        messages.info(request, "Akun Telegram sudah terhubung.")
        return _profile_redirect()

    if result.status == "no_phone":
        messages.error(
            request,
            "Lengkapi nomor HP di profil sebelum menghubungkan Telegram.",
        )
        return _profile_redirect()

    if result.status == "inactive":
        messages.error(request, "Akun Anda tidak aktif.")
        return _profile_redirect()

    # Belum ada pengajuan yang disetujui: buat pengajuan otomatis.
    req = request_connection(request.user)
    if req.status == "created":
        messages.success(
            request,
            "Pengajuan penautan Telegram dikirim. Pustakawan akan "
            "memeriksa nomor HP Anda sebelum konfirmasi di bot.",
        )
    elif req.status == "already_pending":
        messages.info(
            request,
            "Pengajuan penautan sudah menunggu persetujuan pustakawan.",
        )
    elif req.status == "awaiting_confirmation":
        token = create_connection_token(request.user)
        if token.status == "ok":
            request.session[SESSION_CONNECTION_TOKEN] = token.code
            messages.success(
                request,
                "Tautan konfirmasi dibuat. Buka bot dalam 10 menit, lalu "
                "bagikan nomor HP Anda.",
            )
        else:
            messages.info(
                request,
                "Pengajuan penautan sudah disetujui. Muat ulang halaman "
                "profil untuk melanjutkan konfirmasi.",
            )
    elif req.status == "already_active":
        messages.info(request, "Akun Telegram sudah terhubung.")
    elif req.status == "no_phone":
        messages.error(
            request,
            "Lengkapi nomor HP di profil sebelum menghubungkan Telegram.",
        )
    elif req.status == "inactive":
        messages.error(request, "Akun Anda tidak aktif.")
    else:
        messages.error(
            request,
            "Pengajuan penautan tidak dapat dibuat saat ini. Coba lagi "
            "beberapa saat lagi.",
        )
    return _profile_redirect()


@active_login_required
@require_GET
def link_open_view(request):
    if _legacy_enabled():
        return _legacy_link_open(request)

    raw_token = request.session.get(SESSION_CONNECTION_TOKEN)
    context = get_connection_context(request.user, raw_token)
    if context["state"] in (STATE_ACTIVE,):
        messages.info(request, "Akun Telegram sudah terhubung.")
        return _profile_redirect()
    if context["state"] == "pending":
        messages.info(
            request,
            "Permintaan penautan sedang menunggu persetujuan pustakawan.",
        )
        return _profile_redirect()
    if (
        context["state"] != STATE_AWAITING
        or not context["link_ready"]
        or not raw_token
    ):
        request.session.pop(SESSION_CONNECTION_TOKEN, None)
        messages.error(
            request,
            "Tautan Telegram tidak aktif lagi. Buat tautan baru.",
        )
        return _profile_redirect()

    target = build_deep_link(raw_token)
    if not target:
        messages.error(
            request,
            "Layanan tautan Telegram belum siap. Hubungi pustakawan.",
        )
        return _profile_redirect()
    return HttpResponseRedirect(target)


@active_login_required
@require_POST
def link_cancel_view(request):
    if _legacy_enabled():
        return _legacy_link_cancel(request)
    cancel_pending_connection(request.user)
    request.session.pop(SESSION_CONNECTION_TOKEN, None)
    messages.success(request, "Tautan Telegram dibatalkan.")
    return _profile_redirect()


@active_login_required
@require_POST
def link_confirm_view(request):
    if not _legacy_enabled():
        messages.info(
            request,
            "Konfirmasi tidak diperlukan. Verifikasi dilakukan lewat "
            "kontak Telegram dan persetujuan pustakawan.",
        )
        return _profile_redirect()
    return _legacy_link_confirm(request)


@active_login_required
@require_POST
def unlink_view(request):
    chat_id, connection = revoke_active_connection(request.user)
    if connection is not None:
        request.session.pop(SESSION_CONNECTION_TOKEN, None)
        _send_best_effort(
            chat_id,
            "Tautan akun SmartLib diputus. Notifikasi otomatis ke chat "
            "ini dihentikan.",
        )
        messages.success(request, "Tautan Telegram diputus.")
        return _profile_redirect()

    account = TelegramAccount.objects.filter(user=request.user).first()
    if account is None:
        messages.info(request, "Tidak ada tautan Telegram aktif.")
        return _profile_redirect()
    chat_id = account.chat_id
    unlink_account(request.user)
    request.session.pop(SESSION_LINK_CODE, None)
    _send_best_effort(
        chat_id,
        "Tautan akun SmartLib diputus. Notifikasi otomatis ke chat ini "
        "dihentikan.",
        account=account,
    )
    messages.success(request, "Tautan Telegram diputus.")
    return _profile_redirect()


@active_login_required
@require_POST
def preferences_view(request):
    connection = (
        TelegramConnection.objects.filter(
            user=request.user,
            status=TelegramConnection.Status.ACTIVE,
        )
        .order_by("-approved_at", "-created_at")
        .first()
    )
    if connection is not None:
        form = TelegramConnectionPreferencesForm(
            request.POST, instance=connection
        )
        if form.is_valid():
            form.save()
            messages.success(request, "Preferensi Telegram disimpan.")
            return _profile_redirect()
        messages.error(
            request,
            "Preferensi tidak valid. Periksa kembali pilihan Anda.",
        )
        return render(
            request,
            "accounts/profile.html",
            {
                "form": ProfileForm(instance=request.user),
                **build_profile_context(request, telegram_form=form),
            },
        )

    account = TelegramAccount.objects.filter(user=request.user).first()
    if account is None:
        messages.info(request, "Tidak ada tautan Telegram aktif.")
        return _profile_redirect()
    form = TelegramPreferencesForm(request.POST, instance=account)
    if form.is_valid():
        form.save()
        messages.success(request, "Preferensi Telegram disimpan.")
        return _profile_redirect()
    messages.error(
        request, "Preferensi tidak valid. Periksa kembali pilihan Anda."
    )
    return render(
        request,
        "accounts/profile.html",
        {
            "form": ProfileForm(instance=request.user),
            **build_profile_context(request, telegram_form=form),
        },
    )


@role_required("PUSTAKAWAN")
@require_GET
def verification_list_view(request):
    pending = (
        TelegramConnection.objects.filter(
            status=TelegramConnection.Status.PENDING
        )
        .select_related("user")
        .order_by("requested_at", "created_at")
    )
    recent = (
        TelegramConnection.objects.filter(
            status__in=[
                TelegramConnection.Status.ACTIVE,
                TelegramConnection.Status.AWAITING_CONTACT,
                TelegramConnection.Status.REJECTED,
            ]
        )
        .select_related("user", "approved_by")
        .order_by("-updated_at")[:10]
    )
    return render(
        request,
        "telegram/verification_list.html",
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
        TelegramConnection.objects.select_related(
            "user", "approved_by"
        )
        .filter(pk=pk)
        .first()
    )
    if connection is None:
        messages.error(request, "Pengajuan penautan tidak ditemukan.")
        return redirect("telegram:verification_list")
    history = (
        TelegramConnection.objects.filter(user=connection.user)
        .select_related("approved_by")
        .order_by("-created_at", "-pk")
    )
    return render(
        request,
        "telegram/verification_detail.html",
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
        messages.success(request, "Penautan Telegram disetujui.")
    elif result.status == "missing":
        messages.error(request, "Permintaan penautan tidak ditemukan.")
    elif result.status == "not_pending":
        messages.info(
            request,
            "Permintaan ini sudah diproses sebelumnya.",
        )
    elif result.status == "already_active":
        messages.error(
            request,
            "Pengguna sudah memiliki koneksi Telegram aktif.",
        )
    elif result.status == "legacy_conflict":
        messages.error(
            request,
            "Pengguna masih memiliki tautan Telegram lama. Minta pengguna "
            "memutus tautan lama terlebih dahulu.",
        )
    elif result.status == "inactive":
        messages.error(request, "Akun pengguna tidak aktif.")
    elif result.status == "no_phone":
        messages.error(
            request,
            "Pengguna belum memiliki nomor HP yang valid.",
        )
    elif result.status == "phone_changed":
        messages.error(
            request,
            "Nomor HP pengguna berubah setelah pengajuan dibuat. Minta "
            "pengguna menyimpan ulang profil agar pengajuan diperbarui.",
        )
    elif result.status == "conflict":
        messages.error(
            request,
            "Akun Telegram pada pengajuan ini sudah dipakai akun lain.",
        )
    elif result.status == "invalid":
        messages.error(
            request,
            "Data kontak Telegram pada permintaan ini tidak lengkap.",
        )
    else:
        messages.error(request, "Penautan Telegram tidak dapat disetujui.")
    return redirect("telegram:verification_list")


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
        messages.info(
            request,
            "Permintaan ini sudah diproses sebelumnya.",
        )
    else:
        messages.error(request, "Permintaan tidak dapat ditolak.")
    return redirect("telegram:verification_list")


def _legacy_link_start(request):
    if TelegramAccount.objects.filter(user=request.user).exists():
        messages.info(request, "Akun Telegram sudah terhubung.")
        return _profile_redirect()
    code, _token = create_link_token(request.user)
    request.session[SESSION_LINK_CODE] = code
    messages.success(
        request,
        "Tautan Telegram dibuat. Buka bot dalam 10 menit untuk "
        "menyelesaikan tautan.",
    )
    return _profile_redirect()


def _legacy_link_open(request):
    if TelegramAccount.objects.filter(user=request.user).exists():
        messages.info(request, "Akun Telegram sudah terhubung.")
        return _profile_redirect()

    username = (getattr(settings, "TELEGRAM_BOT_USERNAME", "") or "").strip()
    if not username:
        messages.error(
            request,
            "Layanan tautan Telegram belum siap. Hubungi pustakawan.",
        )
        return _profile_redirect()

    token = get_pending_link_token(request.user)
    code = request.session.get(SESSION_LINK_CODE)
    if token is None or not code_matches_token(code, token):
        request.session.pop(SESSION_LINK_CODE, None)
        messages.error(
            request,
            "Tautan Telegram tidak aktif lagi. Buat tautan baru.",
        )
        return _profile_redirect()

    from urllib.parse import quote

    target = f"https://t.me/{quote(username)}?start={quote(code)}"
    return HttpResponseRedirect(target)


def _legacy_link_cancel(request):
    cancel_pending_link(request.user)
    request.session.pop(SESSION_LINK_CODE, None)
    messages.success(request, "Kode tautan dibatalkan.")
    return _profile_redirect()


def _legacy_link_confirm(request):
    result = confirm_link_token(request.user)
    if result.status == STATUS_SUCCESS:
        request.session.pop(SESSION_LINK_CODE, None)
        _send_best_effort(
            result.account.chat_id,
            "Tautan akun SmartLib ke Telegram berhasil. Preferensi "
            "notifikasi dapat diubah di halaman Profil.",
            account=result.account,
        )
        messages.success(request, "Akun Telegram berhasil ditautkan.")
        return _profile_redirect()

    if result.status == STATUS_UNCLAIMED:
        messages.error(
            request,
            "Tautan belum dibuka di bot. Buka tautan Telegram terlebih "
            "dahulu.",
        )
    elif result.status in (STATUS_EXPIRED, STATUS_MISSING):
        messages.error(
            request,
            "Kode tautan tidak aktif lagi. Buat kode baru.",
        )
    elif result.status == STATUS_CONFLICT:
        messages.error(
            request,
            "Akun Telegram ini sudah terhubung ke akun SmartLib lain.",
        )
    elif result.status == STATUS_ALREADY_LINKED:
        messages.info(
            request,
            "Akun SmartLib ini sudah memiliki tautan Telegram.",
        )
    else:
        messages.error(
            request,
            "Tautan Telegram tidak dapat dikonfirmasi. Buat kode baru.",
        )
    return _profile_redirect()
