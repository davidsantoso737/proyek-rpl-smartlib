from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import redirect, render

from apps.accounts.authorization import role_required

from .forms import PolicyForm
from .models import LibrarySetting
from .services import PolicyError, get_active_setting, update_policy

User = get_user_model()


@role_required(User.Role.PUSTAKAWAN)
def policy_view(request):
    """Lihat konfigurasi aktif + ubah kebijakan (FR-50 – FR-53E).

    Hanya Pustakawan. GET menampilkan versi aktif (atau form kosong
    bila belum ada); POST membuat versi baru tanpa mengubah riwayat.
    """
    active = get_active_setting()
    if request.method == "POST":
        form = PolicyForm(request.POST)
        if form.is_valid():
            try:
                update_policy(request.user, **form.cleaned_data)
            except PolicyError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    "Kebijakan berhasil diperbarui dan berlaku untuk "
                    "transaksi baru. Riwayat tidak berubah.",
                )
                return redirect("core:policy")
    else:
        form = PolicyForm(instance=active)
    history = LibrarySetting.objects.order_by("-effective_from")[:10]
    return render(
        request,
        "core/policy.html",
        {"form": form, "active": active, "history": history},
    )
