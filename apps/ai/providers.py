"""Batas provider AI: interface + adapter generik (AI_RULES §8).

Business logic dilarang mengikat langsung ke SDK/API provider.
Satu-satunya adapter memakai stdlib (urllib) agar stack tidak berubah.
API key berasal dari konfigurasi aktif dan tidak pernah di-hardcode,
log, atau respons.
"""

import json
import logging
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_ALPHA_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)

# Tag literal yang dipakai sebagian model untuk membungkus reasoning di dalam
# ``message.content``. Hanya nama tag reasoning yang dikenal yang diproses,
# sehingga tag lain dalam jawaban tidak ikut terhapus.
_REASONING_TAG_NAMES = (
    "think",
    "thinking",
    "thought",
    "thoughts",
    "reasoning",
    "analysis",
    "scratchpad",
)
_REASONING_TAG = (
    r"(?:" + "|".join(_REASONING_TAG_NAMES) + r")"
)
_REASONING_BLOCK_RE = re.compile(
    r"<\s*" + _REASONING_TAG + r"\b[^>]*>.*?<\s*/\s*" + _REASONING_TAG + r"\s*>",
    re.IGNORECASE | re.DOTALL,
)
# Pembuka tanpa penutup: sisa teks setelah tag tidak dapat dipastikan final,
# jadi dibuang sampai akhir. Teks final sebelum tag tetap dipertahankan.
_REASONING_OPEN_TAIL_RE = re.compile(
    r"<\s*" + _REASONING_TAG + r"\b[^>]*>.*\Z",
    re.IGNORECASE | re.DOTALL,
)
# Penutup tanpa pembuka: bagian sebelum penutup berasal dari reasoning.
_REASONING_CLOSE_HEAD_RE = re.compile(
    r"\A.*?<\s*/\s*" + _REASONING_TAG + r"\s*>",
    re.IGNORECASE | re.DOTALL,
)
_REASONING_CLOSE_RE = re.compile(
    r"<\s*/\s*" + _REASONING_TAG + r"\s*>",
    re.IGNORECASE,
)
_REASONING_PART_TYPES = frozenset(
    {"reasoning", "thinking", "thought", "analysis", "tool_call", "function_call"}
)
_TEXT_FIELDS = ("answer", "text", "content", "response", "jawaban", "output_text")
_INTERNAL_FIELDS = frozenset(
    {
        "is_listing",
        "personal_intent",
        "statistics_intents",
        "data_saya",
        "koleksi_fisik",
        "fakta_sistem",
        "peminjaman_saya",
    }
)


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
    MODEL_NOT_CHAT = "model_not_chat"
    BAD_REQUEST = "bad_request"
    DISCOVERY_UNSUPPORTED = "discovery_unsupported"

    def __init__(self, kind, detail=""):
        self.kind = kind
        super().__init__(detail or kind)


def _iter_text_parts(value):
    """Ambil teks final dari string/list/dict respons provider.

    Bagian reasoning/thinking dan tool call sengaja dilewati: isi itu bukan
    jawaban untuk pengguna. Bentuk list mendukung ``content`` OpenAI maupun
    ``parts`` Gemini.
    """
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_text_parts(item)
        return
    if not isinstance(value, dict):
        return
    part_type = str(value.get("type") or "").lstrip("$").lower()
    if value.get("thought") is True or part_type in _REASONING_PART_TYPES:
        return
    for key in ("text", "content", "value"):
        if key in value:
            yield from _iter_text_parts(value[key])
            return


def _unwrap_text_payload(text):
    """Buka satu lapis pembungkus teks yang lazim, tanpa menghapus isi sah.

    Hanya membuka code fence tunggal, string JSON ganda, atau objek JSON yang
    jelas merupakan pembungkus jawaban (mis. ``{"answer": "..."}``). Objek
    berisi field internal tidak dibuka; validasi berikutnya akan menolaknya.
    """
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2:
            body = "\n".join(lines[1:-1]).strip()
            if body:
                stripped = body
    if stripped.startswith('"') and stripped.endswith('"'):
        try:
            decoded = json.loads(stripped)
        except (TypeError, ValueError):
            return stripped
        if isinstance(decoded, str):
            return decoded.strip()
        return stripped
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return stripped
    try:
        decoded = json.loads(stripped)
    except (TypeError, ValueError):
        return stripped
    if isinstance(decoded, dict):
        keys = {str(key).lower() for key in decoded}
        text_keys = keys.intersection(_TEXT_FIELDS)
        if len(text_keys) == 1 and not keys.intersection(_INTERNAL_FIELDS):
            for key, value in decoded.items():
                if str(key).lower() in _TEXT_FIELDS and isinstance(value, str):
                    return value.strip()
    return stripped


