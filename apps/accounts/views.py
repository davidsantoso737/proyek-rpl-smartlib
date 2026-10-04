import logging

from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .authorization import active_login_required, role_required
from .forms import (
    INPUT_CLASS,
    MANAGEABLE_ROLES,
    LoginForm,
    ManagedUserCreateForm,
    ManagedUserUpdateForm,
    ProfileForm,
)

User = get_user_model()

logger = logging.getLogger(__name__)

USER_PAGE_SIZE = 15


def _safe_next(request):
    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}
    ):
        return next_url
    return None


LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 300


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _login_throttle_key(request):
    username = (request.POST.get("username") or "").strip().lower()
    return f"login-attempts:{_client_ip(request)}:{username}"


def login_view(request):
    """Login + identifikasi role (FR-01, FR-02, BR-02).

    Brute-force mitigation: maksimal 5 kegagalan per username+IP
    dalam 5 menit; kunci tanpa membocorkan validitas username
    (pesan generik yang sama).
    """
    if request.user.is_authenticated and request.user.is_active:
        return redirect("accounts:dashboard")
    if request.method == "POST":
        form = LoginForm(request, data=request.POST)
        throttle_key = _login_throttle_key(request)
        if cache.get(throttle_key, 0) >= LOGIN_MAX_ATTEMPTS:
            form.add_error(
                None,
                "Terlalu banyak percobaan masuk. "
                "Coba lagi beberapa menit kemudian.",
            )
        elif form.is_valid():
            cache.delete(throttle_key)
            login(request, form.get_user())
            return redirect(_safe_next(request) or "accounts:dashboard")
        else:
            attempts = cache.get(throttle_key, 0) + 1
            cache.set(throttle_key, attempts, LOGIN_LOCKOUT_SECONDS)
            logger.warning(
                "Login gagal untuk %s dari %s (%s/%s percobaan).",
                request.POST.get("username", "?"),
                _client_ip(request),
                attempts,
                LOGIN_MAX_ATTEMPTS,
            )
    else:
        form = LoginForm(request)
    return render(
        request,
        "accounts/login.html",
        {"form": form, "next": request.GET.get("next", "")},
    )


@require_POST
def logout_view(request):
    """Logout hanya via POST (FR-01)."""
    logout(request)
    return redirect("/accounts/login/?logged_out=1")


@active_login_required
def dashboard_view(request):
    """Dispatcher dashboard sesuai role (FR-02, BR-03)."""
    role = request.user.role
    if role == User.Role.SISWA:
        return redirect("accounts:dashboard_siswa")
    if role == User.Role.GURU:
        return redirect("accounts:dashboard_guru")
    if role == User.Role.PUSTAKAWAN:
        return redirect("accounts:dashboard_pustakawan")
    raise PermissionDenied("Role akun Anda tidak dikenal.")


@role_required(User.Role.SISWA)
def siswa_dashboard_view(request):
    return render(
        request,
        "accounts/dashboard.html",
        {
            "dashboard_title": "Dashboard Siswa",
            "dashboard_intro": (
                "Selamat datang di Digital Library. "
                "Kelola profil dan keamanan akun Anda dari sini."
            ),
        },
    )


@role_required(User.Role.GURU)
def guru_dashboard_view(request):
    return render(
        request,
        "accounts/dashboard.html",
        {
            "dashboard_title": "Dashboard Guru",
            "dashboard_intro": (
                "Selamat datang di Digital Library. "
                "Kelola profil dan keamanan akun Anda dari sini."
            ),
        },
    )


@role_required(User.Role.PUSTAKAWAN)
def pustakawan_dashboard_view(request):
    return render(
        request,
        "accounts/dashboard.html",
        {
            "dashboard_title": "Dashboard Pustakawan",
            "dashboard_intro": (
                "Area administratif perpustakaan. "
                "Pengelolaan lanjutan mengikuti requirement tiap modul."
            ),
        },
    )


@active_login_required
def profile_view(request):
    """Lihat/ubah profil milik sendiri (FR-03, BR-03).

    Form selalu terikat ke ``request.user`` sehingga tidak ada parameter
    id pengguna: profil orang lain tidak dapat diakses maupun diubah.
    """
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Profil berhasil diperbarui.")
            return redirect("accounts:profile")
    else:
        form = ProfileForm(instance=request.user)
    return render(request, "accounts/profile.html", {"form": form})


