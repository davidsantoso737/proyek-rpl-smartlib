import datetime
from io import StringIO
from unittest.mock import patch
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.catalog.models import Book, BookCopy, Category, Ebook
from apps.circulation.services import (
    ApprovalError,
    activate_handover,
    approve_ebook_loan,
    approve_loan,
    process_return,
    request_ebook_loan,
    request_loan,
)
from apps.circulation.models import Loan, LoanItem
from apps.core.models import LibrarySetting
from apps.notifications.models import Notification
from apps.telegram.dispatch import (
    enqueue_telegram_notification,
    process_due,
    retry_failed,
)
from apps.telegram.models import TelegramAccount, TelegramOutbox
from apps.telegram.services import (
    SentMessage,
    TelegramAPIError,
    TelegramTimeoutError,
)

User = get_user_model()


class RecordingClient:
    def __init__(self, errors=None, message_id=7001):
        self.errors = list(errors or [])
        self.message_id = message_id
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
            message_id=self.message_id,
            chat_id=chat_id,
            date=0,
        )


@override_settings(
    TELEGRAM_NOTIFICATIONS_ENABLED=True,
    TELEGRAM_BOT_TOKEN="123456:fake-token",
)
class TelegramOutboxTests(TestCase):
    def setUp(self):
        self.siswa = self._make_user("siswa1", User.Role.SISWA, nis="10001")
        self.pus = self._make_user("pus1", User.Role.PUSTAKAWAN)
        LibrarySetting.objects.create(
            physical_loan_duration=7,
            ebook_access_duration=14,
            pickup_qr_validity=30,
            fine_rate_per_day=Decimal("1000"),
            effective_from=timezone.now(),
        )
        self.category = Category.objects.create(name="Fiksi")
        book = Book.objects.create(
            category=self.category, isbn="ISBN-OUT-1", title="Outbox A"
        )
        self.copy1 = self._make_copy(book, "OUT-001")
        self.copy2 = self._make_copy(book, "OUT-002")
        self.account = TelegramAccount.objects.create(
            user=self.siswa,
            telegram_user_id=81001,
            chat_id=91001,
        )

    def _make_user(self, username, role, **extra):
        defaults = {"full_name": f"{username} Full"}
        defaults.update(extra)
        user = User(username=username, role=role, **defaults)
        user.set_password("secret123")
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

    def _pending_loan(self, copies=None):
        return request_loan(
            self.siswa, [copy.pk for copy in (copies or [self.copy1])]
        )

    def _approved_loan(self):
        loan = self._pending_loan()
        approve_loan(loan.pk, self.pus)
        loan.refresh_from_db()
        return loan

    def test_approve_membuat_outbox_dan_notifikasi_internal(self):
        loan = self._approved_loan()

        outbox = TelegramOutbox.objects.get()
        self.assertEqual(outbox.notification_type, "LOAN_APPROVED")
        self.assertEqual(outbox.status, TelegramOutbox.Status.PENDING)
        self.assertEqual(outbox.reference_type, "loan")
        self.assertEqual(outbox.reference_id, loan.pk)
        self.assertEqual(outbox.attempts, 0)
        self.assertEqual(len(outbox.dedup_key), 64)
        self.assertIn("QR Pickup", outbox.message)
        self.assertTrue(
            Notification.objects.filter(
                user=self.siswa, type="LOAN_APPROVED"
            ).exists()
        )

    def test_replay_kejadian_tidak_menduplikasi_outbox(self):
        loan = self._approved_loan()
        first = TelegramOutbox.objects.get()

        replay = enqueue_telegram_notification(
            user=self.siswa,
            notification_type="LOAN_APPROVED",
            title="Pengajuan disetujui",
            message="Pesan berbeda untuk kejadian yang sama.",
            reference_type="loan",
            reference_id=loan.pk,
            event_key="approved",
        )

        self.assertEqual(replay.pk, first.pk)
        self.assertEqual(TelegramOutbox.objects.count(), 1)

    def test_transaksi_gagal_tidak_membuat_outbox(self):
        loan = self._pending_loan()
        self.copy1.status = "DIPINJAM"
        self.copy1.save(update_fields=["status"])

        with self.assertRaises(ApprovalError):
            approve_loan(loan.pk, self.pus)

        self.assertFalse(TelegramOutbox.objects.exists())
        self.assertFalse(Notification.objects.exists())

    def test_tanpa_akun_telegram_tidak_membuat_outbox(self):
        self.account.delete()
        self._approved_loan()

        self.assertFalse(TelegramOutbox.objects.exists())
        self.assertTrue(
            Notification.objects.filter(type="LOAN_APPROVED").exists()
        )

    def test_preferensi_kategori_off_tidak_membuat_outbox(self):
        self.account.notify_loans = False
        self.account.save(update_fields=["notify_loans"])
        self._approved_loan()

        self.assertFalse(TelegramOutbox.objects.exists())
        self.assertTrue(
            Notification.objects.filter(type="LOAN_APPROVED").exists()
        )

    def test_sistem_telegram_off_tidak_membuat_outbox(self):
        with self.settings(TELEGRAM_NOTIFICATIONS_ENABLED=False):
            self._approved_loan()

        self.assertFalse(TelegramOutbox.objects.exists())

    def test_pencatatan_outbox_tidak_memanggil_telegram(self):
        with patch(
            "apps.telegram.dispatch.get_client",
            side_effect=AssertionError("tidak boleh memanggil Telegram"),
        ):
            self._approved_loan()

        self.assertEqual(TelegramOutbox.objects.count(), 1)

    def test_kirim_sukses_sekali_dan_replay_diabaikan(self):
        self._approved_loan()
        client = RecordingClient()

        stats = process_due(client=client)
        outbox = TelegramOutbox.objects.get()

        self.assertEqual(stats["sent"], 1)
        self.assertEqual(outbox.status, TelegramOutbox.Status.SENT)
        self.assertEqual(outbox.attempts, 1)
        self.assertEqual(outbox.telegram_message_id, 7001)
        self.assertIsNotNone(outbox.sent_at)
        self.assertEqual(len(client.sent), 1)
        self.assertEqual(client.sent[0]["chat_id"], self.account.chat_id)
        self.assertIn("Pengajuan disetujui", client.sent[0]["text"])
        self.assertEqual(client.sent[0]["parse_mode"], "HTML")

        replay = process_due(client=client)
        self.assertEqual(replay["claimed"], 0)
        self.assertEqual(len(client.sent), 1)

    def test_outbox_lama_tetap_dikirim_sebagai_teks_biasa(self):
        TelegramOutbox.objects.create(
            user=self.siswa,
            notification_type="LOAN_APPROVED",
            title="Judul lama",
            message="Pesan lama",
            event_key="legacy",
            dedup_key="legacy-outbox-key",
            next_attempt_at=timezone.now(),
        )
        client = RecordingClient()

        stats = process_due(client=client)

        self.assertEqual(stats["sent"], 1)
        self.assertEqual(len(client.sent), 1)
        self.assertIsNone(client.sent[0]["parse_mode"])
        self.assertIn("Judul lama", client.sent[0]["text"])
        self.assertIn("Pesan lama", client.sent[0]["text"])

    def test_gagal_permanen_tidak_mengulang_dan_transaksi_tetap_utuh(self):
        loan = self._approved_loan()
        client = RecordingClient(
            errors=[TelegramAPIError("ditolak", code=403)]
        )

        stats = process_due(client=client)
        outbox = TelegramOutbox.objects.get()

        self.assertEqual(stats["failed"], 1)
        self.assertEqual(outbox.status, TelegramOutbox.Status.FAILED)
        self.assertEqual(outbox.attempts, 1)
        self.assertNotIn(str(self.account.chat_id), outbox.last_error)
        loan.refresh_from_db()
        self.assertEqual(loan.status, "APPROVED")
        self.assertTrue(
            Notification.objects.filter(type="LOAN_APPROVED").exists()
        )

        replay = process_due(client=client)
        self.assertEqual(replay["claimed"], 0)
        self.assertEqual(len(client.sent), 1)

    def test_timeout_menjadi_tidak_pasti_tanpa_retry_otomatis(self):
        self._approved_loan()
        client = RecordingClient(
            errors=[TelegramTimeoutError("waktu habis")]
        )

        stats = process_due(client=client)
        outbox = TelegramOutbox.objects.get()

        self.assertEqual(stats["uncertain"], 1)
        self.assertEqual(outbox.status, TelegramOutbox.Status.UNCERTAIN)
        self.assertIn("tidak pasti", outbox.last_error)

        replay = process_due(client=client)
        self.assertEqual(replay["claimed"], 0)
        self.assertEqual(len(client.sent), 1)

    def test_gangguan_sementara_diretry_dengan_batas(self):
        self._approved_loan()
        now = timezone.now()
        client = RecordingClient(
            errors=[TelegramAPIError("server", code=500)]
        )

        first = process_due(now=now, client=client)
        outbox = TelegramOutbox.objects.get()
        self.assertEqual(first["retry"], 1)
        self.assertEqual(outbox.status, TelegramOutbox.Status.RETRY)
        self.assertEqual(outbox.attempts, 1)
        self.assertGreater(outbox.next_attempt_at, now)

        second = process_due(now=now, client=client)
        self.assertEqual(second["claimed"], 0)

        client.errors = []
        third = process_due(
            now=now + datetime.timedelta(minutes=2), client=client
        )
        outbox.refresh_from_db()
        self.assertEqual(third["sent"], 1)
        self.assertEqual(outbox.status, TelegramOutbox.Status.SENT)
        self.assertEqual(outbox.attempts, 2)

    def test_retry_manual_mengembalikan_ke_antrean(self):
        self._approved_loan()
        client = RecordingClient(
            errors=[TelegramAPIError("ditolak", code=400)]
        )
        process_due(client=client)
        outbox = TelegramOutbox.objects.get()
        self.assertEqual(outbox.status, TelegramOutbox.Status.FAILED)

        count = retry_failed(ids=[outbox.pk])
        outbox.refresh_from_db()
        self.assertEqual(count, 1)
        self.assertEqual(outbox.status, TelegramOutbox.Status.PENDING)
        self.assertEqual(outbox.attempts, 0)

        client.errors = []
        stats = process_due(client=client)
        outbox.refresh_from_db()
        self.assertEqual(stats["sent"], 1)
        self.assertEqual(outbox.status, TelegramOutbox.Status.SENT)

    def test_akun_diputus_sebelum_kirim_menjadi_skipped(self):
        self._approved_loan()
        self.account.delete()
        client = RecordingClient()

        stats = process_due(client=client)
        outbox = TelegramOutbox.objects.get()

        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(outbox.status, TelegramOutbox.Status.SKIPPED)
        self.assertEqual(client.sent, [])

    def test_handover_dan_pengembalian_membuat_outbox(self):
        loan = self._approved_loan()
        activate_handover(loan.pickup_token, self.pus)
        item = process_return("OUT-001", self.pus, "BAIK")

        types = list(
            TelegramOutbox.objects.order_by("created_at").values_list(
                "notification_type", flat=True
            )
        )
        self.assertEqual(types, ["LOAN_APPROVED", "LOAN_ACTIVE", "LOAN_RETURNED"])
        returned = TelegramOutbox.objects.get(
            notification_type="LOAN_RETURNED"
        )
        self.assertEqual(returned.reference_type, "loan_item")
        self.assertEqual(returned.reference_id, item.pk)

    def test_denda_tetap_dikirim_saat_preferensi_pengembalian_off(self):
        loan = self._approved_loan()
        activate_handover(loan.pickup_token, self.pus)
        self.account.notify_returns = False
        self.account.notify_fines = True
        self.account.save(
            update_fields=["notify_returns", "notify_fines"]
        )
        past_due = timezone.now() - datetime.timedelta(days=2, hours=1)
        LoanItem.objects.filter(loan=loan).update(due_at=past_due)
        Loan.objects.filter(pk=loan.pk).update(due_at=past_due)

        process_return("OUT-001", self.pus, "BAIK")

        returned = TelegramOutbox.objects.get(
            notification_type="LOAN_RETURNED"
        )
        self.assertIn("denda", returned.message.casefold())
        self.assertEqual(returned.reference_type, "loan_item")

    def test_ebook_disetujui_membuat_outbox(self):
        ebook = Ebook.objects.create(
            category=self.category,
            eisbn="EISBN-OUT-1",
            title="E-book Outbox",
            file_path="private/ebook.pdf",
            file_name="ebook.pdf",
            file_type="pdf",
            file_size=1024,
            version="1",
        )
        loan = request_ebook_loan(self.siswa, ebook.pk)
        approve_ebook_loan(loan.pk, self.pus)

        outbox = TelegramOutbox.objects.get()
        self.assertEqual(outbox.notification_type, "EBOOK_APPROVED")
        self.assertEqual(outbox.reference_type, "ebook_loan")
        self.assertEqual(outbox.reference_id, loan.pk)

    def test_command_review_dan_retry_tanpa_mengirim(self):
        self._approved_loan()
        client = RecordingClient(
            errors=[TelegramAPIError("ditolak", code=403)]
        )
        process_due(client=client)

        output = StringIO()
        call_command("telegram_outbox", "--review", stdout=output)
        self.assertIn("Ringkasan outbox Telegram", output.getvalue())
        self.assertIn("Gagal permanen: 1", output.getvalue())

        output = StringIO()
        call_command(
            "telegram_outbox", "--retry-failed", stdout=output
        )
        self.assertIn("dikembalikan ke antrean", output.getvalue())
        self.assertEqual(
            TelegramOutbox.objects.get().status,
            TelegramOutbox.Status.PENDING,
        )
