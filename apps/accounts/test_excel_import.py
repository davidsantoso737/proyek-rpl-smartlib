"""P3 — Excel import & data normalization tests (parser only).

Tahap ini TIDAK melakukan INSERT user; setiap test keberhasilan
parsing menegaskan ``User.objects.count() == 0``.
"""

import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from openpyxl import Workbook

from apps.accounts.excel_import import (
    ExcelImportError,
    build_template_workbook,
    parse_import_workbook,
    parsed_has_errors,
    template_headers,
    valid_rows,
)

User = get_user_model()

SISWA_HEADERS = [
    "NIS",
    "Nama Lengkap",
    "Email",
    "No Telepon",
    "Kelas",
    "Jurusan",
]
GURU_HEADERS = [
    "NIP",
    "Nama Lengkap",
    "Email",
    "No Telepon",
    "Kelas",
    "Jurusan",
]


def make_workbook(headers, rows, formats=None):
    """Bangun bytes .xlsx; formats = {(r, c): number_format} 1-based."""
    workbook = Workbook()
    sheet = workbook.active
    for col, name in enumerate(headers, start=1):
        sheet.cell(row=1, column=col, value=name)
    for r, row in enumerate(rows, start=2):
        for col, value in enumerate(row, start=1):
            cell = sheet.cell(row=r, column=col, value=value)
            fmt = (formats or {}).get((r, col))
            if fmt:
                cell.number_format = fmt
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def upload(content, name="import.xlsx"):
    return SimpleUploadedFile(
        name,
        content,
        content_type=(
            "application/vnd.openxmlformats-officedocument"
            ".spreadsheetml.sheet"
        ),
    )


class ValidWorkbookTests(TestCase):
    def test_valid_siswa_tanpa_insert(self):
        parsed = parse_import_workbook(
            upload(
                make_workbook(
                    SISWA_HEADERS,
                    [
                        [
                            "006112",
                            "Andi Saputra",
                            "andi@sekolah.id",
                            "08123456789",
                            "XII-A",
                            "IPA",
                        ]
                    ],
                )
            ),
            "SISWA",
        )
        self.assertEqual(parsed["kind"], "SISWA")
        self.assertEqual(len(parsed["rows"]), 1)
        row = parsed["rows"][0]
        self.assertEqual(row["errors"], [])
        self.assertEqual(
            row["data"],
            {
                "nis": "006112",
                "nip": "",
                "full_name": "Andi Saputra",
                "email": "andi@sekolah.id",
                "phone": "08123456789",
                "student_class": "XII-A",
                "department": "IPA",
            },
        )
        self.assertFalse(parsed_has_errors(parsed))
        self.assertEqual(len(valid_rows(parsed)), 1)
        self.assertEqual(User.objects.count(), 0)

    def test_valid_guru_tanpa_insert(self):
        parsed = parse_import_workbook(
            upload(
                make_workbook(
                    GURU_HEADERS,
                    [
                        [
                            "007889",
                            "Ibu Guru",
                            "guru@sekolah.id",
                            "0819000001",
                            "XII-A",
                            "Matematika",
                        ]
                    ],
                )
            ),
            "GURU",
        )
        row = parsed["rows"][0]
        self.assertEqual(row["errors"], [])
        self.assertEqual(row["data"]["nip"], "007889")
        self.assertEqual(row["data"]["full_name"], "Ibu Guru")
        self.assertEqual(User.objects.count(), 0)

    def test_kind_menentukan_identifier_bukan_tebakan(self):
        parsed = parse_import_workbook(
            upload(make_workbook(GURU_HEADERS, [["007889", "Guru"]])),
            "GURU",
        )
        self.assertEqual(parsed["identifier_field"], "nip")


