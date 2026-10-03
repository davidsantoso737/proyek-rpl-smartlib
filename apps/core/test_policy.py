import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Book, BookCopy, Category
from apps.circulation.models import Loan, LoanItem
from apps.circulation.services import (
    activate_handover,
    approve_loan,
    process_return,
    request_loan,
)
from apps.core.models import LibrarySetting
from apps.core.services import get_active_setting

User = get_user_model()

POLICY_URL = reverse("core:policy")


class PolicyTests(TestCase):
    def setUp(self):
        self.pus = self._make_user("pus1", User.Role.PUSTAKAWAN)
        self.siswa = self._make_user("siswa1", User.Role.SISWA, nis="10001")
        self.v1 = LibrarySetting.objects.create(
            physical_loan_duration=7,
            ebook_access_duration=14,
            pickup_qr_validity=30,
            fine_rate_per_day=Decimal("1000"),
            effective_from=timezone.now(),
        )
        category = Category.objects.create(name="Fiksi")
        book = Book.objects.create(
            category=category, isbn="ISBN-POL-1", title="Policy A"
        )
        self.copy1 = self._make_copy(book, "POL-001")
        self.copy2 = self._make_copy(book, "POL-002")

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

    def _policy_data(self, **overrides):
        data = {
            "physical_loan_duration": 7,
            "ebook_access_duration": 14,
            "pickup_qr_validity": 30,
            "fine_rate_per_day": "1000",
        }
        data.update(overrides)
        return data

    def _active_loan(self, copy):
        loan = request_loan(self.siswa, [copy.pk])
        approve_loan(loan.pk, self.pus)
        loan.refresh_from_db()
        activate_handover(loan.pickup_token, self.pus)
        return Loan.objects.get(pk=loan.pk)

    # --- kelola via UI ---

    def test_pustakawan_melihat_dan_mengubah_policy(self):
        self.client.force_login(self.pus)
        response = self.client.get(POLICY_URL)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "7 hari")
        response = self.client.post(
            POLICY_URL, self._policy_data(fine_rate_per_day="2500")
        )
        self.assertRedirects(response, POLICY_URL)
        self.v1.refresh_from_db()
        self.assertIsNotNone(self.v1.effective_until)
        active = get_active_setting()
        self.assertEqual(active.fine_rate_per_day, Decimal("2500"))
        self.assertIsNone(active.effective_until)
        self.assertEqual(active.updated_by, self.pus)
        self.assertEqual(LibrarySetting.objects.count(), 2)

    def test_update_duration(self):
        self.client.force_login(self.pus)
        self.client.post(
            POLICY_URL, self._policy_data(physical_loan_duration=14)
        )
        self.assertEqual(get_active_setting().physical_loan_duration, 14)

    def test_invalid_ditolak_tanpa_versi_baru(self):
        self.client.force_login(self.pus)
        response = self.client.post(
            POLICY_URL, self._policy_data(physical_loan_duration=0)
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(LibrarySetting.objects.count(), 1)

    def test_unauthorized(self):
        self.assertEqual(self.client.get(POLICY_URL).status_code, 302)
        self.client.force_login(self.siswa)
        self.assertEqual(self.client.get(POLICY_URL).status_code, 403)
        self.assertEqual(
            self.client.post(POLICY_URL, self._policy_data()).status_code,
            403,
        )
        self.assertEqual(LibrarySetting.objects.count(), 1)

    # --- efek ke transaksi ---

    def test_new_loan_uses_new_duration_old_unchanged(self):
        old_loan = self._active_loan(self.copy1)
        old_due = Loan.objects.get(pk=old_loan.pk).due_at
        self.client.force_login(self.pus)
        self.client.post(
            POLICY_URL, self._policy_data(physical_loan_duration=14)
        )
        new_loan = self._active_loan(self.copy2)
        self.assertEqual(
            (new_loan.due_at - new_loan.borrowed_at).total_seconds(),
            14 * 86400,
        )
        self.assertEqual(Loan.objects.get(pk=old_loan.pk).due_at, old_due)

    def test_new_fine_uses_current_rate_historical_unchanged(self):
        loan = self._active_loan(self.copy1)
        past_due = timezone.now() - datetime.timedelta(days=2, hours=1)
        LoanItem.objects.filter(loan=loan).update(due_at=past_due)
        item = process_return("POL-001", self.pus, "BAIK")
        self.assertEqual(item.fine_amount, Decimal("2000"))
        self.client.force_login(self.pus)
        self.client.post(
            POLICY_URL, self._policy_data(fine_rate_per_day="5000")
        )
        item.refresh_from_db()
        self.assertEqual(item.fine_amount, Decimal("2000"))
        loan2 = self._active_loan(self.copy2)
        past_due2 = timezone.now() - datetime.timedelta(days=2, hours=1)
        LoanItem.objects.filter(loan=loan2).update(due_at=past_due2)
        item2 = process_return("POL-002", self.pus, "BAIK")
        self.assertEqual(item2.fine_amount, Decimal("10000"))

    def test_pickup_uses_configured_validity(self):
        self.client.force_login(self.pus)
        self.client.post(
            POLICY_URL, self._policy_data(pickup_qr_validity=60)
        )
        loan = request_loan(self.siswa, [self.copy1.pk])
        approve_loan(loan.pk, self.pus)
        loan.refresh_from_db()
        self.assertEqual(
            (loan.pickup_exp_at - loan.approved_at).total_seconds(),
            60 * 60,
        )
