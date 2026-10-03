import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.catalog.models import (
    Author,
    Book,
    BookAuthor,
    BookCopy,
    Category,
    Ebook,
    EbookAuthor,
)

User = get_user_model()


class CatalogViewTests(TestCase):
    def setUp(self):
        self.siswa = self._make_user("siswa1", User.Role.SISWA)
        self.fiksi = Category.objects.create(name="Fiksi")
        self.sains = Category.objects.create(name="Sains")

        self.andrea = Author.objects.create(
            name="Andrea Hirata", bio="Penulis Laskar Pelangi."
        )
        self.sains_author = Author.objects.create(name="Penulis Sains")

        self.book1 = Book.objects.create(
            category=self.fiksi,
            isbn="ISBN-LASKAR-1",
            title="Laskar Pelangi",
            publisher="Bentang Pustaka",
            publication_year=2005,
        )
        BookAuthor.objects.create(book=self.book1, author=self.andrea)
        for code, status in (
            ("COPY-A1", "TERSEDIA"),
            ("COPY-A2", "TERSEDIA"),
            ("COPY-A3", "DIPINJAM"),
        ):
            BookCopy.objects.create(
                book=self.book1,
                copy_code=code,
                status=status,
                acquired_at=datetime.date(2026, 1, 10),
                condition="BAIK",
                location="RAK-A1",
            )

        self.book2 = Book.objects.create(
            category=self.sains,
            isbn="ISBN-FISIKA-1",
            title="Fisika Dasar",
            publisher="Erlangga",
        )
        BookAuthor.objects.create(
            book=self.book2, author=self.sains_author
        )
        BookCopy.objects.create(
            book=self.book2,
            copy_code="COPY-B1",
            status="DIPINJAM",
            acquired_at=datetime.date(2026, 1, 11),
            condition="BAIK",
            location="RAK-B1",
        )

        self.book_hidden = Book.objects.create(
            category=self.fiksi,
            isbn="ISBN-HIDDEN-1",
            title="Buku Nonaktif",
            is_active=False,
        )

        self.ebook1 = Ebook.objects.create(
            category=self.sains,
            eisbn="E-001",
            title="Matematika Digital",
            publisher="Erlangga",
            file_path="/files/mat.pdf",
            file_name="mat.pdf",
            file_type="pdf",
            file_size=2048,
            version="1.0",
        )
        EbookAuthor.objects.create(
            ebook=self.ebook1, author=self.sains_author
        )

    def _make_user(self, username, role):
        user = User(
            username=username,
            role=role,
            full_name=f"{username} Full",
            nis=f"nis-{username}",
        )
        user.set_password("secret123")
        user.full_clean()
        user.save()
        return user

    def _get(self, *args, **kwargs):
        self.client.force_login(self.siswa)
        return self.client.get(*args, **kwargs)

    def test_catalog_accessible_memuat_fisik_dan_ebook(self):
        response = self._get(reverse("catalog:catalog"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Laskar Pelangi")
        self.assertContains(response, "Matematika Digital")
        self.assertContains(response, "Buku Fisik")
        self.assertContains(response, "E-Book")

    def test_search_title(self):
        response = self._get(reverse("catalog:catalog"), {"q": "Laskar"})
        self.assertContains(response, "Laskar Pelangi")
        self.assertNotContains(response, "Fisika Dasar")

    def test_search_author(self):
        response = self._get(reverse("catalog:catalog"), {"q": "Hirata"})
        self.assertContains(response, "Laskar Pelangi")
        self.assertNotContains(response, "Fisika Dasar")

    def test_search_category(self):
        response = self._get(reverse("catalog:catalog"), {"q": "Sains"})
        self.assertContains(response, "Fisika Dasar")
        self.assertContains(response, "Matematika Digital")
        self.assertNotContains(response, "Laskar Pelangi")

    def test_search_publisher_dan_isbn(self):
        response = self._get(reverse("catalog:catalog"), {"q": "Bentang"})
        self.assertContains(response, "Laskar Pelangi")
        response = self._get(
            reverse("catalog:catalog"), {"q": "ISBN-FISIKA-1"}
        )
        self.assertContains(response, "Fisika Dasar")

    def test_category_filter(self):
        response = self._get(
            reverse("catalog:catalog"), {"category": self.fiksi.id}
        )
        self.assertContains(response, "Laskar Pelangi")
        self.assertNotContains(response, "Fisika Dasar")
        self.assertNotContains(response, "Matematika Digital")

    def test_type_filter_ebook_saja(self):
        response = self._get(reverse("catalog:catalog"), {"type": "ebook"})
        self.assertContains(response, "Matematika Digital")
        self.assertNotContains(response, "Laskar Pelangi")

    def test_detail_buku_memuat_penulis_dan_availability(self):
        response = self._get(
            reverse("catalog:book_detail", args=[self.book1.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Laskar Pelangi")
        self.assertContains(response, "Andrea Hirata")
        self.assertContains(response, "Bentang Pustaka")
        self.assertContains(response, "2 dari 3 eksemplar tersedia")
        self.assertContains(response, "COPY-A1")
        self.assertContains(response, "TERSEDIA")

    def test_detail_buku_tidak_tersedia(self):
        response = self._get(
            reverse("catalog:book_detail", args=[self.book2.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "sedang tidak tersedia")

    def test_detail_ebook_dibedakan_dari_fisik(self):
        response = self._get(
            reverse("catalog:ebook_detail", args=[self.ebook1.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Matematika Digital")
        self.assertContains(response, "Penulis Sains")
        self.assertContains(response, "PDF")
        self.assertContains(response, "2.0 KB")
        self.assertNotContains(response, "Buku Fisik")

    def test_koleksi_nonaktif_disembunyikan(self):
        response = self._get(reverse("catalog:catalog"))
        self.assertNotContains(response, "Buku Nonaktif")
        response = self._get(
            reverse("catalog:book_detail", args=[self.book_hidden.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_unauthorized_access_ditolak(self):
        login_url = reverse("accounts:login")
        for url in (
            reverse("catalog:catalog"),
            reverse("catalog:book_detail", args=[self.book1.pk]),
            reverse("catalog:ebook_detail", args=[self.ebook1.pk]),
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn(login_url, response["Location"])

    def test_empty_result(self):
        response = self._get(
            reverse("catalog:catalog"), {"q": "zzz-tidak-ada"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tidak ada koleksi yang cocok")
        self.assertNotContains(response, "Laskar Pelangi")

    def test_pagination(self):
        for i in range(13):
            Book.objects.create(
                category=self.fiksi,
                isbn=f"ISBN-PAG-{i}",
                title=f"Buku Paginasi {i:02d}",
            )
        response = self._get(reverse("catalog:catalog"), {"page": 2})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Halaman 2 dari 2")
