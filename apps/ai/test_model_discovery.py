import json
import socket
import urllib.error
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.ai.configuration import (
    ConfigurationError,
    discover_models,
    encrypt_api_key,
    model_discovery_message,
)
from apps.ai.models import AIConfiguration
from apps.ai.providers import AIProviderError, parse_model_catalog
from apps.ai.test_configuration import (
    EMPTY_AI_SETTINGS,
    FakeResponse,
    http_error,
    make_policy,
    make_saved_config,
    make_user,
)

User = get_user_model()
MODELS_URL = reverse("core:ai_config_models")
POLICY_URL = reverse("core:policy")
TEST_URL = reverse("core:ai_config_test")

PUBLIC_ADDRESS = "93.184.216.34"


class RawResponse:
    status = 200

    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


class FakeOpener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def open(self, request, timeout=None):
        self.calls.append((request, timeout))
        if self.error is not None:
            raise self.error
        return self.response


def dns_result(address=PUBLIC_ADDRESS):
    return [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            6,
            "",
            (address, 443),
        )
    ]


def patch_dns(address=PUBLIC_ADDRESS):
    return mock.patch(
        "apps.ai.configuration.socket.getaddrinfo",
        return_value=dns_result(address),
    )


def patch_opener(response=None, error=None):
    if response is None and error is None:
        response = FakeResponse({"data": []})
    opener = FakeOpener(response=response, error=error)
    return opener, mock.patch(
        "apps.ai.configuration._discovery_opener",
        return_value=opener,
    )


