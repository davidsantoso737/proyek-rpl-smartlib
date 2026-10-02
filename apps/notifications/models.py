from django.conf import settings
from django.db import models


class Notification(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    type = models.CharField(max_length=50)
    title = models.CharField(max_length=255)
    message = models.TextField()
    # Polymorphic reference: pasangan (reference_type, reference_id).
    # reference_id SENGAJA bukan ForeignKey (bisa menunjuk ke
    # loan / loan_item / ebook_loan / teaching_material /
    # material_deletion_request). Tanpa ContentType/GenericForeignKey.
    reference_type = models.CharField(
        max_length=50, null=True, blank=True
    )
    reference_id = models.BigIntegerField(null=True, blank=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "notifications"
