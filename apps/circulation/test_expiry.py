import datetime
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Book, BookCopy, Category
from apps.circulation.models import Loan
from apps.circulation.services import (
    AUTO_EXPIRED_REASON,
    EXPIRED_NOTIFICATION_TYPE,
    LoanRequestError,
    PickupTokenError,
    activate_handover,
    active_reserved_copy_ids,
    approve_loan,
    copy_has_active_reservation,
    expire_pickup_loans,
    request_loan,
)
from apps.core.models import LibrarySetting
from apps.notifications.models import Notification
from apps.whatsapp.models import WhatsAppOutbox
from apps.whatsapp.tests.factories import make_connection

User = get_user_model()


@override_settings(WHATSAPP_NOTIFICATIONS_ENABLED=True)
class PickupExpiryTests(TestCase):
    def setUp(self):
        self.siswa = self._make_user("siswa1", User.Role.SISWA, nis="10001")
        self.siswa2 = self._make_user("siswa2", User.Role.SISWA, nis="10002")
        self.pus = self._make_user("pus1", User.Role.PUSTAKAWAN)
        make_connection(self.siswa)
        LibrarySetting.objects.create(
            physical_loan_duration=7,
            ebook_access_duration=14,
            pickup_qr_validity=30,
            fine_rate_per_day=Decimal("1500"),
            effective_from=timezone.now(),
        )
        category = Category.objects.create(name="Fiksi")
        self.book = Book.objects.create(
            category=category, isbn="ISBN-EXP-1", title="Kedaluwarsa A"
        )
        self.copy1 = self._make_copy(self.book, "EXP-001")
        self.copy2 = self._make_copy(self.book, "EXP-002")
        self.copy3 = self._make_copy(self.book, "EXP-003")

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

    def _approved_loan(self, copies=None, user=None):
        loan = request_loan(
            user or self.siswa,
            [c.pk for c in (copies or [self.copy1])],
        )
        approve_loan(loan.pk, self.pus)
        loan.refresh_from_db()
        return loan

    def _expire(self, loan):
        loan.pickup_exp_at = timezone.now() - datetime.timedelta(minutes=1)
        loan.save(update_fields=["pickup_exp_at"])

    def _reserved_ids(self):
        return set(
            active_reserved_copy_ids().values_list(
                "book_copy_id", flat=True
            )
        )

    # --- reservasi aktif ---

    def test_qr_aktif_mereservasi_copy(self):
        loan = self._approved_loan()

        self.assertIn(self.copy1.pk, self._reserved_ids())
        self.assertTrue(copy_has_active_reservation(self.copy1))
        with self.assertRaises(LoanRequestError):
            request_loan(self.siswa2, [self.copy1.pk])

        self.client.force_login(self.siswa)
        detail = self.client.get(
            reverse("circulation:loan_detail", args=[loan.pk])
        )
        self.assertContains(detail, "QR Pickup pengambilan buku")
        self.assertEqual(
            self.client.get(
                reverse("circulation:pickup_qr", args=[loan.pk])
            ).status_code,
            200,
        )

    def test_katalog_menandai_copy_yang_dipesan(self):
        self._approved_loan()

        self.client.force_login(self.siswa2)
        response = self.client.get(
            reverse("catalog:book_detail", args=[self.book.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dipesan")
        self.assertEqual(response.context["available"], 2)

    # --- expiration ---

    def test_expired_melepas_reservasi_dan_mengirim_notifikasi(self):
        loan = self._approved_loan()
        self._expire(loan)

        out = StringIO()
        call_command("expire_pickup_loans", stdout=out)
        self.assertIn("1 pengajuan kedaluwarsa dibatalkan", out.getvalue())

        loan.refresh_from_db()
        self.assertEqual(loan.status, "CANCELLED")
        self.assertEqual(loan.rejection_reason, AUTO_EXPIRED_REASON)
        self.assertIsNotNone(loan.rejected_at)
        self.assertIsNone(loan.pickup_used_at)

        self.assertNotIn(self.copy1.pk, self._reserved_ids())
        self.assertFalse(copy_has_active_reservation(self.copy1))
        self.assertEqual(
            Notification.objects.filter(
                user=self.siswa, type=EXPIRED_NOTIFICATION_TYPE
            ).count(),
            1,
        )
        self.assertEqual(
            WhatsAppOutbox.objects.filter(
                notification_type=EXPIRED_NOTIFICATION_TYPE
            ).count(),
            1,
        )

        request_loan(self.siswa2, [self.copy1.pk])
        self.assertEqual(
            Loan.objects.filter(
                user=self.siswa2, status="PENDING_APPROVAL"
            ).count(),
            1,
        )

    def test_expired_idempoten_tanpa_notifikasi_ganda(self):
        loan = self._approved_loan()
        self._expire(loan)

        self.assertEqual(expire_pickup_loans(), 1)
        self.assertEqual(expire_pickup_loans(), 0)
        self.assertEqual(expire_pickup_loans(), 0)

        self.assertEqual(
            Notification.objects.filter(
                user=self.siswa, type=EXPIRED_NOTIFICATION_TYPE
            ).count(),
            1,
        )
        self.assertEqual(
            WhatsAppOutbox.objects.filter(
                notification_type=EXPIRED_NOTIFICATION_TYPE
            ).count(),
            1,
        )

    def test_hanya_loan_expired_yang_dibatalkan(self):
        expired = self._approved_loan()
        future = self._approved_loan(copies=[self.copy2])
        active = self._approved_loan(copies=[self.copy3])
        activate_handover(active.pickup_token, self.pus)
        self._expire(expired)

        self.assertEqual(expire_pickup_loans(), 1)

        expired.refresh_from_db()
        future.refresh_from_db()
        active.refresh_from_db()
        self.assertEqual(expired.status, "CANCELLED")
        self.assertEqual(future.status, "APPROVED")
        self.assertEqual(active.status, "ACTIVE")

    def test_handover_sebelum_expiry_tidak_dibatalkan(self):
        loan = self._approved_loan()
        self.assertEqual(expire_pickup_loans(), 0)
        loan.refresh_from_db()
        self.assertEqual(loan.status, "APPROVED")

        activated = activate_handover(loan.pickup_token, self.pus)
        self.assertEqual(activated.status, "ACTIVE")
        self.assertFalse(
            Notification.objects.filter(
                user=self.siswa, type=EXPIRED_NOTIFICATION_TYPE
            ).exists()
        )

    # --- akses setelah expiration ---

    def test_qr_dan_handover_ditolak_setelah_expired(self):
        loan = self._approved_loan()
        self._expire(loan)
        expire_pickup_loans()

        self.client.force_login(self.siswa)
        detail = self.client.get(
            reverse("circulation:loan_detail", args=[loan.pk])
        )
        self.assertContains(detail, "Masa Pengambilan Telah Berakhir")
        self.assertContains(detail, "Kedaluwarsa / Dibatalkan")
        self.assertNotContains(detail, "QR Pickup pengambilan buku")
        self.assertEqual(
            self.client.get(
                reverse("circulation:pickup_qr", args=[loan.pk])
            ).status_code,
            404,
        )

        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("circulation:handover"), {"token": loan.pickup_token}
        )
        self.assertContains(response, "kedaluwarsa")
        loan.refresh_from_db()
        self.assertEqual(loan.status, "CANCELLED")
        with self.assertRaises(PickupTokenError):
            activate_handover(loan.pickup_token, self.pus)

    def test_pengajuan_baru_setelah_expired_mendapat_copy_yang_sama(self):
        loan = self._approved_loan()
        self._expire(loan)
        expire_pickup_loans()

        new_loan = request_loan(self.siswa, [self.copy1.pk])
        self.assertEqual(new_loan.status, "PENDING_APPROVAL")
        self.assertIn(self.copy1.pk, self._reserved_ids())

        self.client.force_login(self.siswa)
        detail = self.client.get(
            reverse("catalog:book_detail", args=[self.book.pk])
        )
        self.assertContains(detail, "Dipesan")
