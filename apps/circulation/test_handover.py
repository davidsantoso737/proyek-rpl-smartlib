import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Book, BookCopy, Category
from apps.circulation.models import Loan
from apps.circulation.services import (
    PickupTokenError,
    activate_handover,
    approve_loan,
    request_loan,
)
from apps.core.models import LibrarySetting
from apps.notifications.models import Notification

User = get_user_model()


class HandoverTests(TestCase):
    def setUp(self):
        self.siswa = self._make_user("siswa1", User.Role.SISWA, nis="10001")
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
            category=category, isbn="ISBN-HO-1", title="Handover A"
        )
        self.copy1 = self._make_copy(book, "HO-001")
        self.copy2 = self._make_copy(book, "HO-002")
        book2 = Book.objects.create(
            category=category, isbn="ISBN-HO-2", title="Handover B"
        )
        self.copy3 = self._make_copy(book2, "HO-003")

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

    def _approved_loan(self, user=None, copies=None):
        loan = request_loan(
            user or self.siswa, [c.pk for c in (copies or [self.copy1])]
        )
        approve_loan(loan.pk, self.pus)
        loan.refresh_from_db()
        return loan

    # --- valid pickup ---

    def test_valid_pickup_mengaktifkan_peminjaman(self):
        loan = self._approved_loan(copies=[self.copy1, self.copy3])
        self.client.force_login(self.pus)
        preview = self.client.post(
            reverse("circulation:handover"), {"token": loan.pickup_token}
        )
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "HO-001")
        self.assertContains(preview, "HO-003")
        before = timezone.now()
        response = self.client.post(
            reverse("circulation:handover_confirm"),
            {"token": loan.pickup_token},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Peminjaman aktif")
        loan.refresh_from_db()
        self.assertEqual(loan.status, "ACTIVE")
        # borrowed_at = waktu aktual handover (bukan requested/approved).
        self.assertIsNotNone(loan.borrowed_at)
        self.assertNotEqual(loan.borrowed_at, loan.requested_at)
        self.assertNotEqual(loan.borrowed_at, loan.approved_at)
        self.assertLess(
            abs((loan.borrowed_at - before).total_seconds()), 120
        )
        self.assertEqual(
            (loan.due_at - loan.borrowed_at).total_seconds(), 7 * 86400
        )
        self.assertIsNotNone(loan.pickup_used_at)
        for item in loan.items.all():
            self.assertEqual(item.borrowed_at, loan.borrowed_at)
            self.assertEqual(item.due_at, loan.due_at)
        statuses = sorted(
            BookCopy.objects.filter(
                pk__in=[self.copy1.pk, self.copy3.pk]
            ).values_list("status", flat=True)
        )
        self.assertEqual(statuses, ["DIPINJAM", "DIPINJAM"])
        self.assertTrue(
            Notification.objects.filter(
                user=self.siswa, type="LOAN_ACTIVE"
            ).exists()
        )

    def test_multiple_items_tercatat_semua(self):
        loan = self._approved_loan(copies=[self.copy1, self.copy2])
        activated = activate_handover(loan.pickup_token, self.pus)
        self.assertEqual(activated.items.count(), 2)
        self.assertEqual(activated.status, "ACTIVE")

    # --- penolakan token ---

    def test_expired_pickup_ditolak(self):
        loan = self._approved_loan()
        loan.pickup_exp_at = timezone.now() - datetime.timedelta(minutes=1)
        loan.save(update_fields=["pickup_exp_at"])
        with self.assertRaises(PickupTokenError):
            activate_handover(loan.pickup_token, self.pus)
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("circulation:handover"), {"token": loan.pickup_token}
        )
        self.assertContains(response, "kedaluarsa")
        loan.refresh_from_db()
        self.assertEqual(loan.status, "APPROVED")

    def test_reused_pickup_ditolak(self):
        loan = self._approved_loan()
        activate_handover(loan.pickup_token, self.pus)
        used_at = Loan.objects.get(pk=loan.pk).pickup_used_at
        with self.assertRaises(PickupTokenError):
            activate_handover(loan.pickup_token, self.pus)
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("circulation:handover_confirm"),
            {"token": loan.pickup_token},
        )
        self.assertRedirects(response, reverse("circulation:handover"))
        self.assertEqual(
            Loan.objects.get(pk=loan.pk).pickup_used_at, used_at
        )

    def test_wrong_status_ditolak(self):
        with self.assertRaises(PickupTokenError):
            activate_handover("token-asing", self.pus)
        loan = self._approved_loan()
        activate_handover(loan.pickup_token, self.pus)
        # State ACTIVE bukan APPROVED -> verifikasi gagal.
        with self.assertRaises(PickupTokenError):
            activate_handover(loan.pickup_token, self.pus)

    # --- integritas ---

    def test_unavailable_copy_menggagalkan_seluruh_handover(self):
        loan = self._approved_loan(copies=[self.copy1, self.copy2])
        self.copy2.status = "DIPINJAM"
        self.copy2.save()
        with self.assertRaises(PickupTokenError):
            activate_handover(loan.pickup_token, self.pus)
        loan.refresh_from_db()
        self.assertEqual(loan.status, "APPROVED")
        self.assertIsNone(loan.borrowed_at)
        self.assertIsNone(loan.pickup_used_at)
        self.assertEqual(
            BookCopy.objects.get(pk=self.copy1.pk).status, "TERSEDIA"
        )
        self.assertFalse(
            Notification.objects.filter(
                user=self.siswa, type="LOAN_ACTIVE"
            ).exists()
        )

    def test_handover_atomicity_tanpa_setengah_tersimpan(self):
        loan = self._approved_loan(copies=[self.copy1, self.copy3])
        BookCopy.objects.filter(pk=self.copy3.pk).update(status="RUSAK")
        try:
            activate_handover(loan.pickup_token, self.pus)
            self.fail("seharusnya gagal")
        except PickupTokenError:
            pass
        loan.refresh_from_db()
        self.assertEqual(loan.status, "APPROVED")
        self.assertIsNone(loan.borrowed_at)
        self.assertIsNone(loan.due_at)
        self.assertIsNone(loan.pickup_used_at)
        self.assertEqual(
            BookCopy.objects.get(pk=self.copy1.pk).status, "TERSEDIA"
        )

    # --- authorization ---

    def test_unauthorized_user(self):
        loan = self._approved_loan()
        handover = reverse("circulation:handover")
        confirm = reverse("circulation:handover_confirm")
        self.assertEqual(self.client.get(handover).status_code, 302)
        self.assertEqual(
            self.client.post(confirm, {"token": "x"}).status_code, 302
        )
        self.client.force_login(self.siswa)
        self.assertEqual(self.client.get(handover).status_code, 403)
        self.assertEqual(
            self.client.post(
                confirm, {"token": loan.pickup_token}
            ).status_code,
            403,
        )
        self.client.force_login(self.guru)
        self.assertEqual(self.client.get(handover).status_code, 403)
        loan.refresh_from_db()
        self.assertEqual(loan.status, "APPROVED")
        self.assertIsNone(loan.pickup_used_at)
