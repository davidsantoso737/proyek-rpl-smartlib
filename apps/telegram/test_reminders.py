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
from apps.telegram.dispatch import process_due
from apps.telegram.models import TelegramAccount, TelegramOutbox
from apps.telegram.reminders import enqueue_due_reminders
from apps.telegram.services import SentMessage, TelegramAPIError

User = get_user_model()


class RecordingClient:
    def __init__(self, errors=None):
        self.errors = list(errors or [])
        self.sent = []

    def send_message(self, chat_id, text, *, parse_mode=None):
        self.sent.append(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
            }
        )
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error
        return SentMessage(
            message_id=len(self.sent),
            chat_id=chat_id,
            date=0,
        )


@override_settings(
    TELEGRAM_NOTIFICATIONS_ENABLED=True,
    TELEGRAM_BOT_TOKEN="123456:fake-token",
)
class TelegramReminderTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.siswa = self._make_user("siswa1", User.Role.SISWA, nis="10001")
        self.siswa2 = self._make_user("siswa2", User.Role.SISWA, nis="10002")
        self.account = self._make_account(self.siswa, 81001, 91001)
        self.account2 = self._make_account(self.siswa2, 81002, 91002)
        self.category = Category.objects.create(name="Fiksi")
        self.book1 = self._make_book("ISBN-REM-1", "Pengingat A")
        self.book2 = self._make_book("ISBN-REM-2", "Pengingat B")
        self.book3 = self._make_book("ISBN-REM-3", "Pengingat C")
        self.copy1 = self._make_copy(self.book1, "REM-001")
        self.copy2 = self._make_copy(self.book2, "REM-002")
        self.copy3 = self._make_copy(self.book3, "REM-003")

    def _make_user(self, username, role, **extra):
        defaults = {"full_name": f"{username} Full"}
        defaults.update(extra)
        user = User(username=username, role=role, **defaults)
        user.set_password("secret123")
        user.full_clean()
        user.save()
        return user

    def _make_account(self, user, telegram_user_id, chat_id):
        return TelegramAccount.objects.create(
            user=user,
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
        )

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

        outbox = TelegramOutbox.objects.get()
        self.assertEqual(stats["reminders"], 1)
        self.assertEqual(stats["books"], 1)
        self.assertEqual(outbox.notification_type, "LOAN_DUE_SOON")
        self.assertEqual(
            outbox.title,
            "⏰ <b>SMARTLIB · Jatuh tempo H-3</b>",
        )
        self.assertTrue(outbox.event_key.startswith("due_3:"))
        self.assertIn("3 hari", outbox.message)

    def test_h1_membuat_pengingat(self):
        self._make_loan(days=1)

        enqueue_due_reminders(now=self.now)

        outbox = TelegramOutbox.objects.get()
        self.assertEqual(
            outbox.title,
            "⏰ <b>SMARTLIB · Jatuh tempo besok</b>",
        )
        self.assertTrue(outbox.event_key.startswith("due_1:"))
        self.assertIn("besok", outbox.message)

    def test_terlambat_membuat_pengingat(self):
        self._make_loan(days=-2)

        enqueue_due_reminders(now=self.now)

        outbox = TelegramOutbox.objects.get()
        self.assertEqual(outbox.notification_type, "LOAN_OVERDUE")
        self.assertEqual(
            outbox.title,
            "⏰ <b>SMARTLIB · Peminjaman terlambat</b>",
        )
        self.assertTrue(outbox.event_key.startswith("overdue:"))
        self.assertIn("terlambat 2 hari", outbox.message)

    def test_buku_sudah_dikembalikan_tidak_dipengingatkan(self):
        self._make_loan(days=-2, returned=True)

        stats = enqueue_due_reminders(now=self.now)

        self.assertEqual(stats["reminders"], 0)
        self.assertFalse(TelegramOutbox.objects.exists())

    def test_belum_memenuhi_syarat_tidak_dipengingatkan(self):
        self._make_loan(days=2)
        self._make_loan(days=0, copies=[self.copy2])

        stats = enqueue_due_reminders(now=self.now)

        self.assertEqual(stats["reminders"], 0)
        self.assertFalse(TelegramOutbox.objects.exists())

    def test_pengguna_tanpa_telegram_dilewati(self):
        self.account.delete()
        self._make_loan(days=3)

        stats = enqueue_due_reminders(now=self.now)

        self.assertEqual(stats["reminders"], 1)
        self.assertEqual(stats["queued"], 0)
        self.assertFalse(TelegramOutbox.objects.exists())

    def test_preferensi_nonaktif_dilewati(self):
        self.account.notify_due_soon = False
        self.account.notify_overdue = False
        self.account.save(
            update_fields=["notify_due_soon", "notify_overdue"]
        )
        self._make_loan(days=3)
        self._make_loan(days=-1, copies=[self.copy2])

        stats = enqueue_due_reminders(now=self.now)

        self.assertEqual(stats["reminders"], 2)
        self.assertEqual(stats["queued"], 0)
        self.assertFalse(TelegramOutbox.objects.exists())

    def test_beberapa_buku_digabung_satu_ringkasan(self):
        self._make_loan(days=3, copies=[self.copy1, self.copy2])
        client = RecordingClient()

        stats = enqueue_due_reminders(now=self.now)
        process_due(now=timezone.now(), client=client)

        self.assertEqual(stats["reminders"], 1)
        self.assertEqual(stats["books"], 2)
        self.assertEqual(TelegramOutbox.objects.count(), 1)
        self.assertEqual(len(client.sent), 1)
        text = client.sent[0]["text"]
        self.assertIn("Pengingat A", text)
        self.assertIn("Pengingat B", text)
        self.assertEqual(text.count("• <b>Pengingat"), 2)
        self.assertEqual(client.sent[0]["parse_mode"], "HTML")

    def test_command_berulang_tidak_mengirim_ulang(self):
        self._make_loan(days=3)
        client = RecordingClient()

        with patch(
            "apps.telegram.reminders.timezone.now",
            return_value=self.now,
        ):
            call_command("kirim_pengingat_telegram", "--no-send")
            call_command("kirim_pengingat_telegram", "--no-send")

        self.assertEqual(TelegramOutbox.objects.count(), 1)
        process_due(now=self.now, client=client)
        process_due(now=self.now, client=client)
        self.assertEqual(len(client.sent), 1)

    def test_kegagalan_api_tidak_menghentikan_penerima_lain(self):
        self._make_loan(days=3)
        self._make_loan(
            user=self.siswa2, days=3, copies=[self.copy3]
        )
        enqueue_due_reminders(now=self.now)
        client = RecordingClient(
            errors=[TelegramAPIError("ditolak", code=403)]
        )

        stats = process_due(now=timezone.now(), client=client)

        statuses = set(
            TelegramOutbox.objects.values_list("status", flat=True)
        )
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(stats["sent"], 1)
        self.assertEqual(
            statuses,
            {
                TelegramOutbox.Status.FAILED,
                TelegramOutbox.Status.SENT,
            },
        )
        self.assertEqual(len(client.sent), 2)

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

        outbox = TelegramOutbox.objects.get()
        self.assertEqual(stats["reminders"], 1)
        self.assertTrue(outbox.event_key.startswith("due_3:"))
        self.assertIn("24 Oktober 2026", outbox.message)

    def test_pengiriman_ulang_setelah_gangguan_sementara(self):
        self._make_loan(days=3)
        enqueue_due_reminders(now=self.now)
        client = RecordingClient(
            errors=[TelegramAPIError("server", code=500)]
        )

        process_due(now=timezone.now(), client=client)
        outbox = TelegramOutbox.objects.get()
        self.assertEqual(outbox.status, TelegramOutbox.Status.RETRY)

        client.errors = []
        stats = process_due(
            now=timezone.now() + datetime.timedelta(minutes=2),
            client=client,
        )
        outbox.refresh_from_db()
        self.assertEqual(stats["sent"], 1)
        self.assertEqual(outbox.status, TelegramOutbox.Status.SENT)
        self.assertEqual(outbox.attempts, 2)

    def test_command_dapat_dijalankan_manual(self):
        self._make_loan(days=3)
        output = StringIO()

        with patch(
            "apps.telegram.reminders.timezone.now",
            return_value=self.now,
        ):
            call_command(
                "kirim_pengingat_telegram",
                "--no-send",
                stdout=output,
            )

        self.assertIn("Pengingat dicatat", output.getvalue())
        self.assertEqual(TelegramOutbox.objects.count(), 1)
