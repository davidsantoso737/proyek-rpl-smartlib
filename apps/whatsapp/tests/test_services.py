import io
import json
import urllib.error
import urllib.parse
from unittest.mock import Mock

from django.test import SimpleTestCase, override_settings

from apps.whatsapp.services import (
    FonnteClient,
    SentMessage,
    WhatsAppAPIError,
    WhatsAppConfigError,
    WhatsAppNetworkError,
    WhatsAppResponseError,
    WhatsAppTimeoutError,
    WhatsAppValidationError,
    get_client,
    mask_phone,
    normalize_phone,
    send_text_message,
)

TEST_TOKEN = "test-fonnte-token"


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


def http_error(code, payload, reason="Fonnte error"):
    return urllib.error.HTTPError(
        "https://api.fonnte.com/send",
        code,
        reason,
        None,
        io.BytesIO(json.dumps(payload).encode("utf-8")),
    )


class PhoneTests(SimpleTestCase):
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


@override_settings(
    FONNTE_COUNTRY_CODE="62",
    FONNTE_TIMEOUT_SECONDS=5,
    FONNTE_API_URL="https://api.fonnte.com/send",
)
class FonnteClientTests(SimpleTestCase):
    def test_kirim_pesan_berhasil(self):
        opener = Mock(
            return_value=FakeResponse({"status": True, "id": ["abc123"]})
        )
        sent = FonnteClient(TEST_TOKEN, opener=opener).send_message(
            "081234567890", "Halo SmartLib"
        )

        self.assertEqual(sent.provider_message_id, "abc123")
        self.assertEqual(sent.phone, "6281234567890")
        request = opener.call_args.args[0]
        payload = urllib.parse.parse_qs(
            request.data.decode("utf-8")
        )
        self.assertEqual(payload["target"], ["6281234567890"])
        self.assertEqual(payload["message"], ["Halo SmartLib"])
        self.assertEqual(payload["countryCode"], ["62"])
        self.assertEqual(
            request.get_header("Authorization"), TEST_TOKEN
        )
        self.assertNotIn(TEST_TOKEN, request.data.decode("utf-8"))

    def test_respons_ditolak_menjadi_api_error(self):
        opener = Mock(
            return_value=FakeResponse(
                {
                    "status": False,
                    "reason": f"token {TEST_TOKEN} tidak valid",
                }
            )
        )
        with self.assertLogs(
            "apps.whatsapp.services", level="WARNING"
        ) as logs:
            with self.assertRaises(WhatsAppAPIError) as caught:
                FonnteClient(TEST_TOKEN, opener=opener).send_message(
                    "081234567890", "Halo"
                )
        self.assertNotIn(TEST_TOKEN, str(caught.exception))
        self.assertNotIn(TEST_TOKEN, "\n".join(logs.output))

    def test_http_error_tidak_membocorkan_token(self):
        opener = Mock(
            side_effect=http_error(
                401,
                {"status": False, "reason": "unauthorized"},
            )
        )
        with self.assertLogs(
            "apps.whatsapp.services", level="WARNING"
        ) as logs:
            with self.assertRaises(WhatsAppAPIError) as caught:
                FonnteClient(TEST_TOKEN, opener=opener).send_message(
                    "081234567890", "Halo"
                )
        self.assertEqual(caught.exception.code, 401)
        self.assertNotIn(TEST_TOKEN, str(caught.exception))
        self.assertNotIn(TEST_TOKEN, "\n".join(logs.output))

    def test_timeout_langsung(self):
        opener = Mock(side_effect=TimeoutError("timed out"))
        with self.assertRaises(WhatsAppTimeoutError):
            FonnteClient(TEST_TOKEN, opener=opener).send_message(
                "081234567890", "Halo"
            )

    def test_timeout_dalam_urlerror(self):
        opener = Mock(
            side_effect=urllib.error.URLError(TimeoutError("timed out"))
        )
        with self.assertRaises(WhatsAppTimeoutError):
            FonnteClient(TEST_TOKEN, opener=opener).send_message(
                "081234567890", "Halo"
            )

    def test_kegagalan_jaringan(self):
        opener = Mock(
            side_effect=urllib.error.URLError(OSError("refused"))
        )
        with self.assertLogs(
            "apps.whatsapp.services", level="WARNING"
        ) as logs:
            with self.assertRaises(WhatsAppNetworkError):
                FonnteClient(TEST_TOKEN, opener=opener).send_message(
                    "081234567890", "Halo"
                )
        self.assertNotIn(TEST_TOKEN, "\n".join(logs.output))

    def test_respons_bukan_json(self):
        response = FakeResponse({})
        response._body = b"bukan json"
        opener = Mock(return_value=response)
        with self.assertRaises(WhatsAppResponseError):
            FonnteClient(TEST_TOKEN, opener=opener).send_message(
                "081234567890", "Halo"
            )

    def test_phone_dan_pesan_tidak_valid(self):
        client = FonnteClient(TEST_TOKEN, opener=Mock())
        with self.assertRaises(WhatsAppValidationError):
            client.send_message("021234567", "Halo")
        with self.assertRaises(WhatsAppValidationError):
            client.send_message("081234567890", "   ")

    def test_pesan_panjang_dipotong(self):
        opener = Mock(
            return_value=FakeResponse({"status": True, "id": "1"})
        )
        FonnteClient(TEST_TOKEN, opener=opener).send_message(
            "081234567890", "x" * 4100
        )
        request = opener.call_args.args[0]
        payload = urllib.parse.parse_qs(
            request.data.decode("utf-8")
        )
        message = payload["message"][0]
        self.assertLessEqual(len(message), 4000)
        self.assertTrue(message.endswith("..."))

    def test_url_wajib_https(self):
        with self.assertRaises(WhatsAppConfigError):
            FonnteClient(
                TEST_TOKEN,
                api_url="http://api.fonnte.com/send",
                opener=Mock(),
            )

    def test_waktu_habis_harus_positif(self):
        with self.assertRaises(WhatsAppConfigError):
            FonnteClient(TEST_TOKEN, timeout=0, opener=Mock())

    def test_token_kosong_ditolak(self):
        with self.assertRaises(WhatsAppConfigError):
            FonnteClient("", opener=Mock())


@override_settings(
    FONNTE_API_TOKEN=TEST_TOKEN,
    FONNTE_API_URL="https://api.fonnte.com/send",
    FONNTE_COUNTRY_CODE="62",
    FONNTE_TIMEOUT_SECONDS=5,
    WHATSAPP_NOTIFICATIONS_ENABLED=True,
)
class ServiceSettingsTests(SimpleTestCase):
    def test_get_client_memakai_settings(self):
        client = get_client()
        self.assertIsInstance(client, FonnteClient)
        self.assertEqual(client.timeout, 5)

    @override_settings(FONNTE_API_TOKEN="")
    def test_token_kosong_tidak_bisa_membuat_client(self):
        with self.assertRaises(WhatsAppConfigError):
            get_client()

    @override_settings(WHATSAPP_NOTIFICATIONS_ENABLED=False)
    def test_pengiriman_dinonaktifkan(self):
        with self.assertRaises(WhatsAppConfigError):
            send_text_message("081234567890", "Halo")

    def test_send_text_message_memakai_client(self):
        client = Mock()
        client.send_message.return_value = SentMessage(
            provider_message_id="1", phone="6281234567890"
        )
        result = send_text_message(
            "081234567890", "Halo", client=client
        )
        self.assertEqual(result.provider_message_id, "1")
        client.send_message.assert_called_once_with(
            "081234567890", "Halo"
        )
