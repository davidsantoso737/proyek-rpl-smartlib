from datetime import timedelta
from urllib.parse import quote
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.notifications.models import Notification
from apps.telegram.bot import handle_update
from apps.telegram.connection import (
    approve_connection,
    cancel_pending_connection,
    create_connection_token,
    mask_phone,
    normalize_phone,
    reject_connection,
    resolve_recipient,
    revoke_active_connection,
    start_link_token,
    verify_contact,
)
from apps.telegram.dispatch import (
    enqueue_telegram_notification,
    process_due,
)
from apps.telegram.models import (
    TelegramAccount,
    TelegramConnection,
    TelegramOutbox,
)
from apps.telegram.services import SentMessage

User = get_user_model()


def make_user(
    username="siswa1",
    role=User.Role.SISWA,
    phone="081234567890",
    **extra,
):
    defaults = {
        "full_name": f"{username} Full",
        "phone": phone,
    }
    if role == User.Role.SISWA:
        defaults.setdefault("nis", f"NIS-{username}")
    if role == User.Role.GURU:
        defaults.setdefault("nip", f"NIP-{username}")
    defaults.update(extra)
    return User.objects.create_user(
        username=username,
        password="secret123",
        role=role,
        **defaults,
    )


def start_update(code, *, telegram_user_id=9001):
    return {
        "update_id": 100,
        "message": {
            "text": f"/start {code}",
            "chat": {"id": telegram_user_id, "type": "private"},
            "from": {
                "id": telegram_user_id,
                "is_bot": False,
                "username": "pemilik",
                "first_name": "Pemilik",
            },
        },
    }


def contact_update(
    *,
    telegram_user_id=9001,
    contact_user_id=9001,
    phone_number="081234567890",
):
    return {
        "update_id": 101,
        "message": {
            "chat": {"id": telegram_user_id, "type": "private"},
            "from": {
                "id": telegram_user_id,
                "is_bot": False,
                "username": "pemilik",
                "first_name": "Pemilik",
            },
            "contact": {
                "user_id": contact_user_id,
                "phone_number": phone_number,
            },
        },
    }


class PhoneNormalizationTests(SimpleTestCase):
    def test_normalisasi_nomor_indonesia(self):
        expected = "6281234567890"
        for raw in (
            "081234567890",
            "81234567890",
            "6281234567890",
            "+62 812-3456-7890",
            "0812 3456 7890",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_phone(raw), expected)

    def test_nomor_tidak_valid_ditolak(self):
        for raw in ("", None, "021234567", "12345", "abcdef", "0000"):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_phone(raw), "")

    def test_mask_tidak_membocorkan_nomor_penuh(self):
        masked = mask_phone("081234567890")
        self.assertIn("*", masked)
        self.assertNotEqual(masked, "081234567890")
        self.assertTrue(masked.startswith("0812"))
        self.assertTrue(masked.endswith("90"))


class RecordingClient:
    def __init__(self):
        self.sent = []

    def send_message(
        self,
        chat_id,
        text,
        *,
        parse_mode=None,
        reply_markup=None,
    ):
        self.sent.append(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )
        return SentMessage(
            message_id=9001, chat_id=chat_id, date=0
        )


class TelegramConnectionServiceTests(TestCase):
    def setUp(self):
        self.pus = make_user(
            username="pus1",
            role=User.Role.PUSTAKAWAN,
            phone=None,
        )
        self.siswa = make_user()
        self.other = make_user(username="siswa2")

    def _pending(self, user=None):
        user = user or self.siswa
        return TelegramConnection.objects.get(
            user=user,
            status=TelegramConnection.Status.PENDING,
            active_slot=1,
        )

    def _approved(self, user=None):
        connection = self._pending(user)
        result = approve_connection(connection.pk, self.pus)
        self.assertEqual(result.status, "ok")
        connection.refresh_from_db()
        return connection

    def _token(self, user=None):
        connection = self._approved(user)
        result = create_connection_token(connection.user)
        self.assertEqual(result.status, "ok")
        return result

    def _awaiting(self, user=None, telegram_user_id=9001):
        result = self._token(user)
        start = start_link_token(
            result.code,
            telegram_user_id=telegram_user_id,
            chat_id=telegram_user_id,
            username="pemilik",
            first_name="Pemilik",
        )
        self.assertEqual(start.status, "ok")
        return result.connection

    def _active(self, user=None, telegram_user_id=9001):
        connection = self._awaiting(user, telegram_user_id)
        contact = verify_contact(
            telegram_user_id=telegram_user_id,
            chat_id=telegram_user_id,
            contact_user_id=telegram_user_id,
            phone_number="081234567890",
            username="pemilik",
            first_name="Pemilik",
        )
        self.assertEqual(contact.status, "ok")
        return contact.connection

    def test_pengajuan_otomatis_dibuat_saat_akun_dibuat(self):
        connection = self._pending()
        self.assertEqual(connection.requested_phone, "6281234567890")
        self.assertIsNotNone(connection.requested_at)
        self.assertIsNone(connection.approved_at)
        self.assertTrue(
            Notification.objects.filter(
                user=self.pus,
                type="TELEGRAM_LINK_REQUEST",
                reference_type="telegram_connection",
                reference_id=connection.pk,
            ).exists()
        )

    def test_pengajuan_tidak_dibuat_tanpa_nomor_hp(self):
        user = make_user(username="siswa3", phone=None)
        self.assertFalse(
            TelegramConnection.objects.filter(user=user).exists()
        )

    def test_nomor_baru_membuat_pengajuan_baru(self):
        old = self._approved()
        self.siswa.phone = "081200000000"
        self.siswa.save(update_fields=["phone"])

        old.refresh_from_db()
        self.assertEqual(
            old.status, TelegramConnection.Status.REVOKED
        )
        self.assertIsNone(old.active_slot)

        new = self._pending()
        self.assertNotEqual(new.pk, old.pk)
        self.assertEqual(new.requested_phone, "6281200000000")

    def test_nomor_sama_tidak_membuat_pengajuan_ganda(self):
        self.siswa.save(update_fields=["phone"])
        self.assertEqual(
            TelegramConnection.objects.filter(user=self.siswa).count(), 1
        )

    def test_persetujuan_menunggu_konfirmasi_telegram(self):
        connection = self._approved()
        self.assertEqual(
            connection.status,
            TelegramConnection.Status.AWAITING_CONTACT,
        )
        self.assertEqual(connection.approved_by, self.pus)
        self.assertIsNotNone(connection.approved_at)
        self.assertIsNone(connection.token_hash)
        self.assertTrue(
            Notification.objects.filter(
                user=self.siswa,
                type="TELEGRAM_LINK_APPROVED",
                reference_id=connection.pk,
            ).exists()
        )

    def test_persetujuan_ditolak_bila_nomor_berubah(self):
        connection = self._pending()
        User.objects.filter(pk=self.siswa.pk).update(
            phone="081200000000"
        )
        result = approve_connection(connection.pk, self.pus)
        self.assertEqual(result.status, "phone_changed")
        connection.refresh_from_db()
        self.assertEqual(
            connection.status, TelegramConnection.Status.PENDING
        )

    def test_penolakan_menyimpan_catatan_dan_menutup_slot(self):
        connection = self._pending()
        result = reject_connection(
            connection.pk, self.pus, "Nomor tidak cocok."
        )
        self.assertEqual(result.status, "ok")
        connection.refresh_from_db()
        self.assertEqual(
            connection.status, TelegramConnection.Status.REJECTED
        )
        self.assertEqual(connection.rejection_note, "Nomor tidak cocok.")
        self.assertIsNone(connection.active_slot)
        self.assertTrue(
            Notification.objects.filter(
                user=self.siswa,
                type="TELEGRAM_LINK_REJECTED",
                reference_id=connection.pk,
            ).exists()
        )

    def test_token_disimpan_sebagai_hash_dan_kedaluwarsa(self):
        result = self._token()
        connection = result.connection
        connection.refresh_from_db()

        self.assertNotEqual(connection.token_hash, result.code)
        self.assertEqual(len(connection.token_hash), 64)
        self.assertEqual(
            connection.status, TelegramConnection.Status.AWAITING_CONTACT
        )
        self.assertEqual(connection.active_slot, 1)
        self.assertGreater(connection.token_expires_at, timezone.now())
        self.assertLessEqual(
            connection.token_expires_at,
            timezone.now() + timedelta(minutes=11),
        )

    def test_token_tidak_dibuat_sebelum_disetujui(self):
        result = create_connection_token(self.siswa)
        self.assertEqual(result.status, "pending_admin")
        connection = self._pending()
        connection.refresh_from_db()
        self.assertIsNone(connection.token_hash)

    def test_token_baru_menggantikan_tautan_belum_selesai(self):
        first = self._token()
        second = create_connection_token(self.siswa)
        self.assertEqual(second.status, "ok")
        self.assertNotEqual(first.code, second.code)

        first.connection.refresh_from_db()
        self.assertEqual(
            first.connection.status,
            TelegramConnection.Status.AWAITING_CONTACT,
        )
        self.assertEqual(
            first.connection.token_hash, second.connection.token_hash
        )

        reuse = start_link_token(
            first.code, telegram_user_id=9001, chat_id=9001
        )
        self.assertEqual(reuse.status, "invalid")

    def test_pengguna_tanpa_nomor_hp_tidak_bisa_membuat_token(self):
        user = make_user(username="siswa3", phone=None)
        result = create_connection_token(user)
        self.assertEqual(result.status, "no_phone")

    def test_start_mengikat_identitas_telegram(self):
        result = self._token()
        start = start_link_token(
            result.code,
            telegram_user_id=9001,
            chat_id=9001,
            username="pemilik",
            first_name="Pemilik",
        )
        self.assertEqual(start.status, "ok")
        connection = result.connection
        connection.refresh_from_db()
        self.assertEqual(connection.telegram_user_id, 9001)
        self.assertEqual(connection.chat_id, 9001)
        self.assertEqual(connection.telegram_username, "pemilik")

    def test_start_menolak_token_kedaluwarsa(self):
        result = self._token()
        TelegramConnection.objects.filter(
            pk=result.connection.pk
        ).update(token_expires_at=timezone.now() - timedelta(seconds=1))

        start = start_link_token(
            result.code,
            telegram_user_id=9001,
            chat_id=9001,
        )
        self.assertEqual(start.status, "expired")
        result.connection.refresh_from_db()
        self.assertIsNone(result.connection.token_hash)
        self.assertEqual(
            result.connection.status,
            TelegramConnection.Status.AWAITING_CONTACT,
        )

    def test_start_menolak_telegram_yang_sudah_dipakai(self):
        self._active(self.other, telegram_user_id=9001)
        result = self._token()
        start = start_link_token(
            result.code,
            telegram_user_id=9001,
            chat_id=9001,
        )
        self.assertEqual(start.status, "conflict")

    def test_token_hanya_dapat_dipakai_sekali(self):
        result = self._token()
        first = start_link_token(
            result.code, telegram_user_id=9001, chat_id=9001
        )
        self.assertEqual(first.status, "ok")
        verify_contact(
            telegram_user_id=9001,
            chat_id=9001,
            contact_user_id=9001,
            phone_number="081234567890",
        )
        reuse = start_link_token(
            result.code, telegram_user_id=9001, chat_id=9001
        )
        self.assertEqual(reuse.status, "invalid")

    def test_kontak_cocok_mengaktifkan_koneksi(self):
        connection = self._active()

        self.assertEqual(
            connection.status, TelegramConnection.Status.ACTIVE
        )
        self.assertEqual(connection.verified_phone, "6281234567890")
        self.assertIsNotNone(connection.verified_at)
        self.assertIsNotNone(connection.requested_at)
        self.assertIsNone(connection.token_hash)
        self.assertIsNone(connection.token_expires_at)
        self.assertEqual(connection.chat_id, 9001)
        self.assertTrue(
            Notification.objects.filter(
                user=self.siswa,
                type="TELEGRAM_LINK_ACTIVE",
                reference_id=connection.pk,
            ).exists()
        )

    def test_kontak_tanpa_sesi_ditolak(self):
        result = verify_contact(
            telegram_user_id=9001,
            chat_id=9001,
            contact_user_id=9001,
            phone_number="081234567890",
        )
        self.assertEqual(result.status, "no_session")

    def test_nomor_kontak_berbeda_tidak_mengaktifkan(self):
        result = self._token()
        start_link_token(
            result.code, telegram_user_id=9001, chat_id=9001
        )
        contact = verify_contact(
            telegram_user_id=9001,
            chat_id=9001,
            contact_user_id=9001,
            phone_number="081200000000",
        )
        self.assertEqual(contact.status, "phone_mismatch")
        self.assertEqual(contact.attempts_left, 4)
        result.connection.refresh_from_db()
        self.assertEqual(
            result.connection.status,
            TelegramConnection.Status.AWAITING_CONTACT,
        )
        self.assertEqual(result.connection.attempt_count, 1)

    def test_kontak_milik_orang_lain_ditolak(self):
        result = self._token()
        start_link_token(
            result.code, telegram_user_id=9001, chat_id=9001
        )
        contact = verify_contact(
            telegram_user_id=9001,
            chat_id=9001,
            contact_user_id=9002,
            phone_number="081234567890",
        )
        self.assertEqual(contact.status, "contact_mismatch")
        result.connection.refresh_from_db()
        self.assertEqual(
            result.connection.status,
            TelegramConnection.Status.AWAITING_CONTACT,
        )

    def test_batas_percobaan_membatalkan_tautan(self):
        result = self._token()
        start_link_token(
            result.code, telegram_user_id=9001, chat_id=9001
        )
        for _ in range(5):
            contact = verify_contact(
                telegram_user_id=9001,
                chat_id=9001,
                contact_user_id=9001,
                phone_number="081200000000",
            )
        self.assertEqual(contact.status, "too_many")
        result.connection.refresh_from_db()
        self.assertEqual(
            result.connection.status, TelegramConnection.Status.REVOKED
        )
        self.assertIsNone(result.connection.active_slot)

    def test_pembatalan_pengguna_menutup_slot(self):
        connection = self._pending()
        result = cancel_pending_connection(self.siswa)
        self.assertEqual(result.status, "ok")
        connection.refresh_from_db()
        self.assertEqual(
            connection.status, TelegramConnection.Status.REVOKED
        )
        self.assertIsNone(connection.active_slot)

    def test_pemutusan_mengembalikan_chat_untuk_pemberitahuan(self):
        connection = self._active()
        chat_id, revoked = revoke_active_connection(self.siswa)
        self.assertEqual(chat_id, 9001)
        self.assertEqual(revoked.pk, connection.pk)
        connection.refresh_from_db()
        self.assertEqual(
            connection.status, TelegramConnection.Status.REVOKED
        )
        self.assertIsNone(connection.chat_id)
        self.assertIsNone(connection.active_slot)
        self.assertIsNone(resolve_recipient(self.siswa))

    def test_resolver_mengabaikan_pengajuan_dan_menunggu(self):
        pending_user = make_user(username="siswa4")
        self.assertEqual(
            resolve_recipient(pending_user),
            None,
        )
        self._approved(user=pending_user)
        self.assertIsNone(resolve_recipient(pending_user))

        active = self._active()
        self.assertEqual(resolve_recipient(self.siswa).pk, active.pk)

    def test_resolver_memakai_akun_lama_sebagai_fallback(self):
        account = TelegramAccount.objects.create(
            user=self.siswa,
            telegram_user_id=81001,
            chat_id=91001,
        )
        self.assertEqual(resolve_recipient(self.siswa), account)

    @override_settings(
        TELEGRAM_NOTIFICATIONS_ENABLED=True,
        TELEGRAM_BOT_TOKEN="123456:fake-token",
    )
    def test_outbox_memakai_koneksi_aktif(self):
        self._active()
        outbox = enqueue_telegram_notification(
            user=self.siswa,
            notification_type="LOAN_APPROVED",
            title="Pengajuan disetujui",
            message="Silakan ambil buku.",
            reference_type="loan",
            reference_id=1,
        )
        self.assertIsNotNone(outbox)
        client = RecordingClient()
        stats = process_due(client=client)
        self.assertEqual(stats["sent"], 1)
        self.assertEqual(client.sent[0]["chat_id"], 9001)

    @override_settings(
        TELEGRAM_NOTIFICATIONS_ENABLED=True,
        TELEGRAM_BOT_TOKEN="123456:fake-token",
    )
    def test_outbox_tidak_dikirim_untuk_koneksi_pending(self):
        outbox = enqueue_telegram_notification(
            user=self.siswa,
            notification_type="LOAN_APPROVED",
            title="Pengajuan disetujui",
            message="Silakan ambil buku.",
        )
        self.assertIsNone(outbox)
        self.assertFalse(TelegramOutbox.objects.exists())

    @override_settings(
        TELEGRAM_NOTIFICATIONS_ENABLED=True,
        TELEGRAM_BOT_TOKEN="123456:fake-token",
    )
    def test_outbox_tidak_dikirim_saat_menunggu_konfirmasi(self):
        self._approved()
        outbox = enqueue_telegram_notification(
            user=self.siswa,
            notification_type="LOAN_APPROVED",
            title="Pengajuan disetujui",
            message="Silakan ambil buku.",
        )
        self.assertIsNone(outbox)
        self.assertFalse(TelegramOutbox.objects.exists())


class TelegramBotConnectionTests(TestCase):
    def setUp(self):
        self.pus = make_user(
            username="pus1",
            role=User.Role.PUSTAKAWAN,
            phone=None,
        )
        self.user = make_user()
        self.client = Mock()

    def _token(self):
        connection = TelegramConnection.objects.get(
            user=self.user,
            status=TelegramConnection.Status.PENDING,
            active_slot=1,
        )
        approve_connection(connection.pk, self.pus)
        result = create_connection_token(self.user)
        self.assertEqual(result.status, "ok")
        return result

    def test_start_baru_meminta_kontak(self):
        result = self._token()
        handled = handle_update(
            start_update(result.code), client=self.client
        )
        self.assertTrue(handled)
        reply = self.client.send_message.call_args
        self.assertIn("Bagikan nomor HP", reply.args[1])
        keyboard = reply.kwargs["reply_markup"]["keyboard"][0][0]
        self.assertTrue(keyboard["request_contact"])

    def test_kontak_cocok_mengaktifkan_koneksi(self):
        result = self._token()
        handle_update(start_update(result.code), client=self.client)
        handled = handle_update(
            contact_update(
                telegram_user_id=9001,
                contact_user_id=9001,
                phone_number="081234567890",
            ),
            client=self.client,
        )
        self.assertTrue(handled)
        result.connection.refresh_from_db()
        self.assertEqual(
            result.connection.status, TelegramConnection.Status.ACTIVE
        )
        self.assertIn(
            "berhasil",
            self.client.send_message.call_args.args[1].casefold(),
        )

    def test_kontak_orang_lain_tidak_mengubah_status(self):
        result = self._token()
        handle_update(start_update(result.code), client=self.client)
        handle_update(
            contact_update(
                telegram_user_id=9001,
                contact_user_id=9002,
                phone_number="081234567890",
            ),
            client=self.client,
        )
        result.connection.refresh_from_db()
        self.assertEqual(
            result.connection.status,
            TelegramConnection.Status.AWAITING_CONTACT,
        )
        self.assertIn(
            "tidak cocok",
            self.client.send_message.call_args.args[1].casefold(),
        )


@override_settings(TELEGRAM_BOT_USERNAME="smartlib_bot")
class TelegramConnectionViewTests(TestCase):
    def setUp(self):
        self.pus = make_user(
            username="pus1",
            role=User.Role.PUSTAKAWAN,
            phone=None,
        )
        self.siswa = make_user()

    def _login_siswa(self):
        self.client.force_login(self.siswa)

    def _pending(self):
        return TelegramConnection.objects.get(
            user=self.siswa,
            status=TelegramConnection.Status.PENDING,
            active_slot=1,
        )

    def _approved(self):
        connection = self._pending()
        approve_connection(connection.pk, self.pus)
        connection.refresh_from_db()
        return connection

    def _active(self):
        connection = self._approved()
        result = create_connection_token(self.siswa)
        self.assertEqual(result.status, "ok")
        start_link_token(
            result.code, telegram_user_id=9001, chat_id=9001
        )
        contact = verify_contact(
            telegram_user_id=9001,
            chat_id=9001,
            contact_user_id=9001,
            phone_number="081234567890",
        )
        self.assertEqual(contact.status, "ok")
        connection.refresh_from_db()
        return connection

    def test_profil_menampilkan_status_pending(self):
        self._login_siswa()
        response = self.client.get(reverse("accounts:profile"))
        self.assertContains(response, "Menunggu persetujuan pustakawan")
        self.assertContains(response, "Menunggu persetujuan")
        self.assertContains(response, "0812******90")

    def test_profil_menampilkan_ajukan_penautan_tanpa_pengajuan(self):
        TelegramConnection.objects.filter(user=self.siswa).delete()
        self._login_siswa()
        response = self.client.get(reverse("accounts:profile"))
        self.assertContains(response, "Belum terhubung")
        self.assertContains(response, "Ajukan penautan Telegram")

    def test_profil_menampilkan_konfirmasi_setelah_disetujui(self):
        self._approved()
        self._login_siswa()
        response = self.client.get(reverse("accounts:profile"))
        self.assertContains(response, "Konfirmasi Telegram")
        self.assertContains(response, "Perlu konfirmasi")

    def test_mulai_tautan_membuat_token_dan_sesi(self):
        self._approved()
        self._login_siswa()
        response = self.client.post(reverse("telegram:link_start"))
        self.assertRedirects(response, reverse("accounts:profile"))
        connection = TelegramConnection.objects.get(user=self.siswa)
        raw = self.client.session.get("telegram_connection_token")
        self.assertTrue(raw)
        self.assertNotEqual(connection.token_hash, raw)

    def test_mulai_tautan_pending_tidak_membuat_token(self):
        self._login_siswa()
        response = self.client.post(reverse("telegram:link_start"))
        self.assertRedirects(response, reverse("accounts:profile"))
        connection = TelegramConnection.objects.get(user=self.siswa)
        self.assertIsNone(connection.token_hash)
        self.assertIsNone(
            self.client.session.get("telegram_connection_token")
        )

    def test_profil_tidak_menampilkan_token(self):
        self._approved()
        self._login_siswa()
        self.client.post(reverse("telegram:link_start"))
        raw = self.client.session.get("telegram_connection_token")
        response = self.client.get(reverse("accounts:profile"))
        self.assertContains(response, "Buka bot Telegram")
        self.assertNotContains(response, raw)

    def test_buka_tautan_mengalihkan_ke_bot(self):
        self._approved()
        self._login_siswa()
        self.client.post(reverse("telegram:link_start"))
        raw = self.client.session.get("telegram_connection_token")
        response = self.client.get(reverse("telegram:link_open"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            f"https://t.me/smartlib_bot?start={quote(raw)}",
        )

    def test_buka_tautan_tanpa_sesi_ditolak(self):
        self._login_siswa()
        response = self.client.get(reverse("telegram:link_open"))
        self.assertRedirects(response, reverse("accounts:profile"))

    def test_mulai_tautan_tanpa_nomor_hp_ditolak(self):
        user = make_user(username="siswa9", phone=None)
        self.client.force_login(user)
        self.client.post(reverse("telegram:link_start"))
        self.assertFalse(
            TelegramConnection.objects.filter(user=user).exists()
        )
        response = self.client.get(reverse("accounts:profile"))
        self.assertContains(response, "Lengkapi nomor HP")

    def test_pembatalan_menutup_koneksi(self):
        connection = self._pending()
        self._login_siswa()
        self.client.post(reverse("telegram:link_start"))
        response = self.client.post(reverse("telegram:link_cancel"))
        self.assertRedirects(response, reverse("accounts:profile"))
        connection.refresh_from_db()
        self.assertEqual(
            connection.status, TelegramConnection.Status.REVOKED
        )
        self.assertIsNone(
            self.client.session.get("telegram_connection_token")
        )

    def test_preferensi_koneksi_aktif_disimpan(self):
        connection = self._active()
        self._login_siswa()
        response = self.client.post(
            reverse("telegram:preferences"),
            {
                "notify_returns": "on",
                "notify_fines": "on",
            },
        )
        self.assertRedirects(response, reverse("accounts:profile"))
        connection.refresh_from_db()
        self.assertFalse(connection.notifications_enabled)
        self.assertFalse(connection.notify_loans)
        self.assertTrue(connection.notify_returns)
        self.assertTrue(connection.notify_fines)

    def test_pemutusan_koneksi_aktif(self):
        connection = self._active()
        self._login_siswa()
        response = self.client.post(reverse("telegram:unlink"))
        self.assertRedirects(response, reverse("accounts:profile"))
        connection.refresh_from_db()
        self.assertEqual(
            connection.status, TelegramConnection.Status.REVOKED
        )

    def test_verifikasi_butuh_role_pustakawan(self):
        self._login_siswa()
        response = self.client.get(reverse("telegram:verification_list"))
        self.assertEqual(response.status_code, 403)

    def test_verifikasi_anonim_diarahkan_ke_login(self):
        response = self.client.get(reverse("telegram:verification_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_menu_sidebar_pustakawan_menampilkan_verifikasi_telegram(self):
        self.client.force_login(self.pus)
        response = self.client.get(
            reverse("accounts:dashboard_pustakawan")
        )
        self.assertContains(response, "Verifikasi Telegram")
        self.assertContains(
            response, reverse("telegram:verification_list")
        )

    def test_hanya_menu_verifikasi_telegram_yang_aktif(self):
        self.client.force_login(self.pus)
        response = self.client.get(reverse("telegram:verification_list"))
        self.assertContains(response, 'aria-current="page"', count=2)

    def test_aksi_verifikasi_butuh_role_pustakawan(self):
        connection = self._pending()
        self._login_siswa()
        response = self.client.post(
            reverse(
                "telegram:verification_approve", args=[connection.pk]
            )
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            reverse(
                "telegram:verification_reject", args=[connection.pk]
            )
        )
        self.assertEqual(response.status_code, 403)
        connection.refresh_from_db()
        self.assertEqual(
            connection.status, TelegramConnection.Status.PENDING
        )

    def test_pustakawan_melihat_dan_menyetujui(self):
        connection = self._pending()
        self.client.force_login(self.pus)
        response = self.client.get(reverse("telegram:verification_list"))
        self.assertContains(response, "Siswa")
        self.assertContains(response, "6281234567890")
        self.assertContains(response, "Lihat detail")

        response = self.client.post(
            reverse(
                "telegram:verification_approve", args=[connection.pk]
            )
        )
        self.assertRedirects(
            response, reverse("telegram:verification_list")
        )
        connection.refresh_from_db()
        self.assertEqual(
            connection.status,
            TelegramConnection.Status.AWAITING_CONTACT,
        )

    def test_pustakawan_membuka_detail_verifikasi(self):
        connection = self._pending()
        self.client.force_login(self.pus)
        response = self.client.get(
            reverse(
                "telegram:verification_detail", args=[connection.pk]
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Detail Verifikasi Telegram")
        self.assertContains(response, "6281234567890")

    def test_detail_verifikasi_butuh_role_pustakawan(self):
        connection = self._pending()
        self._login_siswa()
        response = self.client.get(
            reverse(
                "telegram:verification_detail", args=[connection.pk]
            )
        )
        self.assertEqual(response.status_code, 403)

    def test_pustakawan_menolak_dengan_catatan(self):
        connection = self._pending()
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse(
                "telegram:verification_reject", args=[connection.pk]
            ),
            {"note": "Nomor tidak cocok."},
        )
        self.assertRedirects(
            response, reverse("telegram:verification_list")
        )
        connection.refresh_from_db()
        self.assertEqual(
            connection.status, TelegramConnection.Status.REJECTED
        )
        self.assertEqual(connection.rejection_note, "Nomor tidak cocok.")
