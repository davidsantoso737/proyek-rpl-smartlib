import datetime
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.whatsapp.dispatch import (
    MAX_ATTEMPTS,
    build_dedup_key,
    enqueue_whatsapp_notification,
    process_due,
    retry_failed,
    review_summary,
)
from apps.whatsapp.models import WhatsAppOutbox
from apps.whatsapp.services import (
    SentMessage,
    WhatsAppAPIError,
    WhatsAppConfigError,
    WhatsAppResponseError,
    WhatsAppTimeoutError,
)

from .factories import make_connection, make_user


class RecordingClient:
    def __init__(self, errors=None, message_id="provider-1"):
        self.errors = list(errors or [])
        self.message_id = message_id
        self.sent = []

    def send_message(self, phone, text):
        self.sent.append({"phone": phone, "text": text})
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error
        return SentMessage(
            provider_message_id=self.message_id,
            phone=phone,
        )


@override_settings(
    WHATSAPP_NOTIFICATIONS_ENABLED=True,
    FONNTE_API_TOKEN="test-token",
    FONNTE_TIMEOUT_SECONDS=5,
)
class WhatsAppOutboxTests(TestCase):
    def setUp(self):
        self.siswa = make_user()
        self.connection = make_connection(self.siswa)

    def _enqueue(self, **overrides):
        data = {
            "user": self.siswa,
            "notification_type": "LOAN_APPROVED",
            "title": "Pengajuan disetujui",
            "message": "Silakan ambil buku.",
            "reference_type": "loan",
            "reference_id": 1,
            "event_key": "approved",
        }
        data.update(overrides)
        return enqueue_whatsapp_notification(**data)

    def test_enqueue_memakai_koneksi_aktif(self):
        outbox = self._enqueue()
        self.assertIsNotNone(outbox)
        self.assertEqual(outbox.status, WhatsAppOutbox.Status.PENDING)
        self.assertEqual(outbox.attempts, 0)
        self.assertEqual(len(outbox.dedup_key), 64)
        self.assertEqual(outbox.reference_type, "loan")
        self.assertEqual(outbox.reference_id, 1)

    def test_tanpa_koneksi_aktif_tidak_membuat_outbox(self):
        self.connection.delete()
        self.assertIsNone(self._enqueue())
        self.assertFalse(WhatsAppOutbox.objects.exists())

    def test_preferensi_off_tidak_membuat_outbox(self):
        self.connection.notify_loans = False
        self.connection.save(update_fields=["notify_loans"])
        self.assertIsNone(self._enqueue())
        self.assertFalse(WhatsAppOutbox.objects.exists())

    def test_replay_tidak_menduplikasi(self):
        first = self._enqueue()
        second = self._enqueue(message="Pesan berbeda.")
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(WhatsAppOutbox.objects.count(), 1)

    def test_dedup_key_stabil(self):
        first = build_dedup_key(
            user_id=1,
            notification_type="LOAN_APPROVED",
            reference_type="loan",
            reference_id=2,
            event_key="approved",
        )
        second = build_dedup_key(
            user_id=1,
            notification_type="LOAN_APPROVED",
            reference_type="loan",
            reference_id=2,
            event_key="approved",
        )
        self.assertEqual(first, second)
        self.assertNotEqual(
            first,
            build_dedup_key(
                user_id=1,
                notification_type="LOAN_APPROVED",
                reference_type="loan",
                reference_id=3,
                event_key="approved",
            ),
        )

    def test_kirim_sukses_sekali(self):
        self._enqueue()
        client = RecordingClient()

        stats = process_due(client=client)
        outbox = WhatsAppOutbox.objects.get()

        self.assertEqual(stats["sent"], 1)
        self.assertEqual(outbox.status, WhatsAppOutbox.Status.SENT)
        self.assertEqual(outbox.attempts, 1)
        self.assertEqual(outbox.provider_message_id, "provider-1")
        self.assertEqual(outbox.recipient_phone, "6281234567890")
        self.assertIsNotNone(outbox.sent_at)
        self.assertIn("Pengajuan disetujui", client.sent[0]["text"])
        self.assertEqual(client.sent[0]["phone"], "6281234567890")

        replay = process_due(client=client)
        self.assertEqual(replay["claimed"], 0)
        self.assertEqual(len(client.sent), 1)

    def test_koneksi_diputus_sebelum_kirim_menjadi_skipped(self):
        self._enqueue()
        self.connection.delete()
        client = RecordingClient()

        stats = process_due(client=client)
        outbox = WhatsAppOutbox.objects.get()

        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(outbox.status, WhatsAppOutbox.Status.SKIPPED)
        self.assertEqual(client.sent, [])

    def test_preferensi_dimatikan_sebelum_kirim_menjadi_skipped(self):
        self._enqueue()
        self.connection.notify_loans = False
        self.connection.save(update_fields=["notify_loans"])
        client = RecordingClient()

        stats = process_due(client=client)
        outbox = WhatsAppOutbox.objects.get()

        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(outbox.status, WhatsAppOutbox.Status.SKIPPED)

    def test_timeout_menjadi_uncertain_tanpa_retry(self):
        self._enqueue()
        client = RecordingClient(
            errors=[WhatsAppTimeoutError("waktu habis")]
        )

        stats = process_due(client=client)
        outbox = WhatsAppOutbox.objects.get()

        self.assertEqual(stats["uncertain"], 1)
        self.assertEqual(outbox.status, WhatsAppOutbox.Status.UNCERTAIN)
        self.assertIn("tidak pasti", outbox.last_error)

        replay = process_due(client=client)
        self.assertEqual(replay["claimed"], 0)

    def test_respons_invalid_menjadi_uncertain(self):
        self._enqueue()
        client = RecordingClient(
            errors=[WhatsAppResponseError("respons rusak")]
        )

        process_due(client=client)
        outbox = WhatsAppOutbox.objects.get()
        self.assertEqual(outbox.status, WhatsAppOutbox.Status.UNCERTAIN)

    def test_server_error_diretry(self):
        self._enqueue()
        now = timezone.now()
        client = RecordingClient(
            errors=[WhatsAppAPIError("server", code=500)]
        )

        first = process_due(now=now, client=client)
        outbox = WhatsAppOutbox.objects.get()
        self.assertEqual(first["retry"], 1)
        self.assertEqual(outbox.status, WhatsAppOutbox.Status.RETRY)
        self.assertEqual(outbox.attempts, 1)
        self.assertGreater(outbox.next_attempt_at, now)

        second = process_due(now=now, client=client)
        self.assertEqual(second["claimed"], 0)

        third = process_due(
            now=now + datetime.timedelta(minutes=2), client=client
        )
        outbox.refresh_from_db()
        self.assertEqual(third["sent"], 1)
        self.assertEqual(outbox.status, WhatsAppOutbox.Status.SENT)
        self.assertEqual(outbox.attempts, 2)

    def test_rate_limit_diretry(self):
        self._enqueue()
        now = timezone.now()
        client = RecordingClient(
            errors=[WhatsAppAPIError("limit", code=429)]
        )

        process_due(now=now, client=client)
        outbox = WhatsAppOutbox.objects.get()
        self.assertEqual(outbox.status, WhatsAppOutbox.Status.RETRY)
        self.assertEqual(
            outbox.next_attempt_at,
            now + datetime.timedelta(seconds=5),
        )

    def test_token_ditolak_menjadi_failed(self):
        self._enqueue()
        client = RecordingClient(
            errors=[WhatsAppAPIError("ditolak", code=401)]
        )

        stats = process_due(client=client)
        outbox = WhatsAppOutbox.objects.get()
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(outbox.status, WhatsAppOutbox.Status.FAILED)
        self.assertIn("Token", outbox.last_error)

    def test_batas_percobaan_menjadi_failed(self):
        self._enqueue()
        WhatsAppOutbox.objects.filter().update(attempts=MAX_ATTEMPTS - 1)
        client = RecordingClient(
            errors=[WhatsAppAPIError("server", code=500)]
        )

        stats = process_due(client=client)
        outbox = WhatsAppOutbox.objects.get()
        self.assertEqual(outbox.attempts, MAX_ATTEMPTS, stats)
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(outbox.status, WhatsAppOutbox.Status.FAILED)
        self.assertIn("Batas percobaan", outbox.last_error)

    def test_pengiriman_basi_ditandai(self):
        outbox = self._enqueue()
        WhatsAppOutbox.objects.filter(pk=outbox.pk).update(
            status=WhatsAppOutbox.Status.SENDING,
            locked_until=timezone.now() - datetime.timedelta(minutes=1),
        )
        client = RecordingClient()

        stats = process_due(client=client)
        outbox.refresh_from_db()
        self.assertEqual(stats["stale"], 1)
        self.assertEqual(outbox.status, WhatsAppOutbox.Status.UNCERTAIN)

    def test_retry_manual_mengembalikan_ke_antrean(self):
        self._enqueue()
        client = RecordingClient(
            errors=[WhatsAppAPIError("ditolak", code=403)]
        )
        process_due(client=client)
        outbox = WhatsAppOutbox.objects.get()
        self.assertEqual(outbox.status, WhatsAppOutbox.Status.FAILED)

        count = retry_failed(ids=[outbox.pk])
        outbox.refresh_from_db()
        self.assertEqual(count, 1)
        self.assertEqual(outbox.status, WhatsAppOutbox.Status.PENDING)
        self.assertEqual(outbox.attempts, 0)
        self.assertEqual(outbox.last_error, "")

    def test_review_summary(self):
        self._enqueue()
        WhatsAppOutbox.objects.filter().update(
            status=WhatsAppOutbox.Status.FAILED,
            last_error="gagal",
        )
        counts, problems = review_summary()
        self.assertEqual(counts[WhatsAppOutbox.Status.FAILED], 1)
        self.assertEqual(len(problems), 1)

    @override_settings(WHATSAPP_NOTIFICATIONS_ENABLED=False)
    def test_sistem_mati_tidak_mengirim(self):
        outbox = self._enqueue()
        self.assertIsNone(outbox)
        with self.assertRaises(WhatsAppConfigError):
            process_due(client=RecordingClient())