class NormalizationTests(TestCase):
    def _parse_siswa_row(self, nis, name, email="a@b.id"):
        parsed = parse_import_workbook(
            upload(make_workbook(SISWA_HEADERS, [[nis, name, email]])),
            "SISWA",
        )
        return parsed["rows"][0]

    def test_trim_whitespace(self):
        row = self._parse_siswa_row(" 006112 ", "  Andi Saputra  ")
        self.assertEqual(row["errors"], [])
        self.assertEqual(row["data"]["nis"], "006112")
        self.assertEqual(row["data"]["full_name"], "Andi Saputra")

    def test_collapse_multiple_whitespace_tanpa_title_case(self):
        row = self._parse_siswa_row("006112", "  Andi   Saputra  ")
        self.assertEqual(row["data"]["full_name"], "Andi Saputra")
        row = self._parse_siswa_row("006113", "aNDI sAPUTRA")
        self.assertEqual(row["data"]["full_name"], "aNDI sAPUTRA")

    def test_email_trim_lowercase(self):
        row = self._parse_siswa_row("006112", "Andi", " USER@EMAIL.COM ")
        self.assertEqual(row["data"]["email"], "user@email.com")

    def test_leading_zero_teks_dipertahankan(self):
        row = self._parse_siswa_row("006112", "Andi")
        self.assertEqual(row["data"]["nis"], "006112")

    def test_identifier_numerik_tidak_hilang(self):
        parsed = parse_import_workbook(
            upload(make_workbook(SISWA_HEADERS, [[6112, "Andi"]])),
            "SISWA",
        )
        row = parsed["rows"][0]
        self.assertEqual(row["errors"], [])
        self.assertEqual(row["data"]["nis"], "6112")

    def test_identifier_float_integral(self):
        parsed = parse_import_workbook(
            upload(make_workbook(SISWA_HEADERS, [[6112.0, "Andi"]])),
            "SISWA",
        )
        self.assertEqual(parsed["rows"][0]["data"]["nis"], "6112")

    def test_identifier_formatted_zero_pad(self):
        parsed = parse_import_workbook(
            upload(
                make_workbook(
                    SISWA_HEADERS,
                    [[6112, "Andi"]],
                    formats={(2, 1): "000000"},
                )
            ),
            "SISWA",
        )
        row = parsed["rows"][0]
        self.assertEqual(row["errors"], [])
        self.assertEqual(row["data"]["nis"], "006112")

    def test_blank_optional_fields_konsisten(self):
        parsed = parse_import_workbook(
            upload(
                make_workbook(
                    SISWA_HEADERS, [["006112", "Andi", None, None, None, None]]
                )
            ),
            "SISWA",
        )
        row = parsed["rows"][0]
        self.assertEqual(row["errors"], [])
        self.assertEqual(row["data"]["email"], "")
        self.assertEqual(row["data"]["phone"], "")
        self.assertEqual(row["data"]["student_class"], "")
        self.assertEqual(row["data"]["department"], "")


class RowErrorTests(TestCase):
    def test_identifier_kosong_jadi_row_error(self):
        parsed = parse_import_workbook(
            upload(
                make_workbook(
                    SISWA_HEADERS,
                    [["006112", "Andi"], ["", "Budi"]],
                )
            ),
            "SISWA",
        )
        self.assertEqual(len(parsed["rows"]), 2)
        self.assertEqual(parsed["rows"][0]["errors"], [])
        self.assertTrue(
            any("NIS" in e for e in parsed["rows"][1]["errors"])
        )
        self.assertTrue(parsed_has_errors(parsed))
        self.assertEqual(len(valid_rows(parsed)), 1)
        self.assertEqual(User.objects.count(), 0)

    def test_identifier_float_pecahan_tidak_valid(self):
        parsed = parse_import_workbook(
            upload(make_workbook(SISWA_HEADERS, [[61.12, "Andi"]])),
            "SISWA",
        )
        row = parsed["rows"][0]
        self.assertTrue(any("NIS" in e for e in row["errors"]))
        self.assertEqual(User.objects.count(), 0)

    def test_email_tidak_valid_jadi_row_error(self):
        parsed = parse_import_workbook(
            upload(
                make_workbook(
                    SISWA_HEADERS, [["006112", "Andi", "bukan-email"]]
                )
            ),
            "SISWA",
        )
        row = parsed["rows"][0]
        self.assertTrue(any("Email" in e for e in row["errors"]))
        self.assertEqual(User.objects.count(), 0)

    def test_baris_kosong_diabaikan(self):
        parsed = parse_import_workbook(
            upload(
                make_workbook(
                    SISWA_HEADERS,
                    [["006112", "Andi"], [None, None], ["006113", "Budi"]],
                )
            ),
            "SISWA",
        )
        self.assertEqual(len(parsed["rows"]), 2)
        self.assertEqual(
            [r["row_number"] for r in parsed["rows"]], [2, 4]
        )


