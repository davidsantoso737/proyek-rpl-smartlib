"""Batas provider AI: interface + adapter generik (AI_RULES §8).

Business logic dilarang mengikat langsung ke SDK/API provider.
Satu-satunya adapter memakai stdlib (urllib) agar stack tidak berubah.
API key hanya dari environment (settings), tidak pernah di-hardcode,
log, atau respons.
"""

import json
import socket
import urllib.error
import urllib.request
from abc import ABC, abstractmethod


class AIProviderError(Exception):
    """Kegagalan provider dengan kategori aman untuk pesan user."""

    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    MALFORMED = "malformed"
    NO_KEY = "no_key"

    def __init__(self, kind, detail=""):
        self.kind = kind
        super().__init__(detail or kind)


class AIProvider(ABC):
    """Interface service AI: satu pertanyaan + konteks -> teks jawaban."""

    @abstractmethod
    def complete(self, system_prompt, user_text, context):
        """Kembalikan jawaban string; raise AIProviderError bila gagal."""


class OpenAICompatibleProvider(AIProvider):
    """Adapter endpoint chat/completions generik (provider TBD via env)."""

    def __init__(self, base_url, api_key, model, timeout):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

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
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
        except socket.timeout as exc:
            raise AIProviderError(AIProviderError.TIMEOUT) from exc
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise AIProviderError(AIProviderError.RATE_LIMITED) from exc
            raise AIProviderError(AIProviderError.UNAVAILABLE) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise AIProviderError(AIProviderError.UNAVAILABLE) from exc
        try:
            data = json.loads(body)
            return data["choices"][0]["message"]["content"].strip()
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise AIProviderError(AIProviderError.MALFORMED) from exc


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
                raise AIProviderError(AIProviderError.TIMEOUT) from exc
            raise AIProviderError(AIProviderError.UNAVAILABLE) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise AIProviderError(AIProviderError.UNAVAILABLE) from exc
        if status == 429:
            raise AIProviderError(AIProviderError.RATE_LIMITED)
        if status in (408, 504):
            raise AIProviderError(AIProviderError.TIMEOUT)
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
    """Bangun adapter dari konfigurasi; None bila API key belum diisi.

    Tanpa API key tidak ada API call apa pun (mode lokal). Bila layanan
    Node dikonfigurasi (AI_NODE_URL + secret), ia dipakai sebagai jalur
    pemanggilan provider; bila tidak, jalur langsung yang lama dipakai.
    """
    from django.conf import settings

    node_url = (settings.AI_NODE_URL or "").strip()
    node_secret = (settings.AI_NODE_SHARED_SECRET or "").strip()
    if node_url and node_secret:
        return NodeProvider(
            node_url, node_secret, settings.AI_NODE_TIMEOUT_SECONDS
        )
    base_url = (settings.AI_PROVIDER_BASE_URL or "").strip()
    api_key = (settings.AI_API_KEY or "").strip()
    model = (settings.AI_MODEL or "").strip()
    if not base_url or not api_key or not model:
        return None
    return OpenAICompatibleProvider(
        base_url, api_key, model, settings.AI_TIMEOUT_SECONDS
    )
