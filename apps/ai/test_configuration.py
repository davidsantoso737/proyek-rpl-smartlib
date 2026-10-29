import datetime
import io
import json
import socket
import urllib.error
from decimal import Decimal
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.ai.configuration import (
    SOURCE_DATABASE,
    SOURCE_ENVIRONMENT,
    ConfigurationError,
    configuration_summary,
    decrypt_api_key,
    encrypt_api_key,
    probe_configuration,
    resolve_ai_configuration,
    save_configuration,
    test_configuration,
    validation_message,
)
from apps.ai.models import AIConfiguration
from apps.ai.providers import (
    AIProviderError,
    OpenAICompatibleProvider,
    get_provider,
)
from apps.ai.services import ask
from apps.catalog.models import Book, BookCopy, Category
from apps.core.models import LibrarySetting

User = get_user_model()

POLICY_URL = reverse("core:policy")
SAVE_URL = reverse("core:ai_config_save")
TEST_URL = reverse("core:ai_config_test")
MODELS_URL = reverse("core:ai_config_models")
RESET_URL = reverse("core:ai_config_reset")

EMPTY_AI_SETTINGS = {
    "AI_PROVIDER_BASE_URL": "",
    "AI_API_KEY": "",
    "AI_MODEL": "",
    "AI_NODE_URL": "",
    "AI_NODE_SHARED_SECRET": "",
    "AI_SETTINGS_ENCRYPTION_KEY": "",
    "AI_TIMEOUT_SECONDS": 5,
    "AI_CONFIG_TEST_TIMEOUT_SECONDS": 5,
    "AI_MODEL_DISCOVERY_TIMEOUT_SECONDS": 5,
    "AI_PROVIDER_ALLOWED_HOSTS": [],
}


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


def http_error(code, body=b"{}"):
    return urllib.error.HTTPError(
        "https://provider.example/v1/chat/completions",
        code,
        "error",
        {},
        io.BytesIO(body),
    )


def make_user(username, role, password="secret123", **extra):
    defaults = {"full_name": f"{username} Full"}
    defaults.update(extra)
    user = User(username=username, role=role, **defaults)
    user.set_password(password)
    user.full_clean()
    user.save()
    return user


def make_policy():
    return LibrarySetting.objects.create(
        physical_loan_duration=7,
        ebook_access_duration=14,
        pickup_qr_validity=30,
        fine_rate_per_day=Decimal("1000"),
        effective_from=timezone.now(),
    )


def make_saved_config(**overrides):
    values = {
        "pk": 1,
        "provider": AIConfiguration.PROVIDER_OPENAI_COMPATIBLE,
        "base_url": "https://provider.example/v1",
        "model": "model-lama",
        "api_key_encrypted": encrypt_api_key("key-lama"),
        "is_enabled": True,
        "last_validated_at": timezone.now(),
    }
    values.update(overrides)
    return AIConfiguration.objects.create(**values)


@override_settings(**EMPTY_AI_SETTINGS)
class EncryptionTests(TestCase):
    def test_roundtrip_with_secret_key_derived_key(self):
        ciphertext = encrypt_api_key("nilai-rahasia")
        self.assertNotIn("nilai-rahasia", ciphertext)
        self.assertEqual(decrypt_api_key(ciphertext), "nilai-rahasia")

    def test_roundtrip_with_explicit_fernet_key(self):
        with override_settings(
            AI_SETTINGS_ENCRYPTION_KEY=Fernet.generate_key().decode()
        ):
            ciphertext = encrypt_api_key("nilai-rahasia")
            self.assertEqual(decrypt_api_key(ciphertext), "nilai-rahasia")

    def test_wrong_key_is_reported_as_unreadable(self):
        ciphertext = encrypt_api_key("nilai-rahasia")
        with override_settings(
            AI_SETTINGS_ENCRYPTION_KEY=Fernet.generate_key().decode()
        ):
            with self.assertRaises(ConfigurationError):
                decrypt_api_key(ciphertext)


