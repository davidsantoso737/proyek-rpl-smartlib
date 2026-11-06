"""Konfigurasi AI yang dapat diubah pustakawan (database + environment).

Precedence is deterministic:

1. A readable, enabled ``AIConfiguration`` row (pk=1) wins.
2. Otherwise the Node gateway environment (AI_NODE_URL + secret) is used.
3. Otherwise the direct OpenAI-compatible environment is used.
4. Otherwise the assistant runs in local mode without any provider call.

A saved row that is disabled, incomplete, or unreadable never hides a
working environment configuration: it is skipped with a warning and the
environment path below it is used.

The API key is encrypted at rest with Fernet. The encryption key comes
from ``AI_SETTINGS_ENCRYPTION_KEY`` when set; otherwise it is derived from
Django's existing ``SECRET_KEY``. Neither key is stored in the database.
"""

import base64
import hashlib
import ipaddress
import logging
import socket
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import AIConfiguration
from .providers import AIProviderError, OpenAICompatibleProvider

logger = logging.getLogger(__name__)

SOURCE_DATABASE = "database"
SOURCE_ENVIRONMENT = "environment"
SOURCE_GATEWAY = "environment_gateway"


class ConfigurationError(Exception):
    """Kesalahan konfigurasi dengan pesan aman untuk ditampilkan."""


@dataclass(frozen=True)
class ResolvedAIConfiguration:
    source: str
    provider: str
    base_url: str
    model: str
    api_key: str = field(default="", repr=False)
    timeout: float = 15.0


def _fernet():
    configured = (
        getattr(settings, "AI_SETTINGS_ENCRYPTION_KEY", "") or ""
    ).strip()
    if configured:
        key = configured.encode("utf-8")
    else:
        secret = getattr(settings, "SECRET_KEY", "") or ""
        if not secret:
            raise ConfigurationError(
                "Kunci enkripsi konfigurasi AI tidak tersedia."
            )
        digest = hashlib.sha256(
            f"smartlib-ai-config-v1:{secret}".encode("utf-8")
        ).digest()
        key = base64.urlsafe_b64encode(digest)
    try:
        return Fernet(key)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            "Kunci enkripsi konfigurasi AI tidak valid."
        ) from exc


def encrypt_api_key(value):
    if not value:
        raise ConfigurationError("API key tidak boleh kosong.")
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_api_key(value):
    if not value:
        raise ConfigurationError("API key tersimpan tidak tersedia.")
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, TypeError, ValueError) as exc:
        raise ConfigurationError(
            "Konfigurasi API key tersimpan tidak dapat dibaca."
        ) from exc


def _active_row():
    return (
        AIConfiguration.objects.filter(is_enabled=True)
        .order_by("-updated_at", "-pk")
        .first()
    )


def _legacy_row():
    """Baris yang dipakai pemanggil lama tanpa ID provider eksplisit."""
    return (
        _active_row()
        or AIConfiguration.objects.filter(pk=1).first()
        or AIConfiguration.objects.order_by("pk").first()
    )


def _configuration_by_id(configuration_id):
    if not configuration_id:
        return None
    try:
        return AIConfiguration.objects.filter(pk=configuration_id).first()
    except (TypeError, ValueError):
        return None


def saved_configuration_status(configuration_id=None):
    if configuration_id:
        row = _configuration_by_id(configuration_id)
    else:
        row = _legacy_row()
    status = {
        "row": row,
        "key_present": False,
        "readable": False,
        "problem": "",
    }
    if row is None:
        return status
    status["key_present"] = bool(row.api_key_encrypted)
    try:
        decrypt_api_key(row.api_key_encrypted)
        status["readable"] = True
    except ConfigurationError as exc:
        status["problem"] = str(exc)
    return status