class WhatsAppCommandTests(TestCase):
    def setUp(self):
        self.siswa = make_user()
        make_connection(self.siswa)

    def _failed_outbox(self):
        outbox = enqueue_whatsapp_notification(
            user=self.siswa,
            notification_type="LOAN_APPROVED",
            title="Pengajuan disetujui",
            message="Silakan ambil buku.",
            reference_type="loan",
            reference_id=1,
            event_key="approved",
        )
        WhatsAppOutbox.objects.filter(pk=outbox.pk).update(
            status=WhatsAppOutbox.Status.FAILED,
            last_error="gagal",
        )
        return outbox

    @override_settings(
        WHATSAPP_NOTIFICATIONS_ENABLED=True,
        FONNTE_API_TOKEN="test-token",
    )
    def test_review_dan_retry_tanpa_mengirim(self):
        self._failed_outbox()
        output = StringIO()
        call_command("whatsapp_outbox", "--review", stdout=output)
        self.assertIn("Ringkasan outbox WhatsApp", output.getvalue())
        self.assertIn("Gagal permanen: 1", output.getvalue())

        output = StringIO()
        call_command("whatsapp_outbox", "--retry-failed", stdout=output)
        self.assertIn("dikembalikan ke antrean", output.getvalue())
        self.assertEqual(
            WhatsAppOutbox.objects.get().status,
            WhatsAppOutbox.Status.PENDING,
        )

    @override_settings(
        WHATSAPP_NOTIFICATIONS_ENABLED=False,
        FONNTE_API_TOKEN="test-token",
    )
    def test_command_menolak_saat_dinonaktifkan(self):
        with self.assertRaises(CommandError):
            call_command("whatsapp_outbox")

    @override_settings(
        WHATSAPP_NOTIFICATIONS_ENABLED=True,
        FONNTE_API_TOKEN="",
    )
    def test_command_menolak_tanpa_token(self):
        with self.assertRaises(CommandError):
            call_command("whatsapp_outbox")

    @override_settings(
        WHATSAPP_NOTIFICATIONS_ENABLED=True,
        FONNTE_API_TOKEN="test-token",
        FONNTE_API_URL="https://api.fonnte.com/send",
    )
    def test_check_tidak_mencetak_token(self):
        output = StringIO()
        call_command("whatsapp_check", stdout=output)
        self.assertIn("Konfigurasi WhatsApp siap", output.getvalue())
        self.assertNotIn("test-token", output.getvalue())

    @override_settings(
        WHATSAPP_NOTIFICATIONS_ENABLED=True,
        FONNTE_API_TOKEN="test-token",
    )
    @patch(
        "apps.whatsapp.management.commands.whatsapp_send_test.send_text_message",
        return_value=SentMessage(
            provider_message_id="42",
            phone="6281234567890",
        ),
    )
    def test_send_test_hanya_ke_nomor_eksplisit(self, mocked_send):
        output = StringIO()
        call_command(
            "whatsapp_send_test",
            phone="081234567890",
            text="Halo",
            stdout=output,
        )
        mocked_send.assert_called_once_with("081234567890", "Halo")
        self.assertIn("0812******90", output.getvalue())
        self.assertNotIn("test-token", output.getvalue())