@override_settings(**EMPTY_AI_SETTINGS)
class ConfigurationResolutionTests(TestCase):
    def test_database_wins_over_environment(self):
        make_saved_config()
        with override_settings(
            AI_PROVIDER_BASE_URL="https://env.example/v1",
            AI_API_KEY="key-env",
            AI_MODEL="model-env",
        ):
            resolved = resolve_ai_configuration()
        self.assertEqual(resolved.source, SOURCE_DATABASE)
        self.assertEqual(resolved.model, "model-lama")
        self.assertEqual(resolved.api_key, "key-lama")

    def test_environment_fallback_without_saved_config(self):
        with override_settings(
            AI_PROVIDER_BASE_URL="https://env.example/v1",
            AI_API_KEY="key-env",
            AI_MODEL="model-env",
        ):
            resolved = resolve_ai_configuration()
        self.assertEqual(resolved.source, SOURCE_ENVIRONMENT)
        self.assertEqual(resolved.model, "model-env")

    def test_disabled_saved_config_falls_back_to_environment(self):
        make_saved_config(is_enabled=False)
        with override_settings(
            AI_PROVIDER_BASE_URL="https://env.example/v1",
            AI_API_KEY="key-env",
            AI_MODEL="model-env",
        ):
            resolved = resolve_ai_configuration()
        self.assertEqual(resolved.source, SOURCE_ENVIRONMENT)

    def test_unreadable_saved_config_falls_back_to_environment(self):
        make_saved_config(api_key_encrypted="bukan-token-fernet")
        with override_settings(
            AI_PROVIDER_BASE_URL="https://env.example/v1",
            AI_API_KEY="key-env",
            AI_MODEL="model-env",
        ):
            resolved = resolve_ai_configuration()
        self.assertEqual(resolved.source, SOURCE_ENVIRONMENT)

    def test_unreadable_saved_config_is_reported(self):
        make_saved_config(api_key_encrypted="bukan-token-fernet")
        summary = configuration_summary()
        self.assertFalse(summary["saved_readable"])
        self.assertTrue(summary["saved_problem"])


