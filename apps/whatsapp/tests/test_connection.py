import re
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.notifications.models import Notification
from apps.whatsapp.connection import (
    MAX_OTP_ATTEMPTS,
    approve_connection,
    cancel_pending_request,
    get_connection_context,
    reject_connection,
    request_connection,
    resolve_recipient,
    resend_otp,
    send_connection_otp,
    unlink_active,
    verify_otp,
)
from apps.whatsapp.models import WhatsAppConnection, WhatsAppOutbox
from apps.whatsapp.services import WhatsAppNetworkError

from .factories import make_connection, make_user

User = get_user_model()

OTP_PATTERN = re.compile(r"\b(\d{6})\b")


@override_settings(
    WHATSAPP_NOTIFICATIONS_ENABLED=True,
    FONNTE_API_TOKEN="test-token",
    FONNTE_TIMEOUT_SECONDS=5,
)
class WhatsAppConnectionTests(TestCase):
    def setUp(self):
        self.pus = make_user(
            username="pus1", role=User.Role.PUSTAKAWAN, phone=None
        )
        self.siswa = make_user()
        self.other = make_user(username="siswa2", phone="082222222222")

    def _pending(self, user=None):
        user = user or self.siswa
        return WhatsAppConnection.objects.get(
            user=user, status=WhatsAppConnection.Status.PENDING
        )

    def _approve(self, user=None):
        user = user or self.siswa
        if not WhatsAppConnection.objects.filter(
            user=user, status=WhatsAppConnection.Status.PENDING
        ).exists():
            request_connection(user, "081234567890")
        connection = self._pending(user)
        with patch(
            "apps.whatsapp.connection.send_text_message"
        ) as send:
            result = approve_connection(connection.pk, self.pus)
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.otp_sent)
        code = OTP_PATTERN.search(send.call_args.args[1]).group(1)
        connection.refresh_from_db()
        return connection, code

    def _active(self, user=None):
        connection, code = self._approve(user)
        result = verify_otp(connection.user, code)
        self.assertEqual(result.status, "ok")
        connection.refresh_from_db()
        return connection

    def test_pengajuan_invalid(self):
        self.assertEqual(
            request_connection(None, "081234567890").status, "invalid"
        )
        self.siswa.is_active = False
        self.siswa.save(update_fields=["is_active"])
        self.assertEqual(
            request_connection(self.siswa, "081234567890").status,
            "inactive",
        )

    def test_nomor_invalid_ditolak(self):
        result = request_connection(self.siswa, "12345")
        self.assertEqual(result.status, "invalid_phone")
        self.assertFalse(WhatsAppConnection.objects.exists())

    def test_pengajuan_baru_dinormalisasi_dan_dinotifikasi(self):
        result = request_connection(self.siswa, "0812-3456-7890")
        self.assertEqual(result.status, "created")
        self.assertEqual(result.connection.phone, "6281234567890")
        self.assertEqual(
            result.connection.status, WhatsAppConnection.Status.PENDING
        )
        self.assertIsNotNone(result.connection.requested_at)
        self.assertTrue(
            Notification.objects.filter(
                user=self.pus,
                type="WHATSAPP_LINK_REQUEST",
                reference_type="whatsapp_connection",
                reference_id=result.connection.pk,
            ).exists()
        )

    def test_pengajuan_ganda_tidak_membuat_baris_baru(self):
        first = request_connection(self.siswa, "081234567890")
        second = request_connection(self.siswa, "081234567890")
        self.assertEqual(second.status, "already_pending")
        self.assertEqual(second.connection.pk, first.connection.pk)
        self.assertEqual(
            WhatsAppConnection.objects.filter(user=self.siswa).count(), 1
        )

    def test_nomor_aktif_sendiri_tidak_diajukan_lagi(self):
        active = make_connection(self.siswa, phone="081234567890")
        result = request_connection(self.siswa, "081234567890")
        self.assertEqual(result.status, "already_active")
        self.assertEqual(result.connection.pk, active.pk)

    def test_nomor_akun_lain_tidak_membocorkan_koneksi(self):
        request_connection(self.other, "081234567890")
        result = request_connection(self.siswa, "081234567890")
        self.assertEqual(result.status, "phone_in_use")
        self.assertIsNone(result.connection)

    def test_pengajuan_baru_mencabut_pengajuan_lama(self):
        first = request_connection(self.siswa, "081234567890")
        second = request_connection(self.siswa, "082222222222")
        self.assertEqual(second.status, "created")
        first.connection.refresh_from_db()
        self.assertEqual(
            first.connection.status, WhatsAppConnection.Status.REVOKED
        )
        self.assertFalse(
            WhatsAppConnection.objects.filter(
                user=self.siswa,
                status=WhatsAppConnection.Status.PENDING,
            ).exclude(pk=second.connection.pk).exists()
        )

    def test_nomor_lama_tetap_aktif_sampai_nomor_baru_terverifikasi(self):
        old = make_connection(self.siswa, phone="081111111111")
        result = request_connection(self.siswa, "082222222222")
        old.refresh_from_db()
        self.assertEqual(old.status, WhatsAppConnection.Status.ACTIVE)
        self.assertEqual(old.active_phone, "6281111111111")

        new, code = self._approve()
        self.assertEqual(new.pk, result.connection.pk)
        verify_otp(self.siswa, code)
        old.refresh_from_db()
        new.refresh_from_db()
        self.assertEqual(old.status, WhatsAppConnection.Status.REVOKED)
        self.assertIsNone(old.active_phone)
        self.assertEqual(new.status, WhatsAppConnection.Status.ACTIVE)
        self.assertEqual(new.active_phone, "6282222222222")

    def test_persetujuan_mengirim_otp_dan_membuat_notifikasi(self):
        connection, code = self._approve()
        self.assertEqual(
            connection.status, WhatsAppConnection.Status.APPROVED
        )
        self.assertEqual(connection.approved_by, self.pus)
        self.assertIsNotNone(connection.approved_at)
        self.assertIsNotNone(connection.otp_hash)
        self.assertNotEqual(connection.otp_hash, code)
        self.assertIsNotNone(connection.otp_sent_at)
        self.assertGreater(connection.otp_expires_at, timezone.now())
        self.assertTrue(
            Notification.objects.filter(
                user=self.siswa,
                type="WHATSAPP_LINK_APPROVED",
                reference_id=connection.pk,
            ).exists()
        )

    def test_persetujuan_bukan_pending_ditolak(self):
        connection = request_connection(self.siswa, "081234567890")
        with patch("apps.whatsapp.connection.send_text_message"):
            approve_connection(connection.connection.pk, self.pus)
        result = approve_connection(connection.connection.pk, self.pus)
        self.assertEqual(result.status, "not_pending")

    def test_persetujuan_ditolak_bila_nomor_sudah_aktif_di_akun_lain(self):
        make_connection(self.other, phone="081234567890")
        connection = request_connection(self.siswa, "081234567890")
        self.assertEqual(connection.status, "phone_in_use")

        manual = WhatsAppConnection.objects.create(
            user=self.siswa,
            phone="6281234567890",
            status=WhatsAppConnection.Status.PENDING,
            requested_at=timezone.now(),
        )
        result = approve_connection(manual.pk, self.pus)
        self.assertEqual(result.status, "phone_in_use")
        manual.refresh_from_db()
        self.assertEqual(
            manual.status, WhatsAppConnection.Status.PENDING
        )

    def test_penolakan_menyimpan_catatan_dan_notifikasi(self):
        connection = request_connection(self.siswa, "081234567890")
        result = reject_connection(
            connection.connection.pk, self.pus, "Nomor tidak cocok."
        )
        self.assertEqual(result.status, "ok")
        row = connection.connection
        row.refresh_from_db()
        self.assertEqual(row.status, WhatsAppConnection.Status.REJECTED)
        self.assertEqual(row.rejection_note, "Nomor tidak cocok.")
        self.assertTrue(
            Notification.objects.filter(
                user=self.siswa,
                type="WHATSAPP_LINK_REJECTED",
                reference_id=row.pk,
            ).exists()
        )

    def test_kirim_ulang_otp_menghormati_cooldown(self):
        connection, _code = self._approve()
        result = resend_otp(self.siswa)
        self.assertEqual(result.status, "cooldown")
        self.assertGreaterEqual(result.retry_after, 1)

        WhatsAppConnection.objects.filter(pk=connection.pk).update(
            otp_sent_at=timezone.now() - timedelta(seconds=61)
        )
        with patch(
            "apps.whatsapp.connection.send_text_message"
        ) as send:
            result = resend_otp(self.siswa)
        self.assertEqual(result.status, "ok")
        self.assertTrue(OTP_PATTERN.search(send.call_args.args[1]))

    def test_kegagalan_kirim_otp_dicatat(self):
        connection = request_connection(self.siswa, "081234567890")
        with patch(
            "apps.whatsapp.connection.send_text_message",
            side_effect=WhatsAppNetworkError("offline"),
        ):
            result = approve_connection(
                connection.connection.pk, self.pus
            )
        self.assertEqual(result.status, "otp_failed")
        connection.connection.refresh_from_db()
        self.assertEqual(
            connection.connection.status,
            WhatsAppConnection.Status.APPROVED,
        )
        self.assertIn("offline", connection.connection.otp_last_error)

    def test_verifikasi_otp_benar_mengaktifkan(self):
        connection, code = self._approve()
        result = verify_otp(self.siswa, code)
        self.assertEqual(result.status, "ok")
        connection.refresh_from_db()
        self.assertEqual(connection.status, WhatsAppConnection.Status.ACTIVE)
        self.assertEqual(connection.active_phone, "6281234567890")
        self.assertIsNotNone(connection.verified_at)
        self.assertIsNone(connection.otp_hash)
        self.assertTrue(
            Notification.objects.filter(
                user=self.siswa,
                type="WHATSAPP_LINK_ACTIVE",
                reference_id=connection.pk,
            ).exists()
        )
        self.assertTrue(
            WhatsAppOutbox.objects.filter(
                user=self.siswa,
                notification_type="WHATSAPP_LINK_ACTIVE",
            ).exists()
        )

    def test_verifikasi_otp_salah_mengurangi_percobaan(self):
        connection, code = self._approve()
        wrong = "000000" if code != "000000" else "111111"
        result = verify_otp(self.siswa, wrong)
        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.attempts_left, MAX_OTP_ATTEMPTS - 1)
        connection.refresh_from_db()
        self.assertEqual(connection.otp_attempts, 1)

    def test_batas_percobaan_mencabut_pengajuan(self):
        connection, code = self._approve()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(MAX_OTP_ATTEMPTS):
            result = verify_otp(self.siswa, wrong)
        self.assertEqual(result.status, "too_many")
        connection.refresh_from_db()
        self.assertEqual(connection.status, WhatsAppConnection.Status.REVOKED)
        self.assertIsNone(connection.active_phone)

    def test_otp_kedaluwarsa_dibersihkan(self):
        connection, code = self._approve()
        WhatsAppConnection.objects.filter(pk=connection.pk).update(
            otp_expires_at=timezone.now() - timedelta(seconds=1)
        )
        result = verify_otp(self.siswa, code)
        self.assertEqual(result.status, "expired")
        connection.refresh_from_db()
        self.assertIsNone(connection.otp_hash)

    def test_verifikasi_tanpa_otp(self):
        connection = make_connection(
            self.siswa, status=WhatsAppConnection.Status.APPROVED
        )
        result = verify_otp(self.siswa, "123456")
        self.assertEqual(result.status, "no_otp")
        self.assertEqual(result.connection.pk, connection.pk)

    def test_format_otp_invalid_ditolak(self):
        self.assertEqual(
            verify_otp(self.siswa, "abc").status, "invalid_format"
        )
        self.assertEqual(
            verify_otp(self.siswa, "12345").status, "invalid_format"
        )

    def test_verifikasi_bentrok_nomor_aktif_lain(self):
        connection, code = self._approve()
        WhatsAppConnection.objects.create(
            user=self.other,
            phone="6281234567890",
            active_phone="6289999999999",
            status=WhatsAppConnection.Status.ACTIVE,
            requested_at=timezone.now(),
            verified_at=timezone.now(),
        )
        result = verify_otp(self.siswa, code)
        self.assertEqual(result.status, "phone_in_use")
        connection.refresh_from_db()
        self.assertEqual(connection.status, WhatsAppConnection.Status.REVOKED)

    def test_pembatalan_dan_pemutusan(self):
        pending = request_connection(self.siswa, "081234567890")
        result = cancel_pending_request(self.siswa)
        self.assertEqual(result.status, "ok")
        pending.connection.refresh_from_db()
        self.assertEqual(
            pending.connection.status, WhatsAppConnection.Status.REVOKED
        )

        request_connection(self.siswa, "081234567890")
        active = self._active()
        result = unlink_active(self.siswa)
        self.assertEqual(result.status, "ok")
        active.refresh_from_db()
        self.assertEqual(active.status, WhatsAppConnection.Status.REVOKED)
        self.assertIsNone(active.active_phone)
        self.assertIsNone(resolve_recipient(self.siswa))

    def test_resolver_hanya_memakai_koneksi_aktif(self):
        self.assertIsNone(resolve_recipient(self.siswa))
        pending = request_connection(self.siswa, "081234567890")
        self.assertIsNone(resolve_recipient(self.siswa))
        connection, code = self._approve()
        self.assertIsNone(resolve_recipient(self.siswa))
        verify_otp(self.siswa, code)
        self.assertEqual(
            resolve_recipient(self.siswa).pk, connection.pk
        )

    def test_context_status_pengguna(self):
        self.assertEqual(
            get_connection_context(self.siswa)["state"], "none"
        )
        pending = request_connection(self.siswa, "081234567890")
        context = get_connection_context(self.siswa)
        self.assertEqual(context["state"], "pending")
        self.assertEqual(context["pending"].pk, pending.connection.pk)
        self.assertEqual(context["pending_masked_phone"], "0812******90")

        _connection, code = self._approve()
        context = get_connection_context(self.siswa)
        self.assertEqual(context["state"], "approved")
        self.assertFalse(context["otp_expired"])
        verify_otp(self.siswa, code)
        context = get_connection_context(self.siswa)
        self.assertEqual(context["state"], "active")
