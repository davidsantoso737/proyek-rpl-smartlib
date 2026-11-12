"""Client terpusat Telegram Bot API.

Token hanya dibaca dari settings yang berasal dari environment. Modul ini
tidak menyentuh notifikasi internal, transaksi, atau alur AI.
"""

import http.client
import json
import logging
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_API_BASE_URL = "https://api.telegram.org"
MAX_MESSAGE_LENGTH = 4096
PARSE_MODE_HTML = "HTML"


class TelegramError(Exception):
    """Kegagalan Telegram dengan pesan aman untuk operator."""


class TelegramConfigError(TelegramError):
    """Konfigurasi Telegram belum lengkap atau tidak valid."""


class TelegramNetworkError(TelegramError):
    """Telegram tidak dapat dihubungi."""


class TelegramTimeoutError(TelegramNetworkError):
    """Permintaan ke Telegram melewati batas waktu."""


class TelegramAPIError(TelegramError):
    """Telegram mengembalikan respons error yang valid."""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        description: str = "",
        retry_after: int | None = None,
    ):
        self.code = code
        self.description = description
        self.retry_after = retry_after
        super().__init__(message)


class TelegramResponseError(TelegramError):
    """Respons Telegram tidak sesuai kontrak Bot API."""


class TelegramValidationError(TelegramError):
    """Argumen pengiriman tidak valid."""


@dataclass(frozen=True)
class BotInfo:
    id: int
    username: str
    first_name: str
    is_bot: bool


@dataclass(frozen=True)
class SentMessage:
    message_id: int
    chat_id: int
    date: int


