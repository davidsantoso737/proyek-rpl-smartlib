from django.conf import settings
from django.db import models


class TeachingMaterial(models.Model):
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="uploaded_materials",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    file_path = models.CharField(max_length=500)
    file_name = models.CharField(max_length=255)
    file_type = models.CharField(max_length=20)
    file_size = models.BigIntegerField()
    status = models.CharField(max_length=30)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="verified_materials",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "teaching_materials"


class MaterialDeletionRequest(models.Model):
    material = models.ForeignKey(
        TeachingMaterial,
        on_delete=models.CASCADE,
        related_name="deletion_requests",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="requested_deletions",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reviewed_deletions",
    )
    status = models.CharField(max_length=30)
    reason = models.TextField()
    review_note = models.TextField(null=True, blank=True)
    requested_at = models.DateTimeField()
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "material_deletion_requests"
