from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.whatsapp.connection import DecisionResult, OtpResult
from apps.whatsapp.models import WhatsAppConnection

from .factories import make_connection, make_user

User = get_user_model()


class WhatsAppProfileViewTests(TestCase):
    def setUp(self):
        self.siswa = make_user()
        self.url_request = reverse("whatsapp:request")
        self.url_cancel = reverse("whatsapp:cancel_request")
        self.url_unlink = reverse("whatsapp:unlink")
        self.url_verify = reverse("whatsapp:verify_otp")
        self.url_resend = reverse("whatsapp:resend_otp")
        self.url_preferences = reverse("whatsapp:preferences")
        self.url_profile = reverse("accounts:profile")

    def test_semua_aksi_wajib_login(self):
        self.client.logout()
        for url in (
            self.url_request,
            self.url_cancel,
            self.url_unlink,
            self.url_verify,
            self.url_resend,
            self.url_preferences,
        ):
            response = self.client.post(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("accounts:login"), response.url)

    def test_semua_aksi_hanya_post(self):
        self.client.force_login(self.siswa)
        for url in (
            self.url_request,
            self.url_cancel,
            self.url_unlink,
            self.url_verify,
            self.url_resend,
            self.url_preferences,
        ):
            self.assertEqual(self.client.get(url).status_code, 405)

    def test_pengajuan_valid_membuat_baris_dan_pesan(self):
        self.client.force_login(self.siswa)
        response = self.client.post(
            self.url_request,
            {"phone": "0812-3456-7890"},
            follow=True,
        )
        connection = WhatsAppConnection.objects.get(user=self.siswa)
        self.assertEqual(connection.phone, "6281234567890")
        self.assertEqual(connection.status, WhatsAppConnection.Status.PENDING)
        self.assertRedirects(response, self.url_profile)
        self.assertContains(response, "Pengajuan penautan WhatsApp dikirim")

    def test_pengajuan_invalid_tidak_membuat_baris(self):
        self.client.force_login(self.siswa)
        response = self.client.post(self.url_request, {"phone": "12345"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WhatsAppConnection.objects.exists())
        self.assertContains(
            response, "Masukkan nomor WhatsApp Indonesia yang valid"
        )

    def test_pengajuan_nomor_akun_lain_tidak_membocorkan_data(self):
        other = make_user(username="siswa2", phone="082222222222")
        make_connection(other, phone="081234567890")
        self.client.force_login(self.siswa)

        response = self.client.post(
            self.url_request, {"phone": "081234567890"}, follow=True
        )

        self.assertContains(
            response,
            "sudah dipakai akun lain atau sedang dalam proses penautan",
        )
        self.assertFalse(
            WhatsAppConnection.objects.filter(user=self.siswa).exists()
        )

    def test_pembatalan_pengajuan(self):
        connection = make_connection(
            self.siswa, status=WhatsAppConnection.Status.PENDING
        )
        self.client.force_login(self.siswa)

        response = self.client.post(self.url_cancel, follow=True)
        connection.refresh_from_db()

        self.assertRedirects(response, self.url_profile)
        self.assertEqual(connection.status, WhatsAppConnection.Status.REVOKED)
        self.assertContains(response, "Pengajuan penautan WhatsApp dibatalkan")

    def test_pemutusan_tautan(self):
        connection = make_connection(self.siswa)
        self.client.force_login(self.siswa)

        response = self.client.post(self.url_unlink, follow=True)
        connection.refresh_from_db()

        self.assertRedirects(response, self.url_profile)
        self.assertEqual(connection.status, WhatsAppConnection.Status.REVOKED)
        self.assertIsNone(connection.active_phone)
        self.assertContains(response, "Tautan WhatsApp diputus")

    @patch("apps.whatsapp.views.verify_otp")
    def test_verifikasi_otp_salah_menampilkan_sisa_percobaan(self, mocked):
        mocked.return_value = OtpResult("invalid", attempts_left=3)
        self.client.force_login(self.siswa)

        response = self.client.post(
            self.url_verify, {"code": "123456"}, follow=True
        )

        self.assertContains(response, "Sisa percobaan: 3")
        mocked.assert_called_once_with(self.siswa, "123456")

    @patch("apps.whatsapp.views.verify_otp")
    def test_verifikasi_otp_berhasil(self, mocked):
        mocked.return_value = OtpResult("ok")
        self.client.force_login(self.siswa)

        response = self.client.post(
            self.url_verify, {"code": "123456"}, follow=True
        )

        self.assertContains(response, "WhatsApp berhasil dihubungkan")

    def test_format_otp_invalid_tidak_memanggil_service(self):
        make_connection(
            self.siswa,
            status=WhatsAppConnection.Status.APPROVED,
        )
        self.client.force_login(self.siswa)
        response = self.client.post(self.url_verify, {"code": "abc"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kode OTP harus 6 digit angka")

    @patch("apps.whatsapp.views.resend_otp")
    def test_kirim_ulang_otp_cooldown(self, mocked):
        mocked.return_value = OtpResult("cooldown", retry_after=42)
        self.client.force_login(self.siswa)

        response = self.client.post(self.url_resend, follow=True)

        self.assertContains(response, "Tunggu 42 detik")

    def test_preferensi_hanya_untuk_koneksi_aktif(self):
        self.client.force_login(self.siswa)
        response = self.client.post(
            self.url_preferences,
            {"notifications_enabled": "on"},
            follow=True,
        )
        self.assertContains(response, "Tidak ada tautan WhatsApp aktif")

    def test_preferensi_tersimpan(self):
        connection = make_connection(self.siswa)
        self.client.force_login(self.siswa)

        response = self.client.post(
            self.url_preferences,
            {
                "notifications_enabled": "on",
                "notify_loans": "on",
                "notify_due_soon": "on",
            },
            follow=True,
        )
        connection.refresh_from_db()

        self.assertRedirects(response, self.url_profile)
        self.assertTrue(connection.notifications_enabled)
        self.assertTrue(connection.notify_loans)
        self.assertTrue(connection.notify_due_soon)
        self.assertFalse(connection.notify_returns)
        self.assertFalse(connection.notify_fines)
        self.assertFalse(connection.notify_overdue)
        self.assertContains(response, "Preferensi WhatsApp disimpan")

    def test_preferensi_tidak_mengubah_tautan_lain(self):
        connection = make_connection(self.siswa)
        other = make_user(username="siswa2", phone="082222222222")
        other_connection = make_connection(other, phone="082222222222")
        self.client.force_login(self.siswa)

        self.client.post(
            self.url_preferences,
            {"notifications_enabled": "on", "notify_loans": "on"},
        )

        connection.refresh_from_db()
        other_connection.refresh_from_db()
        self.assertTrue(connection.notifications_enabled)
        self.assertTrue(other_connection.notify_loans)


class WhatsAppLibrarianViewTests(TestCase):
    def setUp(self):
        self.pus = make_user(
            username="pus1",
            role=User.Role.PUSTAKAWAN,
            phone=None,
        )
        self.siswa = make_user()
        self.pending = make_connection(
            self.siswa,
            phone="081234567890",
            status=WhatsAppConnection.Status.PENDING,
        )
        self.url_list = reverse("whatsapp:verification_list")
        self.url_detail = reverse(
            "whatsapp:verification_detail", args=[self.pending.pk]
        )
        self.url_approve = reverse(
            "whatsapp:verification_approve", args=[self.pending.pk]
        )
        self.url_reject = reverse(
            "whatsapp:verification_reject", args=[self.pending.pk]
        )

    def test_pengguna_biasa_tidak_boleh_mengakses(self):
        self.client.force_login(self.siswa)
        for url in (self.url_list, self.url_detail):
            self.assertEqual(self.client.get(url).status_code, 403)
        for url in (self.url_approve, self.url_reject):
            self.assertEqual(self.client.post(url).status_code, 403)

    def test_anonim_diarahkan_ke_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url_list).status_code, 302)

    def test_pustakawan_melihat_daftar_dan_detail(self):
        self.client.force_login(self.pus)
        response = self.client.get(self.url_list)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "6281234567890")
        self.assertEqual(response.context["pending_count"], 1)

        response = self.client.get(self.url_detail)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["connection"].pk, self.pending.pk)

    def test_detail_tidak_ditemukan_diarahkan_kembali(self):
        self.client.force_login(self.pus)
        url = reverse("whatsapp:verification_detail", args=[999999])
        response = self.client.get(url, follow=True)
        self.assertRedirects(response, self.url_list)
        self.assertContains(
            response, "Pengajuan penautan tidak ditemukan"
        )

    @patch("apps.whatsapp.views.approve_connection")
    def test_persetujuan_memanggil_service(self, mocked):
        mocked.return_value = DecisionResult("ok")
        self.client.force_login(self.pus)

        response = self.client.post(self.url_approve, follow=True)

        self.assertRedirects(response, self.url_list)
        mocked.assert_called_once_with(self.pending.pk, self.pus)
        self.assertContains(response, "Kode OTP sudah dikirim")

    @patch("apps.whatsapp.views.approve_connection")
    def test_persetujuan_otp_gagal_memberi_peringatan(self, mocked):
        mocked.return_value = DecisionResult("otp_failed")
        self.client.force_login(self.pus)

        response = self.client.post(self.url_approve, follow=True)

        self.assertContains(response, "kode OTP gagal dikirim")

    def test_penolakan_menyimpan_catatan(self):
        self.client.force_login(self.pus)

        response = self.client.post(
            self.url_reject, {"note": "Nomor bukan milik siswa."}, follow=True
        )

        self.pending.refresh_from_db()
        self.assertRedirects(response, self.url_list)
        self.assertEqual(
            self.pending.status, WhatsAppConnection.Status.REJECTED
        )
        self.assertEqual(
            self.pending.rejection_note, "Nomor bukan milik siswa."
        )
        self.assertContains(response, "Permintaan penautan ditolak")

    def test_penolakan_dibatasi_seribu_karakter(self):
        self.client.force_login(self.pus)
        self.client.post(self.url_reject, {"note": "x" * 1200})
        self.pending.refresh_from_db()
        self.assertEqual(len(self.pending.rejection_note), 1000)


class WhatsAppDashboardTests(TestCase):
    def test_dashboard_menampilkan_antrean_whatsapp(self):
        pus = make_user(
            username="pus1",
            role=User.Role.PUSTAKAWAN,
            phone=None,
        )
        siswa = make_user()
        make_connection(
            siswa,
            phone="081234567890",
            status=WhatsAppConnection.Status.PENDING,
        )
        self.client.force_login(pus)

        response = self.client.get(reverse("accounts:dashboard_pustakawan"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["pending_whatsapp"], 1)
        self.assertContains(response, "Verifikasi WhatsApp")
