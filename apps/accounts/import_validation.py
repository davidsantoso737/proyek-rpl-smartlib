"""P4 — Validation engine & import preview untuk provisioning SISWA/GURU.

Menerima hasil ``parse_import_workbook`` (P3) lalu menilai tiap baris:

- ``VALID``: siap dibuat (lolos semua aturan).
- ``INVALID`` (ERROR): gagal aturan file — identifier kosong/tidak
  valid, nama kosong, email tidak valid, atau identifier duplikat
  dalam file yang sama.
- ``CONFLICT``: identifier sudah dipakai akun di database.

Modul ini HANYA membaca database (cek unik) dan TIDAK PERNAH
menulis: tidak ada overwrite, update otomatis, atau pembuatan akun.
Bulk INSERT dikerjakan tahap berikutnya setelah konfirmasi.
"""

from django.contrib.auth import get_user_model
from django.db.models import Q

from .excel_import import IDENTIFIER_FIELD, IDENTIFIER_LABEL, IMPORT_KINDS

STATUS_VALID = "VALID"
STATUS_INVALID = "INVALID"
STATUS_CONFLICT = "CONFLICT"


def validate_parsed_rows(parsed):
    """Nilai tiap baris parsed -> list hasil validasi (tanpa tulis DB).

    Tiap hasil: ``{"row_number", "data", "status", "messages"}``.
    ``messages`` selalu menyebut identifier dan nomor baris yang
    bermasalah agar actionable bagi pustakawan.
    """
    kind = parsed["kind"]
    if kind not in IMPORT_KINDS:
        from .excel_import import ExcelImportError

        raise ExcelImportError(
            "Jenis import tidak dikenal. Gunakan 'SISWA' atau 'GURU'."
        )
    label = IDENTIFIER_LABEL[kind]
    field = IDENTIFIER_FIELD[kind]
    rows = parsed.get("rows") or []

    identifiers = [
        (row["data"] or {}).get(field, "") for row in rows
    ]
    counts = {}
    for ident in identifiers:
        if ident:
            counts[ident] = counts.get(ident, 0) + 1

    taken = _find_taken_identifiers(field, list(counts))

    results = []
    for row in rows:
        data = row.get("data") or {}
        ident = data.get(field, "")
        messages = list(row.get("errors") or [])
        in_file_dup = bool(ident) and counts.get(ident, 0) > 1
        if in_file_dup:
            others = ", ".join(
                str(other.get("row_number"))
                for other in rows
                if other is not row
                and (other.get("data") or {}).get(field) == ident
            )
            messages.append(
                f"{label} '{ident}' duplikat dalam file "
                f"(baris {row.get('row_number')} dan {others})."
            )
        db_hit = taken.get(ident) if ident else None
        if db_hit:
            messages.append(
                f"{label} '{ident}' sudah dipakai "
                f"akun '{db_hit}' di database."
            )
        has_parser_errors = bool(row.get("errors"))
        if has_parser_errors or in_file_dup:
            status = STATUS_INVALID
        elif db_hit:
            status = STATUS_CONFLICT
        else:
            status = STATUS_VALID
        results.append(
            {
                "row_number": row.get("row_number"),
                "data": data,
                "status": status,
                "messages": messages,
            }
        )
    return results


def _find_taken_identifiers(field, identifiers):
    """Petakan identifier -> username pemakai (satu query, tanpa tulis)."""
    if not identifiers:
        return {}
    User = get_user_model()
    lookup = Q(username__in=identifiers) | Q(
        **{f"{field}__in": identifiers}
    )
    wanted = set(identifiers)
    taken = {}
    for username, ident in (
        User.objects.filter(lookup).values_list("username", field)
    ):
        if username in wanted:
            taken.setdefault(username, username)
        if ident and ident in wanted:
            taken.setdefault(ident, username)
    return taken


def summarize(results):
    """Ringkasan hitung: total, valid, invalid, conflict."""
    summary = {"total": len(results), "valid": 0, "invalid": 0, "conflict": 0}
    for row in results:
        if row["status"] == STATUS_VALID:
            summary["valid"] += 1
        elif row["status"] == STATUS_INVALID:
            summary["invalid"] += 1
        else:
            summary["conflict"] += 1
    return summary


def can_confirm(summary):
    """Boleh dikonfirmasi hanya bila tanpa INVALID dan CONFLICT."""
    return (
        summary["total"] > 0
        and summary["invalid"] == 0
        and summary["conflict"] == 0
    )


def build_preview(parsed):
    """Bangun payload preview: baris + ringkasan + status konfirmasi."""
    results = validate_parsed_rows(parsed)
    summary = summarize(results)
    return {
        "kind": parsed["kind"],
        "identifier_label": IDENTIFIER_LABEL[parsed["kind"]],
        "identifier_field": IDENTIFIER_FIELD[parsed["kind"]],
        "rows": results,
        "summary": summary,
        "can_confirm": can_confirm(summary),
    }
