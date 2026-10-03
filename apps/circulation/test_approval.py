import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Book, BookCopy, Category
from apps.circulation.models import Loan
from apps.circulation.services import (
    ApprovalError,
    PickupTokenError,
    approve_loan,
    reject_loan,
    request_loan,
    verify_pickup_token,
)
from apps.core.models import LibrarySetting
from apps.notifications.models import Notification

User = get_user_model()


class ApprovalTests(TestCase):
    def setUp(self):
        self.siswa = self._make_user("siswa1", User.Role.SISWA, nis="10001")
        self.siswa2 = self._make_user("siswa2", User.Role.SISWA, nis="10002")
        self.guru = self._make_user("guru1", User.Role.GURU, nip="20001")
        self.pus = self._make_user("pus1", User.Role.PUSTAKAWAN)
        LibrarySetting.objects.create(
            physical_loan_duration=7,
            ebook_access_duration=14,
            pickup_qr_validity=30,
            fine_rate_per_day=Decimal("1500"),
            effective_from=timezone.now(),
        )
        category = Category.objects.create(name="Fiksi")
        book = Book.objects.create(
            category=category, isbn="ISBN-APP-1", title="Approve A"
        )
        self.copy1 = self._make_copy(book, "APP-001")
        self.copy2 = self._make_copy(book, "APP-002")
        book2 = Book.objects.create(
            category=category, isbn="ISBN-APP-2", title="Approve B"
        )
        self.copy3 = self._make_copy(book2, "APP-003")

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

    def _pending_loan(self, user=None, copies=None):
        return request_loan(
            user or self.siswa, [c.pk for c in (copies or [self.copy1])]
        )

    # --- approve ---

    def test_approve_menghasilkan_qr_pickup_valid(self):
        loan = self._pending_loan(copies=[self.copy1, self.copy3])
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("circulation:approval_approve", args=[loan.pk])
        )
        self.assertRedirects(
            response,
            reverse("circulation:approval_detail", args=[loan.pk]),
        )
        loan.refresh_from_db()
        self.assertEqual(loan.status, "APPROVED")
        self.assertEqual(loan.approved_by, self.pus)
        self.assertIsNotNone(loan.approved_at)
        self.assertTrue(loan.pickup_token and len(loan.pickup_token) >= 32)
        delta = loan.pickup_exp_at - loan.approved_at
        self.assertEqual(delta.total_seconds(), 30 * 60)
        notif = Notification.objects.get(
            user=self.siswa, reference_type="loan", reference_id=loan.pk
        )
        self.assertEqual(notif.type, "LOAN_APPROVED")
        # Eksemplar tetap TERSEDIA namun terikat (tidak bisa diminta lagi).
        self.assertEqual(
            BookCopy.objects.get(pk=self.copy1.pk).status, "TERSEDIA"
        )
        with self.assertRaises(Exception):
            request_loan(self.siswa2, [self.copy1.pk])
        self.assertEqual(
            Loan.objects.filter(user=self.siswa2).count(), 0
        )

    def test_approve_dengan_substitusi_eksemplar(self):
        loan = self._pending_loan()
        item = loan.items.get()
        self.client.force_login(self.pus)
        self.client.post(
            reverse("circulation:approval_approve", args=[loan.pk]),
            {f"copy_{item.pk}": str(self.copy2.pk)},
        )
        loan.refresh_from_db()
        self.assertEqual(loan.status, "APPROVED")
        self.assertEqual(loan.items.get().book_copy_id, self.copy2.pk)

    def test_approve_diblokir_jika_copy_tak_tersedia(self):
        loan = self._pending_loan()
        self.copy1.status = "DIPINJAM"
        self.copy1.save()
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("circulation:approval_approve", args=[loan.pk])
        )
        self.assertEqual(response.status_code, 200)
        loan.refresh_from_db()
        self.assertEqual(loan.status, "PENDING_APPROVAL")
        self.assertIsNone(loan.pickup_token)

    # --- reject ---

    def test_reject_membebaskan_eksemplar(self):
        loan = self._pending_loan()
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("circulation:approval_reject", args=[loan.pk]),
            {"reason": "Stok opname berjalan."},
        )
        self.assertRedirects(
            response, reverse("circulation:approval_list")
        )
        loan.refresh_from_db()
        self.assertEqual(loan.status, "REJECTED")
        self.assertEqual(loan.rejection_reason, "Stok opname berjalan.")
        self.assertIsNotNone(loan.rejected_at)
        self.assertTrue(
            Notification.objects.filter(
                user=self.siswa, type="LOAN_REJECTED"
            ).exists()
        )
        # Eksemplar bebas diminta lagi.
        loan2 = request_loan(self.siswa2, [self.copy1.pk])
        self.assertEqual(loan2.status, "PENDING_APPROVAL")

    def test_reject_tanpa_alasan_ditolak(self):
        loan = self._pending_loan()
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("circulation:approval_reject", args=[loan.pk]),
            {"reason": "  "},
        )
        self.assertEqual(response.status_code, 200)
        loan.refresh_from_db()
        self.assertEqual(loan.status, "PENDING_APPROVAL")

    # --- unauthorized ---

    def test_unauthorized_approval(self):
        loan = self._pending_loan()
        approve = reverse("circulation:approval_approve", args=[loan.pk])
        reject = reverse("circulation:approval_reject", args=[loan.pk])
        for url in (
            reverse("circulation:approval_list"),
            reverse("circulation:approval_detail", args=[loan.pk]),
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
        self.client.force_login(self.siswa)
        self.assertEqual(self.client.get(
            reverse("circulation:approval_list")).status_code, 403)
        self.assertEqual(
            self.client.post(approve).status_code, 403
        )
        self.client.force_login(self.guru)
        self.assertEqual(
            self.client.post(reject, {"reason": "x"}).status_code, 403
        )
        loan.refresh_from_db()
        self.assertEqual(loan.status, "PENDING_APPROVAL")

    def test_approval_list_hanya_pending(self):
        self._pending_loan()
        loan2 = self._pending_loan(user=self.siswa2, copies=[self.copy2])
        approve_loan(loan2.pk, self.pus)
        self.client.force_login(self.pus)
        response = self.client.get(reverse("circulation:approval_list"))
        self.assertContains(response, "siswa1")
        self.assertNotContains(response, "siswa2")

    # --- token verification ---

    def test_invalid_token(self):
        with self.assertRaises(PickupTokenError):
            verify_pickup_token("token-tidak-ada")
        with self.assertRaises(PickupTokenError):
            verify_pickup_token("")

    def test_invalid_transaction_state(self):
        loan = self._pending_loan()
        # PENDING tanpa token tidak bisa diverifikasi.
        with self.assertRaises(PickupTokenError):
            verify_pickup_token("apa-pun")
        approve_loan(loan.pk, self.pus)
        loan.refresh_from_db()
        self.assertEqual(verify_pickup_token(loan.pickup_token).pk, loan.pk)
        # State salah: tolak setelah approve tidak dimungkinkan.
        with self.assertRaises(ApprovalError):
            reject_loan(loan.pk, self.pus, "terlambat")
        with self.assertRaises(ApprovalError):
            approve_loan(loan.pk, self.pus)

    def test_expired_token(self):
        loan = self._pending_loan()
        approve_loan(loan.pk, self.pus)
        loan.refresh_from_db()
        loan.pickup_exp_at = timezone.now() - datetime.timedelta(minutes=1)
        loan.save(update_fields=["pickup_exp_at"])
        with self.assertRaises(PickupTokenError):
            verify_pickup_token(loan.pickup_token)

    def test_reused_token(self):
        loan = self._pending_loan()
        approve_loan(loan.pk, self.pus)
        loan.refresh_from_db()
        loan.pickup_used_at = timezone.now()
        loan.save(update_fields=["pickup_used_at"])
        with self.assertRaises(PickupTokenError):
            verify_pickup_token(loan.pickup_token)

    # --- QR image ---

    def test_qr_image_hanya_state_valid_dan_berhak(self):
        loan = self._pending_loan()
        qr = reverse("circulation:pickup_qr", args=[loan.pk])
        self.client.force_login(self.siswa)
        self.assertEqual(self.client.get(qr).status_code, 404)
        approve_loan(loan.pk, self.pus)
        response = self.client.get(qr)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(response.content.startswith(b"\x89PNG"))
        self.client.force_login(self.siswa2)
        self.assertEqual(self.client.get(qr).status_code, 404)
        self.client.force_login(self.pus)
        self.assertEqual(self.client.get(qr).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(qr).status_code, 302)
