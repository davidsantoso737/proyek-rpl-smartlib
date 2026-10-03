from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.authorization import (
    active_login_required,
    role_required,
)

from .forms import MaterialUploadForm, ReasonForm
from .models import MaterialDeletionRequest, TeachingMaterial
from .services import (
    DELETION_PENDING,
    MATERIAL_AVAILABLE,
    MATERIAL_PENDING,
    MaterialError,
    material_file_path,
    request_deletion,
    review_deletion,
    upload_material,
    verify_material,
)

User = get_user_model()

CONTENT_TYPES = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "txt": "text/plain",
}


@active_login_required
def material_list_view(request):
    """Bahan ajar TERSEDIA untuk pengguna terautentikasi aktif."""
    materials = (
        TeachingMaterial.objects.filter(status=MATERIAL_AVAILABLE)
        .select_related("uploaded_by")
        .order_by("-verified_at", "-id")
    )
    return render(
        request, "materials/material_list.html", {"materials": materials}
    )


@role_required(User.Role.GURU)
def my_materials_view(request):
    """Bahan ajar milik guru + status masing-masing (FR-70)."""
    materials = TeachingMaterial.objects.filter(
        uploaded_by=request.user
    ).order_by("-created_at")
    return render(
        request, "materials/material_my.html", {"materials": materials}
    )


@role_required(User.Role.GURU)
def material_upload_view(request):
    """Upload bahan ajar (FR-64, FR-65)."""
    if request.method == "POST":
        form = MaterialUploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                material = upload_material(
                    request.user,
                    form.cleaned_data["title"],
                    form.cleaned_data["description"],
                    form.cleaned_data["file"],
                )
            except MaterialError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    f"{material.title} diunggah dan menunggu verifikasi.",
                )
                return redirect("materials:my")
    else:
        form = MaterialUploadForm()
    return render(request, "materials/material_upload.html", {"form": form})


@role_required(User.Role.GURU)
@require_POST
def material_deletion_request_view(request, pk):
    """Ajukan penghapusan; tanpa hapus langsung (FR-71, BR-63)."""
    form = ReasonForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Alasan penghapusan wajib diisi.")
        return redirect("materials:my")
    try:
        request_deletion(pk, request.user, form.cleaned_data["reason"])
    except MaterialError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request, "Permintaan penghapusan dikirim ke pustakawan."
        )
    return redirect("materials:my")


@active_login_required
def material_download_view(request, pk):
    """Unduh dari storage privat (NFR-08, NFR-26).

    TERSEDIA untuk semua pengguna aktif; versi non-publik hanya
    pemilik + pustakawan. Tidak ada URL publik ke file.
    """
    material = get_object_or_404(TeachingMaterial, pk=pk)
    if material.status != MATERIAL_AVAILABLE:
        if (
            request.user != material.uploaded_by
            and request.user.role != User.Role.PUSTAKAWAN
        ):
            raise PermissionDenied("Bahan ajar belum tersedia.")
    try:
        path = material_file_path(material)
    except MaterialError as exc:
        raise Http404(str(exc))
    return FileResponse(
        open(path, "rb"),
        content_type=CONTENT_TYPES.get(material.file_type, "application/octet-stream"),
        filename=material.file_name,
        as_attachment=True,
    )


@role_required(User.Role.PUSTAKAWAN)
def material_review_list_view(request):
    """Antre verifikasi + antre penghapusan (FR-67)."""
    pending = TeachingMaterial.objects.filter(
        status=MATERIAL_PENDING
    ).select_related("uploaded_by").order_by("created_at")
    deletions = MaterialDeletionRequest.objects.filter(
        status=DELETION_PENDING
    ).select_related("material", "requested_by").order_by("requested_at")
    return render(
        request,
        "materials/material_review_list.html",
        {"pending": pending, "deletions": deletions},
    )


@role_required(User.Role.PUSTAKAWAN)
def material_review_detail_view(request, pk):
    material = get_object_or_404(
        TeachingMaterial.objects.select_related("uploaded_by"), pk=pk
    )
    return render(
        request,
        "materials/material_review_detail.html",
        {"material": material},
    )


@role_required(User.Role.PUSTAKAWAN)
@require_POST
def material_verify_view(request, pk):
    """Terima/tolak bahan ajar (FR-68, FR-69)."""
    approve = request.POST.get("decision") == "approve"
    try:
        verify_material(
            pk, request.user, approve, request.POST.get("reason", "")
        )
    except MaterialError as exc:
        messages.error(request, str(exc))
        return redirect("materials:review_detail", pk=pk)
    messages.success(
        request,
        "Bahan ajar diterima." if approve else "Bahan ajar ditolak.",
    )
    return redirect("materials:review_list")


@role_required(User.Role.PUSTAKAWAN)
@require_POST
def deletion_review_view(request, pk):
    """Setujui/tolak permintaan penghapusan (FR-72)."""
    approve = request.POST.get("decision") == "approve"
    try:
        review_deletion(
            pk, request.user, approve, request.POST.get("note", "")
        )
    except MaterialError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            "Penghapusan disetujui." if approve else "Penghapusan ditolak.",
        )
    return redirect("materials:review_list")
