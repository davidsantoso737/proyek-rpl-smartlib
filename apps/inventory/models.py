from django.conf import settings
from django.db import models


class StockOpname(models.Model):
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="started_opnames",
    )
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=30)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "stock_opnames"


class StockOpnameItem(models.Model):
    stock_opname = models.ForeignKey(
        StockOpname, on_delete=models.CASCADE, related_name="items"
    )
    book_copy = models.ForeignKey(
        "catalog.BookCopy",
        on_delete=models.PROTECT,
        related_name="opname_items",
    )
    scanned_at = models.DateTimeField()
    result = models.CharField(max_length=30)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "stok_opname_items"