@override_settings(**EMPTY_AI_SETTINGS)
class ProbeTests(TestCase):
    def setUp(self):
        dns = mock.patch(
            "apps.ai.configuration.socket.getaddrinfo",
            return_value=[
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    ("93.184.216.34", 443),
                )
            ],
        )
        dns.start()
        self.addCleanup(dns.stop)

    def test_probe_uses_small_bounded_request(self):
        with mock.patch(
            "apps.ai.providers.urllib.request.urlopen",
            return_value=FakeResponse({"choices": [{"message": {"content": "ok"}}]}),
        ) as urlopen:
            self.assertTrue(
                probe_configuration(
                    "https://provider.example/v1", "key-uji", "model-uji"
                )
            )
        request = urlopen.call_args[0][0]
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "model-uji")
        self.assertEqual(payload["max_tokens"], 1)
        self.assertEqual(len(payload["messages"]), 1)

    def test_probe_classifies_auth_error(self):
        for code in (401, 403):
            with self.subTest(code=code):
                with mock.patch(
                    "apps.ai.providers.urllib.request.urlopen",
                    side_effect=http_error(code),
                ):
                    with self.assertRaises(AIProviderError) as ctx:
                        probe_configuration(
                            "https://provider.example/v1",
                            "key-uji",
                            "model-uji",
                        )
                self.assertEqual(ctx.exception.kind, AIProviderError.AUTH)

    def test_probe_classifies_missing_model(self):
        with mock.patch(
            "apps.ai.providers.urllib.request.urlopen",
            side_effect=http_error(
                404, b'{"error":{"message":"model not found"}}'
            ),
        ):
            with self.assertRaises(AIProviderError) as ctx:
                probe_configuration(
                    "https://provider.example/v1", "key-uji", "model-uji"
                )
        self.assertEqual(
            ctx.exception.kind, AIProviderError.MODEL_UNAVAILABLE
        )

    def test_probe_classifies_rate_limit_and_outage(self):
        cases = (
            (http_error(429), AIProviderError.RATE_LIMITED),
            (http_error(500), AIProviderError.UNAVAILABLE),
            (socket.timeout(), AIProviderError.TIMEOUT),
        )
        for error, kind in cases:
            with self.subTest(kind=kind):
                with mock.patch(
                    "apps.ai.providers.urllib.request.urlopen",
                    side_effect=error,
                ):
                    with self.assertRaises(AIProviderError) as ctx:
                        probe_configuration(
                            "https://provider.example/v1",
                            "key-uji",
                            "model-uji",
                        )
                self.assertEqual(ctx.exception.kind, kind)

    def test_probe_distinguishes_socket_timeout_from_http_timeout(self):
        for code in (408, 504):
            with self.subTest(code=code):
                with mock.patch(
                    "apps.ai.providers.urllib.request.urlopen",
                    side_effect=http_error(code),
                ):
                    with self.assertRaises(AIProviderError) as ctx:
                        probe_configuration(
                            "https://provider.example/v1",
                            "key-uji",
                            "model-uji",
                        )
                self.assertEqual(
                    ctx.exception.kind, AIProviderError.HTTP_TIMEOUT
                )

    def test_probe_classifies_bad_request_and_unavailable_404(self):
        cases = (
            (
                http_error(
                    400, b'{"error":{"message":"model is not supported"}}'
                ),
                AIProviderError.MODEL_UNAVAILABLE,
            ),
            (
                http_error(400, b'{"error":{"message":"invalid payload"}}'),
                AIProviderError.BAD_REQUEST,
            ),
            (
                http_error(404, b'{"error":{"message":"not found"}}'),
                AIProviderError.UNAVAILABLE,
            ),
        )
        for error, kind in cases:
            with self.subTest(kind=kind):
                with mock.patch(
                    "apps.ai.providers.urllib.request.urlopen",
                    side_effect=error,
                ):
                    with self.assertRaises(AIProviderError) as ctx:
                        probe_configuration(
                            "https://provider.example/v1",
                            "key-uji",
                            "model-uji",
                        )
                self.assertEqual(ctx.exception.kind, kind)

    def test_probe_classifies_connection_refused(self):
        with mock.patch(
            "apps.ai.providers.urllib.request.urlopen",
            side_effect=ConnectionRefusedError("refused"),
        ):
            with self.assertRaises(AIProviderError) as ctx:
                probe_configuration(
                    "https://provider.example/v1", "key-uji", "model-uji"
                )
        self.assertEqual(ctx.exception.kind, AIProviderError.UNAVAILABLE)

    def test_probe_blocks_private_provider_address(self):
        with mock.patch(
            "apps.ai.configuration.socket.getaddrinfo",
            return_value=[
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    ("10.0.0.5", 443),
                )
            ],
        ):
            with self.assertRaises(ConfigurationError) as ctx:
                probe_configuration(
                    "https://internal.example/v1",
                    "key-uji",
                    "model-uji",
                )
        self.assertIn("internal", str(ctx.exception).lower())

    def test_probe_uses_configured_timeout(self):
        with override_settings(AI_CONFIG_TEST_TIMEOUT_SECONDS=42):
            with mock.patch(
                "apps.ai.configuration.OpenAICompatibleProvider"
            ) as provider_cls:
                provider_cls.return_value.probe.return_value = True
                self.assertTrue(
                    probe_configuration(
                        "https://provider.example/v1",
                        "key-uji",
                        "model-uji",
                    )
                )
        provider_cls.assert_called_once_with(
            "https://provider.example/v1",
            "key-uji",
            "model-uji",
            42.0,
        )

    def test_probe_logs_safe_http_diagnostics(self):
        with self.assertLogs("apps.ai.providers", level="WARNING") as logs:
            with mock.patch(
                "apps.ai.providers.urllib.request.urlopen",
                side_effect=http_error(504),
            ):
                with self.assertRaises(AIProviderError):
                    probe_configuration(
                        "https://provider.example/v1",
                        "key-uji",
                        "model-uji",
                    )
        output = "\n".join(logs.output)
        self.assertIn("status=504", output)
        self.assertIn("duration_ms=", output)
        self.assertIn("exception=HTTPError", output)
        self.assertIn("category=http_timeout", output)
        self.assertNotIn("key-uji", output)

    def test_probe_logs_socket_timeout_diagnostics(self):
        with self.assertLogs("apps.ai.providers", level="WARNING") as logs:
            with mock.patch(
                "apps.ai.providers.urllib.request.urlopen",
                side_effect=socket.timeout(),
            ):
                with self.assertRaises(AIProviderError):
                    probe_configuration(
                        "https://provider.example/v1",
                        "key-uji",
                        "model-uji",
                    )
        output = "\n".join(logs.output)
        self.assertIn("status=none", output)
        self.assertIn("duration_ms=", output)
        self.assertIn("exception=TimeoutError", output)
        self.assertIn("category=timeout", output)
        self.assertNotIn("key-uji", output)

    def test_http_timeout_has_distinct_safe_message(self):
        self.assertNotEqual(
            validation_message(AIProviderError.TIMEOUT),
            validation_message(AIProviderError.HTTP_TIMEOUT),
        )
        self.assertIn(
            "gateway",
            validation_message(AIProviderError.HTTP_TIMEOUT).lower(),
        )


