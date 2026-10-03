from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from apps.accounts.authorization import active_login_required

from .services import ask

User = get_user_model()


@active_login_required
def assistant_view(request):
    """Halaman chat AI Assistant (semua role aktif)."""
    return render(request, "ai/assistant.html")


@active_login_required
@require_POST
def assistant_ask_view(request):
    """Satu pertanyaan -> JSON. Tanpa mutasi transaksi apa pun."""
    result = ask(request.user, request.POST.get("question", ""))
    return JsonResponse(result)
