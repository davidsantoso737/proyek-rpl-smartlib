from functools import wraps

from django.contrib.auth import logout
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

# P1 — First-login enforcement: view yang tetap boleh diakses saat
# flag must_change_password=True (selain itu semua fitur diblokir
# dan diarahkan ke password change; bukan sekadar warning).
MUST_CHANGE_ALLOWED_VIEWS = frozenset(
    {
        "accounts:password_change",
        "accounts:logout",
    }
)


def _deny_inactive(request):
    """Putus sesi akun non-aktif lalu arahkan ke halaman login."""
    logout(request)
    return redirect_to_login(request.get_full_path())


def _must_change_redirect(request):
    """Redirect ke password change bila flag masih True.

    Mengembalikan response redirect atau None. View yang terdaftar
    di MUST_CHANGE_ALLOWED_VIEWS dikecualikan agar tidak terjadi
    redirect loop (user harus bisa membuka form ganti password
    dan logout).
    """
    user = request.user
    if not getattr(user, "must_change_password", False):
        return None
    resolver_match = getattr(request, "resolver_match", None)
    view_name = getattr(resolver_match, "view_name", None)
    if view_name in MUST_CHANGE_ALLOWED_VIEWS:
        return None
    return redirect("accounts:password_change")


def active_login_required(view_func):
    """Login-required + penolakan server-side untuk akun tidak aktif.

    Menutup celah sesi lama yang akunnya dinonaktifkan setelah login:
    selain di login, setiap akses fitur authenticated memeriksa
    ``is_active`` (BR-02, BR-03, NFR-02, NFR-03).
    """

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not request.user.is_active:
            return _deny_inactive(request)
        enforced = _must_change_redirect(request)
        if enforced is not None:
            return enforced
        return view_func(request, *args, **kwargs)

    return _wrapped


def role_required(*roles):
    """Batasi view hanya untuk role tertentu (server-side).

    - Anonim -> redirect ke login.
    - Terautentikasi tapi tidak aktif -> sesi diputus + redirect ke login.
    - Role tidak diizinkan -> 403 (bukan sekadar menyembunyikan tombol).
    Melindungi view, POST, dan service yang memakai decorator ini
    (BR-03, NFR-02, NFR-03).
    """

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not request.user.is_active:
                return _deny_inactive(request)
            enforced = _must_change_redirect(request)
            if enforced is not None:
                return enforced
            if request.user.role not in roles:
                raise PermissionDenied(
                    "Anda tidak memiliki hak akses ke halaman ini."
                )
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator
