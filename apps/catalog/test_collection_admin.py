import datetime
import tempfile
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import (
    Author, Book, BookAuthor, BookCopy, Category, Ebook, EbookAuthor,
)
from apps.circulation.services import (
    activate_handover, approve_loan, request_loan,
)
from apps.core.models import LibrarySetting

User = get_user_model()


class CollectionAdminTests(TestCase):
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
        self.category = Category.objects.create(name="Fiksi")
        self.author = Author.objects.create(name="Penulis A")
        self.book = Book.objects.create(
            category=self.category, isbn="ISBN-ADM-1", title="Kelola A"
        )
        BookAuthor.objects.create(book=self.book, author=self.author)
        self.copy = BookCopy.objects.create(
            book=self.book,
            copy_code="ADM-001",
            status="TERSEDIA",
            acquired_at=datetime.date(2026, 1, 10),
            condition="BAIK",
            location="RAK-A1",
        )

    def _make_user(self, username, role, password="secret123", **extra):
        defaults = {"full_name": f"{username} Full"}
        defaults.update(extra)
        user = User(username=username, role=role, **defaults)
        user.set_password(password)
        user.full_clean()
        user.save()
        return user

    def _book_data(self, **overrides):
        data = {
            "category": self.category.pk,
            "isbn": "ISBN-BARU-1",
            "title": "Buku Baru",
            "description": "",
            "publisher": "Gramedia",
            "publication_year": 2024,
            "edition": "",
            "cover_url": "",
            "is_active": True,
            "authors": [self.author.pk],
        }
        data.update(overrides)
        return data

    def test_tambah_buku_dengan_penulis(self):
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("catalog:manage_book_create"), self._book_data()
        )
        book = Book.objects.get(isbn="ISBN-BARU-1")
        self.assertRedirects(
            response, reverse("catalog:manage_book_copies", args=[book.pk])
        )
        self.assertEqual(
            list(
                Author.objects.filter(bookauthor__book=book)
            ),
            [self.author],
        )
        self.client.force_login(self.siswa)
        self.assertContains(
            self.client.get(reverse("catalog:catalog")), "Buku Baru"
        )

    def test_isbn_ganda_ditolak(self):
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("catalog:manage_book_create"),
            self._book_data(isbn="ISBN-ADM-1"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Book.objects.filter(isbn="ISBN-ADM-1").count(), 1)

    def test_ubah_buku(self):
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("catalog:manage_book_update", args=[self.book.pk]),
            self._book_data(
                isbn="ISBN-ADM-1", title="Kelola A Revisi", authors=[]
            ),
        )
        self.assertRedirects(
            response,
            reverse("catalog:manage_book_copies", args=[self.book.pk]),
        )
        self.book.refresh_from_db()
        self.assertEqual(self.book.title, "Kelola A Revisi")
        self.assertEqual(BookAuthor.objects.filter(book=self.book).count(), 0)

    def test_nonaktifkan_buku_sembunyi_dari_katalog(self):
        self.client.force_login(self.pus)
        self.client.post(
            reverse("catalog:manage_book_toggle", args=[self.book.pk])
        )
        self.book.refresh_from_db()
        self.assertFalse(self.book.is_active)
        self.client.force_login(self.siswa)
        response = self.client.get(reverse("catalog:catalog"))
        titles = [
            item["title"]
            for item in response.context["page_obj"].object_list
        ]
        self.assertNotIn("Kelola A", titles)
        self.assertEqual(
            self.client.get(
                reverse("catalog:book_detail", args=[self.book.pk])
            ).status_code,
            404,
        )

    def test_tambah_eksemplar_kode_otomatis_unik(self):
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("catalog:manage_copy_create", args=[self.book.pk]),
            {
                "copy_code": "",
                "jumlah": 1,
                "status": "TERSEDIA",
                "acquired_at": "2026-02-01",
                "condition": "BAIK",
                "location": "RAK-A2",
            },
        )
        self.assertRedirects(
            response,
            reverse("catalog:manage_book_copies", args=[self.book.pk]),
        )
        copy = BookCopy.objects.exclude(pk=self.copy.pk).get()
        self.assertTrue(copy.copy_code)
        self.assertNotEqual(copy.copy_code, "ADM-001")

    def test_kode_ganda_ditolak(self):
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("catalog:manage_copy_create", args=[self.book.pk]),
            {
                "copy_code": "ADM-001",
                "jumlah": 1,
                "status": "TERSEDIA",
                "acquired_at": "2026-02-01",
                "condition": "BAIK",
                "location": "RAK-A2",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(BookCopy.objects.filter(book=self.book).count(), 1)

    def test_status_terkunci_saat_terikat_pinjaman_aktif(self):
        loan = request_loan(self.siswa, [self.copy.pk])
        approve_loan(loan.pk, self.pus)
        loan.refresh_from_db()
        activate_handover(loan.pickup_token, self.pus)
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("catalog:manage_copy_update", args=[self.copy.pk]),
            {
                "copy_code": "ADM-001",
                "status": "RUSAK",
                "acquired_at": "2026-01-10",
                "condition": "BAIK",
                "location": "RAK-A1",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.copy.refresh_from_db()
        self.assertEqual(self.copy.status, "DIPINJAM")
        # Lokasi (non-status) tetap boleh diubah.
        response = self.client.post(
            reverse("catalog:manage_copy_update", args=[self.copy.pk]),
            {
                "copy_code": "ADM-001",
                "status": "DIPINJAM",
                "acquired_at": "2026-01-10",
                "condition": "BAIK",
                "location": "RAK-B9",
            },
        )
        self.assertRedirects(
            response,
            reverse("catalog:manage_book_copies", args=[self.book.pk]),
        )
        self.copy.refresh_from_db()
        self.assertEqual(self.copy.location, "RAK-B9")

    def test_kategori_dan_penulis(self):
        self.client.force_login(self.pus)
        self.client.post(
            reverse("catalog:manage_categories"), {"name": "Sejarah"}
        )
        cat = Category.objects.get(name="Sejarah")
        # Dipakai -> hapus ditolak.
        Book.objects.create(
            category=cat, isbn="ISBN-SJR-1", title="Sejarah X"
        )
        self.client.post(
            reverse("catalog:manage_category_delete", args=[cat.pk])
        )
        self.assertTrue(Category.objects.filter(pk=cat.pk).exists())
        # Tak dipakai -> hapus berhasil.
        cat2 = Category.objects.create(name="Hobi")
        self.client.post(
            reverse("catalog:manage_category_delete", args=[cat2.pk])
        )
        self.assertFalse(Category.objects.filter(pk=cat2.pk).exists())
        self.client.post(
            reverse("catalog:manage_authors"),
            {"name": "Penulis B", "bio": ""},
        )
        author = Author.objects.get(name="Penulis B")
        self.client.post(
            reverse("catalog:manage_author_delete", args=[self.author.pk])
        )
        self.assertTrue(Author.objects.filter(pk=self.author.pk).exists())
        self.client.post(
            reverse("catalog:manage_author_delete", args=[author.pk])
        )
        self.assertFalse(Author.objects.filter(pk=author.pk).exists())

    def test_unauthorized(self):
        urls = [
            reverse("catalog:manage_books"),
            reverse("catalog:manage_book_create"),
            reverse("catalog:manage_categories"),
            reverse("catalog:manage_authors"),
        ]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.siswa)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(
            self.client.post(
                reverse("catalog:manage_book_create"), self._book_data()
            ).status_code,
            403,
        )
        self.client.force_login(self.guru)
        self.assertEqual(
            self.client.get(reverse("catalog:manage_books")).status_code,
            403,
        )
        self.assertEqual(Book.objects.filter(isbn="ISBN-BARU-1").count(), 0)

    def test_qr_label_eksemplar_baru(self):
        self.client.force_login(self.pus)
        self.client.post(
            reverse("catalog:manage_copy_create", args=[self.book.pk]),
            {
                "copy_code": "ADM-777",
                "jumlah": 1,
                "status": "TERSEDIA",
                "acquired_at": "2026-02-01",
                "condition": "BAIK",
                "location": "RAK-A2",
            },
        )
        copy = BookCopy.objects.get(copy_code="ADM-777")
        response = self.client.get(
            reverse("circulation:copy_label", args=[copy.pk])
        )
        self.assertContains(response, "ADM-777")

    def test_tambah_eksemplar_bulk(self):
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("catalog:manage_copy_create", args=[self.book.pk]),
            {
                "copy_code": "",
                "jumlah": 3,
                "status": "TERSEDIA",
                "acquired_at": "2026-02-01",
                "condition": "BAIK",
                "location": "RAK-A2",
            },
        )
        self.assertRedirects(
            response,
            reverse("catalog:manage_book_copies", args=[self.book.pk]),
        )
        codes = sorted(
            BookCopy.objects.filter(book=self.book).values_list(
                "copy_code", flat=True
            )
        )
        self.assertEqual(len(codes), 4)
        self.assertEqual(len(set(codes)), 4)
        self.assertIn("ADM-001", codes)

    def test_bulk_jumlah_invalid_ditolak(self):
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("catalog:manage_copy_create", args=[self.book.pk]),
            {
                "copy_code": "",
                "jumlah": 0,
                "status": "TERSEDIA",
                "acquired_at": "2026-02-01",
                "condition": "BAIK",
                "location": "RAK-A2",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(BookCopy.objects.filter(book=self.book).count(), 1)


TMP_EBOOK_ROOT = Path(tempfile.mkdtemp(prefix="ebook_admin_test_"))


@override_settings(EBOOK_STORAGE_ROOT=TMP_EBOOK_ROOT)
class EbookAdminTests(TestCase):
    def setUp(self):
        self.pus = self._make_user("pus1", User.Role.PUSTAKAWAN)
        self.siswa = self._make_user("siswa1", User.Role.SISWA, nis="10001")
        self.guru = self._make_user("guru1", User.Role.GURU, nip="20001")
        self.category = Category.objects.create(name="Digital")
        self.author = Author.objects.create(name="Penulis E")
        self.ebook = Ebook.objects.create(
            category=self.category,
            eisbn="E-ADM-1",
            title="Kelola E",
            file_path="lama.pdf",
            file_name="lama.pdf",
            file_type="pdf",
            file_size=64,
            version="1.0",
        )
        (TMP_EBOOK_ROOT / "lama.pdf").write_bytes(b"%PDF-1.4 lama")
        EbookAuthor.objects.create(ebook=self.ebook, author=self.author)

    def _make_user(self, username, role, password="secret123", **extra):
        defaults = {"full_name": f"{username} Full"}
        defaults.update(extra)
        user = User(username=username, role=role, **defaults)
        user.set_password(password)
        user.full_clean()
        user.save()
        return user

    def _ebook_data(self, **overrides):
        data = {
            "category": self.category.pk,
            "eisbn": "E-BARU-1",
            "title": "Ebook Baru",
            "description": "",
            "publisher": "",
            "publication_year": "",
            "edition": "",
            "cover_url": "",
            "version": "1.0",
            "is_active": True,
            "authors": [self.author.pk],
            "file": SimpleUploadedFile("baru.pdf", b"%PDF-1.4 baru"),
        }
        data.update(overrides)
        return data

    def test_tambah_ebook_dengan_file(self):
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("catalog:manage_ebook_create"), self._ebook_data()
        )
        self.assertRedirects(response, reverse("catalog:manage_ebooks"))
        ebook = Ebook.objects.get(eisbn="E-BARU-1")
        self.assertEqual(ebook.file_type, "pdf")
        self.assertEqual(ebook.file_size, len(b"%PDF-1.4 baru"))
        self.assertTrue((TMP_EBOOK_ROOT / ebook.file_path).is_file())
        self.assertEqual(
            list(Author.objects.filter(ebookauthor__ebook=ebook)),
            [self.author],
        )
        self.client.force_login(self.siswa)
        self.assertContains(
            self.client.get(reverse("catalog:catalog")), "Ebook Baru"
        )

    def test_tipe_invalid_dan_oversize_ditolak(self):
        self.client.force_login(self.pus)
        before = set(TMP_EBOOK_ROOT.iterdir())
        response = self.client.post(
            reverse("catalog:manage_ebook_create"),
            self._ebook_data(
                file=SimpleUploadedFile("x.exe", b"MZ..."),
            ),
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            reverse("catalog:manage_ebook_create"),
            self._ebook_data(
                eisbn="E-BESAR-1",
                file=SimpleUploadedFile(
                    "besar.pdf", b"x" * (51 * 1024 * 1024)
                ),
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Ebook.objects.filter(eisbn="E-BESAR-1").exists())
        self.assertEqual(set(TMP_EBOOK_ROOT.iterdir()), before)

    def test_eisbn_ganda_ditolak(self):
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("catalog:manage_ebook_create"),
            self._ebook_data(eisbn="E-ADM-1"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Ebook.objects.filter(eisbn="E-ADM-1").count(), 1)

    def test_ubah_metadata_tanpa_ganti_file(self):
        self.client.force_login(self.pus)
        data = self._ebook_data()
        data.update(
            {
                "eisbn": "E-ADM-1",
                "title": "Kelola E Revisi",
                "authors": [],
            }
        )
        del data["file"]
        response = self.client.post(
            reverse("catalog:manage_ebook_update", args=[self.ebook.pk]),
            data,
        )
        self.assertRedirects(response, reverse("catalog:manage_ebooks"))
        self.ebook.refresh_from_db()
        self.assertEqual(self.ebook.title, "Kelola E Revisi")
        self.assertEqual(self.ebook.file_path, "lama.pdf")
        self.assertTrue((TMP_EBOOK_ROOT / "lama.pdf").is_file())

    def test_ganti_file_hapus_yang_lama(self):
        self.client.force_login(self.pus)
        data = self._ebook_data()
        data.update({"eisbn": "E-ADM-1", "title": "Kelola E"})
        del data["authors"]
        response = self.client.post(
            reverse("catalog:manage_ebook_update", args=[self.ebook.pk]),
            data,
        )
        self.assertRedirects(response, reverse("catalog:manage_ebooks"))
        self.ebook.refresh_from_db()
        self.assertNotEqual(self.ebook.file_path, "lama.pdf")
        self.assertFalse((TMP_EBOOK_ROOT / "lama.pdf").exists())
        self.assertTrue(
            (TMP_EBOOK_ROOT / self.ebook.file_path).is_file()
        )

    def test_nonaktifkan_sembunyi_dari_katalog(self):
        self.client.force_login(self.pus)
        self.client.post(
            reverse("catalog:manage_ebook_toggle", args=[self.ebook.pk])
        )
        self.ebook.refresh_from_db()
        self.assertFalse(self.ebook.is_active)
        self.client.force_login(self.siswa)
        response = self.client.get(reverse("catalog:catalog"))
        titles = [
            item["title"]
            for item in response.context["page_obj"].object_list
        ]
        self.assertNotIn("Kelola E", titles)

    def test_unauthorized(self):
        urls = [
            reverse("catalog:manage_ebooks"),
            reverse("catalog:manage_ebook_create"),
        ]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.siswa)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(
            self.client.post(
                reverse("catalog:manage_ebook_create"), self._ebook_data()
            ).status_code,
            403,
        )
        self.client.force_login(self.guru)
        self.assertEqual(
            self.client.get(
                reverse("catalog:manage_ebooks")
            ).status_code,
            403,
        )
        self.assertFalse(Ebook.objects.filter(eisbn="E-BARU-1").exists())
