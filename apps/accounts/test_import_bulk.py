"""P5 — Bulk account creation tests (database test).

Mencakup: bulk siswa/guru, hashing, flag, username, leading zero,
atomic rollback, duplicate protection, existing tidak tertimpa.
"""

import io
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from apps.accounts.import_creation import create_imported_accounts

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


def siswa_data(nis, name="Siswa", email="siswa@sekolah.id"):
    return {
        "nis": nis,
        "nip": "",
        "full_name": name,
        "email": email,
        "phone": "08123456789",
        "student_class": "XII-A",
        "department": "IPA",
    }


def guru_data(nip, name="Guru", email="guru@sekolah.id"):
    return {
        "nis": "",
        "nip": nip,
        "full_name": name,
        "email": email,
        "phone": "0819000001",
        "student_class": "",
        "department": "Matematika",
    }


def siswa_row(nis, name="Siswa", email="siswa@sekolah.id"):
    return [nis, name, email, "08123456789", "XII-A", "IPA"]


def guru_row(nip, name="Guru", email="guru@sekolah.id"):
    return [nip, name, email, "0819000001", "", "Matematika"]


class BulkCreationServiceTests(TestCase):
    def test_bulk_create_siswa(self):
        result = create_imported_accounts(
            "SISWA",
            [
                {"row_number": 2, "data": siswa_data("006112")},
                {"row_number": 3, "data": siswa_data("006113", "Budi")},
            ],
        )
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["created_count"], 2)
        self.assertEqual(result["skipped_count"], 0)
        for item in result["created"]:
            # Hasil tidak memuat password dalam bentuk apa pun.
            self.assertNotIn("password", item)
        users = User.objects.filter(username__in=["006112", "006113"])
        self.assertEqual(users.count(), 2)
        for user in users:
            self.assertEqual(user.role, User.Role.SISWA)
            self.assertTrue(user.is_active)
            self.assertTrue(user.must_change_password)

    def test_bulk_create_guru(self):
        result = create_imported_accounts(
            "GURU",
            [{"row_number": 2, "data": guru_data("007889")}],
        )
        self.assertEqual(result["created_count"], 1)
        user = User.objects.get(username="007889")
        self.assertEqual(user.role, User.Role.GURU)
        self.assertEqual(user.nip, "007889")
        self.assertTrue(user.must_change_password)
        self.assertTrue(user.check_password("007889"))

    def test_password_hashing_username_leading_zero(self):
        create_imported_accounts(
            "SISWA",
            [{"row_number": 2, "data": siswa_data("006112", "Andi")}],
        )
        user = User.objects.get(nis="006112")
        self.assertEqual(user.username, "006112")
        self.assertNotEqual(user.password, "006112")
        self.assertTrue(user.check_password("006112"))
        self.assertFalse(User.objects.filter(username="6112").exists())

    def test_duplicate_database_dilewati_tanpa_overwrite(self):
        existing = User(username="siswa1", role=User.Role.SISWA,
                        full_name="Siswa Satu", nis="10001")
        existing.set_password("secret123")
        existing.full_clean()
        existing.save()
        result = create_imported_accounts(
            "SISWA",
            [
                {"row_number": 2, "data": siswa_data("10001", "Baru")},
                {"row_number": 3, "data": siswa_data("006119", "Cici")},
            ],
        )
        self.assertEqual(result["created_count"], 1)
        self.assertEqual(result["skipped_count"], 1)
        skipped = result["skipped"][0]
        self.assertEqual(skipped["row_number"], 2)
        self.assertEqual(skipped["identifier"], "10001")
        self.assertIn("sudah dipakai", skipped["reason"])
        original = User.objects.get(nis="10001")
        self.assertEqual(original.username, "siswa1")
        self.assertEqual(original.full_name, "Siswa Satu")
        self.assertTrue(original.check_password("secret123"))
        self.assertTrue(
            User.objects.filter(username="006119").exists()
        )

    def test_integrity_error_dilewati_graceful(self):
        from apps.accounts import import_creation

        real_provision = import_creation.provision_siswa

        def flaky(nis, full_name, **extra):
            if nis == "006113":
                raise IntegrityError("duplicate key")
            return real_provision(nis, full_name, **extra)

        with mock.patch.object(
            import_creation, "provision_siswa", side_effect=flaky
        ):
            result = create_imported_accounts(
                "SISWA",
                [
                    {"row_number": 2, "data": siswa_data("006112")},
                    {"row_number": 3, "data": siswa_data("006113")},
                ],
            )
        self.assertEqual(result["created_count"], 1)
        self.assertEqual(result["skipped_count"], 1)
        self.assertTrue(
            User.objects.filter(username="006112").exists()
        )
        self.assertFalse(
            User.objects.filter(username="006113").exists()
        )

    def test_unexpected_failure_rollback_seluruh_batch(self):
        from apps.accounts import import_creation

        real_provision = import_creation.provision_siswa

        def flaky(nis, full_name, **extra):
            if nis == "006113":
                raise RuntimeError("koneksi database putus")
            return real_provision(nis, full_name, **extra)

        with mock.patch.object(
            import_creation, "provision_siswa", side_effect=flaky
        ):
            with self.assertRaises(RuntimeError):
                create_imported_accounts(
                    "SISWA",
                    [
                        {"row_number": 2, "data": siswa_data("006112")},
                        {"row_number": 3, "data": siswa_data("006113")},
                        {"row_number": 4, "data": siswa_data("006114")},
                    ],
                )
        # Tidak ada sebagian batch yang tersisa.
        self.assertEqual(User.objects.count(), 0)


