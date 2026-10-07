"""P5 — Bulk account creation dari data import yang sudah confirmed.

Aturan akun mengikuti provisioning P1/P2:

- SISWA: role=SISWA, username=NIS, password awal=NIS (di-hash),
  must_change_password=True, is_active=True (default existing).
- GURU: role=GURU, username=NIP, password awal=NIP (di-hash),
  must_change_password=True, is_active=True.

Keamanan password: tidak ada plaintext di database (hash Django),
tidak ada password di log/exception/audit. Excel tidak memiliki
kolom password.

Transaksi: seluruh batch dalam satu ``transaction.atomic()``;
tiap baris memakai savepoint sehingga conflict yang expected
(``IntegrityError``/``ValidationError`` dari unique constraint:
username/NIS/NIP) hanya melewati baris itu dengan alasan yang
jelas — database adalah final authority dan akun existing tidak
pernah dioverwrite. Error fatal tak terduga merambat keluar dan
me-rollback SELURUH batch.
"""

import logging

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .excel_import import IDENTIFIER_FIELD, IDENTIFIER_LABEL, IMPORT_KINDS
from .provisioning import provision_guru, provision_siswa

logger = logging.getLogger(__name__)


def create_imported_accounts(kind, rows):
    """Buat akun untuk baris-baris valid -> dict hasil (tanpa password).

    ``rows``: list ``{"row_number", "data"}`` ternormalisasi (P3).
    Kembalian: ``{"kind", "total", "created", "skipped"}`` dengan
    ``created`` berisi ``{"row_number", "username", "identifier",
    "full_name"}`` dan ``skipped`` berisi ``{"row_number",
    "identifier", "reason"}``.
    """
    if kind not in IMPORT_KINDS:
        raise ValueError(
            "Jenis import tidak dikenal. Gunakan 'SISWA' atau 'GURU'."
        )
    label = IDENTIFIER_LABEL[kind]
    field = IDENTIFIER_FIELD[kind]
    provision = provision_siswa if kind == "SISWA" else provision_guru

    created = []
    skipped = []
    with transaction.atomic():
        for row in rows:
            data = row.get("data") or {}
            identifier = data.get(field, "")
            try:
                with transaction.atomic():
                    user = provision(
                        identifier,
                        data.get("full_name", ""),
                        email=data.get("email", ""),
                        phone=data.get("phone", ""),
                        student_class=data.get("student_class", ""),
                        department=data.get("department", ""),
                        is_active=True,
                    )
            except (IntegrityError, ValidationError):
                # Final DB authority: konflik username/NIS/NIP yang
                # lolos dari validasi preview (race/keadaan baru).
                # Alasan generik yang actionable; detail exception
                # tidak diekspos (dan tidak memuat password).
                logger.warning(
                    "Import %s baris %s dilewati: %s sudah dipakai.",
                    kind,
                    row.get("row_number"),
                    label,
                )
                skipped.append(
                    {
                        "row_number": row.get("row_number"),
                        "identifier": identifier,
                        "reason": (
                            f"{label} '{identifier}' sudah dipakai "
                            "akun lain di database."
                        ),
                    }
                )
                continue
            created.append(
                {
                    "row_number": row.get("row_number"),
                    "username": user.username,
                    "identifier": identifier,
                    "full_name": user.full_name,
                }
            )
    return {
        "kind": kind,
        "total": len(rows),
        "created": created,
        "skipped": skipped,
        "created_count": len(created),
        "skipped_count": len(skipped),
    }