class StructuralErrorTests(TestCase):
    def test_unsupported_extension_xls_ditolak(self):
        with self.assertRaisesRegex(ExcelImportError, r"\.xlsx"):
            parse_import_workbook(
                upload(make_workbook(SISWA_HEADERS, []), name="data.xls"),
                "SISWA",
            )

    def test_unsupported_extension_csv_ditolak(self):
        with self.assertRaisesRegex(ExcelImportError, r"\.xlsx"):
            parse_import_workbook(
                upload(b"a,b\n1,2", name="data.csv"), "SISWA"
            )

    def test_malformed_workbook_error_jelas(self):
        with self.assertRaisesRegex(
            ExcelImportError, "tidak dapat dibaca"
        ):
            parse_import_workbook(
                upload(b"bukan-isi-excel", name="rusak.xlsx"), "SISWA"
            )
        self.assertEqual(User.objects.count(), 0)

    def test_empty_workbook_ditolak(self):
        workbook = Workbook()
        sheet = workbook.active
        _ = sheet  # tanpa data sama sekali
        buffer = io.BytesIO()
        workbook.save(buffer)
        with self.assertRaisesRegex(ExcelImportError, "[Kk]osong"):
            parse_import_workbook(
                upload(buffer.getvalue()), "SISWA"
            )

    def test_header_saja_tanpa_data_ditolak(self):
        with self.assertRaisesRegex(ExcelImportError, "[Kk]osong"):
            parse_import_workbook(
                upload(make_workbook(SISWA_HEADERS, [])), "SISWA"
            )

    def test_missing_identifier_column(self):
        headers = ["Nama Lengkap", "Email", "Kelas"]
        with self.assertRaisesRegex(ExcelImportError, "NIS"):
            parse_import_workbook(
                upload(make_workbook(headers, [["Andi", "a@b.id", "X"]])),
                "SISWA",
            )
        with self.assertRaisesRegex(ExcelImportError, "NIP"):
            parse_import_workbook(
                upload(make_workbook(headers, [["Guru", "g@b.id", "X"]])),
                "GURU",
            )
        self.assertEqual(User.objects.count(), 0)

    def test_header_case_insensitive_dan_alias(self):
        parsed = parse_import_workbook(
            upload(
                make_workbook(
                    ["nis", "NAMA LENGKAP", "e-mail", "HP", "class", "Jurusan"],
                    [["006112", "Andi", "A@B.ID", "081", "XII", "IPA"]],
                )
            ),
            "SISWA",
        )
        row = parsed["rows"][0]
        self.assertEqual(row["errors"], [])
        self.assertEqual(row["data"]["nis"], "006112")
        self.assertEqual(row["data"]["email"], "a@b.id")

    def test_kind_tidak_dikenal_ditolak(self):
        with self.assertRaises(ExcelImportError):
            parse_import_workbook(
                upload(make_workbook(SISWA_HEADERS, [])), "ADMIN"
            )

    def test_file_terlalu_besar_ditolak(self):
        with self.assertRaisesRegex(ExcelImportError, "[Mm]elebihi"):
            parse_import_workbook(
                upload(make_workbook(SISWA_HEADERS, [["1", "A"]])),
                "SISWA",
                max_size_bytes=10,
            )


class TemplateTests(TestCase):
    def test_template_headers_sesuai_spesifikasi(self):
        self.assertEqual(
            template_headers("SISWA"),
            ["NIS", "Nama Lengkap", "Email", "No Telepon", "Kelas", "Jurusan"],
        )
        self.assertEqual(
            template_headers("GURU"),
            ["NIP", "Nama Lengkap", "Email", "No Telepon", "Kelas", "Jurusan"],
        )

    def test_template_roundtrip_siswa(self):
        parsed = parse_import_workbook(
            upload(build_template_workbook("SISWA")), "SISWA"
        )
        self.assertEqual(len(parsed["rows"]), 1)
        row = parsed["rows"][0]
        self.assertEqual(row["errors"], [])
        self.assertEqual(row["data"]["nis"], "006112")
        self.assertEqual(User.objects.count(), 0)

    def test_template_roundtrip_guru(self):
        parsed = parse_import_workbook(
            upload(build_template_workbook("GURU")), "GURU"
        )
        row = parsed["rows"][0]
        self.assertEqual(row["errors"], [])
        self.assertEqual(row["data"]["nip"], "007889")
        self.assertEqual(User.objects.count(), 0)