class TelegramClient:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_API_BASE_URL,
        timeout: float = 10,
        opener: Callable[..., Any] | None = None,
    ):
        self._token = (token or "").strip()
        if not self._token:
            raise TelegramConfigError("TELEGRAM_BOT_TOKEN belum diatur.")
        self._base_url = (base_url or DEFAULT_API_BASE_URL).rstrip("/")
        if not self._base_url.startswith("https://"):
            raise TelegramConfigError(
                "TELEGRAM_API_BASE_URL harus menggunakan HTTPS."
            )
        if timeout <= 0:
            raise TelegramConfigError(
                "TELEGRAM_TIMEOUT_SECONDS harus lebih dari 0."
            )
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen

    def _endpoint(self, method: str) -> str:
        return f"{self._base_url}/bot{self._token}/{method}"

    def _redact(self, value: Any) -> str:
        text = "" if value is None else str(value)
        if self._token:
            text = text.replace(self._token, "[REDACTED]")
        return text

    def _api_error(
        self,
        method: str,
        code: int | None,
        description: Any = "",
        retry_after: int | None = None,
    ) -> TelegramAPIError:
        safe_description = self._redact(description)
        logger.warning(
            "Telegram API error method=%s code=%s retry_after=%s",
            method,
            code,
            retry_after,
        )
        message = (
            "Telegram menolak permintaan "
            f"(kode {code if code is not None else 'tidak diketahui'})."
        )
        if safe_description:
            message = f"{message} {safe_description}"
        return TelegramAPIError(
            message,
            code=code,
            description=safe_description,
            retry_after=retry_after,
        )

    @staticmethod
    def _decode_object(raw: bytes) -> dict[str, Any] | None:
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _retry_after(data: dict[str, Any]) -> int | None:
        parameters = data.get("parameters")
        if not isinstance(parameters, dict):
            return None
        value = parameters.get("retry_after")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return None

    def _request(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        *,
        request_timeout: float | None = None,
    ) -> Any:
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            request = urllib.request.Request(
                self._endpoint(method),
                data=data,
                headers=headers,
                method="POST" if data is not None else "GET",
            )
        except ValueError:
            raise TelegramConfigError(
                "Konfigurasi URL Telegram tidak valid."
            ) from None

        timeout = self.timeout if request_timeout is None else request_timeout
        try:
            with self._opener(request, timeout=timeout) as response:
                raw = response.read()
        except (socket.timeout, TimeoutError):
            logger.warning("Telegram timeout method=%s", method)
            raise TelegramTimeoutError(
                "Permintaan ke Telegram melewati batas waktu."
            ) from None
        except urllib.error.HTTPError as exc:
            try:
                raw_error = exc.read()
            except (OSError, http.client.HTTPException):
                raw_error = b""
            error_data = self._decode_object(raw_error) or {}
            code = error_data.get("error_code")
            if not isinstance(code, int):
                code = exc.code
            raise self._api_error(
                method,
                code,
                error_data.get("description", ""),
                self._retry_after(error_data),
            ) from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                logger.warning("Telegram timeout method=%s", method)
                raise TelegramTimeoutError(
                    "Permintaan ke Telegram melewati batas waktu."
                ) from None
            logger.warning(
                "Telegram network error method=%s kind=%s",
                method,
                type(exc.reason).__name__,
            )
            raise TelegramNetworkError(
                "Tidak dapat terhubung ke Telegram."
            ) from None
        except (OSError, http.client.HTTPException, ValueError):
            logger.warning(
                "Telegram network error method=%s kind=connection",
                method,
            )
            raise TelegramNetworkError(
                "Tidak dapat terhubung ke Telegram."
            ) from None

        response_data = self._decode_object(raw)
        if response_data is None:
            logger.warning("Telegram response invalid method=%s", method)
            raise TelegramResponseError("Respons Telegram tidak valid.") from None
        if response_data.get("ok") is not True:
            code = response_data.get("error_code")
            if not isinstance(code, int):
                code = None
            raise self._api_error(
                method,
                code,
                response_data.get("description", ""),
                self._retry_after(response_data),
            ) from None
        return response_data.get("result")

    def get_me(self) -> BotInfo:
        result = self._request("getMe")
        if not isinstance(result, dict):
            raise TelegramResponseError("Respons getMe tidak valid.")
        try:
            bot_id = int(result["id"])
            username = str(result.get("username") or "")
            first_name = str(result.get("first_name") or "")
            is_bot = bool(result.get("is_bot"))
        except (KeyError, TypeError, ValueError):
            raise TelegramResponseError(
                "Respons getMe tidak lengkap."
            ) from None
        if not is_bot:
            raise TelegramResponseError("Respons getMe bukan bot Telegram.")
        return BotInfo(
            id=bot_id,
            username=username,
            first_name=first_name,
            is_bot=True,
        )

    def get_updates(
        self,
        *,
        offset: int | None = None,
        limit: int = 100,
        timeout: int = 0,
        allowed_updates: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if offset is not None and (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
        ):
            raise TelegramValidationError(
                "Offset getUpdates harus angka bulat positif."
            )
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 100
        ):
            raise TelegramValidationError(
                "Limit getUpdates harus antara 1 dan 100."
            )
        if (
            not isinstance(timeout, int)
            or isinstance(timeout, bool)
            or not 0 <= timeout <= 50
        ):
            raise TelegramValidationError(
                "Timeout getUpdates harus antara 0 dan 50 detik."
            )
        if allowed_updates is not None:
            if not isinstance(allowed_updates, (list, tuple)):
                raise TelegramValidationError(
                    "allowed_updates harus berupa daftar."
                )
            if not all(
                isinstance(item, str) and item
                for item in allowed_updates
            ):
                raise TelegramValidationError(
                    "allowed_updates harus berisi nama update."
                )
            allowed_updates = list(allowed_updates)

        payload: dict[str, Any] = {
            "limit": limit,
            "timeout": timeout,
        }
        if offset is not None:
            payload["offset"] = offset
        if allowed_updates is not None:
            payload["allowed_updates"] = allowed_updates
        result = self._request(
            "getUpdates",
            payload,
            request_timeout=self.timeout + timeout + 5,
        )
        if not isinstance(result, list):
            raise TelegramResponseError("Respons getUpdates tidak valid.")
        updates: list[dict[str, Any]] = []
        for item in result:
            if not isinstance(item, dict):
                raise TelegramResponseError(
                    "Respons getUpdates berisi data tidak valid."
                )
            update_id = item.get("update_id")
            if (
                not isinstance(update_id, int)
                or isinstance(update_id, bool)
                or update_id <= 0
            ):
                raise TelegramResponseError(
                    "Respons getUpdates tidak memiliki update_id."
                )
            updates.append(item)
        return updates

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        disable_web_page_preview: bool = True,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> SentMessage:
        if (
            not isinstance(chat_id, int)
            or isinstance(chat_id, bool)
            or chat_id == 0
        ):
            raise TelegramValidationError(
                "Chat ID Telegram harus berupa angka."
            )
        message = (text or "").strip()
        if not message:
            raise TelegramValidationError(
                "Pesan Telegram tidak boleh kosong."
            )
        if len(message) > MAX_MESSAGE_LENGTH:
            raise TelegramValidationError(
                f"Pesan Telegram maksimal {MAX_MESSAGE_LENGTH} karakter."
            )
        payload = {
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": bool(disable_web_page_preview),
        }
        if parse_mode is not None:
            if parse_mode != PARSE_MODE_HTML:
                raise TelegramValidationError(
                    "parse_mode Telegram hanya mendukung HTML."
                )
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            if not isinstance(reply_markup, dict) or not reply_markup:
                raise TelegramValidationError(
                    "reply_markup Telegram harus berupa objek."
                )
            payload["reply_markup"] = reply_markup
        result = self._request("sendMessage", payload)
        if not isinstance(result, dict):
            raise TelegramResponseError("Respons sendMessage tidak valid.")
        try:
            message_id = int(result["message_id"])
            chat = result["chat"]
            result_chat_id = int(chat["id"])
            date = int(result.get("date") or 0)
        except (KeyError, TypeError, ValueError):
            raise TelegramResponseError(
                "Respons sendMessage tidak lengkap."
            ) from None
        return SentMessage(
            message_id=message_id,
            chat_id=result_chat_id,
            date=date,
        )


def get_client() -> TelegramClient:
    token = (getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip()
    if not token:
        raise TelegramConfigError("TELEGRAM_BOT_TOKEN belum diatur.")
    base_url = (
        getattr(settings, "TELEGRAM_API_BASE_URL", DEFAULT_API_BASE_URL)
        or DEFAULT_API_BASE_URL
    ).rstrip("/")
    timeout = float(getattr(settings, "TELEGRAM_TIMEOUT_SECONDS", 10))
    return TelegramClient(
        token,
        base_url=base_url,
        timeout=timeout,
    )


def check_connection() -> BotInfo:
    return get_client().get_me()


def send_text_message(
    chat_id: int,
    text: str,
    *,
    client: TelegramClient | None = None,
    require_enabled: bool = True,
    reply_markup: dict[str, Any] | None = None,
) -> SentMessage:
    if require_enabled and not getattr(
        settings, "TELEGRAM_NOTIFICATIONS_ENABLED", False
    ):
        raise TelegramConfigError(
            "Pengiriman Telegram sedang dinonaktifkan. "
            "Set TELEGRAM_NOTIFICATIONS_ENABLED=true untuk mengaktifkan."
        )
    sender = client or get_client()
    return sender.send_message(
        chat_id, text, reply_markup=reply_markup
    )