@override_settings(**EMPTY_AI_SETTINGS)
class SaveConfigurationTests(TestCase):
    def setUp(self):
        self.pus = make_user("pus1", User.Role.PUSTAKAWAN)

    def test_save_requires_successful_probe(self):
        with mock.patch(
            "apps.ai.configuration.probe_configuration",
            side_effect=AIProviderError(AIProviderError.AUTH),
        ):
            with self.assertRaises(AIProviderError):
                save_configuration(
                    self.pus,
                    "https://provider.example/v1",
                    "model-baru",
                    "key-baru",
                )
        self.assertFalse(AIConfiguration.objects.exists())

    def test_failed_probe_preserves_previous_configuration(self):
        make_saved_config()
        with mock.patch(
            "apps.ai.configuration.probe_configuration",
            side_effect=AIProviderError(AIProviderError.UNAVAILABLE),
        ):
            with self.assertRaises(AIProviderError):
                save_configuration(
                    self.pus,
                    "https://provider.example/v1",
                    "model-baru",
                    "key-baru",
                )
        row = AIConfiguration.objects.get(pk=1)
        self.assertEqual(row.model, "model-lama")
        self.assertEqual(decrypt_api_key(row.api_key_encrypted), "key-lama")

    def test_empty_api_key_preserves_existing_key(self):
        make_saved_config()
        with mock.patch(
            "apps.ai.configuration.probe_configuration", return_value=None
        ):
            save_configuration(
                self.pus,
                "https://provider.example/v2",
                "model-baru",
                "",
            )
        row = AIConfiguration.objects.get(pk=1)
        self.assertEqual(row.model, "model-baru")
        self.assertEqual(row.base_url, "https://provider.example/v2")
        self.assertEqual(decrypt_api_key(row.api_key_encrypted), "key-lama")

    def test_new_key_is_encrypted_at_rest(self):
        with mock.patch(
            "apps.ai.configuration.probe_configuration", return_value=None
        ):
            save_configuration(
                self.pus,
                "https://provider.example/v1",
                "model-baru",
                "key-baru",
            )
        row = AIConfiguration.objects.get(pk=1)
        self.assertNotIn("key-baru", row.api_key_encrypted)
        self.assertEqual(decrypt_api_key(row.api_key_encrypted), "key-baru")

    def test_disabled_save_skips_probe(self):
        with mock.patch(
            "apps.ai.configuration.probe_configuration"
        ) as probe:
            save_configuration(
                self.pus,
                "https://provider.example/v1",
                "model-baru",
                "key-baru",
                enabled=False,
            )
        probe.assert_not_called()
        self.assertFalse(AIConfiguration.objects.get(pk=1).is_enabled)


