from django.db import models


class Category(models.Model):
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "categories"

    def __str__(self):
        return self.name


class Author(models.Model):
    name = models.CharField(max_length=255)
    bio = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "authors"

    def __str__(self):
        return self.name


class Book(models.Model):
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="books"
    )
    isbn = models.CharField(max_length=20, unique=True)
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    publisher = models.CharField(max_length=255, null=True, blank=True)
    publication_year = models.PositiveIntegerField(null=True, blank=True)
    edition = models.CharField(max_length=50, null=True, blank=True)
    cover_url = models.URLField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "books"

    def __str__(self):
        return self.title


class BookCopy(models.Model):
    book = models.ForeignKey(
        Book, on_delete=models.PROTECT, related_name="copies"
    )
    copy_code = models.CharField(max_length=50, unique=True)
    status = models.CharField(max_length=30)
    acquired_at = models.DateField()
    condition = models.CharField(max_length=30)
    location = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "book_copies"

    def __str__(self):
        return self.copy_code


class BookAuthor(models.Model):
    # Catatan: Django ORM tidak mendukung composite PRIMARY KEY
    # (system check models.E026), sehingga keunikan pasangan
    # (book_id, author_id) ditegakkan lewat UniqueConstraint —
    # padanan fungsional composite primary key di level database.
    book = models.ForeignKey(Book, on_delete=models.CASCADE)
    author = models.ForeignKey(Author, on_delete=models.CASCADE)

    class Meta:
        db_table = "book_authors"
        constraints = [
            models.UniqueConstraint(
                fields=["book", "author"], name="uniq_book_author"
            )
        ]

    def __str__(self):
        return f"{self.book_id} - {self.author_id}"


class Ebook(models.Model):
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="ebooks"
    )
    eisbn = models.CharField(max_length=20)
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    publisher = models.CharField(max_length=255, null=True, blank=True)
    publication_year = models.PositiveIntegerField(null=True, blank=True)
    edition = models.CharField(max_length=50, null=True, blank=True)
    cover_url = models.URLField(null=True, blank=True)
    file_path = models.CharField(max_length=500)
    file_name = models.CharField(max_length=255)
    file_type = models.CharField(max_length=20)
    file_size = models.BigIntegerField()
    version = models.CharField(max_length=20)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ebooks"

    def __str__(self):
        return self.title


class EbookAuthor(models.Model):
    # Lihat catatan BookAuthor: keunikan (ebook_id, author_id)
    # ditegakkan lewat UniqueConstraint (Django: models.E026).
    ebook = models.ForeignKey(Ebook, on_delete=models.CASCADE)
    author = models.ForeignKey(Author, on_delete=models.CASCADE)

    class Meta:
        db_table = "ebook_authors"
        constraints = [
            models.UniqueConstraint(
                fields=["ebook", "author"], name="uniq_ebook_author"
            )
        ]

    def __str__(self):
        return f"{self.ebook_id} - {self.author_id}"
