import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Book, BookCopy, Category
from apps.circulation.services import (
    activate_handover,
    approve_loan,
    process_return,
    request_loan,
)
from apps.core.models import LibrarySetting

User = get_user_model()

REPORT_URL = reverse("reports:report")


class ReportTests(TestCase):
    def setUp(self):
        self.pus = self._make_user("pus1", User.Role.PUSTAKAWAN)
        self.siswa = self._make_user("siswa1", User.Role.SISWA, nis="10001")
        LibrarySetting.objects.create(
            physical_loan_duration=7,
            ebook_access_duration=14,
            pickup_qr_validity=30,
            fine_rate_per_day=Decimal("1000"),
            effective_from=timezone.now(),
        )
        category = Category.objects.create(name="Fiksi")
        book = Book.objects.create(
            category=category, isbn="ISBN-RP-1", title="Laporan A"
        )
        self.copy1 = BookCopy.objects.create(
            book=book,
            copy_code="RP-001",
            status="TERSEDIA",
            acquired_at=datetime.date(2026, 1, 10),
            condition="BAIK",
            location="RAK-A1",
        )
        self.copy2 = BookCopy.objects.create(
            book=book,
            copy_code="RP-002",
            status="TERSEDIA",
            acquired_at=datetime.date(2026, 1, 10),
            condition="BAIK",
            location="RAK-A1",
        )
        pending = request_loan(self.siswa, [self.copy1.pk])
        active = request_loan(self.siswa, [self.copy2.pk])
        approve_loan(active.pk, self.pus)
        active.refresh_from_db()
        activate_handover(active.pickup_token, self.pus)
        self.pending_pk = pending.pk
        self.active_pk = active.pk

    def _make_user(self, username, role, password="secret123", **extra):
        defaults = {"full_name": f"{username} Full"}
        defaults.update(extra)
        user = User(username=username, role=role, **defaults)
        user.set_password(password)
        user.full_clean()
        user.save()
        return user

    def test_report_access(self):
        self.assertEqual(self.client.get(REPORT_URL).status_code, 302)
        self.client.force_login(self.siswa)
        self.assertEqual(self.client.get(REPORT_URL).status_code, 403)
        self.client.force_login(self.pus)
        self.assertEqual(self.client.get(REPORT_URL).status_code, 200)

    def test_generated_report_memuat_data(self):
        self.client.force_login(self.pus)
        response = self.client.get(
            REPORT_URL, {"report_type": "peminjaman"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Laporan Peminjaman Buku Fisik")
        self.assertContains(response, "siswa1")

    def test_filters_status_dan_tanggal(self):
        self.client.force_login(self.pus)
        response = self.client.get(
            REPORT_URL,
            {"report_type": "peminjaman", "status": "ACTIVE"},
        )
        rows = response.context["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], self.active_pk)
        self.assertEqual(rows[0][4], "ACTIVE")
        today = timezone.localdate().isoformat()
        response = self.client.get(
            REPORT_URL,
            {
                "report_type": "peminjaman",
                "date_from": today,
                "date_to": today,
            },
        )
        self.assertContains(response, "siswa1")
        response = self.client.get(
            REPORT_URL,
            {"report_type": "peminjaman", "date_from": "2000-01-01",
             "date_to": "2000-01-02"},
        )
        self.assertContains(response, "Tidak ada data")

    def test_filter_tanggal_pengembalian_memakai_wib(self):
        from apps.circulation.models import LoanItem

        item = LoanItem.objects.get(loan__pk=self.active_pk)
        item.returned_at = datetime.datetime(
            2026, 10, 20, 18, 0, tzinfo=datetime.timezone.utc
        )
        item.return_status = "KEMBALI"
        item.save(update_fields=["returned_at", "return_status"])
        self.client.force_login(self.pus)

        response = self.client.get(
            REPORT_URL,
            {
                "report_type": "pengembalian",
                "date_from": "2026-10-21",
                "date_to": "2026-10-21",
            },
        )
        self.assertContains(response, "RP-002")

        response = self.client.get(
            REPORT_URL,
            {
                "report_type": "pengembalian",
                "date_from": "2026-10-20",
                "date_to": "2026-10-20",
            },
        )
        self.assertContains(response, "Tidak ada data")

    def test_empty_report(self):
        self.client.force_login(self.pus)
        response = self.client.get(
            REPORT_URL, {"report_type": "anggota", "q": "zzz-tidak-ada"}
        )
        self.assertContains(response, "Tidak ada data")

    def test_export_pdf(self):
        self.client.force_login(self.pus)
        response = self.client.get(
            REPORT_URL, {"report_type": "peminjaman", "export": "pdf"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn("attachment", response["Content-Disposition"])

    def test_export_xlsx(self):
        self.client.force_login(self.pus)
        response = self.client.get(
            REPORT_URL,
            {"report_type": "pengembalian", "export": "xlsx"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("spreadsheetml.sheet", response["Content-Type"])
        self.assertTrue(response.content.startswith(b"PK"))

    def test_return_report_memuat_denda(self):
        process_return("RP-002", self.pus, "BAIK")
        self.client.force_login(self.pus)
        response = self.client.get(
            REPORT_URL, {"report_type": "pengembalian"}
        )
        self.assertContains(response, "RP-002")

    def test_unauthorized_export(self):
        self.client.force_login(self.siswa)
        self.assertEqual(
            self.client.get(
                REPORT_URL, {"report_type": "peminjaman", "export": "pdf"}
            ).status_code,
            403,
        )
