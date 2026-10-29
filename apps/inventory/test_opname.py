import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.catalog.models import Book, BookCopy, Category
from apps.inventory.models import StockOpname, StockOpnameItem
from apps.inventory.services import (
    OpnameError,
    complete_opname,
    scan_copy,
    start_opname,
    summarize,
)

User = get_user_model()


class OpnameTests(TestCase):
    def setUp(self):
        self.pus = self._make_user("pus1", User.Role.PUSTAKAWAN)
        self.siswa = self._make_user("siswa1", User.Role.SISWA, nis="10001")
        self.guru = self._make_user("guru1", User.Role.GURU, nip="20001")
        category = Category.objects.create(name="Fiksi")
        book = Book.objects.create(
            category=category, isbn="ISBN-OP-1", title="Opname A"
        )
        self.copy1 = self._make_copy(book, "OP-001")
        self.copy2 = self._make_copy(book, "OP-002")

    def _make_user(self, username, role, password="secret123", **extra):
        defaults = {"full_name": f"{username} Full"}
        defaults.update(extra)
        user = User(username=username, role=role, **defaults)
        user.set_password(password)
        user.full_clean()
        user.save()
        return user

    def _make_copy(self, book, code):
        return BookCopy.objects.create(
            book=book,
            copy_code=code,
            status="TERSEDIA",
            acquired_at=datetime.date(2026, 1, 10),
            condition="BAIK",
            location="RAK-A1",
        )

    def test_valid_scan(self):
        opname = start_opname(self.pus)
        item = scan_copy(opname.pk, "OP-001")
        self.assertEqual(item.result, "DITEMUKAN")
        self.assertEqual(item.book_copy, self.copy1)
        self.assertIsNotNone(item.scanned_at)

    def test_duplicate_scan_ditolak(self):
        opname = start_opname(self.pus)
        scan_copy(opname.pk, "OP-001")
        with self.assertRaises(OpnameError):
            scan_copy(opname.pk, "OP-001")
        self.assertEqual(
            StockOpnameItem.objects.filter(stock_opname=opname).count(), 1
        )

    def test_unknown_copy_ditolak(self):
        opname = start_opname(self.pus)
        with self.assertRaises(OpnameError):
            scan_copy(opname.pk, "OP-XXX")
        with self.assertRaises(OpnameError):
            scan_copy(opname.pk, "  ")

    def test_found_dan_missing(self):
        opname = start_opname(self.pus)
        scan_copy(opname.pk, "OP-001")
        summary = summarize(opname)
        self.assertEqual(summary["expected_count"], 2)
        self.assertEqual(
            [c.copy_code for c in summary["found"]], ["OP-001"]
        )
        self.assertEqual(
            [c.copy_code for c in summary["missing"]], ["OP-002"]
        )

    def test_complete_opname(self):
        opname = start_opname(self.pus)
        scan_copy(opname.pk, "OP-001")
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("inventory:opname_complete", args=[opname.pk])
        )
        self.assertRedirects(
            response, reverse("inventory:opname_detail", args=[opname.pk])
        )
        opname.refresh_from_db()
        self.assertEqual(opname.status, "SELESAI")
        self.assertIsNotNone(opname.completed_at)
        with self.assertRaises(OpnameError):
            scan_copy(opname.pk, "OP-002")
        # Sesi baru bisa dimulai setelah selesai.
        opname2 = start_opname(self.pus)
        self.assertNotEqual(opname2.pk, opname.pk)

    def test_start_diblokir_selama_berjalan(self):
        start_opname(self.pus)
        with self.assertRaises(OpnameError):
            start_opname(self.pus)

    def test_detail_opname_memakai_filter_buku_realtime(self):
        opname = start_opname(self.pus)
        self.client.force_login(self.pus)
        response = self.client.get(
            reverse("inventory:opname_detail", args=[opname.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-live-filter")
        self.assertContains(response, "data-live-filter-select")

    def test_unauthorized(self):
        self.assertEqual(
            self.client.get(reverse("inventory:opname_list")).status_code,
            302,
        )
        opname = start_opname(self.pus)
        detail = reverse("inventory:opname_detail", args=[opname.pk])
        self.client.force_login(self.siswa)
        self.assertEqual(self.client.get(detail).status_code, 403)
        self.assertEqual(
            self.client.post(detail, {"code": "OP-001"}).status_code, 403
        )
        self.client.force_login(self.guru)
        self.assertEqual(
            self.client.post(
                reverse("inventory:opname_complete", args=[opname.pk])
            ).status_code,
            403,
        )
        self.assertEqual(
            StockOpnameItem.objects.filter(stock_opname=opname).count(), 0
        )

    def test_alur_ui_pustakawan(self):
        self.client.force_login(self.pus)
        response = self.client.post(reverse("inventory:opname_start"))
        opname = StockOpname.objects.get(status="BERJALAN")
        self.assertRedirects(
            response, reverse("inventory:opname_detail", args=[opname.pk])
        )
        response = self.client.post(
            reverse("inventory:opname_detail", args=[opname.pk]),
            {"code": "OP-001"},
        )
        self.assertRedirects(
            response, reverse("inventory:opname_detail", args=[opname.pk])
        )
        response = self.client.get(
            reverse("inventory:opname_detail", args=[opname.pk])
        )
        # Alur per-buku: tanpa buku terpilih, daftar global disembunyikan.
        self.assertContains(response, "Pilih buku untuk mulai memeriksa.")
        self.assertNotContains(response, "OP-002")
        from apps.catalog.models import BookCopy

        book_pk = BookCopy.objects.get(copy_code="OP-001").book_id
        response = self.client.get(
            reverse("inventory:opname_detail", args=[opname.pk]),
            {"book": book_pk},
        )
        self.assertContains(response, "OP-001")
        self.assertContains(response, "OP-002")
