"""P4 — Validation engine & import preview tests.

Tahap ini TIDAK membuat akun: setiap alur (termasuk konfirmasi
dataset valid) menegaskan tidak ada user baru di database.
"""

import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from openpyxl import Workbook

from apps.accounts.excel_import import parse_import_workbook
from apps.accounts.import_validation import (
    STATUS_CONFLICT,
    STATUS_INVALID,
    STATUS_VALID,
    build_preview,
    can_confirm,
    summarize,
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


def make_workbook(headers, rows):
    workbook = Workbook()
    sheet = workbook.active
    for col, name in enumerate(headers, start=1):
        sheet.cell(row=1, column=col, value=name)
    for r, row in enumerate(rows, start=2):
        for col, value in enumerate(row, start=1):
            sheet.cell(row=r, column=col, value=value)
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


def siswa_row(nis, name="Siswa", email="siswa@sekolah.id"):
    return [nis, name, email, "08123456789", "XII-A", "IPA"]


class ValidationEngineTests(TestCase):
    def _preview(self, headers, rows, kind="SISWA"):
        parsed = parse_import_workbook(
            upload(make_workbook(headers, rows)), kind
        )
        return build_preview(parsed)

    def _make_user(self, username, role, password="secret123", **extra):
        defaults = {"full_name": f"{username} Full"}
        if role == User.Role.SISWA:
            defaults.setdefault("nis", f"nis-{username}")
        elif role == User.Role.GURU:
            defaults.setdefault("nip", f"nip-{username}")
        defaults.update(extra)
        user = User(username=username, role=role, **defaults)
        user.set_password(password)
        user.full_clean()
        user.save()
        return user

    def test_valid_dataset_semua_valid(self):
        preview = self._preview(
            SISWA_HEADERS,
            [siswa_row("006112"), siswa_row("006113", "Budi")],
        )
        self.assertEqual(
            preview["summary"],
            {"total": 2, "valid": 2, "invalid": 0, "conflict": 0},
        )
        self.assertTrue(preview["can_confirm"])
        for row in preview["rows"]:
            self.assertEqual(row["status"], STATUS_VALID)
        self.assertEqual(User.objects.count(), 0)

    def test_duplicate_dalam_file_error(self):
        preview = self._preview(
            SISWA_HEADERS,
            [siswa_row("006112", "Andi"), siswa_row("006112", "Andi X")],
        )
        self.assertEqual(preview["summary"]["invalid"], 2)
        self.assertFalse(preview["can_confirm"])
        for row in preview["rows"]:
            self.assertEqual(row["status"], STATUS_INVALID)
            text = " ".join(row["messages"])
            self.assertIn("006112", text)
            self.assertIn("duplikat dalam file", text)
        self.assertEqual(User.objects.count(), 0)

    def test_duplicate_database_conflict_tanpa_overwrite(self):
        self._make_user("siswa1", User.Role.SISWA, nis="10001")
        preview = self._preview(
            SISWA_HEADERS, [siswa_row("10001", "Siswa Baru")]
        )
        row = preview["rows"][0]
        self.assertEqual(row["status"], STATUS_CONFLICT)
        self.assertTrue(
            any("siswa1" in m for m in row["messages"])
        )
        self.assertEqual(preview["summary"]["conflict"], 1)
        self.assertFalse(preview["can_confirm"])
        # Akun existing aman.
        original = User.objects.get(nis="10001")
        self.assertEqual(original.username, "siswa1")
        self.assertEqual(original.full_name, "siswa1 Full")
        self.assertTrue(original.check_password("secret123"))
        self.assertEqual(User.objects.count(), 1)

    def test_duplicate_username_lama_terdeteksi_conflict(self):
        # Akun lama yang username-nya sama dengan NIS import.
        self._make_user("006112", User.Role.SISWA, nis="99999")
        preview = self._preview(
            SISWA_HEADERS, [siswa_row("006112", "Andi")]
        )
        self.assertEqual(
            preview["rows"][0]["status"], STATUS_CONFLICT
        )

    def test_missing_nis_invalid(self):
        preview = self._preview(SISWA_HEADERS, [["", "Tanpa NIS"]])
        row = preview["rows"][0]
        self.assertEqual(row["status"], STATUS_INVALID)
        self.assertTrue(any("NIS" in m for m in row["messages"]))
        self.assertFalse(preview["can_confirm"])

    def test_missing_nip_invalid(self):
        preview = self._preview(
            GURU_HEADERS, [["", "Tanpa NIP"]], kind="GURU"
        )
        row = preview["rows"][0]
        self.assertEqual(row["status"], STATUS_INVALID)
        self.assertTrue(any("NIP" in m for m in row["messages"]))

    def test_invalid_email_tidak_valid(self):
        preview = self._preview(
            SISWA_HEADERS, [siswa_row("006112", "Andi", "bukan-email")]
        )
        self.assertEqual(
            preview["rows"][0]["status"], STATUS_INVALID
        )
        self.assertFalse(preview["can_confirm"])
        self.assertEqual(User.objects.count(), 0)

    def test_mixed_whitespace_ternormalisasi_dan_valid(self):
        preview = self._preview(
            SISWA_HEADERS,
            [[" 006112 ", "  Andi   Saputra  ", " ANDI@SEKOLAH.ID "]],
        )
        row = preview["rows"][0]
        self.assertEqual(row["status"], STATUS_VALID)
        self.assertEqual(row["data"]["nis"], "006112")
        self.assertEqual(row["data"]["full_name"], "Andi Saputra")
        self.assertEqual(row["data"]["email"], "andi@sekolah.id")

    def test_summary_campuran(self):
        self._make_user("siswaDB", User.Role.SISWA, nis="20001")
        preview = self._preview(
            SISWA_HEADERS,
            [
                siswa_row("006112", "Valid"),
                siswa_row("006113", "Dup A"),
                siswa_row("006113", "Dup B"),
                siswa_row("20001", "Conflict"),
                siswa_row("006114", "Bad", "bukan-email"),
            ],
        )
        self.assertEqual(
            preview["summary"],
            {"total": 5, "valid": 1, "invalid": 3, "conflict": 1},
        )
        self.assertFalse(preview["can_confirm"])
        self.assertEqual(User.objects.count(), 1)

    def test_can_confirm_butuh_baris_valid(self):
        self.assertFalse(
            can_confirm({"total": 0, "valid": 0, "invalid": 0, "conflict": 0})
        )
        self.assertTrue(
            can_confirm({"total": 2, "valid": 2, "invalid": 0, "conflict": 0})
        )

    def test_summarize_kosong(self):
        self.assertEqual(
            summarize([]),
            {"total": 0, "valid": 0, "invalid": 0, "conflict": 0},
        )


class ImportPreviewViewTests(TestCase):
    def setUp(self):
        self.pus = self._make_user("pus1", User.Role.PUSTAKAWAN)
        self.siswa = self._make_user(
            "siswa1", User.Role.SISWA, nis="10001"
        )
        self.guru = self._make_user(
            "guru1", User.Role.GURU, nip="20001"
        )

    def _make_user(self, username, role, password="secret123", **extra):
        defaults = {"full_name": f"{username} Full"}
        if role == User.Role.SISWA:
            defaults.setdefault("nis", f"nis-{username}")
        elif role == User.Role.GURU:
            defaults.setdefault("nip", f"nip-{username}")
        defaults.update(extra)
        user = User(username=username, role=role, **defaults)
        user.set_password(password)
        user.full_clean()
        user.save()
        return user

    def _upload_url(self, kind="SISWA"):
        return reverse("accounts:import_upload", args=[kind])

    def _confirm_url(self, kind="SISWA"):
        return reverse("accounts:import_confirm", args=[kind])

    def _post_file(self, rows, kind="SISWA", headers=None, name="import.xlsx"):
        self.client.force_login(self.pus)
        return self.client.post(
            self._upload_url(kind),
            {
                "file": upload(
                    make_workbook(headers or SISWA_HEADERS, rows), name
                )
            },
        )

    def test_upload_get_hanya_pustakawan(self):
        self.client.force_login(self.pus)
        response = self.client.get(self._upload_url("SISWA"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(response, "template")

    def test_non_pustakawan_ditolak(self):
        for user in (self.siswa, self.guru):
            self.client.force_login(user)
            self.assertEqual(
                self.client.get(self._upload_url("SISWA")).status_code, 403
            )
            self.assertEqual(
                self.client.post(
                    self._upload_url("SISWA"),
                    {
                        "file": upload(
                            make_workbook(SISWA_HEADERS, [siswa_row("1")])
                        )
                    },
                ).status_code,
                403,
            )
            self.assertEqual(
                self.client.post(self._confirm_url("SISWA")).status_code,
                403,
            )
        self.client.logout()
        response = self.client.get(self._upload_url("SISWA"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_kind_tidak_dikenal_ditolak(self):
        self.client.force_login(self.pus)
        self.assertEqual(
            self.client.get(
                reverse("accounts:import_upload", args=["PUSTAKAWAN"])
            ).status_code,
            403,
        )

    def test_upload_valid_menampilkan_preview_normalisasi(self):
        before = User.objects.count()
        response = self._post_file(
            [[" 006112 ", "  Andi   Saputra  ", " ANDI@SEKOLAH.ID "]]
        )
        self.assertEqual(response.status_code, 200)
        preview = response.context["preview"]
        self.assertTrue(preview["can_confirm"])
        self.assertEqual(
            preview["summary"],
            {"total": 1, "valid": 1, "invalid": 0, "conflict": 0},
        )
        self.assertContains(response, "006112")
        self.assertContains(response, "Andi Saputra")
        self.assertContains(response, "andi@sekolah.id")
        self.assertContains(response, "Total")
        self.assertContains(response, "Valid")
        self.assertContains(response, "Konfirmasi 1 akun valid")
        self.assertEqual(User.objects.count(), before)

    def test_upload_invalid_menampilkan_alasan_per_baris(self):
        before = User.objects.count()
        response = self._post_file(
            [
                siswa_row("10001", "Conflict"),
                ["", "Tanpa NIS"],
                siswa_row("006115", "Bad", "bukan-email"),
            ]
        )
        self.assertEqual(response.status_code, 200)
        preview = response.context["preview"]
        self.assertFalse(preview["can_confirm"])
        self.assertEqual(preview["summary"]["total"], 3)
        self.assertContains(response, "Conflict")
        self.assertContains(response, "siswa1")
        self.assertContains(response, "Belum dapat dikonfirmasi")
        self.assertEqual(User.objects.count(), before)

    def test_file_rusak_dan_spoof_ditolak_jelas(self):
        self.client.force_login(self.pus)
        response = self.client.post(
            self._upload_url("SISWA"),
            {"file": upload(b"bukan-excel", name="data.xlsx")},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "tidak dapat dibaca")
        response = self.client.post(
            self._upload_url("SISWA"),
            {"file": upload(b"PK-palsu", name="data.xlsx.exe")},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, ".xlsx")
        self.assertEqual(User.objects.count(), 3)

    def test_file_kosong_ditolak(self):
        self.client.force_login(self.pus)
        response = self.client.post(self._upload_url("SISWA"), {})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pilih berkas")

    def test_invalid_dataset_tidak_dapat_dikonfirmasi(self):
        before = User.objects.count()
        self._post_file(
            [siswa_row("10001", "Conflict"), ["", "Tanpa NIS"]]
        )
        response = self.client.post(self._confirm_url("SISWA"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["preview"]["can_confirm"])
        self.assertContains(response, "Masih ada baris error atau conflict")
        self.assertEqual(User.objects.count(), before)

    def test_confirm_valid_membuat_akun_dan_menampilkan_hasil(self):
        before = User.objects.count()
        self._post_file([siswa_row("006112"), siswa_row("006113", "Budi")])
        response = self.client.post(self._confirm_url("SISWA"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/import_result.html")
        self.assertContains(response, "Import berhasil")
        self.assertContains(response, "006112")
        self.assertEqual(User.objects.count(), before + 2)
        created = User.objects.get(username="006112")
        self.assertTrue(created.must_change_password)
        self.assertTrue(created.check_password("006112"))

    def test_confirm_revalidasi_menemukan_conflict_baru(self):
        self._post_file([siswa_row("006116", "Andi")])
        self._make_user("baru", User.Role.SISWA, nis="006116")
        response = self.client.post(self._confirm_url("SISWA"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["preview"]["can_confirm"])
        self.assertContains(response, "Conflict")
        self.assertContains(response, "006116")
        self.assertEqual(User.objects.count(), 4)

    def test_confirm_tanpa_session_ditolak(self):
        self.client.force_login(self.pus)
        response = self.client.post(self._confirm_url("SISWA"))
        self.assertRedirects(response, self._upload_url("SISWA"))
        self.assertEqual(User.objects.count(), 3)

    def test_csrf_tetap_aktif(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.pus)
        response = client.post(
            self._upload_url("SISWA"),
            {
                "file": upload(
                    make_workbook(SISWA_HEADERS, [siswa_row("006112")])
                )
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_template_download(self):
        self.client.force_login(self.pus)
        response = self.client.get(
            reverse("accounts:import_template", args=["SISWA"])
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("spreadsheetml.sheet", response["Content-Type"])
        parsed = parse_import_workbook(
            io.BytesIO(response.content), "SISWA", filename="t.xlsx"
        )
        self.assertEqual(parsed["rows"][0]["data"]["nis"], "006112")
        self.client.force_login(self.siswa)
        self.assertEqual(
            self.client.get(
                reverse("accounts:import_template", args=["SISWA"])
            ).status_code,
            403,
        )
