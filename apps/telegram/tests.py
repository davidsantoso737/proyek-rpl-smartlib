import io
import json
import urllib.error
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from apps.telegram.services import (
    BotInfo,
    SentMessage,
    TelegramAPIError,
    TelegramClient,
    TelegramConfigError,
    TelegramNetworkError,
    TelegramResponseError,
    TelegramTimeoutError,
    TelegramValidationError,
    get_client,
    send_text_message,
)

TEST_TOKEN = "123456:fake-token-for-tests"


class FakeResponse:
    def __init__(self, payload, status=200):
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self, *args, **kwargs):
        return self._body

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def http_error(code, payload, reason="Telegram error"):
    return urllib.error.HTTPError(
        f"https://api.telegram.org/bot{TEST_TOKEN}/getMe",
        code,
        reason,
        None,
        io.BytesIO(json.dumps(payload).encode("utf-8")),
    )


class TelegramConfigTests(SimpleTestCase):
    @override_settings(
        TELEGRAM_BOT_TOKEN=TEST_TOKEN,
        TELEGRAM_API_BASE_URL="https://api.telegram.org",
        TELEGRAM_TIMEOUT_SECONDS=5,
    )
    def test_token_tersedia(self):
        client = get_client()
        self.assertIsInstance(client, TelegramClient)
        self.assertEqual(client.timeout, 5)

    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_token_tidak_tersedia(self):
        with self.assertRaises(TelegramConfigError):
            get_client()

    @override_settings(
        TELEGRAM_BOT_TOKEN=TEST_TOKEN,
        TELEGRAM_API_BASE_URL="http://api.telegram.org",
    )
    def test_base_url_wajib_https(self):
        with self.assertRaises(TelegramConfigError):
            get_client()

    @override_settings(
        TELEGRAM_BOT_TOKEN=TEST_TOKEN,
        TELEGRAM_NOTIFICATIONS_ENABLED=False,
    )
    def test_pengiriman_dinonaktifkan(self):
        with self.assertRaises(TelegramConfigError):
            send_text_message(12345, "Halo")


