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

        loans = Loan.objects.filter(status="PENDING_APPROVAL").count()
        ebooks = EbookLoan.objects.filter(status="PENDING_APPROVAL").count()
        materials = TeachingMaterial.objects.filter(status="MENUNGGU").count()
        deletions = MaterialDeletionRequest.objects.filter(
            status="MENUNGGU"
        ).count()
    except Exception:
        return empty
    return {
        "pending_total": loans + ebooks + materials + deletions,
        "pending_loans": loans,
        "pending_ebooks": ebooks,
        "pending_materials": materials,
        "pending_deletions": deletions,
    }
