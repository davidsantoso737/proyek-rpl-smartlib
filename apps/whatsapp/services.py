"""Client terpusat Fonnte untuk notifikasi WhatsApp.

Token hanya dibaca dari settings yang berasal dari environment. Modul ini
tidak menyentuh notifikasi internal, transaksi, atau alur AI.
"""

import http.client
import json
import logging
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "https://api.fonnte.com/send"
MAX_MESSAGE_LENGTH = 4000


class WhatsAppError(Exception):
    """Kegagalan pengiriman WhatsApp dengan pesan aman untuk operator."""


class WhatsAppConfigError(WhatsAppError):
    """Konfigurasi Fonnte belum lengkap atau tidak valid."""


class WhatsAppNetworkError(WhatsAppError):
    """Fonnte tidak dapat dihubungi."""


class WhatsAppTimeoutError(WhatsAppNetworkError):
    """Permintaan ke Fonnte melewati batas waktu."""


class WhatsAppAPIError(WhatsAppError):
    """Fonnte mengembalikan respons penolakan yang valid."""

    def __init__(self, message, *, code=None, reason=""):
        self.code = code
        self.reason = reason
        super().__init__(message)


class WhatsAppResponseError(WhatsAppError):
    """Respons Fonnte tidak sesuai kontrak API."""


class WhatsAppValidationError(WhatsAppError):
    """Argumen pengiriman tidak valid."""


@dataclass(frozen=True)
class SentMessage:
    provider_message_id: str
    phone: str


def country_code():
    value = str(getattr(settings, "FONNTE_COUNTRY_CODE", "62") or "62")
    digits = re.sub(r"[^0-9]", "", value)
    return digits or "62"


def normalize_phone(value):
    """Normalisasi nomor Indonesia ke bentuk 62xxxxxxxxxx.

    Menerima 08..., 62..., +62..., dengan spasi/tanda hubung/titik.
    Mengembalikan string kosong bila nomor tidak masuk akal.
    """
    raw = "" if value is None else str(value).strip()
    if not raw:
        return ""
    digits = re.sub(r"[^0-9]", "", raw)
    if not digits:
        return ""
    prefix = country_code()
    if digits.startswith(prefix):
        normalized = digits
    elif digits.startswith("0"):
        normalized = prefix + digits[1:]
    elif digits.startswith("8") and prefix == "62":
        normalized = prefix + digits
    else:
        return ""
    if prefix == "62" and not normalized.startswith("628"):
        return ""
    if not 9 <= len(normalized) <= 15:
        return ""
    return normalized


def mask_phone(value):
    normalized = normalize_phone(value)
    if not normalized:
        return ""
    prefix = country_code()
    local = "0" + normalized[len(prefix):]
    if len(local) <= 6:
        return local
    return f"{local[:4]}{'*' * (len(local) - 6)}{local[-2:]}"


