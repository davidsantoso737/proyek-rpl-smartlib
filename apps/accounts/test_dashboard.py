import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Book, BookCopy, Category, Ebook
from apps.circulation.models import Loan, LoanItem

User = get_user_model()


class LibrarianDashboardStatsTests(TestCase):
    def setUp(self):
        self.pus = User(
            username="pus1", role=User.Role.PUSTAKAWAN, full_name="Pustakawan Satu"
        )
        self.pus.set_password("secret123")
        self.pus.full_clean()
        self.pus.save()
        self.siswa = User(
            username="siswa1",
            role=User.Role.SISWA,
            nis="10001",
            full_name="Siswa Satu",
        )
        self.siswa.set_password("secret123")
        self.siswa.full_clean()
        self.siswa.save()
        self.category = Category.objects.create(name="Fiksi")

    def _book(self, isbn, title):
        return Book.objects.create(
            category=self.category, isbn=isbn, title=title
        )

    def _copy(self, book, code, status):
        return BookCopy.objects.create(
            book=book,
            copy_code=code,
            status=status,
            acquired_at=datetime.date(2026, 1, 10),
            condition="BAIK",
            location="RAK-A1",
        )

    def _loan(self, status, requested_at):
        return Loan.objects.create(
            user=self.siswa, status=status, requested_at=requested_at
        )

    def _returned_item(self, loan, copy, returned_at):
        return LoanItem.objects.create(
            loan=loan,
            book_copy=copy,
            return_status="KEMBALI",
            fine_rate_per_day=Decimal("1000"),
            fine_status="LUNAS",
            returned_at=returned_at,
        )

    def test_statistik_memakai_definisi_koleksi_dan_sirkulasi(self):
        book_a = self._book("ISBN-1", "Buku A")
        self._book("ISBN-2", "Buku B")
        self._copy(book_a, "C-1", "TERSEDIA")
        self._copy(book_a, "C-2", "DIPINJAM")
        self._copy(book_a, "C-3", "RUSAK")
        Ebook.objects.create(
            category=self.category,
            eisbn="E-1",
            title="Ebook A",
            file_path="a.pdf",
            file_name="a.pdf",
            file_type="pdf",
            file_size=64,
            version="1.0",
        )

        now = timezone.now()
        active = self._loan("ACTIVE", now)
        self._loan("PENDING_APPROVAL", now)
        recent = self._loan("COMPLETED", now)
        self._returned_item(recent, book_a.copies.last(), now)
        old = self._loan("COMPLETED", now - datetime.timedelta(days=400))
        self._returned_item(
            old, book_a.copies.first(), now - datetime.timedelta(days=400)
        )

        self.client.force_login(self.pus)
        response = self.client.get(reverse("accounts:dashboard_pustakawan"))
        context = response.context

        self.assertEqual(context["total_books"], 2)
        self.assertEqual(context["total_copies"], 3)
        self.assertEqual(context["copies_available"], 1)
        self.assertEqual(context["total_ebooks"], 1)
        self.assertEqual(context["active_loans"], 1)
        self.assertEqual(context["copy_status_total"], 3)

        rows = {row["label"]: row["value"] for row in context["copy_status_rows"]}
        self.assertEqual(
            rows, {"Tersedia": 1, "Dipinjam": 1, "Rusak": 1, "Hilang": 0}
        )

        self.assertEqual(len(context["trend_months"]), 6)
        self.assertEqual(context["trend_loan_total"], 3)
        self.assertEqual(context["trend_return_total"], 1)
        self.assertEqual(context["trend_max"], 3)
        self.assertEqual(active.status, "ACTIVE")

    def test_kartu_profil_dihapus_dan_antrean_tetap_ada(self):
        self.client.force_login(self.pus)
        response = self.client.get(reverse("accounts:dashboard_pustakawan"))

        self.assertContains(response, "Antrean operasional")
        self.assertContains(response, "Administrasi")
        self.assertContains(response, "Tren peminjaman")
        self.assertContains(response, "Ketersediaan eksemplar")
        self.assertNotContains(response, "Akun saya")
        self.assertNotContains(response, "Lihat / ubah profil")

    def test_empty_state_saat_belum_ada_data(self):
        self.client.force_login(self.pus)
        response = self.client.get(reverse("accounts:dashboard_pustakawan"))

        self.assertEqual(response.context["total_books"], 0)
        self.assertEqual(response.context["total_copies"], 0)
        self.assertContains(response, "Belum ada aktivitas sirkulasi.")
        self.assertContains(response, "Belum ada eksemplar terdaftar.")
