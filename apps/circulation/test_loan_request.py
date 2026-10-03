import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Book, BookCopy, Category
from apps.circulation.models import Loan, LoanItem
from apps.circulation.services import LoanRequestError, request_loan
from apps.core.models import LibrarySetting

User = get_user_model()


class LoanRequestTests(TestCase):
    def setUp(self):
        self.siswa = self._make_user("siswa1", User.Role.SISWA, nis="10001")
        self.siswa2 = self._make_user("siswa2", User.Role.SISWA, nis="10002")
        self.guru = self._make_user("guru1", User.Role.GURU, nip="20001")
        self.pus = self._make_user("pus1", User.Role.PUSTAKAWAN)
        self.inactive = self._make_user(
            "siswaX", User.Role.SISWA, nis="10009", is_active=False
        )
        LibrarySetting.objects.create(
            physical_loan_duration=7,
            ebook_access_duration=14,
            pickup_qr_validity=30,
            fine_rate_per_day=Decimal("1500"),
            effective_from=timezone.now(),
        )
        category = Category.objects.create(name="Fiksi")
        book = Book.objects.create(
            category=category, isbn="ISBN-LOAN-1", title="Pinjam A"
        )
        self.copy1 = self._make_copy(book, "LOAN-001", "TERSEDIA")
        self.copy2 = self._make_copy(book, "LOAN-002", "TERSEDIA")
        self.copy_busy = self._make_copy(book, "LOAN-003", "DIPINJAM")
        book2 = Book.objects.create(
            category=category, isbn="ISBN-LOAN-2", title="Pinjam B"
        )
        self.copy_other = self._make_copy(book2, "LOAN-004", "TERSEDIA")

    def _make_user(self, username, role, password="secret123", **extra):
        defaults = {"full_name": f"{username} Full"}
        defaults.update(extra)
        user = User(username=username, role=role, **defaults)
        user.set_password(password)
        user.full_clean()
        user.save()
        return user

    def _make_copy(self, book, code, status):
        return BookCopy.objects.create(
            book=book,
            copy_code=code,
            status=status,
            acquired_at=datetime.date(2026, 1, 10),
            condition="BAIK",
            location="RAK-A1",
        )

    def _set_cart(self, *copies):
        session = self.client.session
        session["loan_cart"] = [copy.pk for copy in copies]
        session.save()

    def test_one_item_membuat_pending_approval(self):
        self.client.force_login(self.siswa)
        self.client.post(
            reverse("circulation:cart_add", args=[self.copy1.pk])
        )
        response = self.client.post(reverse("circulation:loan_create"))
        loan = Loan.objects.get(user=self.siswa)
        self.assertRedirects(
            response, reverse("circulation:loan_detail", args=[loan.pk])
        )
        self.assertEqual(loan.status, "PENDING_APPROVAL")
        self.assertIsNotNone(loan.requested_at)
        self.assertIsNone(loan.borrowed_at)
        self.assertEqual(loan.items.count(), 1)
        item = loan.items.get()
        self.assertEqual(item.book_copy, self.copy1)
        self.assertEqual(item.fine_rate_per_day, Decimal("1500"))
        # Critical rule: eksemplar TIDAK berubah sampai handover.
        self.copy1.refresh_from_db()
        self.assertEqual(self.copy1.status, "TERSEDIA")
        # Keranjang dikosongkan setelah pengajuan.
        self.assertEqual(self.client.session.get("loan_cart", []), [])

    def test_multiple_items_satu_loan(self):
        self.client.force_login(self.guru)
        self._set_cart(self.copy1, self.copy_other)
        self.client.post(reverse("circulation:loan_create"))
        loan = Loan.objects.get(user=self.guru)
        self.assertEqual(loan.status, "PENDING_APPROVAL")
        self.assertEqual(loan.items.count(), 2)
        self.assertEqual(Loan.objects.count(), 1)

    def test_unavailable_book_ditolak(self):
        self.client.force_login(self.siswa)
        response = self.client.post(
            reverse("circulation:cart_add", args=[self.copy_busy.pk])
        )
        self.assertRedirects(response, reverse("circulation:cart"))
        with self.assertRaises(LoanRequestError):
            request_loan(self.siswa, [self.copy_busy.pk])
        self.assertEqual(Loan.objects.count(), 0)

    def test_inactive_user_ditolak(self):
        self.client.force_login(self.inactive)
        response = self.client.post(reverse("circulation:loan_create"))
        self.assertEqual(response.status_code, 302)
        with self.assertRaises(LoanRequestError):
            request_loan(self.inactive, [self.copy1.pk])
        self.assertEqual(Loan.objects.count(), 0)

    def test_duplicate_active_copy_conflict(self):
        request_loan(self.siswa, [self.copy1.pk])
        with self.assertRaises(LoanRequestError):
            request_loan(self.siswa2, [self.copy1.pk])
        with self.assertRaises(LoanRequestError):
            request_loan(self.siswa, [self.copy1.pk, self.copy2.pk])
        self.assertEqual(Loan.objects.count(), 1)
        self.assertEqual(LoanItem.objects.count(), 1)

    def test_unauthorized_anonymous_dan_pustakawan(self):
        login_url = reverse("accounts:login")
        cart = reverse("circulation:cart")
        for url in (
            cart,
            reverse("circulation:loan_create"),
            reverse("circulation:loan_list"),
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn(login_url, response["Location"])
        self.client.force_login(self.pus)
        self.assertEqual(self.client.get(cart).status_code, 403)
        self.assertEqual(
            self.client.post(reverse("circulation:loan_create")).status_code,
            403,
        )
        self.assertEqual(Loan.objects.count(), 0)

    def test_empty_selection_tidak_membuat_loan(self):
        self.client.force_login(self.siswa)
        response = self.client.post(reverse("circulation:loan_create"))
        self.assertRedirects(response, reverse("circulation:cart"))
        self.assertEqual(Loan.objects.count(), 0)

    def test_gagal_atomik_tidak_setengah_tersimpan(self):
        with self.assertRaises(LoanRequestError):
            request_loan(self.siswa, [self.copy1.pk, 999999])
        self.assertEqual(Loan.objects.count(), 0)
        self.assertEqual(LoanItem.objects.count(), 0)

    def test_riwayat_hanya_milik_sendiri(self):
        loan = request_loan(self.siswa, [self.copy1.pk])
        self.client.force_login(self.siswa)
        response = self.client.get(reverse("circulation:loan_list"))
        self.assertContains(response, f"Pengajuan #{loan.pk}")
        self.assertEqual(
            self.client.get(
                reverse("circulation:loan_detail", args=[loan.pk])
            ).status_code,
            200,
        )
        self.client.force_login(self.siswa2)
        response = self.client.get(reverse("circulation:loan_list"))
        self.assertNotContains(response, f"Pengajuan #{loan.pk}")
        self.assertEqual(
            self.client.get(
                reverse("circulation:loan_detail", args=[loan.pk])
            ).status_code,
            404,
        )

    def test_cart_remove(self):
        self.client.force_login(self.siswa)
        self._set_cart(self.copy1, self.copy2)
        self.client.post(
            reverse("circulation:cart_remove", args=[self.copy1.pk])
        )
        self.assertEqual(
            self.client.session.get("loan_cart"), [self.copy2.pk]
        )
