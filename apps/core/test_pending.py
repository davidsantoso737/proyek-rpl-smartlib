import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Book, BookCopy, Category, Ebook
from apps.circulation.services import approve_loan, request_ebook_loan, request_loan
from apps.core.models import LibrarySetting
from apps.materials.models import MaterialDeletionRequest, TeachingMaterial

User = get_user_model()


class LibrarianPendingTests(TestCase):
    def setUp(self):
        self.pus = self._make_user("pus1", User.Role.PUSTAKAWAN)
        self.siswa = self._make_user("siswa1", User.Role.SISWA, nis="10001")
        self.guru = self._make_user("guru1", User.Role.GURU, nip="20001")
        LibrarySetting.objects.create(
            physical_loan_duration=7,
            ebook_access_duration=14,
            pickup_qr_validity=30,
            fine_rate_per_day=Decimal("1000"),
            effective_from=timezone.now(),
        )
        category = Category.objects.create(name="Fiksi")
        book = Book.objects.create(
            category=category, isbn="ISBN-PD-1", title="Pending A"
        )
        self.copy = BookCopy.objects.create(
            book=book,
            copy_code="PD-001",
            status="TERSEDIA",
            acquired_at=datetime.date(2026, 1, 10),
            condition="BAIK",
            location="RAK-A1",
        )
        self.ebook = Ebook.objects.create(
            category=category,
            eisbn="E-PD-1",
            title="Ebook Pending",
            file_path="p.pdf",
            file_name="p.pdf",
            file_type="pdf",
            file_size=64,
            version="1.0",
        )
        self.material = TeachingMaterial.objects.create(
            uploaded_by=self.guru,
            title="Materi Pending",
            file_path="m.pdf",
            file_name="m.pdf",
            file_type="pdf",
            file_size=64,
            status="MENUNGGU",
        )

    def _make_user(self, username, role, password="secret123", **extra):
        defaults = {"full_name": f"{username} Full"}
        defaults.update(extra)
        user = User(username=username, role=role, **defaults)
        user.set_password(password)
        user.full_clean()
        user.save()
        return user

    def _seed_pending(self):
        request_loan(self.siswa, [self.copy.pk])
        request_ebook_loan(self.siswa, self.ebook.pk)
        return MaterialDeletionRequest.objects.create(
            material=self.material,
            requested_by=self.guru,
            status="MENUNGGU",
            reason="Usang.",
            requested_at=timezone.now(),
        )

    def test_badge_muncul_untuk_pustakawan(self):
        self._seed_pending()
        # 1 loan pending + 1 ebook pending; materi masih MENUNGGU
        # (belum TERSEDIA sehingga deletion request tidak dibuat di sini).
        self.client.force_login(self.pus)
        response = self.client.get(reverse("accounts:dashboard_pustakawan"))
        self.assertEqual(response.context["pending_loans"], 1)
        self.assertEqual(response.context["pending_ebooks"], 1)
        self.assertEqual(response.context["pending_materials"], 1)
        self.assertEqual(response.context["pending_deletions"], 1)
        self.assertEqual(response.context["pending_total"], 4)
        self.assertContains(response, "Ada 4 hal menunggu aksi")

    def test_badge_hilang_setelah_diproses(self):
        loan = request_loan(self.siswa, [self.copy.pk])
        approve_loan(loan.pk, self.pus)
        self.client.force_login(self.pus)
        response = self.client.get(reverse("accounts:dashboard_pustakawan"))
        self.assertEqual(response.context["pending_loans"], 0)
        # Materi setUp masih MENUNGGU sehingga total tinggal 1.
        self.assertEqual(response.context["pending_total"], 1)
        self.assertContains(response, "Ada 1 hal menunggu aksi")

    def test_non_pustakawan_tanpa_badge(self):
        self._seed_pending()
        self.client.force_login(self.siswa)
        response = self.client.get(reverse("accounts:dashboard_siswa"))
        self.assertEqual(response.context["pending_total"], 0)
        self.assertNotContains(response, "hal menunggu aksi")

    def test_deletion_request_masuk_hitungan(self):
        self.material.status = "TERSEDIA"
        self.material.save(update_fields=["status"])
        MaterialDeletionRequest.objects.create(
            material=self.material,
            requested_by=self.guru,
            status="MENUNGGU",
            reason="Usang.",
            requested_at=timezone.now(),
        )
        self.client.force_login(self.pus)
        response = self.client.get(reverse("accounts:dashboard_pustakawan"))
        self.assertEqual(response.context["pending_deletions"], 1)
        self.assertEqual(response.context["pending_total"], 1)
