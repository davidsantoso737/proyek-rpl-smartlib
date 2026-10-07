"""P3 — Excel import & data normalization untuk provisioning SISWA/GURU.

Tahap ini HANYA parsing + normalisasi workbook ``.xlsx``; TIDAK ada
INSERT/mutasi database di modul ini (bulk creation tahap berikutnya).

- Library: ``openpyxl`` (sudah menjadi dependency project, dipakai juga
  oleh ``apps.reports``) — tidak ada dependency baru.
- Dua context terpisah: ``SISWA`` (identifier NIS) dan ``GURU``
  (identifier NIP). Jenis import menentukan role; tidak ada
  role detection dari isi cell.
- NIS/NIP selalu diperlakukan sebagai string; leading zero
  dipertahankan, tanpa konversi ke integer.
"""

import io
import re
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from openpyxl import Workbook, load_workbook

IMPORT_KINDS = ("SISWA", "GURU")

SUPPORTED_EXTENSION = ".xlsx"

MAX_IMPORT_SIZE = 5 * 1024 * 1024

IDENTIFIER_FIELD = {"SISWA": "nis", "GURU": "nip"}
IDENTIFIER_LABEL = {"SISWA": "NIS", "GURU": "NIP"}

SISWA_TEMPLATE_HEADERS = [
    "NIS",
    "Nama Lengkap",
    "Email",
    "No Telepon",
    "Kelas",
    "Jurusan",
]
GURU_TEMPLATE_HEADERS = [
    "NIP",
    "Nama Lengkap",
    "Email",
    "No Telepon",
    "Kelas",
    "Jurusan",
]

TEMPLATE_HEADERS = {
    "SISWA": SISWA_TEMPLATE_HEADERS,
    "GURU": GURU_TEMPLATE_HEADERS,
}

# Header file -> field kanonis (dicocokkan case-insensitive,
# whitespace di-collapse). Kolom tak dikenal diabaikan.
_HEADER_ALIASES = {
    "nis": "nis",
    "nip": "nip",
    "nama lengkap": "full_name",
    "nama": "full_name",
    "full name": "full_name",
    "fullname": "full_name",
    "full_name": "full_name",
    "email": "email",
    "e-mail": "email",
    "e mail": "email",
    "no telepon": "phone",
    "no. telepon": "phone",
    "telepon": "phone",
    "no telepon/hp": "phone",
    "no telepon / hp": "phone",
    "no hp": "phone",
    "no. hp": "phone",
    "hp": "phone",
    "phone": "phone",
    "kelas": "student_class",
    "class": "student_class",
    "student class": "student_class",
    "student_class": "student_class",
    "jurusan": "department",
    "jurusan / departemen": "department",
    "jurusan/departemen": "department",
    "departemen": "department",
    "department": "department",
}

_WHITESPACE_RUN = re.compile(r"\s+")


class ExcelImportError(Exception):
    """Kegagalan baca/validasi file import (pesan siap tampil)."""


