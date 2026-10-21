import logging
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.authorization import active_login_required
from apps.accounts.forms import ProfileForm

from .context import SESSION_LINK_CODE, build_profile_context
from .forms import TelegramPreferencesForm
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
from .models import TelegramAccount
from .services import TelegramError, send_text_message

logger = logging.getLogger(__name__)


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


@active_login_required
@require_GET
def link_open_view(request):
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

    target = f"https://t.me/{quote(username)}?start={quote(code)}"
    return HttpResponseRedirect(target)


@active_login_required
@require_POST
def link_cancel_view(request):
    cancel_pending_link(request.user)
    request.session.pop(SESSION_LINK_CODE, None)
    messages.success(request, "Kode tautan dibatalkan.")
    return _profile_redirect()


@active_login_required
@require_POST
def link_confirm_view(request):
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


@active_login_required
@require_POST
def unlink_view(request):
    account = get_object_or_404(
        TelegramAccount, user=request.user
    )
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
    account = get_object_or_404(
        TelegramAccount, user=request.user
    )
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
