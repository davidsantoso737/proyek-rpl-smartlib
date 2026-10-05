import datetime
import tempfile
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Category, Ebook, EbookAuthor, Author
from apps.circulation.models import EbookLoan
from apps.circulation.services import (
    EbookLoanError,
    approve_ebook_loan,
    get_active_ebook_loan,
    request_ebook_loan,
)
from apps.core.models import LibrarySetting
from apps.notifications.models import Notification

User = get_user_model()

TMP_ROOT = Path(tempfile.mkdtemp(prefix="ebook_test_"))


@override_settings(EBOOK_STORAGE_ROOT=TMP_ROOT)
class EbookTests(TestCase):
    def setUp(self):
        self.siswa = self._make_user("siswa1", User.Role.SISWA, nis="10001")
        self.siswa2 = self._make_user("siswa2", User.Role.SISWA, nis="10002")
        self.guru = self._make_user("guru1", User.Role.GURU, nip="20001")
        self.pus = self._make_user("pus1", User.Role.PUSTAKAWAN)
        LibrarySetting.objects.create(
            physical_loan_duration=7,
            ebook_access_duration=14,
            pickup_qr_validity=30,
            fine_rate_per_day=Decimal("1000"),
            effective_from=timezone.now(),
        )
        category = Category.objects.create(name="Digital")
        author = Author.objects.create(name="Penulis E")
        self.ebook1 = self._make_ebook(category, author, "E-101", "Ebook Satu", "satu.pdf")
        self.ebook2 = self._make_ebook(category, author, "E-102", "Ebook Dua", "dua.pdf")

    def _make_user(self, username, role, password="secret123", **extra):
        defaults = {"full_name": f"{username} Full"}
        defaults.update(extra)
        user = User(username=username, role=role, **defaults)
        user.set_password(password)
        user.full_clean()
        user.save()
        return user

    def _make_ebook(self, category, author, eisbn, title, filename):
        (TMP_ROOT / filename).write_bytes(b"%PDF-1.4 dummy " + filename.encode())
        ebook = Ebook.objects.create(
            category=category,
            eisbn=eisbn,
            title=title,
            file_path=filename,
            file_name=filename,
            file_type="pdf",
            file_size=64,
            version="1.0",
        )
        EbookAuthor.objects.create(ebook=ebook, author=author)
        return ebook

    def _approve(self, loan):
        approve_ebook_loan(loan.pk, self.pus)
        return EbookLoan.objects.get(pk=loan.pk)

    # --- request & approval ---

    def test_request_membuat_pending(self):
        self.client.force_login(self.siswa)
        response = self.client.post(
            reverse("circulation:ebook_request", args=[self.ebook1.pk])
        )
        self.assertRedirects(
            response,
            reverse("catalog:ebook_detail", args=[self.ebook1.pk]),
        )
        loan = EbookLoan.objects.get(user=self.siswa, ebook=self.ebook1)
        self.assertEqual(loan.status, "PENDING_APPROVAL")
        self.assertIsNotNone(loan.requested_at)

    def test_approval_memulai_akses_sesuai_durasi(self):
        loan = request_ebook_loan(self.siswa, self.ebook1.pk)
        approved = self._approve(loan)
        self.assertEqual(approved.status, "APPROVED")
        self.assertEqual(approved.approved_by, self.pus)
        self.assertEqual(
            (approved.expires_at - approved.started_at).total_seconds(),
            14 * 86400,
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.siswa, type="EBOOK_APPROVED"
            ).exists()
        )

    def test_reject_butuh_alasan(self):
        loan = request_ebook_loan(self.siswa, self.ebook1.pk)
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("circulation:ebook_reject", args=[loan.pk]),
            {"reason": ""},
        )
        self.assertRedirects(
            response,
            reverse("circulation:ebook_approval_detail", args=[loan.pk]),
        )
        loan.refresh_from_db()
        self.assertEqual(loan.status, "PENDING_APPROVAL")

    # --- akses ---

    def test_active_access_reader_dan_file(self):
        loan = self._approve(request_ebook_loan(self.siswa, self.ebook1.pk))
        self.client.force_login(self.siswa)
        reader = self.client.get(
            reverse("circulation:ebook_reader", args=[loan.pk])
        )
        self.assertEqual(reader.status_code, 200)
        self.assertContains(reader, "Ebook Satu")
        self.assertContains(reader, "<embed")
        content = self.client.get(
            reverse("circulation:ebook_file", args=[loan.pk])
        )
        self.assertEqual(content.status_code, 200)
        self.assertEqual(content["Content-Type"], "application/pdf")
        self.assertEqual(content["X-Frame-Options"], "SAMEORIGIN")
        self.assertNotIn("attachment", content.get("Content-Disposition", ""))
        self.assertTrue(b"".join(content.streaming_content).startswith(b"%PDF"))

    def test_non_pdf_tanpa_embed_tapi_unduh(self):
        (TMP_ROOT / "tiga.epub").write_bytes(b"epub-dummy")
        category = Category.objects.first()
        author = Author.objects.first()
        ebook = Ebook.objects.create(
            category=category,
            eisbn="E-103",
            title="Ebook Tiga",
            file_path="tiga.epub",
            file_name="tiga.epub",
            file_type="epub",
            file_size=64,
            version="1.0",
        )
        EbookAuthor.objects.create(ebook=ebook, author=author)
        loan = self._approve(request_ebook_loan(self.siswa, ebook.pk))
        self.client.force_login(self.siswa)
        reader = self.client.get(
            reverse("circulation:ebook_reader", args=[loan.pk])
        )
        self.assertEqual(reader.status_code, 200)
        self.assertNotContains(reader, "<embed")
        self.assertContains(reader, "Unduh e-book")
        content = self.client.get(
            reverse("circulation:ebook_file", args=[loan.pk])
        )
        self.assertEqual(content.status_code, 200)
        self.assertEqual(
            content["Content-Type"], "application/epub+zip"
        )
        self.assertIn("attachment", content["Content-Disposition"])

    def test_expired_access_ditolak_dan_bisa_pinjam_lagi(self):
        loan = self._approve(request_ebook_loan(self.siswa, self.ebook1.pk))
        EbookLoan.objects.filter(pk=loan.pk).update(
            expires_at=timezone.now() - datetime.timedelta(hours=1)
        )
        self.assertIsNone(get_active_ebook_loan(self.siswa, self.ebook1))
        self.client.force_login(self.siswa)
        self.assertEqual(
            self.client.get(
                reverse("circulation:ebook_reader", args=[loan.pk])
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse("circulation:ebook_file", args=[loan.pk])
            ).status_code,
            403,
        )
        loan2 = request_ebook_loan(self.siswa, self.ebook1.pk)
        self.assertEqual(loan2.status, "PENDING_APPROVAL")

    def test_tidak_bisa_ganda_saat_aktif(self):
        loan = self._approve(request_ebook_loan(self.siswa, self.ebook1.pk))
        self.assertIsNotNone(get_active_ebook_loan(self.siswa, self.ebook1))
        with self.assertRaises(EbookLoanError):
            request_ebook_loan(self.siswa, self.ebook1.pk)
        self.assertEqual(
            EbookLoan.objects.filter(
                user=self.siswa, ebook=self.ebook1
            ).count(),
            1,
        )
        _ = loan

    def test_multiple_active_ebooks(self):
        loan1 = self._approve(request_ebook_loan(self.siswa, self.ebook1.pk))
        loan2 = self._approve(request_ebook_loan(self.siswa, self.ebook2.pk))
        self.client.force_login(self.siswa)
        for loan in (loan1, loan2):
            self.assertEqual(
                self.client.get(
                    reverse("circulation:ebook_file", args=[loan.pk])
                ).status_code,
                200,
            )

    # --- keamanan file & role ---

    def test_unauthorized_file(self):
        loan = self._approve(request_ebook_loan(self.siswa, self.ebook1.pk))
        self.assertEqual(
            self.client.get(
                reverse("circulation:ebook_file", args=[loan.pk])
            ).status_code,
            302,  # anonim -> login
        )
        self.client.force_login(self.siswa2)
        self.assertEqual(
            self.client.get(
                reverse("circulation:ebook_file", args=[loan.pk])
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse("circulation:ebook_reader", args=[loan.pk])
            ).status_code,
            403,
        )

    def test_direct_file_url_tidak_ada(self):
        for url in ("/files/satu.pdf", "/media/satu.pdf", "/static/satu.pdf"):
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_role_authorization(self):
        self.client.force_login(self.pus)
        self.assertEqual(
            self.client.post(
                reverse("circulation:ebook_request", args=[self.ebook1.pk])
            ).status_code,
            403,
        )
        self.client.force_login(self.siswa)
        self.assertEqual(
            self.client.get(
                reverse("circulation:ebook_approval_list")
            ).status_code,
            403,
        )
        loan = request_ebook_loan(self.siswa, self.ebook1.pk)
        self.client.force_login(self.guru)
        self.assertEqual(
            self.client.post(
                reverse("circulation:ebook_approve", args=[loan.pk])
            ).status_code,
            403,
        )
        loan.refresh_from_db()
        self.assertEqual(loan.status, "PENDING_APPROVAL")