class FonnteClient:
    def __init__(
        self,
        token,
        *,
        api_url=None,
        timeout=None,
        opener: Callable[..., Any] | None = None,
    ):
        self._token = (token or "").strip()
        if not self._token:
            raise WhatsAppConfigError("FONNTE_API_TOKEN belum diatur.")
        self._api_url = (
            api_url
            or getattr(settings, "FONNTE_API_URL", DEFAULT_API_URL)
            or DEFAULT_API_URL
        ).strip()
        if not self._api_url.startswith("https://"):
            raise WhatsAppConfigError(
                "FONNTE_API_URL harus menggunakan HTTPS."
            )
        if timeout is None:
            timeout = float(
                getattr(settings, "FONNTE_TIMEOUT_SECONDS", 15)
            )
        if timeout <= 0:
            raise WhatsAppConfigError(
                "FONNTE_TIMEOUT_SECONDS harus lebih dari 0."
            )
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen

    def _redact(self, value):
        text = "" if value is None else str(value)
        if self._token:
            text = text.replace(self._token, "[REDACTED]")
        return text

    @staticmethod
    def _decode_object(raw: bytes):
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _provider_id(data):
        value = data.get("id")
        if isinstance(value, (list, tuple)):
            return ",".join(str(item) for item in value)[:128]
        if value is None:
            return ""
        return str(value)[:128]

    def send_message(self, phone, text):
        target = normalize_phone(phone)
        if not target:
            raise WhatsAppValidationError(
                "Nomor WhatsApp tidak valid."
            )
        message = (text or "").strip()
        if not message:
            raise WhatsAppValidationError(
                "Pesan WhatsApp tidak boleh kosong."
            )
        if len(message) > MAX_MESSAGE_LENGTH:
            message = message[: MAX_MESSAGE_LENGTH - 3].rstrip() + "..."

        payload = urllib.parse.urlencode(
            {
                "target": target,
                "message": message,
                "countryCode": country_code(),
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self._api_url,
            data=payload,
            headers={
                "Authorization": self._token,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read()
        except (socket.timeout, TimeoutError):
            logger.warning("Fonnte timeout")
            raise WhatsAppTimeoutError(
                "Permintaan ke Fonnte melewati batas waktu."
            ) from None
        except urllib.error.HTTPError as exc:
            try:
                raw_error = exc.read()
            except (OSError, http.client.HTTPException):
                raw_error = b""
            error_data = self._decode_object(raw_error) or {}
            reason = self._redact(
                error_data.get("reason")
                or error_data.get("detail")
                or ""
            )
            logger.warning(
                "Fonnte HTTP error code=%s", exc.code
            )
            raise WhatsAppAPIError(
                (
                    "Fonnte menolak permintaan "
                    f"(kode {exc.code})."
                ),
                code=exc.code,
                reason=reason,
            ) from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                logger.warning("Fonnte timeout")
                raise WhatsAppTimeoutError(
                    "Permintaan ke Fonnte melewati batas waktu."
                ) from None
            logger.warning(
                "Fonnte network error kind=%s",
                type(exc.reason).__name__,
            )
            raise WhatsAppNetworkError(
                "Tidak dapat terhubung ke Fonnte."
            ) from None
        except (OSError, http.client.HTTPException, ValueError):
            logger.warning("Fonnte network error kind=connection")
            raise WhatsAppNetworkError(
                "Tidak dapat terhubung ke Fonnte."
            ) from None

        data = self._decode_object(raw)
        if data is None:
            logger.warning("Fonnte response invalid")
            raise WhatsAppResponseError("Respons Fonnte tidak valid.")
        if data.get("status") is not True:
            reason = self._redact(
                data.get("reason") or data.get("detail") or ""
            )
            logger.warning("Fonnte rejected request")
            raise WhatsAppAPIError(
                "Fonnte menolak permintaan pengiriman.",
                reason=reason,
            )
        return SentMessage(
            provider_message_id=self._provider_id(data),
            phone=target,
        )


def get_client():
    token = (getattr(settings, "FONNTE_API_TOKEN", "") or "").strip()
    if not token:
        raise WhatsAppConfigError("FONNTE_API_TOKEN belum diatur.")
    return FonnteClient(
        token,
        api_url=getattr(settings, "FONNTE_API_URL", DEFAULT_API_URL),
        timeout=float(
            getattr(settings, "FONNTE_TIMEOUT_SECONDS", 15)
        ),
    )


def check_configuration():
    """Validasi konfigurasi tanpa mengirim pesan."""
    client = get_client()
    return {
        "api_url": client._api_url,
        "timeout": client.timeout,
        "country_code": country_code(),
    }


def send_text_message(
    phone,
    text,
    *,
    client=None,
    require_enabled=True,
):
    if require_enabled and not getattr(
        settings, "WHATSAPP_NOTIFICATIONS_ENABLED", True
    ):
        raise WhatsAppConfigError(
            "Pengiriman WhatsApp sedang dinonaktifkan. "
            "Set WHATSAPP_NOTIFICATIONS_ENABLED=true untuk mengaktifkan."
        )
    sender = client or get_client()
    return sender.send_message(phone, text)
