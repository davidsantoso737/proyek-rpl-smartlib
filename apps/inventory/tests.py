from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import Book, BookCopy, Category
from apps.inventory.models import StockOpname, StockOpnameItem

User = get_user_model()


class StockOpnameModelTests(TestCase):
    def test_stock_opname_dan_item(self):
        self.assertEqual(StockOpname._meta.db_table, "stock_opnames")
        self.assertEqual(
            StockOpnameItem._meta.db_table, "stok_opname_items"
        )
        pus = User.objects.create_user(
            username="pus1",
            password="secret123",
            role=User.Role.PUSTAKAWAN,
            full_name="Pustakawan Satu",
        )
        category = Category.objects.create(name="Fiksi")
        book = Book.objects.create(
            category=category, isbn="ISBN-INV-1", title="Inventaris"
        )
        copy = BookCopy.objects.create(
            book=book,
            copy_code="INV-001",
            status="TERSEDIA",
            acquired_at=timezone.localdate(),
            condition="BAIK",
            location="RAK-A1",
        )
        opname = StockOpname.objects.create(
            started_by=pus,
            started_at=timezone.now(),
            status="BERJALAN",
        )
        item = StockOpnameItem.objects.create(
            stock_opname=opname,
            book_copy=copy,
            scanned_at=timezone.now(),
            result="DITEMUKAN",
        )
        self.assertEqual(item.stock_opname, opname)
        self.assertEqual(item.book_copy, copy)
        self.assertEqual(
            StockOpnameItem._meta.get_field(
                "stock_opname"
            ).remote_field.on_delete.__name__,
            "CASCADE",
        )
