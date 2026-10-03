"""Layanan bahan ajar Guru (FR-64 – FR-73, BR-59 – BR-67, NFR-08 – NFR-10, NFR-26 – NFR-28).

Storage privat (bukan static/media publik). Keputusan teknis yang
didokumentasikan (bukan dari requirement):
- tipe diizinkan: pdf/doc/docx/ppt/pptx/xls/xlsx/txt (dokumen ajar umum);
- ukuran maksimum 20 MB;
- nama simpan acak (uuid) agar tidak tabrakan & aman dari traversal.
Status memakai kosakata Indonesia mengikuti konvensi modul lain.
"""

import uuid
from pathlib import Path

from django.conf import settings as dj_settings
from django.core.files.storage import FileSystemStorage
from django.db import transaction
from django.utils import timezone

from apps.notifications.models import Notification

from .models import MaterialDeletionRequest, TeachingMaterial

ALLOWED_EXTENSIONS = frozenset(
    {"pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "txt"}
)
MAX_UPLOAD_SIZE = 20 * 1024 * 1024

MATERIAL_PENDING = "MENUNGGU"
MATERIAL_AVAILABLE = "TERSEDIA"
MATERIAL_REJECTED = "DITOLAK"
MATERIAL_DELETION_PENDING = "MENUNGGU_PENGHAPUSAN"
MATERIAL_DELETED = "DIHAPUS"

DELETION_PENDING = "MENUNGGU"
DELETION_APPROVED = "DISETUJUI"
DELETION_REJECTED = "DITOLAK"


class MaterialError(Exception):
    """Kegagalan validasi/alur bahan ajar (pesan siap tampil)."""


def _storage():
    root = Path(dj_settings.MATERIAL_STORAGE_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return FileSystemStorage(location=str(root))


def validate_upload(uploaded_file):
    """Validasi tipe, ukuran, dan integritas upload (FR-65, NFR-27)."""
    if uploaded_file is None or uploaded_file.size == 0:
        raise MaterialError("File tidak boleh kosong.")
    ext = Path(uploaded_file.name).suffix.lower().lstrip(".")
    if ext not in ALLOWED_EXTENSIONS:
        raise MaterialError(
            f"Tipe file .{ext or '?'} tidak diizinkan. "
            f"Gunakan: {', '.join(sorted(ALLOWED_EXTENSIONS))}."
        )
    if uploaded_file.size > MAX_UPLOAD_SIZE:
        raise MaterialError(
            f"Ukuran file melebihi {MAX_UPLOAD_SIZE // (1024 * 1024)} MB."
        )
    return ext


def upload_material(user, title, description, uploaded_file):
    """Simpan file DULU lalu metadata dalam transaksi (FR-64 – FR-66).

    Jika penulisan gagal, tidak ada metadata; jika DB gagal, file
    yang sudah tertulis dibersihkan (NFR-28).
    """
    ext = validate_upload(uploaded_file)
    storage = _storage()
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    try:
        saved_name = storage.save(stored_name, uploaded_file)
    except Exception:
        raise MaterialError(
            "File gagal diproses dan tidak disimpan."
        )
    try:
        with transaction.atomic():
            material = TeachingMaterial.objects.create(
                uploaded_by=user,
                title=title.strip(),
                description=(description or "").strip() or None,
                file_path=saved_name,
                file_name=Path(uploaded_file.name).name,
                file_type=ext,
                file_size=uploaded_file.size,
                status=MATERIAL_PENDING,
            )
    except Exception:
        storage.delete(saved_name)
        raise MaterialError(
            "Metadata gagal disimpan; file dibatalkan."
        )
    return material


def _notify(user, notif_type, title, message, ref_type, ref_id):
    Notification.objects.create(
        user=user,
        type=notif_type,
        title=title,
        message=message,
        reference_type=ref_type,
        reference_id=ref_id,
    )


def verify_material(material_id, approver, approve, reason=""):
    """Terima/tolak bahan ajar (FR-67 – FR-69, FR-73, BR-60 – BR-62)."""
    with transaction.atomic():
        try:
            material = TeachingMaterial.objects.select_for_update().get(
                pk=material_id
            )
        except (TeachingMaterial.DoesNotExist, TypeError, ValueError):
            raise MaterialError("Bahan ajar tidak ditemukan.")
        if material.status != MATERIAL_PENDING:
            raise MaterialError(
                "Hanya bahan ajar MENUNGGU yang dapat diverifikasi."
            )
        now = timezone.now()
        if approve:
            material.status = MATERIAL_AVAILABLE
            material.verified_by = approver
            material.verified_at = now
            material.rejection_reason = None
            material.save(
                update_fields=[
                    "status", "verified_by", "verified_at",
                    "rejection_reason", "updated_at",
                ]
            )
            _notify(
                material.uploaded_by, "MATERIAL_APPROVED",
                "Bahan ajar diterima",
                f"{material.title} telah tersedia bagi pengguna.",
                "teaching_material", material.pk,
            )
        else:
            reason = (reason or "").strip()
            if not reason:
                raise MaterialError("Alasan penolakan wajib diisi.")
            material.status = MATERIAL_REJECTED
            material.verified_by = approver
            material.verified_at = now
            material.rejection_reason = reason
            material.save(
                update_fields=[
                    "status", "verified_by", "verified_at",
                    "rejection_reason", "updated_at",
                ]
            )
            _notify(
                material.uploaded_by, "MATERIAL_REJECTED",
                "Bahan ajar ditolak", f"{material.title}: {reason}",
                "teaching_material", material.pk,
            )
    return material


def request_deletion(material_id, user, reason):
    """Guru mengajukan penghapusan; TIDAK ada hapus langsung (FR-71, BR-63 – BR-65)."""
    reason = (reason or "").strip()
    if not reason:
        raise MaterialError("Alasan penghapusan wajib diisi.")
    with transaction.atomic():
        try:
            material = TeachingMaterial.objects.select_for_update().get(
                pk=material_id, uploaded_by=user
            )
        except (TeachingMaterial.DoesNotExist, TypeError, ValueError):
            raise MaterialError(
                "Bahan ajar tidak ditemukan atau bukan milik Anda."
            )
        if material.status != MATERIAL_AVAILABLE:
            raise MaterialError(
                "Hanya bahan ajar TERSEDIA yang dapat diminta penghapusannya."
            )
        if MaterialDeletionRequest.objects.filter(
            material=material, status=DELETION_PENDING
        ).exists():
            raise MaterialError(
                "Permintaan penghapusan bahan ini masih menunggu."
            )
        deletion = MaterialDeletionRequest.objects.create(
            material=material,
            requested_by=user,
            status=DELETION_PENDING,
            reason=reason,
            requested_at=timezone.now(),
        )
        material.status = MATERIAL_DELETION_PENDING
        material.save(update_fields=["status", "updated_at"])
    return deletion


def review_deletion(deletion_id, approver, approve, note=""):
    """Pustakawan memutuskan permintaan penghapusan (FR-72, BR-66)."""
    with transaction.atomic():
        try:
            deletion = MaterialDeletionRequest.objects.select_for_update().get(
                pk=deletion_id
            )
        except (MaterialDeletionRequest.DoesNotExist, TypeError, ValueError):
            raise MaterialError("Permintaan penghapusan tidak ditemukan.")
        if deletion.status != DELETION_PENDING:
            raise MaterialError(
                "Hanya permintaan MENUNGGU yang dapat diputuskan."
            )
        material = TeachingMaterial.objects.select_for_update().get(
            pk=deletion.material_id
        )
        now = timezone.now()
        deletion.reviewed_by = approver
        deletion.reviewed_at = now
        deletion.review_note = (note or "").strip() or None
        if approve:
            deletion.status = DELETION_APPROVED
            deletion.save(
                update_fields=[
                    "status", "reviewed_by", "reviewed_at",
                    "review_note",
                ]
            )
            material.status = MATERIAL_DELETED
            material.save(update_fields=["status", "updated_at"])
            _storage().delete(material.file_path)
            _notify(
                material.uploaded_by, "MATERIAL_DELETION_APPROVED",
                "Penghapusan disetujui",
                f"{material.title} telah dihapus dari sistem.",
                "material_deletion_request", deletion.pk,
            )
        else:
            deletion.status = DELETION_REJECTED
            deletion.save(
                update_fields=[
                    "status", "reviewed_by", "reviewed_at",
                    "review_note",
                ]
            )
            material.status = MATERIAL_AVAILABLE
            material.save(update_fields=["status", "updated_at"])
            _notify(
                material.uploaded_by, "MATERIAL_DELETION_REJECTED",
                "Penghapusan ditolak",
                f"{material.title} tetap tersedia.",
                "material_deletion_request", deletion.pk,
            )
    return deletion


def material_file_path(material):
    """Path file di storage privat; hilang -> error jelas (bukan 500)."""
    candidate = Path(_storage().location) / Path(material.file_path).name
    if candidate.is_file():
        return candidate
    raise MaterialError(
        "File bahan ajar tidak tersedia di server. Hubungi pustakawan."
    )
