def librarian_pending(request):
    """Hitung antrean yang butuh aksi pustakawan untuk badge navigasi.

    Hanya dihitung untuk role PUSTAKAWAN agar request lain bebas
    query tambahan. Nilai: total + rincian per antrean.
    """
    empty = {
        "pending_total": 0,
        "pending_loans": 0,
        "pending_ebooks": 0,
        "pending_materials": 0,
        "pending_deletions": 0,
        "pending_telegram": 0,
    }
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return empty
    if getattr(user, "role", "") != "PUSTAKAWAN":
        return empty
    try:
        from apps.circulation.models import EbookLoan, Loan
        from apps.materials.models import (
            MaterialDeletionRequest,
            TeachingMaterial,
        )
        from apps.telegram.models import TelegramConnection

        loans = Loan.objects.filter(status="PENDING_APPROVAL").count()
        ebooks = EbookLoan.objects.filter(status="PENDING_APPROVAL").count()
        materials = TeachingMaterial.objects.filter(status="MENUNGGU").count()
        deletions = MaterialDeletionRequest.objects.filter(
            status="MENUNGGU"
        ).count()
        telegram = TelegramConnection.objects.filter(
            status="pending"
        ).count()
    except Exception:
        return empty
    return {
        "pending_total": loans + ebooks + materials + deletions + telegram,
        "pending_loans": loans,
        "pending_ebooks": ebooks,
        "pending_materials": materials,
        "pending_deletions": deletions,
        "pending_telegram": telegram,
    }
