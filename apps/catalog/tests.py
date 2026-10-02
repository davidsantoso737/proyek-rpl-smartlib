import datetime

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.catalog.models import Author, Book, BookCopy, Category


class CatalogModelTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name="Fiksi")

    def _make_book(self, isbn="978-123"):
        return Book.objects.create(
            category=self.category, isbn=isbn, title="Judul Buku"
        )

    def test_8_isbn_unique(self):
        self._make_book(isbn="ISBN-UNIK-1")
        with transaction.atomic(), self.assertRaises(IntegrityError):
            self._make_book(isbn="ISBN-UNIK-1")

    def test_9_bookcopy_copy_code_unique(self):
        book = self._make_book()
        BookCopy.objects.create(
            book=book,
            copy_code="COPY-001",
            status="TERSEDIA",
            acquired_at=datetime.date(2026, 1, 10),
            condition="BAIK",
            location="RAK-A1",
        )
        with transaction.atomic(), self.assertRaises(IntegrityError):
            BookCopy.objects.create(
                book=book,
                copy_code="COPY-001",
                status="TERSEDIA",
                acquired_at=datetime.date(2026, 1, 11),
                condition="BAIK",
                location="RAK-A2",
            )

    def test_10_acquired_at_menggunakan_datefield(self):
        field = BookCopy._meta.get_field("acquired_at")
        self.assertEqual(field.get_internal_type(), "DateField")
        copy = BookCopy.objects.create(
            book=self._make_book(),
            copy_code="COPY-002",
            status="TERSEDIA",
            acquired_at=datetime.date(2026, 2, 1),
            condition="BAIK",
            location="RAK-A1",
        )
        copy.refresh_from_db()
        self.assertEqual(copy.acquired_at, datetime.date(2026, 2, 1))

    def test_book_category_on_delete_protect(self):
        self.assertEqual(
            Book._meta.get_field("category").remote_field.on_delete.__name__,
            "PROTECT",
        )

    def test_bookauthor_dan_ebookauthor_tanpa_id_artifisial(self):
        # Pasangan (book, author) / (ebook, author) dijamin unik di level DB.
        from apps.catalog.models import BookAuthor, EbookAuthor, Ebook

        author = Author.objects.create(name="Penulis A")
        book = self._make_book()
        BookAuthor.objects.create(book=book, author=author)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            BookAuthor.objects.create(book=book, author=author)

        ebook = Ebook.objects.create(
            category=self.category,
            eisbn="E-001",
            title="Ebook A",
            file_path="/files/a.pdf",
            file_name="a.pdf",
            file_type="pdf",
            file_size=1024,
            version="1.0",
        )
        EbookAuthor.objects.create(ebook=ebook, author=author)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            EbookAuthor.objects.create(ebook=ebook, author=author)