def _environment_configuration():
    node_url = (getattr(settings, "AI_NODE_URL", "") or "").strip()
    node_secret = (
        getattr(settings, "AI_NODE_SHARED_SECRET", "") or ""
    ).strip()
    if node_url and node_secret:
        return ResolvedAIConfiguration(
            source=SOURCE_GATEWAY,
            provider="node_gateway",
            base_url="",
            model=(getattr(settings, "AI_MODEL", "") or "").strip(),
            timeout=float(
                getattr(settings, "AI_NODE_TIMEOUT_SECONDS", 15.0)
            ),
        )
    base_url = (
        getattr(settings, "AI_PROVIDER_BASE_URL", "") or ""
    ).strip()
    api_key = (getattr(settings, "AI_API_KEY", "") or "").strip()
    model = (getattr(settings, "AI_MODEL", "") or "").strip()
    if not base_url or not api_key or not model:
        return None
    return ResolvedAIConfiguration(
        source=SOURCE_ENVIRONMENT,
        provider=AIConfiguration.PROVIDER_OPENAI_COMPATIBLE,
        base_url=base_url,
        model=model,
        api_key=api_key,
        timeout=float(getattr(settings, "AI_TIMEOUT_SECONDS", 15.0)),
    )


def resolve_ai_configuration():
    """Konfigurasi efektif saat ini, atau None untuk mode lokal."""
    row = _active_row()
    if row is not None:
        try:
            api_key = decrypt_api_key(row.api_key_encrypted)
        except ConfigurationError:
            api_key = ""
        if (
            (row.base_url or "").strip()
            and (row.model or "").strip()
            and api_key
        ):
            return ResolvedAIConfiguration(
                source=SOURCE_DATABASE,
                provider=row.provider,
                base_url=row.base_url.strip(),
                model=row.model.strip(),
                api_key=api_key,
                timeout=float(
                    getattr(settings, "AI_TIMEOUT_SECONDS", 15.0)
                ),
            )
        logger.warning(
            "Konfigurasi AI tersimpan tidak dapat dipakai; "
            "memakai konfigurasi environment."
        )
    return _environment_configuration()


def configuration_summary(selected_id=None):
    configurations = list(
        AIConfiguration.objects.order_by("-is_enabled", "name", "pk")
    )
    active = next(
        (row for row in configurations if row.is_enabled), None
    )
    selected = None
    if selected_id:
        selected = _configuration_by_id(selected_id)
    if selected is None:
        selected = active or (
            configurations[0] if configurations else None
        )
    status = saved_configuration_status(
        selected.pk if selected is not None else None
    )
    environment = _environment_configuration()
    return {
        "configurations": configurations,
        "active": active,
        "selected": selected,
        "saved": status["row"],
        "saved_key_present": status["key_present"],
        "saved_readable": status["readable"],
        "saved_problem": status["problem"],
        "environment_configured": environment is not None,
        "effective": resolve_ai_configuration(),
    }


def validation_message(kind):
    return {
        AIProviderError.AUTH: (
            "Autentikasi ditolak. Periksa kembali API key."
        ),
        AIProviderError.MODEL_UNAVAILABLE: (
            "Model tidak tersedia untuk kredensial ini. Periksa ID model."
        ),
        AIProviderError.MODEL_NOT_CHAT: (
            "Model tidak menghasilkan jawaban percakapan. Pilih model "
            "chat/instruct, bukan model parsing, embedding, atau rerank."
        ),
        AIProviderError.RATE_LIMITED: (
            "Kuota atau batas permintaan penyedia tercapai. Coba lagi nanti."
        ),
        AIProviderError.TIMEOUT: (
            "Koneksi ke penyedia melewati batas waktu tanpa respons."
        ),
        AIProviderError.HTTP_TIMEOUT: (
            "Penyedia atau gateway mengembalikan timeout HTTP. "
            "Periksa status layanan atau coba lagi nanti."
        ),
        AIProviderError.UNAVAILABLE: (
            "Penyedia sedang tidak dapat dihubungi."
        ),
        AIProviderError.BAD_REQUEST: (
            "Permintaan ditolak penyedia. Periksa URL endpoint dan model."
        ),
        AIProviderError.MALFORMED: (
            "Respons penyedia tidak dikenali."
        ),
        AIProviderError.NO_KEY: "API key belum diisi.",
    }.get(kind, "Uji koneksi gagal.")