def normalize_text(value):
    """Trim + collapse whitespace; tanpa title-case; angka jadi string.

    ``None``/blank menjadi ``""``. Nilai numerik integral (mis. nomor
    telepon ``8123456789`` atau ``8123456789.0``) menjadi string digit.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    if not text:
        return ""
    return _WHITESPACE_RUN.sub(" ", text)


def normalize_email(value):
    """Trim + collapse whitespace + lowercase; kosong -> ``""``."""
    return normalize_text(value).lower()


def _zero_pad_width(number_format):
    """Lebar zero-padding dari format Excel murni ``0...0`` (mis. 000000).

    Nol bila format bukan zero-pad: informasi padding tidak ada di
    file sehingga tidak direkonstruksi (anti-fabrication).
    """
    fmt = (number_format or "").strip().strip('"').strip("'")
    if fmt and set(fmt) == {"0"}:
        return len(fmt)
    return 0


def parse_identifier_cell(value, number_format=None):
    """NIS/NIP cell -> string; kembalikan ``(text, error)``.

    - ``None``/blank -> ``("", None)`` (wajib-isi ditangani per baris).
    - string -> trim apa adanya (leading zero utuh).
    - int / float integral -> string digit; bila cell berformat
      zero-pad Excel (mis. ``000000``) padding tampilan diterapkan
      sesuai metadata file (bukan karangan).
    - bool, float non-integral, tanggal, dsb. -> ``(None, pesan)``.
    """
    from datetime import date, datetime

    if value is None:
        return "", None
    if isinstance(value, bool):
        return None, "tidak valid"
    if isinstance(value, (datetime, date)):
        return None, "tidak valid"
    if isinstance(value, float):
        if not value.is_integer():
            return None, "tidak valid"
        value = int(value)
    if isinstance(value, int):
        text = str(value)
        width = _zero_pad_width(number_format)
        if width and value >= 0 and len(text) < width:
            text = text.zfill(width)
        return text, None
    text = str(value).strip()
    if not text:
        return "", None
    return text, None


def template_headers(kind):
    """Daftar header template untuk jenis import (``SISWA``/``GURU``)."""
    _require_kind(kind)
    return list(TEMPLATE_HEADERS[kind])


def template_filename(kind):
    """Nama file template yang disarankan untuk diunduh."""
    _require_kind(kind)
    return f"template-import-{kind.lower()}.xlsx"


def build_template_workbook(kind):
    """Bangun workbook template (header + 1 baris contoh) -> bytes.

    Identifier contoh ditulis sebagai teks (format ``@``) agar
    leading zero tidak hilang saat dibuka di Excel.
    """
    _require_kind(kind)
    label = IDENTIFIER_LABEL[kind]
    identifier = "006112" if kind == "SISWA" else "007889"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = kind.capitalize()
    headers = template_headers(kind)
    for col, name in enumerate(headers, start=1):
        sheet.cell(row=1, column=col, value=name)
    example = [
        identifier,
        "Andi Saputra",
        "andi@sekolah.id",
        "08123456789",
        "XII-A",
        "IPA",
    ]
    for col, value in enumerate(example, start=1):
        cell = sheet.cell(row=2, column=col, value=value)
        if col == 1:
            cell.number_format = "@"
    sheet.column_dimensions["A"].width = max(
        len(label), len(identifier)
    ) + 4
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


def _require_kind(kind):
    if kind not in IMPORT_KINDS:
        raise ExcelImportError(
            "Jenis import tidak dikenal. Gunakan 'SISWA' atau 'GURU'."
        )


def _resolve_filename(source, filename):
    name = filename or getattr(source, "name", None)
    if isinstance(name, (str, Path)):
        return str(name)
    return ""


def _source_size(source):
    size = getattr(source, "size", None)
    if isinstance(size, int):
        return size
    getbuffer = getattr(source, "getbuffer", None)
    if callable(getbuffer):
        try:
            return getbuffer().nbytes
        except (ValueError, BufferError):
            return None
    return None


def _normalize_header_cell(value):
    return normalize_text(value).lower()


def parse_import_workbook(
    source, kind, filename=None, max_size_bytes=MAX_IMPORT_SIZE
):
    """Parse workbook ``.xlsx`` menjadi baris-baris ternormalisasi.

    Tidak menyentuh database. Masalah struktural (ekstensi, file
    rusak, sheet kosong, header hilang, kolom identifier absen)
    melempar ``ExcelImportError``. Masalah per baris (identifier
    kosong/tidak valid, nama kosong, email tidak valid) dikumpulkan
    di ``errors`` tiap baris.

    Kembalian: ``{"kind", "identifier_field", "headers", "rows"}``
    dengan ``rows`` berisi ``{"row_number", "data", "errors"}``.
    """
    _require_kind(kind)
    label = IDENTIFIER_LABEL[kind]
    identifier_field = IDENTIFIER_FIELD[kind]

    display_name = _resolve_filename(source, filename)
    if display_name:
        ext = Path(display_name).suffix.lower()
        if ext != SUPPORTED_EXTENSION:
            raise ExcelImportError(
                "Format file tidak didukung. "
                "Gunakan file berekstensi .xlsx."
            )
    size = _source_size(source)
    if size is not None and size > max_size_bytes:
        raise ExcelImportError(
            "Ukuran file melebihi "
            f"{max_size_bytes // (1024 * 1024)} MB."
        )
    if hasattr(source, "seekable") and source.seekable():
        try:
            source.seek(0)
        except (OSError, ValueError):
            pass
    try:
        workbook = load_workbook(source, data_only=True)
    except Exception:
        raise ExcelImportError(
            "Berkas .xlsx tidak dapat dibaca. "
            "Pastikan file tidak rusak dan benar-benar format Excel."
        ) from None

    sheet = workbook.active
    if sheet is None:
        raise ExcelImportError("Berkas Excel kosong; tidak ada sheet.")

    header_row_idx = None
    header_cells = None
    for row in sheet.iter_rows(values_only=False):
        if any(
            cell.value is not None and str(cell.value).strip() != ""
            for cell in row
        ):
            header_row_idx = row[0].row
            header_cells = row
            break
    if header_row_idx is None:
        raise ExcelImportError(
            "Berkas Excel kosong; header tidak ditemukan."
        )

    col_to_field = {}
    for cell in header_cells:
        field = _HEADER_ALIASES.get(_normalize_header_cell(cell.value))
        if field and field not in col_to_field.values():
            col_to_field[cell.column] = field
    if identifier_field not in col_to_field.values():
        raise ExcelImportError(
            f"Kolom '{label}' tidak ditemukan pada header. "
            f"Gunakan template: {', '.join(template_headers(kind))}."
        )

    rows = []
    for row in sheet.iter_rows(
        min_row=header_row_idx + 1, values_only=False
    ):
        if all(
            cell.value is None
            or (isinstance(cell.value, str) and cell.value.strip() == "")
            for cell in row
        ):
            continue
        data = {
            "nis": "",
            "nip": "",
            "full_name": "",
            "email": "",
            "phone": "",
            "student_class": "",
            "department": "",
        }
        row_errors = []
        identifier_text = ""
        by_field = {}
        for cell in row:
            field = col_to_field.get(cell.column)
            if field:
                by_field[field] = cell
        ident_cell = by_field.get(identifier_field)
        if ident_cell is not None:
            identifier_text, ident_error = parse_identifier_cell(
                ident_cell.value, ident_cell.number_format
            )
            if ident_error:
                row_errors.append(f"{label} {ident_error}.")
            else:
                data[identifier_field] = identifier_text
        if not identifier_text and not any(
            e.startswith(label) for e in row_errors
        ):
            row_errors.append(f"{label} wajib diisi.")
        name_cell = by_field.get("full_name")
        if name_cell is not None:
            data["full_name"] = normalize_text(name_cell.value)
        if not data["full_name"]:
            row_errors.append("Nama lengkap wajib diisi.")
        email_cell = by_field.get("email")
        if email_cell is not None:
            data["email"] = normalize_email(email_cell.value)
        if data["email"]:
            try:
                validate_email(data["email"])
            except ValidationError:
                row_errors.append(
                    f"Email '{data['email']}' tidak valid."
                )
        phone_cell = by_field.get("phone")
        if phone_cell is not None:
            data["phone"] = normalize_text(phone_cell.value)
        class_cell = by_field.get("student_class")
        if class_cell is not None:
            data["student_class"] = normalize_text(class_cell.value)
        dept_cell = by_field.get("department")
        if dept_cell is not None:
            data["department"] = normalize_text(dept_cell.value)
        rows.append(
            {
                "row_number": row[0].row,
                "data": data,
                "errors": row_errors,
            }
        )

    if not rows:
        raise ExcelImportError(
            "Sheet kosong; tidak ada baris data di bawah header."
        )
    return {
        "kind": kind,
        "identifier_field": identifier_field,
        "headers": template_headers(kind),
        "rows": rows,
    }


def parsed_has_errors(parsed):
    """True bila ada baris dengan error validasi."""
    return any(row["errors"] for row in parsed["rows"])


def valid_rows(parsed):
    """Baris-baris tanpa error (kandidat import tahap berikutnya)."""
    return [row for row in parsed["rows"] if not row["errors"]]
