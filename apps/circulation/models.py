from django.conf import settings
from django.db import models


class Loan(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="loans",
    )
    status = models.CharField(max_length=30)
    pickup_token = models.CharField(
        max_length=100, unique=True, null=True, blank=True
    )
    pickup_exp_at = models.DateTimeField(null=True, blank=True)
    pickup_used_at = models.DateTimeField(null=True, blank=True)
    requested_at = models.DateTimeField()
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_loans",
    )
    borrowed_at = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "loans"


class LoanItem(models.Model):
    loan = models.ForeignKey(
        Loan, on_delete=models.CASCADE, related_name="items"
    )
    book_copy = models.ForeignKey(
        "catalog.BookCopy",
        on_delete=models.PROTECT,
        related_name="loan_items",
    )
    borrowed_at = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    return_condition = models.CharField(
        max_length=30, null=True, blank=True
    )
    return_status = models.CharField(max_length=30)
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="processed_loan_items",
    )
    replacement_status = models.CharField(
        max_length=30, null=True, blank=True
    )
    fine_rate_per_day = models.DecimalField(max_digits=12, decimal_places=2)
    late_day = models.PositiveIntegerField(default=0)
    fine_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )
    fine_status = models.CharField(max_length=30)
    fine_paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "loan_items"


class EbookLoan(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ebook_loans",
    )
    ebook = models.ForeignKey(
        "catalog.Ebook",
        on_delete=models.PROTECT,
        related_name="ebook_loans",
    )
    status = models.CharField(max_length=30)
    requested_at = models.DateTimeField()
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_ebook_loans",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ebook_loans"
