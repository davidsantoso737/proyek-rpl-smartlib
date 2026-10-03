from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .authorization import active_login_required, role_required
from .forms import INPUT_CLASS, LoginForm, ProfileForm

User = get_user_model()


def _safe_next(request):
    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}
    ):
        return next_url
    return None


def login_view(request):
    """Login + identifikasi role (FR-01, FR-02, BR-02)."""
    if request.user.is_authenticated and request.user.is_active:
        return redirect("accounts:dashboard")
    if request.method == "POST":
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            return redirect(_safe_next(request) or "accounts:dashboard")
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