def model_discovery_message(kind):
    return {
        AIProviderError.AUTH: (
            "Autentikasi ditolak. Periksa kembali API key."
        ),
        AIProviderError.RATE_LIMITED: (
            "Kuota atau batas permintaan penyedia tercapai. Coba lagi nanti "
            "atau masukkan ID model manual."
        ),
        AIProviderError.TIMEOUT: (
            "Permintaan daftar model melewati batas waktu. Coba lagi atau "
            "masukkan ID model manual."
        ),
        AIProviderError.HTTP_TIMEOUT: (
            "Penyedia atau gateway mengembalikan timeout HTTP saat memuat "
            "model. Coba lagi atau masukkan ID model manual."
        ),
        AIProviderError.UNAVAILABLE: (
            "Daftar model tidak dapat diambil karena penyedia tidak dapat "
            "dihubungi. Masukkan ID model manual bila perlu."
        ),
        AIProviderError.BAD_REQUEST: (
            "Permintaan daftar model ditolak penyedia. Periksa URL endpoint "
            "atau masukkan ID model manual."
        ),
        AIProviderError.MALFORMED: (
            "Respons daftar model tidak dikenali. Masukkan ID model secara "
            "manual."
        ),
        AIProviderError.DISCOVERY_UNSUPPORTED: (
            "Endpoint daftar model tidak tersedia. Masukkan ID model secara "
            "manual."
        ),
        AIProviderError.NO_KEY: (
            "API key belum diisi. Isi API key atau simpan konfigurasi "
            "terlebih dahulu."
        ),
    }.get(kind, "Daftar model tidak dapat dimuat.")


def _clean_base_url(value):
    value = (value or "").strip().rstrip("/")
    if not value:
        raise ConfigurationError("URL endpoint provider wajib diisi.")
    return value


def _clean_model(value):
    value = (value or "").strip()
    if not value:
        raise ConfigurationError("ID model wajib diisi.")
    return value


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _allowed_provider_hosts():
    return {
        str(host).strip().lower()
        for host in getattr(settings, "AI_PROVIDER_ALLOWED_HOSTS", [])
        if str(host).strip()
    }


def _validate_provider_url(base_url):
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ConfigurationError("Gunakan URL http atau https yang valid.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError(
            "URL endpoint tidak boleh memuat kredensial, query, atau fragmen."
        )
    host = parsed.hostname.lower()
    if host in _allowed_provider_hosts():
        return
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ConfigurationError(
            "Port pada URL endpoint tidak valid."
        ) from exc
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ConfigurationError(
            "Nama host provider tidak dapat diselesaikan."
        ) from exc
    if not addresses:
        raise ConfigurationError("Nama host provider tidak dapat diselesaikan.")
    for info in addresses:
        raw_address = info[4][0].split("%", 1)[0]
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise ConfigurationError("Alamat provider tidak valid.") from exc
        if not address.is_global:
            raise ConfigurationError(
                "URL provider tidak boleh mengarah ke jaringan internal."
            )


def _discovery_opener():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirectHandler(),
    )


def _discovery_timeout():
    return float(
        getattr(settings, "AI_MODEL_DISCOVERY_TIMEOUT_SECONDS", 15.0)
    )


def probe_configuration(base_url, api_key, model, timeout=None):
    """Permintaan kecil untuk memeriksa autentikasi dan model.

    Raise AIProviderError/ConfigurationError; tidak menyimpan apa pun.
    """
    base_url = _clean_base_url(base_url)
    model = _clean_model(model)
    api_key = (api_key or "").strip()
    if not api_key:
        raise AIProviderError(AIProviderError.NO_KEY)
    _validate_provider_url(base_url)
    if timeout is None:
        timeout = float(
            getattr(settings, "AI_CONFIG_TEST_TIMEOUT_SECONDS", 60.0)
        )
    provider = OpenAICompatibleProvider(
        base_url, api_key, model, timeout
    )
    return provider.probe()


def _proposed_api_key(api_key, configuration_id=None):
    api_key = (api_key or "").strip()
    if api_key:
        return api_key
    status = saved_configuration_status(configuration_id)
    if status["row"] is None:
        return ""
    return decrypt_api_key(status["row"].api_key_encrypted)


def discover_models(base_url, api_key="", configuration_id=None):
    """Ambil katalog model tanpa menyimpan atau mengubah konfigurasi."""
    base_url = _clean_base_url(base_url)
    key = _proposed_api_key(api_key, configuration_id)
    if not key:
        raise AIProviderError(AIProviderError.NO_KEY)
    _validate_provider_url(base_url)
    provider = OpenAICompatibleProvider(
        base_url,
        key,
        "",
        _discovery_timeout(),
        opener=_discovery_opener(),
    )
    return provider.list_models()


