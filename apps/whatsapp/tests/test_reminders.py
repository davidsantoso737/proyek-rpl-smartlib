import datetime
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.catalog.models import Book, BookCopy, Category
from apps.circulation.models import Loan, LoanItem
from apps.whatsapp.models import WhatsAppOutbox
from apps.whatsapp.reminders import enqueue_due_reminders

from .factories import make_connection, make_user

User = get_user_model()


@override_settings(
    WHATSAPP_NOTIFICATIONS_ENABLED=True,
    FONNTE_API_TOKEN="test-token",
    FONNTE_TIMEOUT_SECONDS=5,
)
class WhatsAppReminderTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.siswa = make_user()
        self.siswa2 = make_user(username="siswa2", phone="082222222222")
        self.connection = make_connection(self.siswa)
        self.connection2 = make_connection(self.siswa2, phone="082222222222")
        self.category = Category.objects.create(name="Fiksi")
        self.book1 = self._make_book("ISBN-REM-1", "Pengingat A")
        self.book2 = self._make_book("ISBN-REM-2", "Pengingat B")
        self.book3 = self._make_book("ISBN-REM-3", "Pengingat C")
        self.copy1 = self._make_copy(self.book1, "REM-001")
        self.copy2 = self._make_copy(self.book2, "REM-002")
        self.copy3 = self._make_copy(self.book3, "REM-003")

    def _make_book(self, isbn, title):
        return Book.objects.create(
            category=self.category, isbn=isbn, title=title
        )

    def _make_copy(self, book, code):
        return BookCopy.objects.create(
            book=book,
            copy_code=code,
            status="DIPINJAM",
            acquired_at=datetime.date(2026, 1, 10),
            condition="BAIK",
            location="RAK-A1",
        )

    def _due_in(self, days):
        local = timezone.localtime(self.now) + datetime.timedelta(days=days)
        return local.replace(hour=12, minute=0, second=0, microsecond=0)

    def _make_loan(
        self,
        user=None,
        *,
        days=3,
        due_at=None,
        copies=None,
        returned=False,
    ):
        due_at = due_at or self._due_in(days)
        status = "COMPLETED" if returned else "ACTIVE"
        loan = Loan.objects.create(
            user=user or self.siswa,
            status=status,
            requested_at=self.now,
            borrowed_at=self.now,
            due_at=due_at,
            completed_at=self.now if returned else None,
        )
        for copy in copies or [self.copy1]:
            LoanItem.objects.create(
                loan=loan,
                book_copy=copy,
                borrowed_at=self.now,
                due_at=due_at,
                returned_at=self.now if returned else None,
                return_status="KEMBALI" if returned else "BELUM_KEMBALI",
                fine_rate_per_day=Decimal("1000"),
                fine_status="LUNAS" if returned else "BELUM_BAYAR",
            )
        return loan

    def test_h3_membuat_pengingat(self):
        self._make_loan(days=3)

        stats = enqueue_due_reminders(now=self.now)

        outbox = WhatsAppOutbox.objects.get()
        self.assertEqual(stats["reminders"], 1)
        self.assertEqual(stats["books"], 1)
        self.assertEqual(outbox.notification_type, "LOAN_DUE_SOON")
        self.assertIn("Jatuh tempo H-3", outbox.title)
        self.assertTrue(outbox.event_key.startswith("due_3:"))
        self.assertIn("3 hari", outbox.message)

    def test_h1_membuat_pengingat(self):
        self._make_loan(days=1)

        enqueue_due_reminders(now=self.now)

        outbox = WhatsAppOutbox.objects.get()
        self.assertIn("Jatuh tempo besok", outbox.title)
        self.assertTrue(outbox.event_key.startswith("due_1:"))
        self.assertIn("besok", outbox.message)

    def test_terlambat_membuat_pengingat(self):
        self._make_loan(days=-2)

        enqueue_due_reminders(now=self.now)

        outbox = WhatsAppOutbox.objects.get()
        self.assertEqual(outbox.notification_type, "LOAN_OVERDUE")
        self.assertIn("Peminjaman terlambat", outbox.title)
        self.assertTrue(outbox.event_key.startswith("overdue:"))
        self.assertIn("terlambat 2 hari", outbox.message)

    def test_buku_sudah_dikembalikan_tidak_dipengingatkan(self):
        self._make_loan(days=-2, returned=True)

        stats = enqueue_due_reminders(now=self.now)

        self.assertEqual(stats["reminders"], 0)
        self.assertFalse(WhatsAppOutbox.objects.exists())

    def test_belum_memenuhi_syarat_tidak_dipengingatkan(self):
        self._make_loan(days=2)
        self._make_loan(days=0, copies=[self.copy2])

        stats = enqueue_due_reminders(now=self.now)

        self.assertEqual(stats["reminders"], 0)
        self.assertFalse(WhatsAppOutbox.objects.exists())

    def test_beberapa_buku_digabung_satu_ringkasan(self):
        self._make_loan(days=3, copies=[self.copy1, self.copy2])

        stats = enqueue_due_reminders(now=self.now)

        outbox = WhatsAppOutbox.objects.get()
        self.assertEqual(stats["reminders"], 1)
        self.assertEqual(stats["books"], 2)
        self.assertEqual(WhatsAppOutbox.objects.count(), 1)
        self.assertIn("Pengingat A", outbox.message)
        self.assertIn("Pengingat B", outbox.message)

    def test_pengguna_tanpa_whatsapp_aktif_dilewati(self):
        self.connection.delete()
        self._make_loan(days=3)

        stats = enqueue_due_reminders(now=self.now)

        self.assertEqual(stats["reminders"], 1)
        self.assertEqual(stats["queued"], 0)
        self.assertFalse(WhatsAppOutbox.objects.exists())

    def test_preferensi_nonaktif_dilewati(self):
        self.connection.notify_due_soon = False
        self.connection.notify_overdue = False
        self.connection.save(
            update_fields=["notify_due_soon", "notify_overdue"]
        )
        self._make_loan(days=3)
        self._make_loan(days=-1, copies=[self.copy2])

        stats = enqueue_due_reminders(now=self.now)

        self.assertEqual(stats["reminders"], 2)
        self.assertEqual(stats["queued"], 0)
        self.assertFalse(WhatsAppOutbox.objects.exists())

    def test_command_berulang_tidak_menduplikasi(self):
        self._make_loan(days=3)

        with patch(
            "apps.whatsapp.reminders.timezone.now",
            return_value=self.now,
        ):
            call_command("kirim_pengingat_whatsapp", "--no-send")
            call_command("kirim_pengingat_whatsapp", "--no-send")

        self.assertEqual(WhatsAppOutbox.objects.count(), 1)

    def test_tanggal_memakai_zona_waktu_django(self):
        now_utc = datetime.datetime(
            2026, 10, 20, 17, 30, tzinfo=datetime.timezone.utc
        )
        due_utc = datetime.datetime(
            2026, 10, 24, 16, 30, tzinfo=datetime.timezone.utc
        )
        self._make_loan(user=self.siswa, due_at=due_utc)

        with timezone.override("Asia/Jakarta"):
            stats = enqueue_due_reminders(now=now_utc)

        outbox = WhatsAppOutbox.objects.get()
        self.assertEqual(stats["reminders"], 1)
        self.assertTrue(outbox.event_key.startswith("due_3:"))
        self.assertIn("24 Oktober 2026", outbox.message)

    def test_command_dapat_dijalankan_manual(self):
        self._make_loan(days=3)
        output = StringIO()

        with patch(
            "apps.whatsapp.reminders.timezone.now",
            return_value=self.now,
        ):
            call_command(
                "kirim_pengingat_whatsapp",
                "--no-send",
                stdout=output,
            )

        self.assertIn("Pengingat dicatat", output.getvalue())
        self.assertEqual(WhatsAppOutbox.objects.count(), 1)