class TelegramClientTests(SimpleTestCase):
    def test_get_me_berhasil(self):
        opener = Mock(
            return_value=FakeResponse(
                {
                    "ok": True,
                    "result": {
                        "id": 999,
                        "is_bot": True,
                        "first_name": "SmartLib",
                        "username": "smartlib_bot",
                    },
                }
            )
        )
        bot = TelegramClient(TEST_TOKEN, opener=opener).get_me()
        self.assertEqual(bot.id, 999)
        self.assertEqual(bot.username, "smartlib_bot")
        self.assertTrue(bot.is_bot)
        request = opener.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/getMe"))
        self.assertEqual(opener.call_args.kwargs["timeout"], 10)

    def test_token_tidak_valid(self):
        opener = Mock(
            side_effect=http_error(
                401,
                {
                    "ok": False,
                    "error_code": 401,
                    "description": "Unauthorized",
                },
            )
        )
        with self.assertLogs("apps.telegram.services", level="WARNING") as logs:
            with self.assertRaises(TelegramAPIError) as caught:
                TelegramClient(TEST_TOKEN, opener=opener).get_me()
        self.assertEqual(caught.exception.code, 401)
        self.assertNotIn(TEST_TOKEN, str(caught.exception))
        self.assertNotIn(TEST_TOKEN, "\n".join(logs.output))

    def test_api_timeout(self):
        opener = Mock(side_effect=TimeoutError("timed out"))
        with self.assertLogs("apps.telegram.services", level="WARNING") as logs:
            with self.assertRaises(TelegramTimeoutError):
                TelegramClient(TEST_TOKEN, opener=opener).get_me()
        self.assertNotIn(TEST_TOKEN, "\n".join(logs.output))

    def test_api_timeout_dalam_urlerror(self):
        opener = Mock(side_effect=urllib.error.URLError(TimeoutError("timed out")))
        with self.assertRaises(TelegramTimeoutError):
            TelegramClient(TEST_TOKEN, opener=opener).get_me()

    def test_api_error(self):
        opener = Mock(
            side_effect=http_error(
                400,
                {
                    "ok": False,
                    "error_code": 400,
                    "description": "Bad Request: chat not found",
                },
            )
        )
        with self.assertRaises(TelegramAPIError) as caught:
            TelegramClient(TEST_TOKEN, opener=opener).send_message(
                12345, "Halo"
            )
        self.assertEqual(caught.exception.code, 400)
        self.assertIn("chat not found", caught.exception.description)

    def test_kegagalan_jaringan(self):
        opener = Mock(
            side_effect=urllib.error.URLError(OSError("connection refused"))
        )
        with self.assertLogs("apps.telegram.services", level="WARNING") as logs:
            with self.assertRaises(TelegramNetworkError):
                TelegramClient(TEST_TOKEN, opener=opener).get_me()
        self.assertNotIn(TEST_TOKEN, "\n".join(logs.output))

    def test_respons_bukan_json(self):
        response = FakeResponse({})
        response._body = b"bukan json"
        opener = Mock(return_value=response)
        with self.assertRaises(TelegramResponseError):
            TelegramClient(TEST_TOKEN, opener=opener).get_me()

    def test_kirim_pesan_berhasil(self):
        opener = Mock(
            return_value=FakeResponse(
                {
                    "ok": True,
                    "result": {
                        "message_id": 42,
                        "chat": {"id": 12345},
                        "date": 1710000000,
                    },
                }
            )
        )
        sent = TelegramClient(TEST_TOKEN, opener=opener).send_message(
            12345, "Halo SmartLib"
        )
        self.assertEqual(sent.message_id, 42)
        self.assertEqual(sent.chat_id, 12345)
        request = opener.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["chat_id"], 12345)
        self.assertEqual(payload["text"], "Halo SmartLib")
        self.assertTrue(payload["disable_web_page_preview"])
        self.assertNotIn(TEST_TOKEN, request.data.decode("utf-8"))

    def test_chat_id_dan_pesan_tidak_valid(self):
        client = TelegramClient(TEST_TOKEN, opener=Mock())
        with self.assertRaises(TelegramValidationError):
            client.send_message("12345", "Halo")
        with self.assertRaises(TelegramValidationError):
            client.send_message(12345, "   ")
        with self.assertRaises(TelegramValidationError):
            client.send_message(12345, "x" * 4097)


class TelegramCommandTests(SimpleTestCase):
    @override_settings(TELEGRAM_BOT_TOKEN=TEST_TOKEN)
    @patch(
        "apps.telegram.management.commands.telegram_check.check_connection",
        return_value=BotInfo(
            id=999,
            username="smartlib_bot",
            first_name="SmartLib",
            is_bot=True,
        ),
    )
    def test_check_tidak_mencetak_token(self, mocked_check):
        from io import StringIO

        output = StringIO()
        call_command("telegram_check", stdout=output)
        self.assertIn("@smartlib_bot", output.getvalue())
        self.assertNotIn(TEST_TOKEN, output.getvalue())
        mocked_check.assert_called_once_with()

    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_check_tanpa_token(self):
        with self.assertRaises(CommandError) as caught:
            call_command("telegram_check")
        self.assertIn("TELEGRAM_BOT_TOKEN", str(caught.exception))

    @override_settings(TELEGRAM_BOT_TOKEN=TEST_TOKEN)
    @patch(
        "apps.telegram.management.commands.telegram_send_test.send_text_message",
        return_value=SentMessage(
            message_id=42,
            chat_id=12345,
            date=1710000000,
        ),
    )
    def test_send_test_hanya_ke_chat_id_eksplisit(self, mocked_send):
        from io import StringIO

        output = StringIO()
        call_command(
            "telegram_send_test",
            chat_id=12345,
            text="Halo",
            stdout=output,
        )
        mocked_send.assert_called_once_with(12345, "Halo")
        self.assertIn("12345", output.getvalue())
        self.assertNotIn(TEST_TOKEN, output.getvalue())

    def test_send_test_menolak_chat_id_negatif(self):
        with self.assertRaises(CommandError):
            call_command("telegram_send_test", chat_id=-1001)
