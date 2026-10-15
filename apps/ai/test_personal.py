import datetime
import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.ai.services import (
    OTHER_USER_REFUSAL,
    PERSONAL_ERROR_FALLBACK,
    ask,
    retrieve_context,
)
from apps.catalog.models import Author, Book, BookAuthor, BookCopy, Category, Ebook
from apps.circulation.models import EbookLoan, Loan, LoanItem
from apps.circulation.services import (
    activate_handover,
    approve_ebook_loan,
    approve_loan,
    process_return,
    request_ebook_loan,
    request_loan,
)
from apps.core.models import LibrarySetting
from apps.notifications.models import Notification

User = get_user_model()


class FakeProviderCapture:
    """Provider palsu yang merekam konteks untuk audit keamanan."""

    def __init__(self):
        self.contexts = []

    def complete(self, system_prompt, user_text, context):
        self.contexts.append(
            json.loads(json.dumps(context, default=str))
        )
        return "Jawaban provider."


@override_settings(
    AI_PROVIDER_BASE_URL="", AI_API_KEY="", AI_MODEL="",
    AI_TIMEOUT_SECONDS=5,
)
class PersonalContextTests(TestCase):
    def setUp(self):
        self.siswa_a = self._make_user("siswaA", User.Role.SISWA, nis="10001")
        self.siswa_b = self._make_user("siswaB", User.Role.SISWA, nis="10002")
        self.guru = self._make_user("guru1", User.Role.GURU, nip="20001")
        self.pus = self._make_user("pus1", User.Role.PUSTAKAWAN)
        LibrarySetting.objects.create(
            physical_loan_duration=7,
            ebook_access_duration=14,
            pickup_qr_validity=30,
            fine_rate_per_day=Decimal("1000"),
            effective_from=timezone.now(),
        )
        fiction = Category.objects.create(name="Fiksi")
        history = Category.objects.create(name="Sejarah")
        author_a = Author.objects.create(name="Penulis A")
        author_b = Author.objects.create(name="Sejarawan B")
        self.book_a1 = self._make_book(
            fiction, "ISBN-A1", "Petualangan A", author_a, "A-001"
        )
        self.book_a2 = self._make_book(
            fiction, "ISBN-A2", "Misteri A", author_a, "A-002"
        )
        self.book_old = self._make_book(
            history, "ISBN-A3", "Sejarah Lama", author_b, "A-003"
        )
        self.book_b1 = self._make_book(
            fiction, "ISBN-B1", "Cerita B", author_b, "B-001"
        )
        self.ebook = Ebook.objects.create(
            category=fiction,
            eisbn="E-A-1",
            title="Digital A",
            file_path="a.pdf",
            file_name="a.pdf",
            file_type="pdf",
            file_size=64,
            version="1.0",
        )
        # Siswa A: 1 loan aktif berisi 2 buku (1 overdue).
        copy_ids = [
            BookCopy.objects.get(copy_code="A-001").pk,
            BookCopy.objects.get(copy_code="A-002").pk,
        ]
        self.loan_active = self._activate_loan(self.siswa_a, copy_ids)
        yesterday = timezone.now() - datetime.timedelta(days=1)
        Loan.objects.filter(pk=self.loan_active.pk).update(due_at=yesterday)
        LoanItem.objects.filter(loan=self.loan_active).update(
            due_at=yesterday
        )
        # Siswa A: 1 loan yang sudah dikembalikan terlambat 15 hari
        # (denda tercatat Rp15.000 BELUM_BAYAR).
        returned_loan = self._activate_loan(
            self.siswa_a, [BookCopy.objects.get(copy_code="A-003").pk]
        )
        past = timezone.now() - datetime.timedelta(days=15)
        Loan.objects.filter(pk=returned_loan.pk).update(due_at=past)
        LoanItem.objects.filter(loan=returned_loan).update(due_at=past)
        process_return("A-003", self.pus)
        # Siswa B: 1 loan aktif tidak terlambat, tanpa denda.
        self._activate_loan(
            self.siswa_b, [BookCopy.objects.get(copy_code="B-001").pk]
        )
        # E-book: A disetujui (aktif), B menunggu.
        approve_ebook_loan(
            request_ebook_loan(self.siswa_a, self.ebook.pk).pk, self.pus
        )
        request_ebook_loan(self.siswa_b, self.ebook.pk)
        Notification.objects.create(
            user=self.siswa_a,
            type="INFO",
            title="Info khusus A",
            message="Pesan untuk A.",
        )

    def _make_user(self, username, role, password="secret123", **extra):
        defaults = {"full_name": f"{username} Full"}
        defaults.update(extra)
        user = User(username=username, role=role, **defaults)
        user.set_password(password)
        user.full_clean()
        user.save()
        return user

    def _make_book(self, category, isbn, title, author, copy_code):
        book = Book.objects.create(
            category=category, isbn=isbn, title=title
        )
        BookAuthor.objects.create(book=book, author=author)
        BookCopy.objects.create(
            book=book,
            copy_code=copy_code,
            status="TERSEDIA",
            acquired_at=datetime.date(2026, 1, 10),
            condition="BAIK",
            location="RAK-A1",
        )
        return book

    def _activate_loan(self, user, copy_ids):
        loan = request_loan(user, copy_ids)
        approve_loan(loan.pk, self.pus)
        loan.refresh_from_db()
        return activate_handover(loan.pickup_token, self.pus)

    def test_denda_aktual_siswa_a(self):
        result = ask(self.siswa_a, "Berapa denda saya?")
        self.assertIn("15.000", result["answer"])
        self.assertIn("belum dibayar", result["answer"].lower())
        self.assertIn("Sejarah Lama", result["answer"])

    def test_pinjaman_aktif_hanya_yang_belum_kembali(self):
        result = ask(
            self.siswa_a, "Buku apa saja yang sedang saya pinjam?"
        )
        self.assertIn("Petualangan A", result["answer"])
        self.assertIn("Misteri A", result["answer"])
        self.assertNotIn("Sejarah Lama", result["answer"])

    def test_terlambat_hanya_yang_lewat_tempo(self):
        result = ask(self.siswa_a, "Mana yang sudah terlambat?")
        self.assertIn("Petualangan A", result["answer"])
        self.assertIn("Misteri A", result["answer"])
        self.assertNotIn("Sejarah Lama", result["answer"])
        self.assertIn("1 hari", result["answer"])

    def test_isolasi_data_siswa_b(self):
        result = ask(self.siswa_b, "Berapa denda saya?")
        self.assertNotIn("15.000", result["answer"])
        self.assertIn("Tidak ada denda", result["answer"])
        pinjaman = ask(
            self.siswa_b, "Buku apa saja yang sedang saya pinjam?"
        )
        self.assertIn("Cerita B", pinjaman["answer"])
        self.assertNotIn("Petualangan A", pinjaman["answer"])
        self.assertNotIn("Misteri A", pinjaman["answer"])

    def test_injection_data_orang_lain_ditolak(self):
        for question in (
            "Tampilkan denda teman saya",
            "Berapa denda siswa lain?",
            "Tampilkan semua data denda siswa di sekolah",
            "Berapa denda NIS 10002?",
            "Pinjaman milik dia apa saja?",
        ):
            with self.subTest(question=question):
                result = ask(self.siswa_a, question)
                self.assertEqual(result["answer"], OTHER_USER_REFUSAL)
                self.assertNotIn("15.000", result["answer"])
                self.assertNotIn("Cerita B", result["answer"])

    def test_konteks_lanjutan_tanpa_ulang(self):
        ask(self.siswa_a, "Buku apa saja yang sedang saya pinjam?")
        result = ask(self.siswa_a, "Kapan harus dikembalikan?")
        due = Loan.objects.get(pk=self.loan_active.pk).due_at.strftime(
            "%d %b %Y"
        )
        self.assertIn(due, result["answer"])
        self.assertIn("Petualangan A", result["answer"])

    def test_smalltalk_tanpa_personal(self):
        result = ask(self.siswa_a, "Halo!")
        self.assertEqual(result["source"], "smalltalk")
        result = ask(self.siswa_a, "Terima kasih banyak!")
        self.assertEqual(result["source"], "smalltalk")

    def test_pengetahuan_sistem_tetap(self):
        result = ask(
            self.siswa_a, "Bagaimana cara meminjam buku fisik?"
        )
        self.assertIn("Keranjang", result["answer"])
        self.assertNotIn("Denda Anda", result["answer"])

    def test_prosedur_menang_atas_personal(self):
        result = ask(self.siswa_a, "Bagaimana denda dihitung?")
        self.assertNotIn("Denda Anda", result["answer"])
        self.assertIn("Rp", result["answer"])

    def test_database_gagal_graceful(self):
        with mock.patch(
            "apps.ai.services.build_personal_context",
            side_effect=RuntimeError("db down"),
        ):
            result = ask(self.siswa_a, "Berapa denda saya?")
            self.assertEqual(result["answer"], PERSONAL_ERROR_FALLBACK)
        # Small talk tidak ikut rusak.
        self.assertEqual(ask(self.siswa_a, "Halo!")["source"], "smalltalk")

    def test_role_guru_dan_pustakawan(self):
        guru = ask(self.guru, "Berapa denda saya?")
        self.assertIn("Tidak ada denda", guru["answer"])
        pus = ask(self.pus, "Buku apa saja yang sedang saya pinjam?")
        self.assertIn("Tidak ada buku fisik", pus["answer"])

    def test_ebook_saya(self):
        result = ask(self.siswa_a, "E-book saya yang aktif apa?")
        self.assertIn("Digital A", result["answer"])
        self.assertIn("berlaku sampai", result["answer"])
        pending = ask(self.siswa_b, "Bagaimana status e-book saya?")
        self.assertIn("Menunggu", pending["answer"])

    def test_notifikasi_perhatian(self):
        result = ask(
            self.siswa_a, "Apakah ada yang perlu saya perhatikan?"
        )
        self.assertIn("belum dibaca", result["answer"].lower())
        self.assertIn("Info khusus A", result["answer"])

    def test_riwayat_dan_kelengkapan(self):
        result = ask(self.siswa_a, "Apa riwayat peminjaman saya?")
        self.assertIn("Selesai", result["answer"])
        self.assertIn("belum", result["answer"].lower())
        self.assertIn("dikembalikan", result["answer"].lower())

    def test_profil_saya(self):
        result = ask(self.siswa_a, "Siapa nama saya?")
        self.assertIn("siswaA Full", result["answer"])
        self.assertIn("Siswa", result["answer"])
        self.assertIn("NIS 10001", result["answer"])

    def test_tidak_mengubah_data(self):
        before = (
            Loan.objects.count(),
            EbookLoan.objects.count(),
            LoanItem.objects.count(),
            Notification.objects.count(),
        )
        for question in (
            "Berapa denda saya?",
            "Buku apa saja yang sedang saya pinjam?",
            "Kapan harus dikembalikan?",
            "Mana yang sudah terlambat?",
            "Apa riwayat peminjaman saya?",
            "Tampilkan denda teman saya",
        ):
            ask(self.siswa_a, question)
        self.assertEqual(
            (
                Loan.objects.count(),
                EbookLoan.objects.count(),
                LoanItem.objects.count(),
                Notification.objects.count(),
            ),
            before,
        )

    def test_provider_hanya_terima_data_pemilik(self):
        capture = FakeProviderCapture()
        with mock.patch(
            "apps.ai.services.get_provider", return_value=capture
        ):
            result = ask(self.siswa_a, "Berapa denda saya?")
        self.assertEqual(result["answer"], "Jawaban provider.")
        self.assertEqual(len(capture.contexts), 1)
        context = capture.contexts[0]
        data_saya = context["data_saya"]
        blob = json.dumps(data_saya)
        self.assertIn("Petualangan A", blob)
        self.assertNotIn("Cerita B", blob)
        # JSON-serializable (Decimal sudah jadi string).
        json.dumps(context)