class BulkCreationViewTests(TestCase):
    def setUp(self):
        self.pus = self._make_user("pus1", User.Role.PUSTAKAWAN)

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

    def _upload_and_confirm(self, headers, rows, kind="SISWA"):
        self.client.force_login(self.pus)
        self.client.post(
            reverse("accounts:import_upload", args=[kind]),
            {"file": upload(make_workbook(headers, rows))},
        )
        return self.client.post(
            reverse("accounts:import_confirm", args=[kind])
        )

    def test_full_flow_siswa_dibuat_dan_bisa_login_pertama(self):
        response = self._upload_and_confirm(
            SISWA_HEADERS,
            [siswa_row("006112"), siswa_row("006113", "Budi")],
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/import_result.html")
        self.assertContains(response, "Import berhasil")
        self.assertContains(response, "Total data")
        self.assertContains(response, "Berhasil dibuat")
        self.assertContains(response, "Password awal siswa = NIS")
        self.assertContains(response, "wajib mengganti password")
        for username in ("006112", "006113"):
            user = User.objects.get(username=username)
            self.assertTrue(user.check_password(username))
            self.assertTrue(user.must_change_password)
        # Login pertama dipaksa ganti password (P1 enforcement).
        self.assertTrue(
            self.client.login(username="006112", password="006112")
        )

    def test_full_flow_guru(self):
        response = self._upload_and_confirm(
            GURU_HEADERS,
            [guru_row("007889"), guru_row("007890", "Guru Dua")],
            kind="GURU",
        )
        self.assertContains(response, "Import berhasil")
        self.assertContains(response, "Password awal guru = NIP")
        self.assertEqual(
            User.objects.filter(role=User.Role.GURU).count(), 2
        )
        user = User.objects.get(username="007889")
        self.assertTrue(user.check_password("007889"))

    def test_conflict_saat_creation_dilewati_dengan_alasan(self):
        self._make_user("lama", User.Role.SISWA, nis="10001")
        with mock.patch(
            "apps.accounts.import_validation._find_taken_identifiers",
            return_value={},
        ):
            self.client.force_login(self.pus)
            self.client.post(
                reverse("accounts:import_upload", args=["SISWA"]),
                {
                    "file": upload(
                        make_workbook(
                            SISWA_HEADERS,
                            [
                                siswa_row("10001", "Tabrakan"),
                                siswa_row("006120", "Baru"),
                            ],
                        )
                    )
                },
            )
            response = self.client.post(
                reverse("accounts:import_confirm", args=["SISWA"])
            )
        self.assertEqual(response.status_code, 200)
        result = response.context["result"]
        self.assertEqual(result["created_count"], 1)
        self.assertEqual(result["skipped_count"], 1)
        self.assertContains(response, "10001")
        self.assertContains(response, "sudah dipakai")
        original = User.objects.get(nis="10001")
        self.assertEqual(original.username, "lama")
        self.assertTrue(original.check_password("secret123"))
        self.assertTrue(
            User.objects.filter(username="006120").exists()
        )

    def test_fatal_error_tidak_membuat_akun(self):
        self.client.force_login(self.pus)
        self.client.post(
            reverse("accounts:import_upload", args=["SISWA"]),
            {
                "file": upload(
                    make_workbook(SISWA_HEADERS, [siswa_row("006112")])
                )
            },
        )
        with mock.patch(
            "apps.accounts.views.create_imported_accounts",
            side_effect=RuntimeError("fatal"),
        ):
            response = self.client.post(
                reverse("accounts:import_confirm", args=["SISWA"])
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tidak ada akun yang dibuat")
        self.assertFalse(User.objects.filter(username="006112").exists())

    def test_double_submit_tidak_menduplikat(self):
        response = self._upload_and_confirm(
            SISWA_HEADERS, [siswa_row("006112")]
        )
        self.assertTemplateUsed(response, "accounts/import_result.html")
        self.assertEqual(
            User.objects.filter(username="006112").count(), 1
        )
        # Session sudah dibersihkan: submit ulang dialihkan ke upload.
        response = self.client.post(
            reverse("accounts:import_confirm", args=["SISWA"])
        )
        self.assertRedirects(
            response, reverse("accounts:import_upload", args=["SISWA"])
        )
        self.assertEqual(
            User.objects.filter(username="006112").count(), 1
        )