@active_login_required
def password_change_view(request):
    """Ubah password sendiri dengan verifikasi password lama (NFR-04)."""
    if request.method == "POST":
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            form.save()
            update_session_auth_hash(request, form.user)
            messages.success(request, "Password berhasil diubah.")
            return redirect("accounts:profile")
    else:
        form = PasswordChangeForm(request.user)
    for field in form.fields.values():
        field.widget.attrs.update({"class": INPUT_CLASS})
    return render(request, "accounts/password_change.html", {"form": form})


def _manageable_user_or_404(pk):
    """Hanya Siswa/Guru yang dapat dikelola via public UI (scope)."""
    return get_object_or_404(User, pk=pk, role__in=MANAGEABLE_ROLES)


@role_required(User.Role.PUSTAKAWAN)
def user_list_view(request):
    """Daftar akun Siswa/Guru (FR-04, BR-77). Hanya Pustakawan."""
    q = (request.GET.get("q") or "").strip()
    role_filter = request.GET.get("role") or ""
    users = User.objects.filter(role__in=MANAGEABLE_ROLES)
    if role_filter in MANAGEABLE_ROLES:
        users = users.filter(role=role_filter)
    else:
        role_filter = ""
    if q:
        users = users.filter(
            Q(username__icontains=q) | Q(full_name__icontains=q)
        )
    users = users.order_by("role", "username")
    paginator = Paginator(users, USER_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(
        request,
        "accounts/user_list.html",
        {
            "page_obj": page_obj,
            "q": q,
            "role_filter": role_filter,
            "querystring": params.urlencode(),
            "result_count": paginator.count,
        },
    )


@role_required(User.Role.PUSTAKAWAN)
def user_create_view(request, role):
    """Tambah akun Siswa/Guru (FR-04, FR-07, BR-77). Hanya Pustakawan."""
    if role not in MANAGEABLE_ROLES:
        raise PermissionDenied(
            "Role tersebut tidak dapat dibuat melalui halaman ini."
        )
    if request.method == "POST":
        form = ManagedUserCreateForm(request.POST, role=role)
        if form.is_valid():
            user = form.save()
            logger.info(
                "Pustakawan %s membuat akun %s (%s).",
                request.user.username,
                user.username,
                user.role,
            )
            messages.success(
                request,
                f"Akun {user.username} ({user.get_role_display()}) "
                "berhasil dibuat.",
            )
            return redirect("accounts:user_list")
    else:
        form = ManagedUserCreateForm(role=role)
    return render(
        request,
        "accounts/user_form.html",
        {
            "form": form,
            "form_title": (
                "Tambah Siswa" if role == User.Role.SISWA else "Tambah Guru"
            ),
            "submit_label": "Buat akun",
            "cancel_url": "accounts:user_list",
        },
    )


@role_required(User.Role.PUSTAKAWAN)
def user_update_view(request, pk):
    """Ubah data Siswa/Guru (FR-05, FR-07, BR-77). Hanya Pustakawan."""
    user = _manageable_user_or_404(pk)
    if request.method == "POST":
        form = ManagedUserUpdateForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            logger.info(
                "Pustakawan %s mengubah akun %s (%s).",
                request.user.username,
                user.username,
                user.role,
            )
            messages.success(
                request, f"Data {user.username} berhasil diperbarui."
            )
            return redirect("accounts:user_list")
    else:
        form = ManagedUserUpdateForm(instance=user)
    return render(
        request,
        "accounts/user_form.html",
        {
            "form": form,
            "form_title": f"Ubah {user.username}",
            "submit_label": "Simpan perubahan",
            "cancel_url": "accounts:user_list",
        },
    )


@role_required(User.Role.PUSTAKAWAN)
@require_POST
def user_toggle_active_view(request, pk):
    """Aktif/nonaktifkan akun Siswa/Guru (FR-06, BR-78). Hanya Pustakawan."""
    user = _manageable_user_or_404(pk)
    user.is_active = not user.is_active
    user.save(update_fields=["is_active", "updated_at"])
    logger.info(
        "Pustakawan %s %s akun %s (%s).",
        request.user.username,
        "mengaktifkan" if user.is_active else "menonaktifkan",
        user.username,
        user.role,
    )
    messages.success(
        request,
        f"Akun {user.username} berhasil "
        f"{'diaktifkan' if user.is_active else 'dinonaktifkan'}.",
    )
    return redirect("accounts:user_list")
