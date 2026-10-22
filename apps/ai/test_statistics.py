import datetime
import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.ai.services import (
    NO_DATA_FALLBACK,
    OTHER_USER_REFUSAL,
    STATISTICS_ERROR_FALLBACK,
    ask,
    retrieve_context,
)
from apps.catalog.models import (
    Author,
    Book,
    BookAuthor,
    BookCopy,
    Category,
    Ebook,
)
from apps.catalog.statistics import collection_statistics
from apps.circulation.services import (
    activate_handover,
    approve_loan,
    process_return,
    request_loan,
)
from apps.core.models import LibrarySetting

User = get_user_model()


class FakeProviderCapture:
    def __init__(self):
        self.contexts = []

    def complete(self, system_prompt, user_text, context):
        self.contexts.append(json.loads(json.dumps(context, default=str)))
        return "Jawaban provider."


@override_settings(
    AI_PROVIDER_BASE_URL="", AI_API_KEY="", AI_MODEL="",
    AI_TIMEOUT_SECONDS=5,
)
class CollectionStatisticsTests(TestCase):
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
        self.fiction = Category.objects.create(name="Fiksi")
        self.history = Category.objects.create(name="Sejarah")
        self.author = Author.objects.create(name="Penulis A")

        self.book_a = self._make_book(
            self.fiction, "ISBN-A", "Petualangan A", "A-001"
        )
        self.copy_a2 = self._add_copy(self.book_a, "A-002")
        self.copy_a3 = self._add_copy(self.book_a, "A-003", "RUSAK")
        self.copy_a4 = self._add_copy(self.book_a, "A-004", "HILANG")
        self.book_b = self._make_book(
            self.fiction, "ISBN-B", "Misteri B", "B-001"
        )
        self.book_c = self._make_book(
            self.history, "ISBN-C", "Sejarah C", "C-001"
        )
        self.book_d = Book.objects.create(
            category=self.history, isbn="ISBN-D", title="Tanpa Eksemplar"
        )
        self.book_inactive = Book.objects.create(
            category=self.history,
            isbn="ISBN-E",
            title="Arsip Lama",
            is_active=False,
        )
        self._add_copy(self.book_inactive, "E-001")

        self.ebook_active = self._make_ebook("Digital Fiksi", "E-A-1")
        self._make_ebook("Digital Arsip", "E-A-2", is_active=False)

        self._active_loan(self.siswa2, [self.copy_a2.pk])
        self._active_loan(self.siswa2, [self.book_b.copies.get().pk])
        process_return("B-001", self.pus)

    def _make_user(self, username, role, password="secret123", **extra):
        defaults = {"full_name": f"{username} Full"}
        defaults.update(extra)
        user = User(username=username, role=role, **defaults)
        user.set_password(password)
        user.full_clean()
        user.save()
        return user

    def _make_book(self, category, isbn, title, copy_code):
        book = Book.objects.create(
            category=category, isbn=isbn, title=title
        )
        BookAuthor.objects.create(book=book, author=self.author)
        self._add_copy(book, copy_code)
        return book

    def _add_copy(self, book, copy_code, status="TERSEDIA"):
        return BookCopy.objects.create(
            book=book,
            copy_code=copy_code,
            status=status,
            acquired_at=datetime.date(2026, 1, 10),
            condition="BAIK",
            location="RAK-A1",
        )

    def _make_ebook(self, title, eisbn, is_active=True):
        return Ebook.objects.create(
            category=self.fiction,
            eisbn=eisbn,
            title=title,
            file_path="a.pdf",
            file_name="a.pdf",
            file_type="pdf",
            file_size=64,
            version="1.0",
            is_active=is_active,
        )

    def _active_loan(self, user, copy_ids):
        loan = request_loan(user, copy_ids)
        approve_loan(loan.pk, self.pus)
        loan.refresh_from_db()
        return activate_handover(loan.pickup_token, self.pus)

    def test_snapshot_statistik_sesuai_fixture(self):
        stats = collection_statistics()
        self.assertEqual(stats["titles_total"], 5)
        self.assertEqual(stats["titles_active"], 4)
        self.assertEqual(stats["ebooks_total"], 2)
        self.assertEqual(stats["ebooks_active"], 1)
        self.assertEqual(stats["copies_total"], 7)
        self.assertEqual(stats["copies_available"], 4)
        self.assertEqual(stats["copies_borrowed"], 1)
        self.assertEqual(stats["copies_damaged"], 1)
        self.assertEqual(stats["copies_lost"], 1)
        self.assertEqual(stats["titles_with_available"], 3)
        self.assertEqual(stats["titles_without_available"], 1)
        self.assertEqual(stats["titles_borrowed"], 1)
        self.assertEqual(stats["active_loans"], 1)
        self.assertEqual(stats["categories_total"], 2)

    def test_katalog_fisik_tidak_menyertakan_ebook(self):
        result = ask(self.siswa, "Buku fisik apa saja yang tersedia?")
        self.assertEqual(result["source"], "local")
        self.assertIn("Petualangan A", result["answer"])
        self.assertIn("Sejarah C", result["answer"])
        self.assertIn("3 judul", result["answer"])
        self.assertNotIn("Digital Fiksi", result["answer"])
        self.assertNotIn("e-book", result["answer"].lower())
        self.assertNotIn("Kategori:", result["answer"])
        self.assertNotEqual(result["answer"], NO_DATA_FALLBACK)

    def test_availability_only_answers_availability(self):
        answer = ask(self.siswa, "Buku apa saja yang tersedia?")["answer"]
        self.assertIn("Buku fisik yang tersedia", answer)
        self.assertNotIn("Eksemplar fisik", answer)
        self.assertNotIn("Peminjaman fisik aktif", answer)
        self.assertNotIn("Kategori:", answer)

    def test_koleksi_umum_tanpa_kata_ebook_tetap_menemukan_ebook(self):
        for question in (
            "Apa saja koleksi perpustakaan saat ini?",
            "Berapa jumlah buku dan ebook secara keseluruhan?",
            "Apakah perpustakaan menyediakan ebook?",
        ):
            with self.subTest(question=question):
                answer = ask(self.guru, question)["answer"]
                self.assertIn("e-book", answer.lower())
                self.assertNotEqual(answer, NO_DATA_FALLBACK)

    def test_ebook_khusus_tetap_berfungsi(self):
        result = ask(self.siswa, "Apa saja ebook yang tersedia?")
        self.assertIn("Digital Fiksi", result["answer"])
        self.assertIn("1 e-book aktif", result["answer"])
        self.assertNotIn("Digital Arsip", result["answer"])

    def test_judul_tidak_dicampur_dengan_eksemplar(self):
        answer = ask(self.siswa, "Berapa jumlah buku?")["answer"]
        self.assertIn("4 judul buku fisik aktif", answer)
        self.assertIn("dari 5 judul terdaftar", answer)
        self.assertIn("total eksemplar fisik ada 7", answer)
        self.assertNotIn("Kategori:", answer)
        self.assertNotIn("Peminjaman fisik aktif", answer)

    def test_ebook_tidak_dihitung_ganda(self):
        stats = collection_statistics()
        self.assertEqual(stats["titles_total"], 5)
        self.assertEqual(stats["ebooks_total"], 2)
        answer = ask(
            self.siswa, "Berapa jumlah judul buku dan ebook?"
        )["answer"]
        self.assertIn("4 judul buku fisik aktif", answer)
        self.assertIn("1 e-book aktif", answer)
        self.assertIn("dari 2 e-book terdaftar", answer)
        self.assertIn("metrik berbeda", answer)

    def test_distribusi_status_eksemplar(self):
        answer = ask(
            self.siswa, "Bagaimana distribusi status seluruh koleksi fisik?"
        )["answer"]
        self.assertIn("Tersedia: 4", answer)
        self.assertIn("Dipinjam: 1", answer)
        self.assertIn("Rusak: 1", answer)
        self.assertIn("Hilang: 1", answer)

    def test_dipinjam_hanya_transaksi_aktif(self):
        stats = collection_statistics()
        self.assertEqual(stats["active_loans"], 1)
        self.assertEqual(stats["titles_borrowed"], 1)
        answer = ask(self.siswa, "Buku apa saja yang dipinjam?")["answer"]
        self.assertIn("Petualangan A", answer)
        self.assertNotIn("Misteri B", answer)
        self.assertIn("1 transaksi peminjaman fisik aktif", answer)

    def test_rusak_dan_hilang_sesuai_status_inventaris(self):
        answer = ask(
            self.siswa,
            "Buku apa saja yang memiliki eksemplar rusak atau hilang?",
        )["answer"]
        self.assertIn("1 eksemplar RUSAK", answer)
        self.assertIn("1 eksemplar HILANG", answer)
        self.assertIn("Petualangan A", answer)
        self.assertIn("1 eksemplar rusak", answer)

    def test_judul_tanpa_eksemplar_tersedia(self):
        answer = ask(
            self.siswa, "Judul apa yang tidak memiliki eksemplar tersedia?"
        )["answer"]
        self.assertIn("Tanpa Eksemplar", answer)
        self.assertIn("belum ada eksemplar tercatat", answer)
        self.assertIn("1 dari 4 judul", answer)

    def test_kategori_breakdown(self):
        answer = ask(self.siswa, "Berapa jumlah buku berdasarkan kategori?")[
            "answer"
        ]
        self.assertIn("Fiksi: 2 judul buku fisik", answer)
        self.assertIn("Sejarah: 2 judul buku fisik", answer)
        self.assertIn("menghitung judul, bukan jumlah eksemplar", answer)
        self.assertNotIn("e-book", answer.lower())

    def test_inventory_total_tidak_membahas_ebook_atau_kategori(self):
        answer = ask(
            self.siswa, "Ada berapa total eksemplar buku fisik?"
        )["answer"]
        self.assertIn("total 7 eksemplar", answer)
        self.assertIn("4 tersedia, 1 dipinjam, 1 rusak, 1 hilang", answer)
        self.assertNotIn("e-book", answer.lower())
        self.assertNotIn("Kategori", answer)

    def test_multi_intent_total_dan_dipinjam(self):
        answer = ask(
            self.siswa,
            "Berapa total eksemplar fisik dan berapa yang sedang dipinjam?",
        )["answer"]
        self.assertIn("total 7 eksemplar", answer)
        self.assertIn("1 eksemplar sedang dipinjam", answer)

    def test_jenis_koleksi_tanpa_daftar_judul(self):
        answer = ask(
            self.siswa, "Jenis koleksi apa saja yang ada di perpustakaan?"
        )["answer"]
        self.assertIn("buku fisik", answer.lower())
        self.assertIn("e-book", answer.lower())
        self.assertNotIn("Petualangan A", answer)
        self.assertNotIn("Digital Fiksi", answer)

    def test_daftar_kategori_tanpa_jumlah(self):
        answer = ask(self.siswa, "Kategori buku apa saja yang ada?")["answer"]
        self.assertIn("Fiksi", answer)
        self.assertIn("Sejarah", answer)
        self.assertNotIn("judul buku fisik", answer)
        self.assertNotIn("e-book aktif", answer)

    def test_ringkasan_hanya_saat_diminta(self):
        answer = ask(
            self.siswa, "Berikan statistik keseluruhan perpustakaan."
        )["answer"]
        self.assertIn("Ringkasan perpustakaan", answer)
        self.assertIn("Eksemplar fisik", answer)
        self.assertIn("E-book aktif", answer)
        self.assertIn("tidak dijumlahkan", answer)

    def test_status_lanjutan_tanpa_konteks_percakapan(self):
        answer = ask(self.siswa, "Kalau yang rusak berapa?")["answer"]
        self.assertIn("1 eksemplar berstatus Rusak", answer)
        self.assertNotIn("E-book", answer)
        self.assertNotIn("Kategori", answer)

    def test_identitas_peminjam_ditolak_dengan_tegas(self):
        answer = ask(self.siswa, "Siapa yang sedang meminjam buku?")["answer"]
        self.assertIn("tidak menampilkan identitas peminjam", answer)
        self.assertNotIn("siswa2", answer)

    def test_statistik_agregat_tidak_ditolak(self):
        for question in (
            "Berapa total judul buku di perpustakaan?",
            "Berapa jumlah eksemplar fisik?",
            "Berapa buku yang sedang dipinjam pengguna lain?",
        ):
            with self.subTest(question=question):
                answer = ask(self.siswa, question)["answer"]
                self.assertNotEqual(answer, OTHER_USER_REFUSAL)
                self.assertNotEqual(answer, NO_DATA_FALLBACK)
                self.assertRegex(answer, r"\d")

    def test_identitas_peminjam_tidak_bocor(self):
        fake = FakeProviderCapture()
        with mock.patch("apps.ai.services.get_provider", return_value=fake):
            result = ask(
                self.siswa,
                "Buku apa saja yang sedang dipinjam pengguna lain?",
            )
        self.assertIn("Petualangan A", result["answer"])
        self.assertNotIn("siswa2", result["answer"])
        self.assertNotIn("siswa2 Full", result["answer"])
        self.assertNotIn("siswa2 Full", json.dumps(fake.contexts, default=str))
        context = retrieve_context(self.siswa, "Buku apa saja yang dipinjam?")
        self.assertNotIn("operational_items", context["statistik"])
        self.assertNotIn("peminjam", json.dumps(context["statistik"]))

    def test_detail_operasional_hanya_untuk_pihak_berwenang(self):
        biasa = collection_statistics()
        operasional = collection_statistics(include_operational=True)
        self.assertNotIn("operational_items", biasa)
        self.assertIn("operational_items", operasional)
        item = operasional["operational_items"][0]
        self.assertEqual(item["kode_eksemplar"], "A-002")
        self.assertNotIn("Full", json.dumps(operasional["operational_items"]))

    def test_smalltalk_tanpa_query_database(self):
        with mock.patch(
            "apps.ai.services.collection_statistics",
            side_effect=AssertionError("query tidak diperlukan"),
        ) as mocked:
            result = ask(self.siswa, "Halo!")
        self.assertEqual(result["source"], "smalltalk")
        mocked.assert_not_called()

    def test_denda_dan_pinjaman_personal_tetap(self):
        denda = ask(self.siswa, "Berapa denda saya?")["answer"]
        self.assertIn("Tidak ada denda", denda)
        pinjaman = ask(
            self.siswa, "Buku apa saja yang sedang saya pinjam?"
        )["answer"]
        self.assertIn("Tidak ada buku fisik yang sedang Anda pinjam", pinjaman)

    def test_aturan_dan_fitur_tetap(self):
        alur = ask(self.siswa, "Bagaimana cara meminjam buku?")["answer"]
        self.assertIn("Keranjang", alur)
        fitur = ask(self.siswa, "Fitur apa saja yang tersedia?")["answer"]
        self.assertIn("katalog", fitur)

    def test_kegagalan_statistik_tidak_mengarang(self):
        with mock.patch(
            "apps.ai.services.collection_statistics",
            side_effect=RuntimeError("db down"),
        ):
            result = ask(self.siswa, "Berapa jumlah eksemplar fisik?")
        self.assertEqual(result["answer"], STATISTICS_ERROR_FALLBACK)
        self.assertNotRegex(result["answer"], r"\d")
