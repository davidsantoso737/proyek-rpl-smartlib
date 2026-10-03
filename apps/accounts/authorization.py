from functools import wraps

from django.contrib.auth import logout
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


def _deny_inactive(request):
    """Putus sesi akun non-aktif lalu arahkan ke halaman login."""
    logout(request)
    return redirect_to_login(request.get_full_path())


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
            if request.user.role not in roles:
                raise PermissionDenied(
                    "Anda tidak memiliki hak akses ke halaman ini."
                )
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator
