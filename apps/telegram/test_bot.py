import json
from io import StringIO
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from apps.telegram.bot import (
    acquire_bot_lease,
    advance_update_id,
    get_last_update_id,
    handle_update,
    release_bot_lease,
    renew_bot_lease,
)
from apps.telegram.linking import create_link_token
from apps.telegram.models import TelegramAccount
from apps.telegram.services import (
    TelegramClient,
    TelegramNetworkError,
    TelegramResponseError,
    TelegramValidationError,
)

User = get_user_model()
TEST_TOKEN = "123456:fake-token-for-tests"


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self, *args, **kwargs):
        return self._body

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def make_user(username="siswa1", nis="1001", full_name="Siswa Satu"):
    return User.objects.create_user(
        username=username,
        password="rahasia123",
        role=User.Role.SISWA,
        full_name=full_name,
        nis=nis,
    )


def start_update(code, update_id=100, chat_id=9001):
    return {
        "update_id": update_id,
        "message": {
            "text": f"/start {code}",
            "chat": {"id": chat_id, "type": "private"},
            "from": {
                "id": chat_id,
                "is_bot": False,
                "username": "pemilik",
                "first_name": "Pemilik",
            },
        },
    }


class TelegramGetUpdatesTests(SimpleTestCase):
    def test_get_updates_parsing_dan_timeout_request(self):
        opener = Mock(
            return_value=FakeResponse(
                {
                    "ok": True,
                    "result": [
                        {"update_id": 101, "message": {"text": "/start"}}
                    ],
                }
            )
        )
        client = TelegramClient(
            TEST_TOKEN, timeout=10, opener=opener
        )
        updates = client.get_updates(
            offset=100,
            timeout=5,
            allowed_updates=["message"],
        )
        self.assertEqual(updates[0]["update_id"], 101)
        request = opener.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["offset"], 100)
        self.assertEqual(payload["timeout"], 5)
        self.assertEqual(payload["allowed_updates"], ["message"])
        self.assertEqual(opener.call_args.kwargs["timeout"], 20)

    def test_get_updates_menolak_respons_bukan_daftar(self):
        opener = Mock(
            return_value=FakeResponse({"ok": True, "result": {}})
        )
        client = TelegramClient(TEST_TOKEN, opener=opener)
        with self.assertRaises(TelegramResponseError):
            client.get_updates()

    def test_get_updates_menolak_update_tanpa_id(self):
        opener = Mock(
            return_value=FakeResponse(
                {"ok": True, "result": [{"message": {}}]}
            )
        )
        client = TelegramClient(TEST_TOKEN, opener=opener)
        with self.assertRaises(TelegramResponseError):
            client.get_updates()

    def test_get_updates_parameter_tidak_valid(self):
        client = TelegramClient(TEST_TOKEN, opener=Mock())
        with self.assertRaises(TelegramValidationError):
            client.get_updates(limit=0)
        with self.assertRaises(TelegramValidationError):
            client.get_updates(timeout=51)
        with self.assertRaises(TelegramValidationError):
            client.get_updates(offset=-1)


class TelegramBotUpdateTests(TestCase):
    def setUp(self):
        self.user = make_user(full_name="Rahasia Siswa")
        self.client = Mock()

    def test_start_dengan_kode_mengklaim_tanpa_menautkan(self):
        code, token = create_link_token(self.user)
        handled = handle_update(
            start_update(code), client=self.client
        )
        self.assertTrue(handled)
        token.refresh_from_db()
        self.assertIsNotNone(token.claimed_at)
        self.assertEqual(token.telegram_user_id, 9001)
        self.assertFalse(
            TelegramAccount.objects.filter(user=self.user).exists()
        )
        reply = self.client.send_message.call_args.args[1]
        self.assertIn("Konfirmasi", reply)

    def test_pesan_tidak_membocorkan_nama_pengguna(self):
        code, _token = create_link_token(self.user)
        handle_update(start_update(code), client=self.client)
        reply = self.client.send_message.call_args.args[1]
        self.assertNotIn("Rahasia Siswa", reply)
        self.assertNotIn("pinjam", reply.casefold())

    def test_start_tanpa_kode_memberi_instruksi(self):
        update = start_update("")
        update["message"]["text"] = "/start"
        handle_update(update, client=self.client)
        reply = self.client.send_message.call_args.args[1]
        self.assertIn("Profil SmartLib", reply)

    def test_kode_tidak_valid_dibalas_aman(self):
        handle_update(
            start_update("kode-yang-tidak-ada"), client=self.client
        )
        reply = self.client.send_message.call_args.args[1]
        self.assertIn("tidak valid", reply)

    def test_update_grup_diabaikan(self):
        update = start_update("apa-saja")
        update["message"]["chat"]["type"] = "group"
        handled = handle_update(update, client=self.client)
        self.assertFalse(handled)
        self.client.send_message.assert_not_called()

    def test_update_duplikat_tidak_mengubah_identitas(self):
        code, token = create_link_token(self.user)
        handle_update(start_update(code), client=self.client)
        token.refresh_from_db()
        first_claim = token.claimed_at
        handle_update(start_update(code), client=self.client)
        token.refresh_from_db()
        self.assertEqual(token.claimed_at, first_claim)
        self.assertEqual(token.telegram_user_id, 9001)
        self.assertEqual(self.client.send_message.call_count, 2)

    def test_kegagalan_kirim_tidak_melempar(self):
        code, _token = create_link_token(self.user)
        self.client.send_message.side_effect = TelegramNetworkError(
            "offline"
        )
        self.assertTrue(
            handle_update(start_update(code), client=self.client)
        )


class TelegramBotLeaseTests(TestCase):
    def test_lease_hanya_satu_pemilik(self):
        self.assertTrue(acquire_bot_lease("poller-a", 60))
        self.assertFalse(acquire_bot_lease("poller-b", 60))
        release_bot_lease("poller-a")
        self.assertTrue(acquire_bot_lease("poller-b", 60))

    def test_renew_hanya_oleh_pemilik(self):
        acquire_bot_lease("poller-a", 60)
        self.assertFalse(renew_bot_lease("poller-b", 60))
        self.assertTrue(renew_bot_lease("poller-a", 60))

    def test_update_id_tidak_mundur(self):
        advance_update_id(10)
        advance_update_id(5)
        self.assertEqual(get_last_update_id(), 10)


class TelegramBotCommandTests(TestCase):
    def setUp(self):
        self.user = make_user()

    @override_settings(TELEGRAM_BOT_TOKEN=TEST_TOKEN)
    def test_command_menolak_poller_kedua(self):
        acquire_bot_lease("poller-lain", 60)
        with self.assertRaises(CommandError) as caught:
            call_command("telegram_bot", once=True)
        self.assertIn("Poller Telegram lain", str(caught.exception))

    @override_settings(TELEGRAM_BOT_TOKEN=TEST_TOKEN)
    def test_command_once_memproses_update_dan_menyimpan_offset(self):
        code, token = create_link_token(self.user)
        update = start_update(code)
        telegram_client = Mock()
        telegram_client.get_updates.return_value = [update]
        output = StringIO()
        with patch(
            "apps.telegram.management.commands.telegram_bot.get_client",
            return_value=telegram_client,
        ):
            call_command(
                "telegram_bot", once=True, stdout=output
            )
        token.refresh_from_db()
        self.assertIsNotNone(token.claimed_at)
        self.assertEqual(get_last_update_id(), 100)
        telegram_client.get_updates.assert_called_once_with(
            offset=1,
            timeout=0,
            allowed_updates=["message"],
        )
        from apps.telegram.models import TelegramBotState

        state = TelegramBotState.objects.get(pk=1)
        self.assertEqual(state.lease_owner, "")
        self.assertIsNone(state.lease_until)
