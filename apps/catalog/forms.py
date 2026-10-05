from django import forms

from .models import Author, Book, BookCopy, Category, Ebook, EbookAuthor

INPUT_CLASS = (
    "w-full border border-slate-300 rounded-xl px-3.5 py-2.5 text-sm "
    "bg-white placeholder:text-slate-400 "
    "focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
)

# Status eksemplar yang dapat ditetapkan pustakawan (BR-13: kondisi aktual).
COPY_STATUSES = ["TERSEDIA", "DIPINJAM", "RUSAK", "HILANG"]
COPY_CONDITIONS = ["BAIK", "RUSAK RINGAN", "RUSAK BERAT"]


def style_fields(form):
    for field in form.fields.values():
        field.widget.attrs.update({"class": INPUT_CLASS})
    return form


class BookForm(forms.ModelForm):
    """Tambah/ubah buku + penulisnya (FR-82 – FR-84)."""

    authors = forms.ModelMultipleChoiceField(
        label="Penulis",
        queryset=Author.objects.order_by("name"),
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )

    class Meta:
        model = Book
        fields = [
            "category", "isbn", "title", "description", "publisher",
            "publication_year", "edition", "cover_url", "is_active",
        ]
        labels = {
            "category": "Kategori",
            "isbn": "ISBN",
            "title": "Judul",
            "description": "Deskripsi",
            "publisher": "Penerbit",
            "publication_year": "Tahun terbit",
            "edition": "Edisi",
            "cover_url": "URL sampul",
            "is_active": "Aktif di katalog",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["authors"].initial = Author.objects.filter(
                bookauthor__book=self.instance
            )
        style_fields(self)

    def save(self, commit=True):
        from .models import BookAuthor

        book = super().save(commit=commit)
        if commit:
            BookAuthor.objects.filter(book=book).exclude(
                author__in=self.cleaned_data["authors"]
            ).delete()
            existing = set(
                BookAuthor.objects.filter(book=book).values_list(
                    "author_id", flat=True
                )
            )
            BookAuthor.objects.bulk_create(
                BookAuthor(book=book, author=author)
                for author in self.cleaned_data["authors"]
                if author.pk not in existing
            )
        return book


class BookCopyForm(forms.ModelForm):
    """Tambah/ubah eksemplar (FR-85). Kode kosong -> dibuatkan sistem (BR-81).

    Mode tambah mendukung bulk: isi jumlah > 1 dan sistem generate kode
    unik untuk tiap eksemplar (atribut lain berlaku untuk semuanya).
    """

    copy_code = forms.CharField(
        label="Kode eksemplar (kosongkan untuk otomatis; diabaikan bila jumlah > 1)",
        max_length=50,
        required=False,
    )
    jumlah = forms.IntegerField(
        label="Jumlah eksemplar",
        min_value=1,
        max_value=100,
        initial=1,
    )
    status = forms.ChoiceField(
        label="Status",
        choices=[(status, status) for status in COPY_STATUSES],
    )
    condition = forms.ChoiceField(
        label="Kondisi",
        choices=[(condition, condition) for condition in COPY_CONDITIONS],
    )
    acquired_at = forms.DateField(
        label="Tanggal peroleh",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    class Meta:
        model = BookCopy
        fields = ["copy_code", "status", "acquired_at", "condition", "location"]
        labels = {"location": "Lokasi"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            # Mode ubah: satu eksemplar, tanpa jumlah.
            del self.fields["jumlah"]
        style_fields(self)


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name"]
        labels = {"name": "Nama kategori"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_fields(self)


class AuthorForm(forms.ModelForm):
    class Meta:
        model = Author
        fields = ["name", "bio"]
        labels = {"name": "Nama penulis", "bio": "Bio"}
        widgets = {"bio": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_fields(self)


# Tipe/ukuran e-book: keputusan teknis terdokumentasi (NFR-27).
EBOOK_EXTENSIONS = frozenset({"pdf", "epub"})
EBOOK_MAX_SIZE = 50 * 1024 * 1024


def generate_copy_code(book):
    """Kode unik sistematis; tetap menghormati unique DB (BR-81)."""
    seq = book.copies.count() + 1
    while True:
        code = f"COPY-{book.pk:04d}-{seq:02d}"
        if not BookCopy.objects.filter(copy_code=code).exists():
            return code
        seq += 1


class EbookForm(forms.ModelForm):
    """Tambah/ubah e-book + penulisnya (FR-82 – FR-84).

    File divalidasi tipe/ukuran (NFR-27); eisbn unik ditegakkan di level
    form karena kolom DB existing tidak unique (tanpa migration).
    """

    authors = forms.ModelMultipleChoiceField(
        label="Penulis",
        queryset=Author.objects.order_by("name"),
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    file = forms.FileField(
        label="File e-book (pdf/epub, maks 50 MB)",
        required=False,
    )

    class Meta:
        model = Ebook
        fields = [
            "category", "eisbn", "title", "description", "publisher",
            "publication_year", "edition", "cover_url", "version",
            "is_active",
        ]
        labels = {
            "category": "Kategori",
            "eisbn": "E-ISBN",
            "title": "Judul",
            "description": "Deskripsi",
            "publisher": "Penerbit",
            "publication_year": "Tahun terbit",
            "edition": "Edisi",
            "cover_url": "URL sampul",
            "version": "Versi",
            "is_active": "Aktif di katalog",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["authors"].initial = Author.objects.filter(
                ebookauthor__ebook=self.instance
            )
            self.fields["file"].help_text = (
                "Kosongkan bila file tidak diganti."
            )
        style_fields(self)

    def clean_eisbn(self):
        eisbn = (self.cleaned_data.get("eisbn") or "").strip()
        qs = Ebook.objects.filter(eisbn=eisbn)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("E-ISBN sudah dipakai e-book lain.")
        return eisbn

    def save(self, commit=True):
        ebook = super().save(commit=commit)
        if commit:
            EbookAuthor.objects.filter(ebook=ebook).exclude(
                author__in=self.cleaned_data["authors"]
            ).delete()
            existing = set(
                EbookAuthor.objects.filter(ebook=ebook).values_list(
                    "author_id", flat=True
                )
            )
            EbookAuthor.objects.bulk_create(
                EbookAuthor(ebook=ebook, author=author)
                for author in self.cleaned_data["authors"]
                if author.pk not in existing
            )
        return ebook
