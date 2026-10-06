import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Book, BookCopy, Category
from apps.circulation.models import Loan, LoanItem
from apps.circulation.services import (
    ReturnError,
    activate_handover,
    approve_loan,
    lookup_return,
    process_return,
    request_loan,
)
from apps.core.models import LibrarySetting

User = get_user_model()


class ReturnTests(TestCase):
    def setUp(self):
        self.siswa = self._make_user("siswa1", User.Role.SISWA, nis="10001")
        self.guru = self._make_user("guru1", User.Role.GURU, nip="20001")
        self.pus = self._make_user("pus1", User.Role.PUSTAKAWAN)
        LibrarySetting.objects.create(
            physical_loan_duration=7,
            ebook_access_duration=14,
            pickup_qr_validity=30,
            fine_rate_per_day=Decimal("1000"),
            effective_from=timezone.now(),
        )
        category = Category.objects.create(name="Fiksi")
        book = Book.objects.create(
            category=category, isbn="ISBN-RT-1", title="Return A"
        )
        self.copy1 = self._make_copy(book, "RT-001")
        self.copy2 = self._make_copy(book, "RT-002")
        book2 = Book.objects.create(
            category=category, isbn="ISBN-RT-2", title="Return B"
        )
        self.copy3 = self._make_copy(book2, "RT-003")

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

    def _active_loan(self, user=None, copies=None):
        loan = request_loan(
            user or self.siswa, [c.pk for c in (copies or [self.copy1])]
        )
        approve_loan(loan.pk, self.pus)
        loan.refresh_from_db()
        activate_handover(loan.pickup_token, self.pus)
        return Loan.objects.get(pk=loan.pk)

    # --- on-time & late ---

    def test_on_time_return_tanpa_denda(self):
        loan = self._active_loan()
        self.client.force_login(self.pus)
        lookup = self.client.post(
            reverse("circulation:return"), {"code": "RT-001"}
        )
        self.assertContains(lookup, "siswa1")
        self.assertContains(lookup, "Jatuh tempo")
        response = self.client.post(
            reverse("circulation:return_confirm"),
            {"code": "RT-001", "condition": "BAIK"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pengembalian tercatat")
        item = LoanItem.objects.get(loan=loan, book_copy=self.copy1)
        self.assertEqual(item.return_status, "KEMBALI")
        self.assertEqual(item.late_day, 0)
        self.assertEqual(item.fine_amount, 0)
        self.assertEqual(item.fine_status, "LUNAS")
        self.assertIsNotNone(item.returned_at)
        # Acceptance: copy available hanya setelah return tercatat.
        self.assertEqual(
            BookCopy.objects.get(pk=self.copy1.pk).status, "TERSEDIA"
        )
        loan.refresh_from_db()
        self.assertEqual(loan.status, "COMPLETED")
        self.assertIsNotNone(loan.completed_at)

    def test_late_return_denda_rate_kali_hari(self):
        loan = self._active_loan()
        past_due = timezone.now() - datetime.timedelta(days=3, hours=1)
        LoanItem.objects.filter(loan=loan).update(due_at=past_due)
        Loan.objects.filter(pk=loan.pk).update(due_at=past_due)
        item = process_return("RT-001", self.pus, "BAIK")
        self.assertEqual(item.late_day, 3)
        self.assertEqual(item.fine_amount, Decimal("3000"))
        self.assertEqual(item.fine_status, "BELUM_BAYAR")

    def test_fine_memakai_snapshot_bukan_tarif_baru(self):
        loan = self._active_loan()
        past_due = timezone.now() - datetime.timedelta(days=2, hours=1)
        LoanItem.objects.filter(loan=loan).update(due_at=past_due)
        LibrarySetting.objects.create(
            physical_loan_duration=7,
            ebook_access_duration=14,
            pickup_qr_validity=30,
            fine_rate_per_day=Decimal("9999"),
            effective_from=timezone.now(),
        )
        item = process_return("RT-001", self.pus, "BAIK")
        self.assertEqual(item.fine_amount, Decimal("2000"))

    # --- lookup gagal ---

    def test_invalid_qr_ditolak(self):
        with self.assertRaises(ReturnError):
            lookup_return("")
        with self.assertRaises(ReturnError):
            lookup_return("   ")
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("circulation:return"), {"code": ""}
        )
        self.assertEqual(response.status_code, 200)

    def test_unknown_qr_ditolak(self):
        with self.assertRaises(ReturnError):
            lookup_return("COPY-TIDAK-ADA")
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("circulation:return_confirm"),
            {"code": "COPY-TIDAK-ADA", "condition": "BAIK"},
        )
        self.assertRedirects(response, reverse("circulation:return"))

    def test_no_active_loan_ditolak(self):
        with self.assertRaises(ReturnError):
            lookup_return("RT-002")  # tersedia, tanpa pinjaman
        pending = request_loan(self.siswa, [self.copy3.pk])
        self.assertEqual(pending.status, "PENDING_APPROVAL")
        with self.assertRaises(ReturnError):
            lookup_return("RT-003")  # masih pengajuan, bukan aktif

    # --- partial, multiple, lost ---

    def test_partial_return_loan_tetap_active(self):
        loan = self._active_loan(copies=[self.copy1, self.copy2])
        process_return("RT-001", self.pus, "BAIK")
        loan.refresh_from_db()
        self.assertEqual(loan.status, "ACTIVE")
        self.assertIsNone(loan.completed_at)
        self.assertEqual(
            BookCopy.objects.get(pk=self.copy1.pk).status, "TERSEDIA"
        )
        self.assertEqual(
            BookCopy.objects.get(pk=self.copy2.pk).status, "DIPINJAM"
        )

    def test_multiple_return_menyelesaikan_loan(self):
        loan = self._active_loan(copies=[self.copy1, self.copy2])
        process_return("RT-001", self.pus, "BAIK")
        process_return("RT-002", self.pus, "RUSAK RINGAN")
        loan.refresh_from_db()
        self.assertEqual(loan.status, "COMPLETED")
        item = LoanItem.objects.get(loan=loan, book_copy=self.copy2)
        self.assertEqual(item.return_condition, "RUSAK RINGAN")

    def test_lost_item_tetap_tidak_tersedia(self):
        loan = self._active_loan()
        item = process_return("RT-001", self.pus, "BAIK", lost=True)
        self.assertEqual(item.return_status, "HILANG")
        self.assertEqual(item.replacement_status, "BELUM_DIGANTI")
        self.assertEqual(
            BookCopy.objects.get(pk=self.copy1.pk).status, "HILANG"
        )
        loan.refresh_from_db()
        self.assertEqual(loan.status, "COMPLETED")
        # Tidak bisa diminta lagi.
        with self.assertRaises(Exception):
            request_loan(self.siswa, [self.copy1.pk])

    # --- integritas & auth ---

    def test_atomic_failure_tidak_setengah_tersimpan(self):
        loan = self._active_loan()
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("circulation:return_confirm"),
            {"code": "RT-999", "condition": "BAIK"},
        )
        self.assertRedirects(response, reverse("circulation:return"))
        loan.refresh_from_db()
        self.assertEqual(loan.status, "ACTIVE")
        self.assertEqual(
            BookCopy.objects.get(pk=self.copy1.pk).status, "DIPINJAM"
        )
        self.assertEqual(
            LoanItem.objects.filter(
                loan=loan, returned_at__isnull=False
            ).count(),
            0,
        )

    def test_unauthorized_user(self):
        for url in (
            reverse("circulation:return"),
            reverse("circulation:return_history"),
        ):
            self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.siswa)
        self.assertEqual(
            self.client.get(reverse("circulation:return")).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                reverse("circulation:return_confirm"), {"code": "RT-001"}
            ).status_code,
            403,
        )
        self.client.force_login(self.guru)
        self.assertEqual(
            self.client.get(
                reverse("circulation:return_history")
            ).status_code,
            403,
        )

    def test_return_history_menampilkan_yang_kembali(self):
        self._active_loan()
        process_return("RT-001", self.pus, "BAIK")
        self.client.force_login(self.pus)
        response = self.client.get(reverse("circulation:return_history"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "RT-001")
        self.assertContains(response, "siswa1 Full")

    def test_identity_qr_hanya_pustakawan(self):
        qr = reverse("circulation:copy_identity_qr", args=[self.copy1.pk])
        self.assertEqual(self.client.get(qr).status_code, 302)
        self.client.force_login(self.siswa)
        self.assertEqual(self.client.get(qr).status_code, 403)
        self.client.force_login(self.pus)
        response = self.client.get(qr)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")

    def test_label_menampilkan_qr_dan_kode_teks(self):
        label = reverse("circulation:copy_label", args=[self.copy1.pk])
        self.assertEqual(self.client.get(label).status_code, 302)
        self.client.force_login(self.siswa)
        self.assertEqual(self.client.get(label).status_code, 403)
        self.client.force_login(self.pus)
        response = self.client.get(label)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "RT-001")
        self.assertContains(
            response,
            reverse("circulation:copy_identity_qr", args=[self.copy1.pk]),
        )

    def test_label_hanya_cetak_qr(self):
        self.client.force_login(self.pus)
        response = self.client.get(
            reverse("circulation:copy_label", args=[self.copy1.pk])
        )
        html = response.content.decode()
        self.assertIn("print:hidden", html)
        self.assertIn("RT-001", html)

    def test_label_batch_filter_per_buku(self):
        batch = reverse("circulation:copy_label_batch")
        self.client.force_login(self.pus)
        response = self.client.get(batch)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pilih buku terlebih dahulu")
        book_pk = self.copy1.book.pk
        response = self.client.get(batch, {"book": book_pk})
        self.assertContains(response, "RT-001")
        self.assertContains(response, "RT-002")
        self.assertNotContains(response, "RT-003")

    def test_label_print_beberapa_copy_satu_halaman(self):
        url = (
            reverse("circulation:copy_label_print")
            + f"?copy={self.copy1.pk}&copy={self.copy2.pk}&copy=abc&copy=99999"
        )
        self.client.force_login(self.pus)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "RT-001")
        self.assertContains(response, "RT-002")
        self.assertNotContains(response, "RT-003")
        self.assertContains(response, "window.print")

    def test_label_print_kosong(self):
        self.client.force_login(self.pus)
        response = self.client.get(reverse("circulation:copy_label_print"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tidak ada eksemplar terpilih")

    def test_label_batch_unauthorized(self):
        for url in (
            reverse("circulation:copy_label_batch"),
            reverse("circulation:copy_label_print"),
        ):
            self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.siswa)
        for url in (
            reverse("circulation:copy_label_batch"),
            reverse("circulation:copy_label_print"),
        ):
            self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.guru)
        self.assertEqual(
            self.client.get(reverse("circulation:copy_label_batch")).status_code,
            403,
        )
