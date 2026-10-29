from django.contrib import messages
from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.authorization import role_required
from apps.ai.configuration import (
    ConfigurationError,
    configuration_summary,
    discover_models,
    model_discovery_message,
    reset_configuration,
    save_configuration,
    test_configuration,
    validation_message,
)
from apps.ai.forms import AIConfigurationForm, AIModelDiscoveryForm
from apps.ai.providers import AIProviderError

from .forms import PolicyForm
from .models import LibrarySetting
from .services import PolicyError, get_active_setting, update_policy

User = get_user_model()


def _initial_ai_form(summary=None):
    summary = summary or configuration_summary()
    row = summary["saved"]
    initial = {"is_enabled": True}
    if row is not None:
        initial.update(
            {
                "base_url": row.base_url,
                "model": row.model,
                "is_enabled": row.is_enabled,
            }
        )
    return AIConfigurationForm(initial=initial)


def _render_policy(
    request,
    *,
    policy_form=None,
    ai_form=None,
    ai_status=None,
    status=200,
):
    active = get_active_setting()
    if policy_form is None:
        policy_form = PolicyForm(instance=active)
    ai_status = ai_status or configuration_summary()
    if ai_form is None:
        ai_form = _initial_ai_form(ai_status)
    history = LibrarySetting.objects.order_by("-effective_from")[:10]
    return render(
        request,
        "core/policy.html",
        {
            "form": policy_form,
            "active": active,
            "history": history,
            "ai_form": ai_form,
            "ai_status": ai_status,
        },
        status=status,
    )


def _wants_json(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _json_form_errors(form):
    errors = {}
    for name, field_errors in form.errors.items():
        errors[name] = [str(error) for error in field_errors]
    return errors


@role_required(User.Role.PUSTAKAWAN)
def policy_view(request):
    """Konfigurasi kebijakan + AI Assistant (FR-50 – FR-53E).

    Hanya Pustakawan. GET menampilkan versi aktif dan form AI; POST
    membuat versi kebijakan baru tanpa mengubah riwayat. Perubahan AI
    memakai endpoint terpisah agar tervalidasi sebelum diaktifkan.
    """
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
        return _render_policy(request, policy_form=form)
    return _render_policy(request)


@role_required(User.Role.PUSTAKAWAN)
@require_POST
def ai_config_save_view(request):
    """Validasi lalu simpan konfigurasi AI (khusus pustakawan)."""
    form = AIConfigurationForm(request.POST)
    if not form.is_valid():
        if _wants_json(request):
            return JsonResponse(
                {
                    "ok": False,
                    "message": "Periksa kembali isian konfigurasi.",
                    "errors": _json_form_errors(form),
                },
                status=400,
            )
        return _render_policy(request, ai_form=form, status=400)
    try:
        save_configuration(
            request.user,
            base_url=form.cleaned_data["base_url"],
            model=form.cleaned_data["model"],
            api_key=form.cleaned_data.get("api_key", ""),
            enabled=form.cleaned_data.get("is_enabled", False),
        )
    except (AIProviderError, ConfigurationError) as exc:
        if isinstance(exc, AIProviderError):
            message = validation_message(exc.kind)
        else:
            message = str(exc)
        if _wants_json(request):
            return JsonResponse(
                {
                    "ok": False,
                    "message": message,
                    "category": getattr(exc, "kind", "configuration"),
                },
                status=400,
            )
        messages.error(
            request, f"Konfigurasi AI tidak disimpan: {message}"
        )
        return _render_policy(request, ai_form=form)
    messages.success(
        request,
        "Konfigurasi AI tersimpan dan berlaku untuk permintaan "
        "AI Assistant berikutnya.",
    )
    if _wants_json(request):
        return JsonResponse({"ok": True})
    return redirect("core:policy")


@role_required(User.Role.PUSTAKAWAN)
@require_POST
def ai_config_test_view(request):
    """Uji kredensial yang diusulkan tanpa mengubah konfigurasi aktif."""
    form = AIConfigurationForm(request.POST)
    if not form.is_valid():
        if _wants_json(request):
            return JsonResponse(
                {
                    "ok": False,
                    "message": "Periksa kembali isian konfigurasi.",
                    "errors": _json_form_errors(form),
                },
                status=400,
            )
        return _render_policy(request, ai_form=form, status=400)
    try:
        test_configuration(
            form.cleaned_data["base_url"],
            form.cleaned_data["model"],
            form.cleaned_data.get("api_key", ""),
        )
    except (AIProviderError, ConfigurationError) as exc:
        if isinstance(exc, AIProviderError):
            message = validation_message(exc.kind)
        else:
            message = str(exc)
        if _wants_json(request):
            return JsonResponse(
                {
                    "ok": False,
                    "message": message,
                    "category": getattr(exc, "kind", "configuration"),
                },
                status=400,
            )
        messages.error(request, f"Uji konfigurasi gagal: {message}")
        return _render_policy(request, ai_form=form)
    message = (
        "Koneksi ke penyedia AI berhasil. Model tersedia untuk "
        "kredensial ini."
    )
    if _wants_json(request):
        return JsonResponse({"ok": True, "message": message})
    messages.success(request, f"Uji konfigurasi berhasil. {message}")
    return _render_policy(request, ai_form=form)


@role_required(User.Role.PUSTAKAWAN)
@require_POST
def ai_config_models_view(request):
    """Muat daftar model dari provider tanpa menyimpan konfigurasi."""
    form = AIModelDiscoveryForm(request.POST)
    if not form.is_valid():
        return JsonResponse(
            {
                "ok": False,
                "message": "Periksa kembali URL dan API key.",
                "errors": _json_form_errors(form),
            },
            status=400,
        )
    try:
        models = discover_models(
            form.cleaned_data["base_url"],
            form.cleaned_data.get("api_key", ""),
        )
    except (AIProviderError, ConfigurationError) as exc:
        if isinstance(exc, AIProviderError):
            message = model_discovery_message(exc.kind)
        else:
            message = str(exc)
        return JsonResponse(
            {
                "ok": False,
                "message": message,
                "category": getattr(exc, "kind", "configuration"),
            },
            status=400,
        )
    return JsonResponse(
        {
            "ok": True,
            "models": models,
            "count": len(models),
            "message": f"Ditemukan {len(models)} model.",
        }
    )


@role_required(User.Role.PUSTAKAWAN)
@require_POST
def ai_config_reset_view(request):
    """Hapus konfigurasi tersimpan setelah konfirmasi eksplisit."""
    if request.POST.get("confirm") != "1":
        if _wants_json(request):
            return JsonResponse(
                {"ok": False, "message": "Konfirmasi diperlukan."},
                status=400,
            )
        messages.error(request, "Konfirmasi diperlukan untuk menghapus.")
        return _render_policy(request)
    reset_configuration()
    messages.success(
        request,
        "Konfigurasi AI tersimpan dihapus. Sistem kembali memakai "
        "konfigurasi environment bila tersedia.",
    )
    if _wants_json(request):
        return JsonResponse({"ok": True})
    return redirect("core:policy")
