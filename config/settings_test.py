"""Isolated settings for the SmartLib baseline test run.

This module is intentionally separate from config.settings. It forces a
dedicated MySQL schema and blanks every external integration before the
main settings module reads the local .env file.
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

TEST_DB_NAME = (
    os.environ.get("SMARTLIB_TEST_DB_NAME", "smartlib_test_baseline").strip()
)
if not TEST_DB_NAME or TEST_DB_NAME == "digital_library":
    raise RuntimeError(
        "SMARTLIB_TEST_DB_NAME must be set to a dedicated test database."
    )

os.environ["DB_NAME"] = TEST_DB_NAME
os.environ["SECRET_KEY"] = (
    "django-insecure-smartlib-test-only-"
    "do-not-use-in-production-0123456789abcdefghijklmnopqrstuvwxyz"
)
os.environ["DEBUG"] = "False"
os.environ["ALLOWED_HOSTS"] = "localhost,127.0.0.1,testserver"

os.environ["AI_PROVIDER_BASE_URL"] = ""
os.environ["AI_API_KEY"] = ""
os.environ["AI_MODEL"] = ""
os.environ["AI_SETTINGS_ENCRYPTION_KEY"] = ""
os.environ["AI_PROVIDER_ALLOWED_HOSTS"] = ""
os.environ["AI_NODE_URL"] = ""
os.environ["AI_NODE_SHARED_SECRET"] = ""

os.environ["FONNTE_API_TOKEN"] = ""
os.environ["FONNTE_API_URL"] = ""
os.environ["WHATSAPP_NOTIFICATIONS_ENABLED"] = "False"

from .settings import *  # noqa: E402,F403


TEST_STORAGE_ROOT = Path(tempfile.mkdtemp(prefix="smartlib_test_storage_"))
atexit.register(shutil.rmtree, TEST_STORAGE_ROOT, ignore_errors=True)

EBOOK_STORAGE_ROOT = TEST_STORAGE_ROOT / "ebooks"
MATERIAL_STORAGE_ROOT = TEST_STORAGE_ROOT / "materials"

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "smartlib-test-cache",
    }
}

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]
