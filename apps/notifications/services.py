"""Layanan notifikasi in-app (FR-73, FR-96, BR-80, NFR-10).

Event pemicu dibuat di service masing-masing modul (approval,
handover, return, e-book, materi). Modul ini menambah:
- pengingat jatuh tempo (H-2) dan keterlambatan (FR-96);
- daftar, baca, dan tautan referensi polimorfik.
Tanpa push/email/SMS (out of scope).
"""

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from apps.circulation.models import EbookLoan, Loan

from .models import Notification

DUE_SOON_DAYS = 2


def reference_url(notif, user):
    """URL tujuan referensi polimorfik dengan cek kepemilikan.

    Mengembalikan None bila target hilang atau bukan hak pengguna,
    sehingga template menyembunyikan tautan (bukan 403/404 buta).
    """
    try:
        if notif.reference_type == "loan":
            loan = Loan.objects.select_related("user").get(
                pk=notif.reference_id
            )
            if loan.user_id == user.pk:
                return reverse("circulation:loan_detail", args=[loan.pk])
        elif notif.reference_type == "ebook_loan":
            eloan = EbookLoan.objects.select_related("user", "ebook").get(
                pk=notif.reference_id
            )
            if eloan.user_id == user.pk:
                return reverse("circulation:my_ebooks")
        elif notif.reference_type == "teaching_material":
            from apps.materials.models import TeachingMaterial

            material = TeachingMaterial.objects.get(pk=notif.reference_id)
            if material.status == "TERSEDIA":
                return reverse("materials:list")
            if material.uploaded_by_id == user.pk:
                return reverse("materials:my")
            if user.role == "PUSTAKAWAN":
                return reverse(
                    "materials:review_detail", args=[material.pk]
                )
        elif notif.reference_type == "material_deletion_request":
            from apps.materials.models import MaterialDeletionRequest

            deletion = MaterialDeletionRequest.objects.select_related(
                "requested_by"
            ).get(pk=notif.reference_id)
            if deletion.requested_by_id == user.pk:
                return reverse("materials:my")
            if user.role == "PUSTAKAWAN":
                return reverse("materials:review_list")
    except Exception:
        return None
    return None


def mark_as_read(user, pk):
    """Tandai satu notifikasi milik sendiri; catat read_at (FR-73)."""
    try:
        notif = Notification.objects.get(pk=pk, user=user)
    except (Notification.DoesNotExist, TypeError, ValueError):
        return None
    if not notif.is_read:
        notif.is_read = True
        notif.read_at = timezone.now()
        notif.save(update_fields=["is_read", "read_at"])
    return notif


def mark_all_read(user):
    """Tandai semua milik sendiri; kembalikan jumlah yang berubah."""
    now = timezone.now()
    qs = Notification.objects.filter(user=user, is_read=False)
    count = qs.count()
    qs.update(is_read=True, read_at=now)
    return count


def sync_due_overdue(now=None):
    """Buat pengingat jatuh tempo & keterlambatan (FR-96, NFR-10).

    Idempoten: LOAN_DUE_SOON sekali per loan; LOAN_OVERDUE sekali
    per loan. Dipanggil lazy dari daftar pinjaman + via management
    command (penjadwal eksternal di luar scope).
    """
    moment = now or timezone.now()
    today = moment.date()
    created_due = created_overdue = 0
    loans = (
        Loan.objects.filter(status="ACTIVE", due_at__isnull=False)
        .select_related("user")
        .prefetch_related("items__book_copy__book")
    )
    for loan in loans:
        days_left = (loan.due_at.date() - today).days
        if days_left < 0:
            if not Notification.objects.filter(
                user=loan.user,
                type="LOAN_OVERDUE",
                reference_type="loan",
                reference_id=loan.pk,
            ).exists():
                titles = ", ".join(
                    sorted(
                        {
                            item.book_copy.book.title
                            for item in loan.items.all()
                        }
                    )[:3]
                )
                Notification.objects.create(
                    user=loan.user,
                    type="LOAN_OVERDUE",
                    title="Peminjaman melewati jatuh tempo",
                    message=(
                        f"Pengajuan #{loan.pk} ({titles}) terlambat "
                        f"{-days_left} hari. Denda dihitung per hari "
                        "keterlambatan."
                    ),
                    reference_type="loan",
                    reference_id=loan.pk,
                )
                created_overdue += 1
        elif days_left <= DUE_SOON_DAYS:
            if not Notification.objects.filter(
                user=loan.user,
                type="LOAN_DUE_SOON",
                reference_type="loan",
                reference_id=loan.pk,
            ).exists():
                Notification.objects.create(
                    user=loan.user,
                    type="LOAN_DUE_SOON",
                    title="Jatuh tempo mendekat",
                    message=(
                        f"Pengajuan #{loan.pk} jatuh tempo "
                        f"{loan.due_at.strftime('%d %b %Y')}. "
                        "Segera kembalikan untuk menghindari denda."
                    ),
                    reference_type="loan",
                    reference_id=loan.pk,
                )
                created_due += 1
    return created_due, created_overdue


def unread_count(user):
    if not getattr(user, "is_authenticated", False):
        return 0
    return Notification.objects.filter(user=user, is_read=False).count()