def _strip_reasoning_tags(text):
    """Buang blok reasoning bertag yang bocor ke dalam jawaban final.

    Hanya tag bernama reasoning yang dikenal yang disentuh. Blok lengkap
    dihapus; pembuka tanpa penutup membuang sisa teks (bukan jawaban final);
    penutup tanpa pembuka membuang awalan reasoning. Tag lain dan Markdown
    jawaban dibiarkan utuh.
    """
    text = _REASONING_BLOCK_RE.sub("", text)
    text = _REASONING_CLOSE_HEAD_RE.sub("", text)
    text = _REASONING_CLOSE_RE.sub("", text)
    text = _REASONING_OPEN_TAIL_RE.sub("", text)
    return text


def _clean_text(text):
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_CHARS_RE.sub("", text)
    text = _strip_reasoning_tags(text)
    for _ in range(2):
        unwrapped = _unwrap_text_payload(text)
        if unwrapped == text:
            break
        text = unwrapped
    text = _strip_reasoning_tags(text)
    return text.strip()


def looks_like_natural_text(text):
    """True bila teks cukup wajar sebagai jawaban percakapan.

    Tidak menghapus karakter sah. Teks ditolak hanya bila nyaris tidak berisi
    kata (mis. keluaran parser ``\\_ & & ...``), berupa JSON mentah, boolean
    tunggal, atau escape/struktur yang mendominasi.
    """
    if not isinstance(text, str):
        return False
    plain = text.strip()
    if not plain:
        return False
    token = plain.strip(" .,!:;\"'").casefold()
    if token in {"true", "false", "null", "none"}:
        return False
    letters = sum(1 for char in plain if char.isalpha())
    visible = len(plain)
    if letters < 2 or not _ALPHA_WORD_RE.search(plain):
        return False
    if letters / visible < 0.25:
        return False
    structural = sum(plain.count(char) for char in "\\{}[]<>|&")
    if structural > letters * 2:
        return False
    if plain.count("\\") >= 4 and '\\"' in plain:
        return False
    if plain.startswith(("{", "[")) and plain.endswith(("}", "]")):
        try:
            decoded = json.loads(plain)
        except (TypeError, ValueError):
            pass
        else:
            if isinstance(decoded, (dict, list)):
                return False
    return True


def normalize_answer_text(text, error_kind=AIProviderError.MALFORMED):
    """Bersihkan dan validasi teks jawaban; raise bila bukan percakapan."""
    cleaned = _clean_text(text)
    if not looks_like_natural_text(cleaned):
        raise AIProviderError(error_kind)
    return cleaned


def normalize_response_text(data, error_kind=AIProviderError.MALFORMED):
    """Ekstrak jawaban final dari bentuk respons provider yang berbeda."""
    if not isinstance(data, dict):
        raise AIProviderError(error_kind)
    candidates = []
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                candidates.append(message.get("content"))
            if first.get("text") is not None:
                candidates.append(first.get("text"))
    native = data.get("candidates")
    if isinstance(native, list) and native:
        first = native[0]
        if isinstance(first, dict):
            content = first.get("content")
            if isinstance(content, dict):
                candidates.append(content.get("parts"))
            else:
                candidates.append(content)
    for key in ("output_text", "response", "content", "text"):
        if key in data:
            candidates.append(data.get(key))
    for candidate in candidates:
        text = "".join(_iter_text_parts(candidate)).strip()
        if text:
            return normalize_answer_text(text, error_kind=error_kind)
    raise AIProviderError(error_kind)


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
                        "Referensi fakta sistem (gunakan hanya sebagai sumber "
                        "fakta; JANGAN disalin mentah dan jangan tampilkan "
                        "JSON, nama field, nilai true/false, atau proses "
                        f"analisis):\n"
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
            return normalize_response_text(data)
        except (ValueError, TypeError) as exc:
            raise AIProviderError(AIProviderError.MALFORMED) from exc
        except AIProviderError as exc:
            logger.warning(
                "AI provider answer rejected: model=%s category=%s",
                self.model or "-",
                exc.kind,
            )
            raise

    def probe(self):
        """Cek kredensial + apakah model benar-benar menghasilkan jawaban chat.

        Bukan sekadar memastikan ``choices`` ada: model parsing/embedding juga
        bisa mengembalikan struktur itu, tetapi isinya simbol/escape yang tidak
        layak ditampilkan ke pengguna.
        """
        body = self._send(
            {
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Jawab dengan satu kalimat pendek: apa ibu kota "
                            "Indonesia?"
                        ),
                    }
                ],
                "max_tokens": 32,
                "temperature": 0,
            }
        )
        try:
            data = json.loads(body)
        except (ValueError, TypeError) as exc:
            raise AIProviderError(AIProviderError.MALFORMED) from exc
        normalize_response_text(data, error_kind=AIProviderError.MODEL_NOT_CHAT)
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
            return normalize_response_text(data)
        except (ValueError, TypeError) as exc:
            raise AIProviderError(AIProviderError.MALFORMED) from exc
        except AIProviderError as exc:
            logger.warning(
                "AI node answer rejected: category=%s", exc.kind
            )
            raise


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
