import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse

from apps.materials.models import MaterialDeletionRequest, TeachingMaterial
from apps.notifications.models import Notification

User = get_user_model()

TMP_ROOT = Path(tempfile.mkdtemp(prefix="material_test_"))


@override_settings(MATERIAL_STORAGE_ROOT=TMP_ROOT)
class MaterialWorkflowTests(TestCase):
    def setUp(self):
        self.guru = self._make_user("guru1", User.Role.GURU, nip="20001")
        self.guru2 = self._make_user("guru2", User.Role.GURU, nip="20002")
        self.siswa = self._make_user("siswa1", User.Role.SISWA, nis="10001")
        self.pus = self._make_user("pus1", User.Role.PUSTAKAWAN)

    def _make_user(self, username, role, password="secret123", **extra):
        defaults = {"full_name": f"{username} Full"}
        defaults.update(extra)
        user = User(username=username, role=role, **defaults)
        user.set_password(password)
        user.full_clean()
        user.save()
        return user

    def _upload(self, name="materi.pdf", content=b"%PDF-1.4 uji", **overrides):
        self.client.force_login(self.guru)
        data = {
            "title": "Materi IPA",
            "description": "Bab 1",
            "file": SimpleUploadedFile(name, content),
        }
        data.update(overrides)
        return self.client.post(reverse("materials:upload"), data)

    def _stored_files(self):
        return [p for p in TMP_ROOT.iterdir() if p.is_file()]

    # --- upload & validasi ---

    def test_valid_upload(self):
        response = self._upload()
        self.assertRedirects(response, reverse("materials:my"))
        material = TeachingMaterial.objects.get(title="Materi IPA")
        self.assertEqual(material.status, "MENUNGGU")
        self.assertEqual(material.uploaded_by, self.guru)
        self.assertEqual(material.file_name, "materi.pdf")
        self.assertEqual(material.file_type, "pdf")
        self.assertEqual(material.file_size, len(b"%PDF-1.4 uji"))
        self.assertTrue((TMP_ROOT / material.file_path).is_file())

    def test_invalid_type_ditolak_tanpa_jejak(self):
        before = set(self._stored_files())
        response = self._upload(name="jahat.exe", content=b"MZ...")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(TeachingMaterial.objects.count(), 0)
        self.assertEqual(set(self._stored_files()), before)

    def test_oversized_file_ditolak(self):
        before = set(self._stored_files())
        response = self._upload(
            name="besar.pdf", content=b"x" * (21 * 1024 * 1024)
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(TeachingMaterial.objects.count(), 0)
        self.assertEqual(set(self._stored_files()), before)

    def test_teacher_upload_only_guru(self):
        self.assertEqual(
            self.client.get(reverse("materials:upload")).status_code, 302
        )
        self.client.force_login(self.siswa)
        response = self.client.post(
            reverse("materials:upload"),
            {
                "title": "X",
                "file": SimpleUploadedFile("x.pdf", b"%PDF"),
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            self.client.get(reverse("materials:upload")).status_code, 403
        )
        self.assertEqual(TeachingMaterial.objects.count(), 0)

    # --- verifikasi pustakawan ---

    def test_librarian_approval(self):
        self._upload()
        material = TeachingMaterial.objects.get()
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("materials:verify", args=[material.pk]),
            {"decision": "approve"},
        )
        self.assertRedirects(response, reverse("materials:review_list"))
        material.refresh_from_db()
        self.assertEqual(material.status, "TERSEDIA")
        self.assertEqual(material.verified_by, self.pus)
        self.assertIsNotNone(material.verified_at)
        self.assertTrue(
            Notification.objects.filter(
                user=self.guru, type="MATERIAL_APPROVED"
            ).exists()
        )
        self.client.force_login(self.siswa)
        self.assertContains(
            self.client.get(reverse("materials:list")), "Materi IPA"
        )

    def test_librarian_rejection_dengan_alasan(self):
        self._upload()
        material = TeachingMaterial.objects.get()
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("materials:verify", args=[material.pk]),
            {"decision": "reject", "reason": "Plagiat terdeteksi."},
        )
        self.assertRedirects(response, reverse("materials:review_list"))
        material.refresh_from_db()
        self.assertEqual(material.status, "DITOLAK")
        self.assertEqual(material.rejection_reason, "Plagiat terdeteksi.")
        self.assertTrue(
            Notification.objects.filter(
                user=self.guru, type="MATERIAL_REJECTED"
            ).exists()
        )

    def test_rejection_reason_wajib(self):
        self._upload()
        material = TeachingMaterial.objects.get()
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("materials:verify", args=[material.pk]),
            {"decision": "reject", "reason": ""},
        )
        self.assertRedirects(
            response, reverse("materials:review_detail", args=[material.pk])
        )
        material.refresh_from_db()
        self.assertEqual(material.status, "MENUNGGU")

    # --- deletion request ---

    def test_deletion_request_disetujui(self):
        self._upload()
        material = TeachingMaterial.objects.get()
        self.client.force_login(self.pus)
        self.client.post(
            reverse("materials:verify", args=[material.pk]),
            {"decision": "approve"},
        )
        stored = TMP_ROOT / material.file_path
        self.assertTrue(stored.is_file())
        self.client.force_login(self.guru)
        response = self.client.post(
            reverse("materials:deletion_request", args=[material.pk]),
            {"reason": "Sudah kedaluwarsa."},
        )
        self.assertRedirects(response, reverse("materials:my"))
        material.refresh_from_db()
        self.assertEqual(material.status, "MENUNGGU_PENGHAPUSAN")
        deletion = MaterialDeletionRequest.objects.get(material=material)
        self.client.force_login(self.pus)
        self.client.post(
            reverse("materials:deletion_review", args=[deletion.pk]),
            {"decision": "approve"},
        )
        material.refresh_from_db()
        deletion.refresh_from_db()
        self.assertEqual(material.status, "DIHAPUS")
        self.assertEqual(deletion.status, "DISETUJUI")
        self.assertFalse(stored.exists())
        self.assertTrue(
            Notification.objects.filter(
                user=self.guru, type="MATERIAL_DELETION_APPROVED"
            ).exists()
        )

    def test_deletion_request_ditolak_kembali_tersedia(self):
        self._upload()
        material = TeachingMaterial.objects.get()
        self.client.force_login(self.pus)
        self.client.post(
            reverse("materials:verify", args=[material.pk]),
            {"decision": "approve"},
        )
        self.client.force_login(self.guru)
        self.client.post(
            reverse("materials:deletion_request", args=[material.pk]),
            {"reason": "Salah pencet."},
        )
        deletion = MaterialDeletionRequest.objects.get(material=material)
        self.client.force_login(self.pus)
        self.client.post(
            reverse("materials:deletion_review", args=[deletion.pk]),
            {"decision": "reject", "note": "Masih dipakai."},
        )
        material.refresh_from_db()
        self.assertEqual(material.status, "TERSEDIA")
        self.assertTrue((TMP_ROOT / material.file_path).is_file())

    def test_unauthorized_direct_delete(self):
        self._upload()
        material = TeachingMaterial.objects.get()
        # Tidak ada endpoint hapus langsung di URLConf.
        with self.assertRaises(NoReverseMatch):
            reverse("materials:delete", args=[material.pk])
        with self.assertRaises(NoReverseMatch):
            reverse("materials:material_delete", args=[material.pk])
        # Guru lain tidak bisa meminta hapus materi orang lain.
        self.client.force_login(self.guru2)
        self.client.post(
            reverse("materials:deletion_request", args=[material.pk]),
            {"reason": "iseng"},
        )
        self.assertEqual(MaterialDeletionRequest.objects.count(), 0)
        material.refresh_from_db()
        self.assertEqual(material.status, "MENUNGGU")
        # Siswa tidak bisa meminta hapus.
        self.client.force_login(self.siswa)
        self.assertEqual(
            self.client.post(
                reverse("materials:deletion_request", args=[material.pk]),
                {"reason": "iseng"},
            ).status_code,
            403,
        )

    # --- unduhan terproteksi ---

    def test_download_terproteksi(self):
        self._upload()
        material = TeachingMaterial.objects.get()
        download = reverse("materials:download", args=[material.pk])
        self.client.logout()
        self.assertEqual(self.client.get(download).status_code, 302)
        self.client.force_login(self.siswa)
        self.assertEqual(self.client.get(download).status_code, 403)
        self.client.force_login(self.guru)
        response = self.client.get(download)
        self.assertEqual(response.status_code, 200)
        self.client.force_login(self.pus)
        self.client.post(
            reverse("materials:verify", args=[material.pk]),
            {"decision": "approve"},
        )
        self.client.force_login(self.siswa)
        response = self.client.get(download)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            b"".join(response.streaming_content), b"%PDF-1.4 uji"
        )
