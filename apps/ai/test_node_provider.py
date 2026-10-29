import io
import json
import socket
import urllib.error
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.ai.providers import (
    AIProviderError,
    NodeProvider,
    OpenAICompatibleProvider,
    get_provider,
)
from apps.ai.services import ask

User = get_user_model()

NODE_URL = "http://127.0.0.1:3101"
NODE_SECRET = "unit-test-secret"


class FakeNodeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def _http_error(code):
    return urllib.error.HTTPError(
        f"{NODE_URL}/api/complete", code, "err", {}, io.BytesIO(b"{}")
    )


@override_settings(
    AI_PROVIDER_BASE_URL="", AI_API_KEY="", AI_MODEL="",
    AI_NODE_URL="", AI_NODE_SHARED_SECRET="",
)
class NodeProviderTests(TestCase):
    def _make_user(self, username="siswa1", role=None, **extra):
        role = role or User.Role.SISWA
        defaults = {"full_name": f"{username} Full", "nis": "90001"}
        defaults.update(extra)
        user = User(username=username, role=role, **defaults)
        user.set_password("secret123")
        user.full_clean()
        user.save()
        return user

    def test_success_mengembalikan_teks(self):
        provider = NodeProvider(NODE_URL, NODE_SECRET, 5)
        with mock.patch(
            "apps.ai.providers.urllib.request.urlopen",
            return_value=FakeNodeResponse({"text": "  Halo node.  "}),
        ):
            self.assertEqual(
                provider.complete("sys", "halo", {"a": 1}), "Halo node."
            )

    def test_pemetaan_error(self):
        provider = NodeProvider(NODE_URL, NODE_SECRET, 5)
        cases = (
            (_http_error(429), AIProviderError.RATE_LIMITED),
            (_http_error(408), AIProviderError.HTTP_TIMEOUT),
            (_http_error(504), AIProviderError.HTTP_TIMEOUT),
            (_http_error(500), AIProviderError.UNAVAILABLE),
            (_http_error(401), AIProviderError.UNAVAILABLE),
        )
        for error, kind in cases:
            with self.subTest(code=error.code):
                with mock.patch(
                    "apps.ai.providers.urllib.request.urlopen",
                    side_effect=error,
                ):
                    with self.assertRaises(AIProviderError) as ctx:
                        provider.complete("sys", "halo", {})
                    self.assertEqual(ctx.exception.kind, kind)
        with mock.patch(
            "apps.ai.providers.urllib.request.urlopen",
            side_effect=urllib.error.URLError("down"),
        ):
            with self.assertRaises(AIProviderError) as ctx:
                provider.complete("sys", "halo", {})
            self.assertEqual(ctx.exception.kind, AIProviderError.UNAVAILABLE)
        with mock.patch(
            "apps.ai.providers.urllib.request.urlopen",
            side_effect=socket.timeout(),
        ):
            with self.assertRaises(AIProviderError) as ctx:
                provider.complete("sys", "halo", {})
            self.assertEqual(ctx.exception.kind, AIProviderError.TIMEOUT)

    def test_malformed(self):
        provider = NodeProvider(NODE_URL, NODE_SECRET, 5)
        for payload in ({"text": ""}, {"text": "   "}, {"nope": 1}, {"text": 5}):
            with self.subTest(payload=payload):
                with mock.patch(
                    "apps.ai.providers.urllib.request.urlopen",
                    return_value=FakeNodeResponse(payload),
                ):
                    with self.assertRaises(AIProviderError) as ctx:
                        provider.complete("sys", "halo", {})
                    self.assertEqual(
                        ctx.exception.kind, AIProviderError.MALFORMED
                    )

    def test_paritas_dengan_adapter_langsung(self):
        direct = OpenAICompatibleProvider("http://x", "k", "m", 5)
        node = NodeProvider(NODE_URL, NODE_SECRET, 5)
        upstream = {
            "choices": [{"message": {"content": "  Sama.  "}}],
        }
        with mock.patch(
            "apps.ai.providers.urllib.request.urlopen",
            return_value=FakeNodeResponse(upstream),
        ):
            direct_text = direct.complete("sys", "halo", {})
        with mock.patch(
            "apps.ai.providers.urllib.request.urlopen",
            return_value=FakeNodeResponse({"text": "  Sama.  "}),
        ):
            node_text = node.complete("sys", "halo", {})
        self.assertEqual(direct_text, node_text)

    def test_get_provider_cabang_node(self):
        with override_settings(
            AI_NODE_URL=NODE_URL, AI_NODE_SHARED_SECRET=NODE_SECRET
        ):
            self.assertIsInstance(get_provider(), NodeProvider)

    def test_get_provider_tetap_lokal_tanpa_konfigurasi(self):
        self.assertIsNone(get_provider())

    def test_node_mati_jatuh_ke_jawaban_lokal(self):
        user = self._make_user()
        with override_settings(
            AI_NODE_URL=NODE_URL, AI_NODE_SHARED_SECRET=NODE_SECRET
        ):
            with mock.patch(
                "apps.ai.providers.urllib.request.urlopen",
                side_effect=urllib.error.URLError("node down"),
            ):
                result = ask(user, "Halo!")
                self.assertEqual(result["source"], "smalltalk")
                catalog = ask(user, "Apakah ada buku Python?")
                # Provider dicoba lalu gagal -> fallback lokal yang aman.
                self.assertTrue(catalog["answer"])
                self.assertIn(catalog["source"], ("local", "fallback"))