class ParseModelCatalogTests(TestCase):
    def test_parses_sorts_dedupes_and_keeps_official_metadata(self):
        body = json.dumps(
            {
                "data": [
                    {"id": "b-model", "owned_by": "vendor-b"},
                    {
                        "id": "A-model",
                        "name": "Alpha",
                        "owned_by": "vendor-a",
                    },
                    {"id": "b-model", "owned_by": "ignored"},
                    {"id": "a-model"},
                ]
            }
        )
        models = parse_model_catalog(body)
        self.assertEqual(
            [model["id"] for model in models],
            ["A-model", "a-model", "b-model"],
        )
        self.assertEqual(models[0]["name"], "Alpha")
        self.assertEqual(models[0]["owned_by"], "vendor-a")
        self.assertEqual(models[1]["name"], "a-model")
        self.assertEqual(models[1]["owned_by"], "")
        self.assertEqual(models[2]["owned_by"], "vendor-b")

    def test_accepts_empty_catalog(self):
        self.assertEqual(parse_model_catalog('{"data": []}'), [])

    def test_rejects_invalid_json(self):
        with self.assertRaises(AIProviderError) as ctx:
            parse_model_catalog("bukan-json")
        self.assertEqual(ctx.exception.kind, AIProviderError.MALFORMED)

    def test_rejects_wrong_top_level_structure(self):
        for payload in ([], {"models": []}, {"data": {}}):
            with self.subTest(payload=payload):
                with self.assertRaises(AIProviderError) as ctx:
                    parse_model_catalog(json.dumps(payload))
                self.assertEqual(
                    ctx.exception.kind, AIProviderError.MALFORMED
                )

    def test_rejects_malformed_items(self):
        payloads = (
            {"data": ["model-a"]},
            {"data": [{"id": ""}]},
            {"data": [{"id": "   "}]},
            {"data": [{"id": 123}]},
            {"data": [{"name": "tanpa-id"}]},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(AIProviderError) as ctx:
                    parse_model_catalog(json.dumps(payload))
                self.assertEqual(
                    ctx.exception.kind, AIProviderError.MALFORMED
                )


@override_settings(**EMPTY_AI_SETTINGS)
class DiscoverModelsTests(TestCase):
    def test_uses_get_models_with_auth_and_timeout(self):
        opener, opener_patch = patch_opener(
            response=FakeResponse({"data": [{"id": "model-a"}]})
        )
        with patch_dns(), opener_patch:
            models = discover_models(
                "https://provider.example/v1/", "key-form"
            )
        self.assertEqual(models[0]["id"], "model-a")
        request, timeout = opener.calls[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(
            request.full_url, "https://provider.example/v1/models"
        )
        self.assertEqual(request.headers["Authorization"], "Bearer key-form")
        self.assertIsNone(request.data)
        self.assertEqual(timeout, 5.0)

    def test_uses_saved_key_when_form_key_empty(self):
        make_saved_config()
        opener, opener_patch = patch_opener()
        with patch_dns(), opener_patch:
            discover_models("https://provider.example/v1", "")
        request, _ = opener.calls[0]
        self.assertEqual(request.headers["Authorization"], "Bearer key-lama")

    def test_uses_selected_provider_key_when_form_key_empty(self):
        make_saved_config()
        other = AIConfiguration.objects.create(
            name="Provider Lain",
            provider=AIConfiguration.PROVIDER_OPENAI_COMPATIBLE,
            base_url="https://other.example/v1",
            model="model-other",
            api_key_encrypted=encrypt_api_key("key-other"),
            is_enabled=False,
        )
        opener, opener_patch = patch_opener()
        with patch_dns(), opener_patch:
            discover_models(other.base_url, "", other.pk)
        request, _ = opener.calls[0]
        self.assertEqual(
            request.headers["Authorization"], "Bearer key-other"
        )

    def test_form_key_wins_over_saved_key(self):
        make_saved_config()
        opener, opener_patch = patch_opener()
        with patch_dns(), opener_patch:
            discover_models("https://provider.example/v1", "key-form")
        request, _ = opener.calls[0]
        self.assertEqual(request.headers["Authorization"], "Bearer key-form")

    def test_missing_key_does_not_use_environment_key(self):
        with override_settings(AI_API_KEY="key-env"):
            with self.assertRaises(AIProviderError) as ctx:
                discover_models("https://provider.example/v1", "")
        self.assertEqual(ctx.exception.kind, AIProviderError.NO_KEY)

    def test_discovery_timeout_is_separate_from_assistant_timeout(self):
        with override_settings(
            AI_TIMEOUT_SECONDS=30,
            AI_MODEL_DISCOVERY_TIMEOUT_SECONDS=42,
        ):
            opener, opener_patch = patch_opener()
            with patch_dns(), opener_patch:
                discover_models("https://provider.example/v1", "key-form")
        _, timeout = opener.calls[0]
        self.assertEqual(timeout, 42.0)

    def test_discovery_does_not_mutate_database(self):
        make_saved_config()
        before = AIConfiguration.objects.count()
        _, opener_patch = patch_opener()
        with patch_dns(), opener_patch:
            discover_models("https://provider.example/v1", "key-form")
        row = AIConfiguration.objects.get(pk=1)
        self.assertEqual(AIConfiguration.objects.count(), before)
        self.assertEqual(row.model, "model-lama")
        self.assertEqual(row.base_url, "https://provider.example/v1")

    def test_empty_catalog_is_successful(self):
        _, opener_patch = patch_opener(
            response=FakeResponse({"data": []})
        )
        with patch_dns(), opener_patch:
            models = discover_models(
                "https://provider.example/v1", "key-form"
            )
        self.assertEqual(models, [])

    def test_invalid_json_is_malformed(self):
        _, opener_patch = patch_opener(response=RawResponse(b"bukan-json"))
        with patch_dns(), opener_patch:
            with self.assertRaises(AIProviderError) as ctx:
                discover_models("https://provider.example/v1", "key-form")
        self.assertEqual(ctx.exception.kind, AIProviderError.MALFORMED)

    def test_error_categories(self):
        cases = (
            (http_error(401), AIProviderError.AUTH),
            (http_error(403), AIProviderError.AUTH),
            (http_error(429), AIProviderError.RATE_LIMITED),
            (http_error(408), AIProviderError.HTTP_TIMEOUT),
            (http_error(504), AIProviderError.HTTP_TIMEOUT),
            (http_error(400), AIProviderError.BAD_REQUEST),
            (http_error(404), AIProviderError.DISCOVERY_UNSUPPORTED),
            (http_error(405), AIProviderError.DISCOVERY_UNSUPPORTED),
            (http_error(501), AIProviderError.DISCOVERY_UNSUPPORTED),
            (http_error(500), AIProviderError.UNAVAILABLE),
            (socket.timeout(), AIProviderError.TIMEOUT),
            (ConnectionRefusedError("refused"), AIProviderError.UNAVAILABLE),
        )
        for error, kind in cases:
            with self.subTest(kind=kind):
                _, opener_patch = patch_opener(error=error)
                with patch_dns(), opener_patch:
                    with self.assertRaises(AIProviderError) as ctx:
                        discover_models(
                            "https://provider.example/v1", "key-form"
                        )
                self.assertEqual(ctx.exception.kind, kind)

    def test_failure_logs_do_not_include_key(self):
        with self.assertLogs("apps.ai.providers", level="WARNING") as logs:
            _, opener_patch = patch_opener(error=http_error(401))
            with patch_dns(), opener_patch:
                with self.assertRaises(AIProviderError):
                    discover_models(
                        "https://provider.example/v1",
                        "key-sangat-rahasia",
                    )
        output = "\n".join(logs.output)
        self.assertIn("operation=models", output)
        self.assertIn("category=auth", output)
        self.assertNotIn("key-sangat-rahasia", output)

    def test_discovery_unsupported_mentions_manual_fallback(self):
        message = model_discovery_message(
            AIProviderError.DISCOVERY_UNSUPPORTED
        )
        self.assertIn("manual", message.lower())
        self.assertIn("endpoint", message.lower())

    def test_ssrf_blocks_non_global_addresses(self):
        for address in ("10.0.0.5", "127.0.0.1", "169.254.169.254", "::1"):
            with self.subTest(address=address):
                with patch_dns(address):
                    with self.assertRaises(ConfigurationError) as ctx:
                        discover_models(
                            "https://internal.example/v1", "key-form"
                        )
                self.assertIn("internal", str(ctx.exception).lower())

    def test_ssrf_blocks_credentials_query_and_fragment(self):
        urls = (
            "https://user:pass@provider.example/v1",
            "https://provider.example/v1?token=abc",
            "https://provider.example/v1#frag",
        )
        for url in urls:
            with self.subTest(url=url):
                with self.assertRaises(ConfigurationError):
                    discover_models(url, "key-form")

    def test_ssrf_blocks_dns_failure(self):
        with mock.patch(
            "apps.ai.configuration.socket.getaddrinfo",
            side_effect=socket.gaierror(),
        ):
            with self.assertRaises(ConfigurationError) as ctx:
                discover_models("https://provider.example/v1", "key-form")
        self.assertIn("diselesaikan", str(ctx.exception).lower())

    def test_ssrf_allows_public_address(self):
        _, opener_patch = patch_opener()
        with patch_dns(), opener_patch:
            models = discover_models(
                "https://provider.example/v1", "key-form"
            )
        self.assertEqual(models, [])

    def test_ssrf_allows_explicit_allowlist_without_dns(self):
        with override_settings(
            AI_PROVIDER_ALLOWED_HOSTS=["provider.example"]
        ):
            with mock.patch(
                "apps.ai.configuration.socket.getaddrinfo",
                side_effect=AssertionError("DNS should not be called"),
            ):
                _, opener_patch = patch_opener()
                with opener_patch:
                    models = discover_models(
                        "https://provider.example/v1", "key-form"
                    )
        self.assertEqual(models, [])

    def test_rejects_non_http_scheme(self):
        with self.assertRaises(ConfigurationError):
            discover_models("ftp://provider.example/v1", "key-form")


@override_settings(**EMPTY_AI_SETTINGS)
class ModelDiscoveryViewTests(TestCase):
    def setUp(self):
        make_policy()
        self.pus = make_user("pus1", User.Role.PUSTAKAWAN)
        self.siswa = make_user("siswa1", User.Role.SISWA, nis="10001")
        self.guru = make_user("guru1", User.Role.GURU, nip="20001")

    def _post_data(self, **overrides):
        data = {
            "base_url": "https://provider.example/v1",
            "api_key": "key-baru",
        }
        data.update(overrides)
        return data

    def test_librarian_can_load_models(self):
        models = [
            {"id": "model-a", "name": "model-a", "owned_by": "vendor"},
            {"id": "model-b", "name": "model-b", "owned_by": ""},
        ]
        self.client.force_login(self.pus)
        with mock.patch(
            "apps.core.views.discover_models", return_value=models
        ) as discover:
            response = self.client.post(
                MODELS_URL,
                self._post_data(api_key="key-form"),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["models"], models)
        discover.assert_called_once_with(
            "https://provider.example/v1", "key-form", None
        )
        self.assertFalse(AIConfiguration.objects.exists())

    def test_new_provider_model_load_requires_its_own_key(self):
        make_saved_config()
        self.client.force_login(self.pus)
        with mock.patch(
            "apps.core.views.discover_models"
        ) as discover:
            response = self.client.post(
                MODELS_URL,
                {
                    "base_url": "https://baru.example/v1",
                    "api_key": "",
                    "create_new": "1",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("api_key", response.json()["errors"])
        discover.assert_not_called()

    def test_selected_configuration_id_is_forwarded(self):
        row = make_saved_config()
        self.client.force_login(self.pus)
        with mock.patch(
            "apps.core.views.discover_models", return_value=[]
        ) as discover:
            response = self.client.post(
                MODELS_URL,
                self._post_data(api_key="", configuration_id=row.pk),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        self.assertEqual(response.status_code, 200)
        discover.assert_called_once_with(
            "https://provider.example/v1", "", row.pk
        )

    def test_unauthorized_cannot_load_models(self):
        self.assertEqual(
            self.client.post(MODELS_URL, self._post_data()).status_code,
            302,
        )
        for user in (self.siswa, self.guru):
            self.client.force_login(user)
            with self.subTest(role=user.role):
                self.assertEqual(
                    self.client.post(
                        MODELS_URL, self._post_data()
                    ).status_code,
                    403,
                )
        self.assertFalse(AIConfiguration.objects.exists())

    def test_endpoint_requires_post_and_csrf(self):
        self.client.force_login(self.pus)
        self.assertEqual(self.client.get(MODELS_URL).status_code, 405)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.pus)
        self.assertEqual(
            csrf_client.post(MODELS_URL, self._post_data()).status_code,
            403,
        )

    def test_invalid_form_returns_errors(self):
        self.client.force_login(self.pus)
        with mock.patch("apps.core.views.discover_models") as discover:
            response = self.client.post(
                MODELS_URL,
                self._post_data(base_url=""),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertIn("base_url", response.json()["errors"])
        discover.assert_not_called()

    def test_failure_is_safe_and_does_not_save(self):
        self.client.force_login(self.pus)
        with mock.patch(
            "apps.core.views.discover_models",
            side_effect=AIProviderError(AIProviderError.AUTH),
        ):
            response = self.client.post(
                MODELS_URL,
                self._post_data(api_key="key-sangat-rahasia"),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["category"], AIProviderError.AUTH)
        self.assertIn("autentikasi", data["message"].lower())
        self.assertNotIn(
            "key-sangat-rahasia", response.content.decode("utf-8")
        )
        self.assertFalse(AIConfiguration.objects.exists())

    def test_empty_catalog_is_successful(self):
        self.client.force_login(self.pus)
        with mock.patch(
            "apps.core.views.discover_models", return_value=[]
        ):
            response = self.client.post(
                MODELS_URL,
                self._post_data(),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["count"], 0)
        self.assertIn("0 model", data["message"])

    def test_page_has_model_discovery_controls(self):
        self.client.force_login(self.pus)
        response = self.client.get(POLICY_URL)
        self.assertContains(response, 'id="ai-model-load"')
        self.assertContains(response, 'id="ai-model-search"')
        self.assertContains(response, 'id="ai-model-select"')
        self.assertContains(response, "Muat model")
        self.assertContains(response, "Pilih dari daftar model")

    def test_saved_model_is_preselected(self):
        make_saved_config(model="model-lama")
        self.client.force_login(self.pus)
        response = self.client.get(POLICY_URL)
        self.assertContains(response, 'value="model-lama"')

    def test_selected_model_is_passed_exactly_to_test(self):
        model_id = "nvidia/llama-3.1-nemotron-70b-instruct"
        self.client.force_login(self.pus)
        with mock.patch(
            "apps.ai.configuration.probe_configuration", return_value=None
        ) as probe:
            response = self.client.post(
                TEST_URL,
                {
                    "base_url": "https://provider.example/v1",
                    "model": model_id,
                    "api_key": "key-form",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(probe.call_args[0][2], model_id)

    def test_long_model_id_is_not_truncated(self):
        model_id = "m" * 120
        self.client.force_login(self.pus)
        with mock.patch(
            "apps.ai.configuration.probe_configuration", return_value=None
        ) as probe:
            response = self.client.post(
                TEST_URL,
                {
                    "base_url": "https://provider.example/v1",
                    "model": model_id,
                    "api_key": "key-form",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(probe.call_args[0][2], model_id)
