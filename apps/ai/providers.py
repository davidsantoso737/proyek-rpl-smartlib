"""Batas provider AI: interface + adapter generik (AI_RULES §8).

Business logic dilarang mengikat langsung ke SDK/API provider.
Satu-satunya adapter memakai stdlib (urllib) agar stack tidak berubah.
API key berasal dari konfigurasi aktif dan tidak pernah di-hardcode,
log, atau respons.
"""

import json
import logging
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class AIProviderError(Exception):
    """Kegagalan provider dengan kategori aman untuk pesan user."""

    TIMEOUT = "timeout"
    HTTP_TIMEOUT = "http_timeout"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    MALFORMED = "malformed"
    NO_KEY = "no_key"
    AUTH = "auth"
    MODEL_UNAVAILABLE = "model_unavailable"
    BAD_REQUEST = "bad_request"
    DISCOVERY_UNSUPPORTED = "discovery_unsupported"

    def __init__(self, kind, detail=""):
        self.kind = kind
        super().__init__(detail or kind)


class AIProvider(ABC):
    """Interface service AI: satu pertanyaan + konteks -> teks jawaban."""

    @abstractmethod
    def complete(self, system_prompt, user_text, context):
        """Kembalikan jawaban string; raise AIProviderError bila gagal."""


def parse_model_catalog(body):
    """Validasi respons OpenAI-compatible ``/models`` menjadi daftar unik."""
    try:
        data = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise AIProviderError(AIProviderError.MALFORMED) from exc
    if not isinstance(data, dict):
        raise AIProviderError(AIProviderError.MALFORMED)
    items = data.get("data")
    if not isinstance(items, list):
        raise AIProviderError(AIProviderError.MALFORMED)

    models = {}
    for item in items:
        if not isinstance(item, dict):
            raise AIProviderError(AIProviderError.MALFORMED)
        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id.strip():
            raise AIProviderError(AIProviderError.MALFORMED)
        model_id = model_id.strip()
        if model_id in models:
            continue
        name = item.get("name")
        owned_by = item.get("owned_by")
        models[model_id] = {
            "id": model_id,
            "name": (
                name.strip()
                if isinstance(name, str) and name.strip()
                else model_id
            ),
            "owned_by": (
                owned_by.strip() if isinstance(owned_by, str) else ""
            ),
        }
    return sorted(
        models.values(), key=lambda model: (model["id"].casefold(), model["id"])
    )


class OpenAICompatibleProvider(AIProvider):
    """Adapter endpoint chat/completions generik (provider TBD via env)."""

    def __init__(self, base_url, api_key, model, timeout, opener=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.opener = opener

    @staticmethod
    def _error_kind(exc, *, discovery=False):
        """Kategori aman dari HTTPError tanpa menampilkan isi respons."""
        code = getattr(exc, "code", None)
        if discovery and code in (404, 405, 501):
            return AIProviderError.DISCOVERY_UNSUPPORTED
        if code in (401, 403):
            return AIProviderError.AUTH
        if code == 429:
            return AIProviderError.RATE_LIMITED
        if code in (408, 504):
            return AIProviderError.HTTP_TIMEOUT
        if code in (400, 404):
            try:
                detail = exc.read(4096).decode("utf-8", "replace").lower()
            except Exception:
                detail = ""
            if "model" in detail:
                return AIProviderError.MODEL_UNAVAILABLE
            if code == 404:
                return AIProviderError.UNAVAILABLE
            return AIProviderError.BAD_REQUEST
        if isinstance(code, int) and code >= 500:
            return AIProviderError.UNAVAILABLE
        return AIProviderError.UNAVAILABLE

    def _log_failure(self, host, started, status, exc, kind, operation):
        logger.warning(
            "AI provider request failed: operation=%s model=%s host=%s "
            "timeout=%ss duration_ms=%.1f status=%s exception=%s category=%s",
            operation,
            self.model or "-",
            host,
            self.timeout,
            (time.perf_counter() - started) * 1000,
            status if status is not None else "none",
            type(exc).__name__,
            kind,
        )

    def _request(self, method, path, payload=None, operation="request"):
        data = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            host = urllib.parse.urlsplit(self.base_url).hostname or "unknown"
        except ValueError:
            host = "unknown"
        started = time.perf_counter()
        try:
            if self.opener is None:
                response = urllib.request.urlopen(
                    request, timeout=self.timeout
                )
            else:
                response = self.opener.open(request, timeout=self.timeout)
            with response as resp:
                body = resp.read().decode("utf-8")
                logger.debug(
                    "AI provider request succeeded: operation=%s model=%s "
                    "host=%s duration_ms=%.1f status=%s",
                    operation,
                    self.model or "-",
                    host,
                    (time.perf_counter() - started) * 1000,
                    getattr(resp, "status", "unknown"),
                )
                return body
        except socket.timeout as exc:
            self._log_failure(
                host,
                started,
                None,
                exc,
                AIProviderError.TIMEOUT,
                operation,
            )
            raise AIProviderError(AIProviderError.TIMEOUT) from exc
        except urllib.error.HTTPError as exc:
            kind = self._error_kind(exc, discovery=operation == "models")
            self._log_failure(
                host, started, exc.code, exc, kind, operation
            )
            raise AIProviderError(kind) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self._log_failure(
                host,
                started,
                None,
                exc,
                AIProviderError.UNAVAILABLE,
                operation,
            )
            raise AIProviderError(AIProviderError.UNAVAILABLE) from exc

    def _send(self, payload):
        return self._request(
            "POST", "/chat/completions", payload, operation="chat_completions"
        )

    def list_models(self):
        """Ambil katalog model dari ``GET {base_url}/models``."""
        body = self._request("GET", "/models", operation="models")
        return parse_model_catalog(body)

    def complete(self, system_prompt, user_text, context):
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"Konteks sistem (faktual, jangan dikarang di luar ini):\n"
                        f"{json.dumps(context, ensure_ascii=False)}\n\n"
                        f"Pertanyaan pengguna:\n{user_text}"
                    ),
                },
            ],
            "temperature": 0.2,
        }
        body = self._send(payload)
        try:
            data = json.loads(body)
            return data["choices"][0]["message"]["content"].strip()
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise AIProviderError(AIProviderError.MALFORMED) from exc

    def probe(self):
        """Cek autentikasi + ketersediaan model dengan permintaan sangat kecil."""
        body = self._send(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "temperature": 0,
            }
        )
        try:
            data = json.loads(body)
        except (ValueError, TypeError) as exc:
            raise AIProviderError(AIProviderError.MALFORMED) from exc
        if not isinstance(data, dict) or not data.get("choices"):
            raise AIProviderError(AIProviderError.MALFORMED)
        return True


class NodeProvider(AIProvider):
    """Adapter layanan Node.js ai-node/ (gateway pemanggilan provider).

    Django tetap membangun system prompt + konteks dan memegang fallback;
    Node hanya meneruskan ke provider eksternal. Gagal/tidak terjangkau
    -> AIProviderError seperti adapter langsung, sehingga alur fallback
    di services.ask() tidak berubah.
    """

    def __init__(self, node_url, shared_secret, timeout):
        self.node_url = node_url.rstrip("/")
        self.shared_secret = shared_secret
        self.timeout = timeout

    def complete(self, system_prompt, user_text, context):
        payload = {
            "systemPrompt": system_prompt,
            "userText": user_text,
            "context": context,
        }
        request = urllib.request.Request(
            f"{self.node_url}/api/complete",
            data=json.dumps(payload, default=str).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-AI-Node-Secret": self.shared_secret,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                status = getattr(resp, "status", 200)
                body = resp.read().decode("utf-8")
        except socket.timeout as exc:
            raise AIProviderError(AIProviderError.TIMEOUT) from exc
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise AIProviderError(AIProviderError.RATE_LIMITED) from exc
            if exc.code in (408, 504):
                raise AIProviderError(AIProviderError.HTTP_TIMEOUT) from exc
            raise AIProviderError(AIProviderError.UNAVAILABLE) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise AIProviderError(AIProviderError.UNAVAILABLE) from exc
        if status == 429:
            raise AIProviderError(AIProviderError.RATE_LIMITED)
        if status in (408, 504):
            raise AIProviderError(AIProviderError.HTTP_TIMEOUT)
        if status != 200:
            raise AIProviderError(AIProviderError.UNAVAILABLE)
        try:
            data = json.loads(body)
            text = data["text"]
            if not isinstance(text, str) or not text.strip():
                raise AIProviderError(AIProviderError.MALFORMED)
            return text.strip()
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise AIProviderError(AIProviderError.MALFORMED) from exc


def get_provider():
    """Bangun adapter dari konfigurasi efektif; None bila belum diisi.

    Konfigurasi tersimpan (database) diutamakan, lalu environment. Adapter
    dibuat ulang setiap permintaan sehingga perubahan API key/model berlaku
    tanpa restart dan tidak ada client dengan kredensial lama.
    """
    from django.conf import settings

    from .configuration import resolve_ai_configuration

    config = resolve_ai_configuration()
    if config is None:
        return None
    if config.provider == "node_gateway":
        return NodeProvider(
            settings.AI_NODE_URL,
            settings.AI_NODE_SHARED_SECRET,
            settings.AI_NODE_TIMEOUT_SECONDS,
        )
    return OpenAICompatibleProvider(
        config.base_url, config.api_key, config.model, config.timeout
    )