def test_configuration(
    base_url, model, api_key="", configuration_id=None
):
    """Uji kredensial yang diusulkan tanpa mengubah konfigurasi aktif."""
    key = _proposed_api_key(api_key, configuration_id)
    probe_configuration(base_url, key, model)


def _default_provider_name(base_url):
    host = (urllib.parse.urlsplit(base_url).hostname or "").strip()
    return (host or "Provider")[:100]


def save_configuration(
    user,
    base_url,
    model,
    api_key="",
    enabled=True,
    name="",
    configuration_id=None,
    create_new=False,
):
    """Validasi lalu simpan konfigurasi secara atomik.

    API key kosong berarti "pertahankan key milik baris ini". Baris yang
    diaktifkan menonaktifkan provider lain dalam transaksi yang sama.
    Konfigurasi baru hanya diaktifkan setelah probe berhasil.
    """
    base_url = _clean_base_url(base_url)
    model = _clean_model(model)
    api_key = (api_key or "").strip()
    if create_new:
        existing = None
    elif configuration_id:
        existing = _configuration_by_id(configuration_id)
        if existing is None:
            raise ConfigurationError(
                "Konfigurasi provider tidak ditemukan."
            )
    else:
        existing = _legacy_row()
    name = (
        (name or "").strip()
        or (existing.name if existing is not None else "")
        or _default_provider_name(base_url)
    )
    name = name[:100]
    existing_cipher = existing.api_key_encrypted if existing else ""
    changed = (
        existing is None
        or (existing.base_url or "") != base_url
        or (existing.model or "") != model
        or bool(api_key)
    )
    existing_key = ""
    if existing is not None:
        try:
            existing_key = decrypt_api_key(existing.api_key_encrypted)
        except ConfigurationError:
            existing_key = ""
    effective_key = api_key or existing_key
    if not effective_key:
        raise ConfigurationError(
            "API key wajib diisi untuk konfigurasi baru."
        )
    if enabled:
        probe_configuration(base_url, effective_key, model)
    try:
        with transaction.atomic():
            row = None
            if existing is not None:
                row = (
                    AIConfiguration.objects.select_for_update()
                    .filter(pk=existing.pk)
                    .first()
                )
                if row is None:
                    raise ConfigurationError(
                        "Konfigurasi baru saja berubah. Silakan muat ulang."
                    )
                if (
                    not api_key
                    and row.api_key_encrypted != existing_cipher
                ):
                    raise ConfigurationError(
                        "Konfigurasi baru saja berubah. "
                        "Silakan muat ulang."
                    )
            else:
                row = AIConfiguration()
            row.name = name
            row.provider = AIConfiguration.PROVIDER_OPENAI_COMPATIBLE
            row.base_url = base_url
            row.model = model
            if api_key:
                row.api_key_encrypted = encrypt_api_key(api_key)
            elif not row.api_key_encrypted:
                raise ConfigurationError(
                    "API key wajib diisi untuk konfigurasi baru."
                )
            row.is_enabled = enabled
            row.updated_by = user
            if enabled:
                row.last_validated_at = timezone.now()
            elif changed:
                row.last_validated_at = None
            row.save()
            if enabled:
                (
                    AIConfiguration.objects.select_for_update()
                    .filter(is_enabled=True)
                    .exclude(pk=row.pk)
                    .update(is_enabled=False)
                )
    except IntegrityError as exc:
        raise ConfigurationError(
            "Konfigurasi baru saja diubah pengguna lain. Silakan ulangi."
        ) from exc
    return row


def delete_configuration(configuration_id):
    """Hapus satu provider. Provider aktif harus diganti lebih dulu."""
    row = _configuration_by_id(configuration_id)
    if row is None:
        raise ConfigurationError("Konfigurasi provider tidak ditemukan.")
    if row.is_enabled:
        raise ConfigurationError(
            "Provider aktif tidak dapat dihapus. Aktifkan provider lain "
            "terlebih dahulu."
        )
    row.delete()


def reset_configuration():
    """Hapus semua konfigurasi tersimpan; environment menjadi fallback."""
    AIConfiguration.objects.all().delete()