@override_settings(**EMPTY_AI_SETTINGS)
class ConfigurationViewTests(TestCase):
    def setUp(self):
        make_policy()
        self.pus = make_user("pus1", User.Role.PUSTAKAWAN)
        self.siswa = make_user("siswa1", User.Role.SISWA, nis="10001")
        self.guru = make_user("guru1", User.Role.GURU, nip="20001")
        category = Category.objects.create(name="Pemrograman")
        book = Book.objects.create(
            category=category, isbn="ISBN-CONF-1", title="Belajar Python"
        )
        BookCopy.objects.create(
            book=book,
            copy_code="CONF-001",
            status="TERSEDIA",
            acquired_at=datetime.date(2026, 1, 10),
            condition="BAIK",
            location="RAK-A1",
        )

    def _post_data(self, **overrides):
        data = {
            "base_url": "https://provider.example/v1",
            "model": "model-baru",
            "api_key": "key-baru",
            "is_enabled": "on",
        }
        data.update(overrides)
        return data

    def test_librarian_can_open_configuration(self):
        self.client.force_login(self.pus)
        response = self.client.get(POLICY_URL)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AI Assistant")
        self.assertContains(response, "Konfigurasi")

    def test_unauthorized_cannot_read_or_mutate(self):
        self.assertEqual(self.client.get(POLICY_URL).status_code, 302)
        for user in (self.siswa, self.guru):
            self.client.force_login(user)
            with self.subTest(role=user.role):
                self.assertEqual(self.client.get(POLICY_URL).status_code, 403)
                self.assertEqual(
                    self.client.post(SAVE_URL, self._post_data()).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.post(TEST_URL, self._post_data()).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.post(
                        MODELS_URL,
                        {
                            "base_url": "https://provider.example/v1",
                            "api_key": "key-baru",
                        },
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.post(
                        RESET_URL, {"confirm": "1"}
                    ).status_code,
                    403,
                )
        self.assertFalse(AIConfiguration.objects.exists())

    def test_endpoints_require_post_and_csrf(self):
        self.client.force_login(self.pus)
        self.assertEqual(self.client.get(SAVE_URL).status_code, 405)
        self.assertEqual(self.client.get(TEST_URL).status_code, 405)
        self.assertEqual(self.client.get(MODELS_URL).status_code, 405)
        self.assertEqual(self.client.get(RESET_URL).status_code, 405)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.pus)
        self.assertEqual(
            csrf_client.post(SAVE_URL, self._post_data()).status_code, 403
        )
        self.assertEqual(
            csrf_client.post(MODELS_URL, self._post_data()).status_code, 403
        )
        self.assertFalse(AIConfiguration.objects.exists())

    def test_page_never_returns_saved_key(self):
        make_saved_config(api_key_encrypted=encrypt_api_key("key-rahasia"))
        self.client.force_login(self.pus)
        response = self.client.get(POLICY_URL)
        self.assertNotContains(response, "key-rahasia")
        self.assertContains(response, "API key tersimpan")

    def test_page_reports_environment_fallback(self):
        with override_settings(
            AI_PROVIDER_BASE_URL="https://env.example/v1",
            AI_API_KEY="key-env",
            AI_MODEL="model-env",
        ):
            self.client.force_login(self.pus)
            response = self.client.get(POLICY_URL)
        self.assertContains(response, "Aktif dari environment")
        self.assertContains(response, "model-env")
        self.assertNotContains(response, "key-env")

    def test_configuration_page_has_test_control(self):
        self.client.force_login(self.pus)
        response = self.client.get(POLICY_URL)
        self.assertContains(response, 'id="ai-config-test"')
        self.assertContains(response, "Uji konfigurasi")

    def test_save_endpoint_validates_before_saving(self):
        self.client.force_login(self.pus)
        with mock.patch(
            "apps.ai.configuration.probe_configuration", return_value=None
        ):
            response = self.client.post(
                SAVE_URL,
                self._post_data(),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        row = AIConfiguration.objects.get(pk=1)
        self.assertEqual(row.model, "model-baru")
        self.assertEqual(decrypt_api_key(row.api_key_encrypted), "key-baru")
        self.assertEqual(row.updated_by, self.pus)

    def test_save_failure_does_not_replace_active_configuration(self):
        make_saved_config()
        self.client.force_login(self.pus)
        with mock.patch(
            "apps.ai.configuration.probe_configuration",
            side_effect=AIProviderError(AIProviderError.AUTH),
        ):
            response = self.client.post(
                SAVE_URL,
                self._post_data(),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        row = AIConfiguration.objects.get(pk=1)
        self.assertEqual(row.model, "model-lama")
        self.assertEqual(decrypt_api_key(row.api_key_encrypted), "key-lama")

    def test_test_endpoint_never_returns_key_or_saves(self):
        self.client.force_login(self.pus)
        with mock.patch(
            "apps.ai.configuration.probe_configuration", return_value=None
        ):
            response = self.client.post(
                TEST_URL,
                self._post_data(api_key="key-sangat-rahasia"),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertNotIn("key-sangat-rahasia", body)
        self.assertFalse(AIConfiguration.objects.exists())

    def test_test_endpoint_uses_saved_key_when_field_empty(self):
        make_saved_config()
        self.client.force_login(self.pus)
        with mock.patch(
            "apps.ai.configuration.probe_configuration"
        ) as probe:
            response = self.client.post(
                TEST_URL,
                self._post_data(api_key=""),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(probe.call_args[0][1], "key-lama")

    def test_test_endpoint_uses_form_key(self):
        self.client.force_login(self.pus)
        with mock.patch(
            "apps.ai.configuration.probe_configuration"
        ) as probe:
            response = self.client.post(
                TEST_URL,
                self._post_data(api_key="key-form"),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(probe.call_args[0][1], "key-form")
        self.assertFalse(AIConfiguration.objects.exists())

    def test_test_endpoint_returns_safe_http_timeout_category(self):
        self.client.force_login(self.pus)
        with mock.patch(
            "apps.ai.configuration.probe_configuration",
            side_effect=AIProviderError(AIProviderError.HTTP_TIMEOUT),
        ):
            response = self.client.post(
                TEST_URL,
                self._post_data(api_key="key-sangat-rahasia"),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["category"], AIProviderError.HTTP_TIMEOUT)
        self.assertIn("gateway", data["message"].lower())
        self.assertNotIn(
            "key-sangat-rahasia", response.content.decode("utf-8")
        )
        self.assertFalse(AIConfiguration.objects.exists())

    def test_test_endpoint_returns_success_message(self):
        self.client.force_login(self.pus)
        with mock.patch(
            "apps.ai.configuration.probe_configuration", return_value=True
        ):
            response = self.client.post(
                TEST_URL,
                self._post_data(api_key="key-form"),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertIn("berhasil", data["message"].lower())
        self.assertFalse(AIConfiguration.objects.exists())

    def test_empty_key_submission_preserves_saved_key(self):
        make_saved_config()
        self.client.force_login(self.pus)
        with mock.patch(
            "apps.ai.configuration.probe_configuration", return_value=None
        ):
            response = self.client.post(
                SAVE_URL,
                self._post_data(api_key=""),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            decrypt_api_key(AIConfiguration.objects.get(pk=1).api_key_encrypted),
            "key-lama",
        )

    def test_reset_requires_confirmation(self):
        make_saved_config()
        self.client.force_login(self.pus)
        response = self.client.post(RESET_URL, {})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(AIConfiguration.objects.exists())
        response = self.client.post(RESET_URL, {"confirm": "1"})
        self.assertRedirects(response, POLICY_URL)
        self.assertFalse(AIConfiguration.objects.exists())

    def test_saved_configuration_is_used_by_subsequent_ai_request(self):
        self.client.force_login(self.pus)
        with mock.patch(
            "apps.ai.configuration.probe_configuration", return_value=None
        ):
            self.client.post(
                SAVE_URL,
                self._post_data(model="model-baru", api_key="key-baru"),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        with mock.patch(
            "apps.ai.providers.urllib.request.urlopen",
            return_value=FakeResponse(
                {"choices": [{"message": {"content": "Jawaban dari model baru."}}]}
            ),
        ) as urlopen:
            result = ask(self.siswa, "Apakah ada buku Python?")
        self.assertTrue(result["provider_used"])
        self.assertEqual(result["answer"], "Jawaban dari model baru.")
        request = urlopen.call_args[0][0]
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "model-baru")
        self.assertEqual(
            request.headers["Authorization"], "Bearer key-baru"
        )

    def test_save_requires_valid_model_id(self):
        self.client.force_login(self.pus)
        response = self.client.post(
            SAVE_URL,
            self._post_data(model=""),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(AIConfiguration.objects.exists())

    def test_save_rejects_url_with_credentials(self):
        self.client.force_login(self.pus)
        response = self.client.post(
            SAVE_URL,
            self._post_data(base_url="https://user:pass@provider.example/v1"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(AIConfiguration.objects.exists())

    def test_policy_update_still_creates_new_version(self):
        self.client.force_login(self.pus)
        response = self.client.post(
            POLICY_URL,
            {
                "physical_loan_duration": 10,
                "ebook_access_duration": 14,
                "pickup_qr_validity": 30,
                "fine_rate_per_day": "1500",
            },
        )
        self.assertRedirects(response, POLICY_URL)
        self.assertEqual(LibrarySetting.objects.count(), 2)
        self.assertEqual(
            LibrarySetting.objects.order_by("-effective_from").first().physical_loan_duration,
            10,
        )


@override_settings(**EMPTY_AI_SETTINGS)
class ProviderSelectionTests(TestCase):
    def test_get_provider_uses_saved_model_and_key(self):
        make_saved_config(model="model-tersimpan")
        provider = get_provider()
        self.assertIsInstance(provider, OpenAICompatibleProvider)
        self.assertEqual(provider.model, "model-tersimpan")
        self.assertEqual(provider.base_url, "https://provider.example/v1")
        self.assertEqual(provider.api_key, "key-lama")

    def test_get_provider_is_none_without_any_configuration(self):
        self.assertIsNone(get_provider())

    def test_test_configuration_without_any_key_fails_safely(self):
        with self.assertRaises(AIProviderError) as ctx:
            test_configuration(
                "https://provider.example/v1", "model-baru", ""
            )
        self.assertEqual(ctx.exception.kind, AIProviderError.NO_KEY)
