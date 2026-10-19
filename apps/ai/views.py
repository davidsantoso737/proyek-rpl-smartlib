from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from apps.accounts.authorization import role_required

from .presentation import attach_covers, format_narration
from .providers import get_provider
from .recommendations import narrate, recommend_for_user
from .services import ask

User = get_user_model()

# AI Assistant hanya untuk Siswa dan Guru. Pustakawan tidak memerlukan
# asisten (punya menu operasional langsung) sehingga seluruh view AI
# menolak role tersebut dengan 403.
AI_ROLES = (User.Role.SISWA, User.Role.GURU)


@role_required(*AI_ROLES)
def assistant_view(request):
    """Halaman chat AI Assistant (Siswa/Guru aktif)."""
    return render(request, "ai/assistant.html")


@role_required(*AI_ROLES)
@require_POST
def assistant_ask_view(request):
    """Satu pertanyaan -> JSON. Tanpa mutasi transaksi apa pun."""
    result = ask(request.user, request.POST.get("question", ""))
    return JsonResponse(result)


@role_required(*AI_ROLES)
def recommendation_view(request):
    """Rekomendasi koleksi INFORMATION ONLY (FR-78 – FR-81).

    Tidak membuat peminjaman/akses/persetujuan dalam bentuk apa pun.
    """
    recommendation = recommend_for_user(request.user)
    try:
        narration = narrate(
            get_provider(), request.user.full_name, recommendation
        )
    except Exception:
        narration = None
    # Presentasi saja: lampirkan sampul katalog dan tata teks AI dengan
    # aman. Isi, urutan, dan alasan rekomendasi tidak berubah.
    attach_covers(recommendation["items"])
    return render(
        request,
        "ai/recommendations.html",
        {
            "recommendation": recommendation,
            "narration_blocks": format_narration(narration),
        },
    )
