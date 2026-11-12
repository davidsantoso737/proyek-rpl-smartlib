from datetime import timedelta
from unittest.mock import patch
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.telegram.forms import TelegramPreferencesForm
from apps.telegram.linking import (
    STATUS_ALREADY_LINKED,
    STATUS_CLAIMED,
    STATUS_CONFLICT,
    STATUS_EXPIRED,
    STATUS_INVALID,
    STATUS_MISSING,
    STATUS_SUCCESS,
    STATUS_UNCLAIMED,
    cancel_pending_link,
    claim_link_code,
    confirm_link_token,
    create_link_token,
    get_pending_link_token,
    unlink_account,
)
from apps.telegram.models import TelegramAccount, TelegramLinkToken
from apps.telegram.preferences import (
    categories_for_notification,
    should_send_telegram,
)

User = get_user_model()


def make_user(username="siswa1", nis="1001", full_name="Siswa Satu"):
    return User.objects.create_user(
        username=username,
        password="rahasia123",
        role=User.Role.SISWA,
        full_name=full_name,
        nis=nis,
    )


def make_account(user, telegram_user_id=9001, chat_id=9001):
    return TelegramAccount.objects.create(
        user=user,
        telegram_user_id=telegram_user_id,
        chat_id=chat_id,
        username="telegram_user",
        first_name="Telegram User",
    )


class LinkTokenServiceTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_kode_disimpan_sebagai_hash(self):
        code, token = create_link_token(self.user)
        self.assertNotEqual(token.token_hash, code)
        self.assertEqual(len(token.token_hash), 64)
        self.assertGreater(token.expires_at, timezone.now())

    def test_kode_kedaluwarsa_tidak_aktif(self):
        code, token = create_link_token(self.user)
        TelegramLinkToken.objects.filter(pk=token.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertIsNone(get_pending_link_token(self.user))
        result = claim_link_code(
            code,
            telegram_user_id=9001,
            chat_id=9001,
        )
        self.assertEqual(result.status, STATUS_EXPIRED)

    def test_klaim_dan_konfirmasi_menautkan_akun(self):
        code, _token = create_link_token(self.user)
        claim = claim_link_code(
            code,
            telegram_user_id=9001,
            chat_id=9001,
            username="pemilik",
            first_name="Pemilik",
        )
        self.assertEqual(claim.status, STATUS_CLAIMED)
        self.assertIsNotNone(claim.token.claimed_at)

        result = confirm_link_token(self.user)
        self.assertEqual(result.status, STATUS_SUCCESS)
        account = TelegramAccount.objects.get(user=self.user)
        self.assertEqual(account.telegram_user_id, 9001)
        self.assertEqual(account.chat_id, 9001)
        self.assertEqual(account.username, "pemilik")
        self.assertTrue(account.notifications_enabled)
        self.assertTrue(account.notify_loans)

    def test_konfirmasi_sebelum_klaim_ditolak(self):
        create_link_token(self.user)
        result = confirm_link_token(self.user)
        self.assertEqual(result.status, STATUS_UNCLAIMED)
        self.assertFalse(
            TelegramAccount.objects.filter(user=self.user).exists()
        )

    def test_kode_hanya_dapat_digunakan_sekali(self):
        code, _token = create_link_token(self.user)
        claim_link_code(
            code, telegram_user_id=9001, chat_id=9001
        )
        confirm_link_token(self.user)
        reuse = claim_link_code(
            code, telegram_user_id=9002, chat_id=9002
        )
        self.assertEqual(reuse.status, STATUS_INVALID)

    def test_kode_terikat_pada_pemilik(self):
        code, _token = create_link_token(self.user)
        other = make_user(username="siswa2", nis="1002")
        claim_link_code(
            code, telegram_user_id=9001, chat_id=9001
        )
        result = confirm_link_token(other)
        self.assertEqual(result.status, STATUS_MISSING)
        self.assertFalse(
            TelegramAccount.objects.filter(user=other).exists()
        )

    def test_klaim_oleh_identitas_kedua_ditolak(self):
        code, token = create_link_token(self.user)
        first = claim_link_code(
            code, telegram_user_id=9001, chat_id=9001
        )
        second = claim_link_code(
            code, telegram_user_id=9002, chat_id=9002
        )
        self.assertEqual(first.status, STATUS_CLAIMED)
        self.assertEqual(second.status, STATUS_INVALID)
        token.refresh_from_db()
        self.assertEqual(token.telegram_user_id, 9001)

    def test_konflik_chat_id_antar_akun_ditolak(self):
        other = make_user(username="siswa2", nis="1002")
        make_account(
            other, telegram_user_id=9002, chat_id=9002
        )
        code, _token = create_link_token(self.user)
        result = claim_link_code(
            code, telegram_user_id=9002, chat_id=9002
        )
        self.assertEqual(result.status, STATUS_CONFLICT)
        self.assertFalse(
            TelegramAccount.objects.filter(user=self.user).exists()
        )

    def test_pengguna_sudah_terhubung_tidak_bisa_klaim_lagi(self):
        make_account(self.user)
        code, _token = create_link_token(self.user)
        result = claim_link_code(
            code, telegram_user_id=9003, chat_id=9003
        )
        self.assertEqual(result.status, STATUS_ALREADY_LINKED)

    def test_pemutusan_menghapus_akun_dan_kode(self):
        make_account(self.user)
        create_link_token(self.user)
        unlink_account(self.user)
        self.assertFalse(
            TelegramAccount.objects.filter(user=self.user).exists()
        )
        self.assertFalse(
            TelegramLinkToken.objects.filter(user=self.user).exists()
        )

    def test_pembatalan_menghapus_kode(self):
        create_link_token(self.user)
        cancel_pending_link(self.user)
        self.assertIsNone(get_pending_link_token(self.user))


class TelegramPreferencesTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.account = make_account(self.user)

    @override_settings(TELEGRAM_NOTIFICATIONS_ENABLED=True)
    def test_global_dan_kategori(self):
        self.assertTrue(
            should_send_telegram(self.account, "LOAN_APPROVED")
        )
        self.account.notifications_enabled = False
        self.assertFalse(
            should_send_telegram(self.account, "LOAN_APPROVED")
        )
        self.account.notifications_enabled = True
        self.account.notify_loans = False
        self.assertFalse(
            should_send_telegram(self.account, "LOAN_APPROVED")
        )
        self.assertTrue(
            should_send_telegram(self.account, "LOAN_RETURNED")
        )

    @override_settings(TELEGRAM_NOTIFICATIONS_ENABLED=True)
    def test_denda_dipetakan_dari_pesan_pengembalian(self):
        categories = categories_for_notification(
            "LOAN_RETURNED", "Pengembalian selesai. Denda Rp5.000."
        )
        self.assertIn(TelegramAccount.Category.RETURN, categories)
        self.assertIn(TelegramAccount.Category.FINE, categories)

        self.account.notify_returns = False
        self.account.notify_fines = True
        self.assertTrue(
            should_send_telegram(
                self.account,
                "LOAN_RETURNED",
                "Denda Rp5.000.",
            )
        )

    @override_settings(TELEGRAM_NOTIFICATIONS_ENABLED=True)
    def test_tipe_tanpa_kategori_tidak_dikirim(self):
        self.assertFalse(
            should_send_telegram(
                self.account, "MATERIAL_APPROVED"
            )
        )

    @override_settings(TELEGRAM_NOTIFICATIONS_ENABLED=False)
    def test_sistem_mati_tidak_mengirim(self):
        self.assertFalse(
            should_send_telegram(self.account, "LOAN_APPROVED")
        )

    @override_settings(TELEGRAM_NOTIFICATIONS_ENABLED=True)
    def test_akun_nonaktif_tidak_mengirim(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertFalse(
            should_send_telegram(self.account, "LOAN_APPROVED")
        )

    def test_form_preferensi_menyimpan_nilai(self):
        form = TelegramPreferencesForm(
            {
                "notifications_enabled": "on",
                "notify_loans": "on",
                "notify_returns": "on",
            },
            instance=self.account,
        )
        self.assertTrue(form.is_valid())
        account = form.save()
        self.assertTrue(account.notifications_enabled)
        self.assertTrue(account.notify_loans)
        self.assertFalse(account.notify_fines)
        self.assertFalse(account.notify_due_soon)
        self.assertFalse(account.notify_overdue)


@override_settings(TELEGRAM_LEGACY_LINKING_ENABLED=True)
class TelegramProfileViewTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.user.phone = "081234567890"
        self.user.save(update_fields=["phone"])
        self.client.force_login(self.user)

    def test_profil_menampilkan_bagian_telegram(self):
        response = self.client.get(reverse("accounts:profile"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Belum terhubung")
        self.assertContains(response, "Hubungkan Telegram")

    def test_mulai_tautan_membuat_kode_dan_sesi(self):
        response = self.client.post(reverse("telegram:link_start"))
        self.assertRedirects(response, reverse("accounts:profile"))
        token = TelegramLinkToken.objects.get(user=self.user)
        code = self.client.session.get("telegram_link_code")
        self.assertTrue(code)
        self.assertNotEqual(token.token_hash, code)

    @override_settings(TELEGRAM_BOT_USERNAME="smartlib_bot")
    def test_profil_tidak_menampilkan_kode_tautan(self):
        self.client.post(reverse("telegram:link_start"))
        code = self.client.session.get("telegram_link_code")
        response = self.client.get(reverse("accounts:profile"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, code)

    @override_settings(TELEGRAM_BOT_USERNAME="smartlib_bot")
    def test_buka_tautan_mengalihkan_ke_bot(self):
        self.client.post(reverse("telegram:link_start"))
        code = self.client.session.get("telegram_link_code")
        response = self.client.get(reverse("telegram:link_open"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            f"https://t.me/smartlib_bot?start={quote(code)}",
        )

    @override_settings(TELEGRAM_BOT_USERNAME="")
    def test_buka_tautan_tanpa_username_bot_ditolak(self):
        self.client.post(reverse("telegram:link_start"))
        response = self.client.get(reverse("telegram:link_open"))
        self.assertRedirects(response, reverse("accounts:profile"))

    def test_buka_tautan_tanpa_sesi_ditolak(self):
        create_link_token(self.user)
        response = self.client.get(reverse("telegram:link_open"))
        self.assertRedirects(response, reverse("accounts:profile"))

    def test_profil_tidak_menampilkan_kode_setelah_klaim(self):
        code, _token = create_link_token(self.user)
        claim_link_code(
            code, telegram_user_id=9001, chat_id=9001
        )
        response = self.client.get(reverse("accounts:profile"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, code)

    def test_mulai_tautan_ditolak_jika_sudah_terhubung(self):
        make_account(self.user)
        self.client.post(reverse("telegram:link_start"))
        self.assertFalse(
            TelegramLinkToken.objects.filter(user=self.user).exists()
        )

    def test_konfirmasi_menautkan_akun(self):
        code, _token = create_link_token(self.user)
        claim_link_code(
            code, telegram_user_id=9001, chat_id=9001
        )
        with patch(
            "apps.telegram.views.send_text_message"
        ) as send:
            response = self.client.post(
                reverse("telegram:link_confirm")
            )
        self.assertRedirects(response, reverse("accounts:profile"))
        self.assertTrue(
            TelegramAccount.objects.filter(user=self.user).exists()
        )
        send.assert_called_once()

    def test_pemutusan_tidak_mengirim_saat_notifikasi_mati(self):
        account = make_account(self.user)
        account.notifications_enabled = False
        account.save(update_fields=["notifications_enabled"])
        with patch(
            "apps.telegram.views.send_text_message"
        ) as send:
            response = self.client.post(reverse("telegram:unlink"))
        self.assertRedirects(response, reverse("accounts:profile"))
        send.assert_not_called()

    def test_pemutusan_menghapus_akun(self):
        make_account(self.user)
        with patch("apps.telegram.views.send_text_message"):
            response = self.client.post(reverse("telegram:unlink"))
        self.assertRedirects(response, reverse("accounts:profile"))
        self.assertFalse(
            TelegramAccount.objects.filter(user=self.user).exists()
        )

    def test_preferensi_disimpan(self):
        make_account(self.user)
        response = self.client.post(
            reverse("telegram:preferences"),
            {
                "notify_returns": "on",
                "notify_fines": "on",
            },
        )
        self.assertRedirects(response, reverse("accounts:profile"))
        account = TelegramAccount.objects.get(user=self.user)
        self.assertFalse(account.notifications_enabled)
        self.assertFalse(account.notify_loans)
        self.assertTrue(account.notify_returns)
        self.assertTrue(account.notify_fines)

    def test_aksi_tautan_butuh_login(self):
        self.client.logout()
        response = self.client.post(reverse("telegram:link_start"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_aksi_tautan_butuh_post(self):
        response = self.client.get(reverse("telegram:link_start"))
        self.assertEqual(response.status_code, 405)
