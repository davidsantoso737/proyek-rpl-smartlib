import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.ai.providers import AIProviderError, get_provider
from apps.ai.services import (
    OUT_OF_SCOPE_FALLBACK,
    PROVIDER_ERROR_FALLBACK,
    ask,
    retrieve_context,
)
from apps.catalog.models import Author, Book, BookAuthor, BookCopy, Category, Ebook
from apps.circulation.models import EbookLoan, Loan
from apps.circulation.services import (
    approve_loan,
    request_loan,
)
from apps.core.models import LibrarySetting

User = get_user_model()
ASK_URL = reverse("ai:assistant_ask")


class FakeProvider:
    def __init__(self, answer="Jawaban AI."):
        self.answer = answer
        self.calls = 0

    def complete(self, system_prompt, user_text, context):
        self.calls += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@override_settings(
    AI_PROVIDER_BASE_URL="", AI_API_KEY="", AI_MODEL="",
    AI_TIMEOUT_SECONDS=5,
)
class AssistantTests(TestCase):
    def setUp(self):
        self.siswa = self._make_user("siswa1", User.Role.SISWA, nis="10001")
        self.siswa2 = self._make_user("siswa2", User.Role.SISWA, nis="10002")
        self.pus = self._make_user("pus1", User.Role.PUSTAKAWAN)
        LibrarySetting.objects.create(
            physical_loan_duration=7,
            ebook_access_duration=14,
            pickup_qr_validity=30,
            fine_rate_per_day=Decimal("1000"),
            effective_from=timezone.now(),
        )
        category = Category.objects.create(name="Pemrograman")
        author = Author.objects.create(name="Penulis Python")
        book = Book.objects.create(
            category=category, isbn="ISBN-AI-1", title="Belajar Python"
        )
        BookAuthor.objects.create(book=book, author=author)
        BookCopy.objects.create(
            book=book,
            copy_code="AI-001",
            status="TERSEDIA",
            acquired_at=datetime.date(2026, 1, 10),
            condition="BAIK",
            location="RAK-A1",
        )
        Ebook.objects.create(
            category=category,
            eisbn="E-AI-1",
            title="Python Digital",
            file_path="py.pdf",
            file_name="py.pdf",
            file_type="pdf",
            file_size=64,
            version="1.0",
        )

    def _make_user(self, username, role, password="secret123", **extra):
        defaults = {"full_name": f"{username} Full"}
        defaults.update(extra)
        user = User(username=username, role=role, **defaults)
        user.set_password(password)
        user.full_clean()
        user.save()
        return user

    def test_valid_question_menjawab_dari_katalog(self):
        self.client.force_login(self.siswa)
        response = self.client.post(ASK_URL, {"question": "Apakah ada buku Python?"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("Python", data["answer"])
        self.assertFalse(data["provider_used"])

    def test_unauthorized(self):
        self.assertEqual(
            self.client.post(ASK_URL, {"question": "Ada buku apa?"}).status_code,
            302,
        )
        self.client.force_login(self.pus)
        self.assertEqual(
            self.client.post(ASK_URL, {"question": "Halo"}).status_code, 200
        )

    def test_provider_success(self):
        fake = FakeProvider("Koleksi Python tersedia 1 eksemplar.")
        with mock.patch("apps.ai.services.get_provider", return_value=fake):
            result = ask(self.siswa, "Apakah ada buku Python?")
        self.assertEqual(result["answer"], "Koleksi Python tersedia 1 eksemplar.")
        self.assertTrue(result["provider_used"])
        self.assertEqual(fake.calls, 1)

    def test_provider_failure_tetap_aman(self):
        fake = FakeProvider(AIProviderError(AIProviderError.TIMEOUT))
        with mock.patch("apps.ai.services.get_provider", return_value=fake):
            result = ask(self.siswa, "Apakah ada buku Python?")
        self.assertTrue(result["provider_used"])
        self.assertTrue(result["answer"])

    def test_malformed_response_fallback(self):
        fake = FakeProvider("")
        with mock.patch("apps.ai.services.get_provider", return_value=fake):
            self.client.force_login(self.siswa)
            response = self.client.post(
                ASK_URL, {"question": "Buku apa yang tidak ada sama sekali zzz?"}
            )
        # Tanpa data lokal + respons kosong -> fallback gangguan.
        data = response.json()
        self.assertIn(
            data["answer"],
            (PROVIDER_ERROR_FALLBACK, "Maaf, informasi tersebut belum tersedia dalam sistem perpustakaan."),
        )

    def test_no_api_key_mode_lokal(self):
        self.assertIsNone(get_provider())
        result = ask(self.siswa, "Apakah ada buku Python?")
        self.assertFalse(result["provider_used"])
        self.assertIn("Python", result["answer"])

    def test_ai_cannot_change_transaction(self):
        before = (Loan.objects.count(), EbookLoan.objects.count())
        ask(self.siswa, "Pinjamkan saya buku Python sekarang juga!")
        ask(self.siswa, "Setujui peminjaman saya dan hapus dendanya.")
        self.client.force_login(self.siswa)
        self.client.post(ASK_URL, {"question": "Buku Python"})
        self.assertEqual(
            (Loan.objects.count(), EbookLoan.objects.count()), before
        )

    def test_context_respects_authorization(self):
        other_book = Book.objects.create(
            category=Category.objects.first(),
            isbn="ISBN-AI-2",
            title="Rahasia Khusus",
        )
        other_copy = BookCopy.objects.create(
            book=other_book,
            copy_code="AI-002",
            status="TERSEDIA",
            acquired_at=datetime.date(2026, 1, 10),
            condition="BAIK",
            location="RAK-A1",
        )
        loan = request_loan(self.siswa2, [other_copy.pk])
        approve_loan(loan.pk, self.pus)
        own_copy = BookCopy.objects.get(copy_code="AI-001")
        request_loan(self.siswa, [own_copy.pk])
        context = retrieve_context(self.siswa, "Python")
        mine = context["peminjaman_saya"]
        self.assertEqual(len(mine), 1)
        self.assertTrue(
            all("Rahasia Khusus" not in str(entry) for entry in mine)
        )
        titles = " ".join(
            item["judul"] for item in context["koleksi_fisik"]
        )
        self.assertIn("Belajar Python", titles)

    def test_smalltalk_tanpa_provider(self):
        fake = FakeProvider("tidak boleh dipakai")
        with mock.patch("apps.ai.services.get_provider", return_value=fake):
            result = ask(self.siswa, "Halo, selamat pagi!")
        self.assertEqual(result["source"], "smalltalk")
        self.assertEqual(fake.calls, 0)

    def test_out_of_scope_tanpa_provider(self):
        fake = FakeProvider("tidak boleh dipakai")
        with mock.patch("apps.ai.services.get_provider", return_value=fake):
            result = ask(self.siswa, "Siapa presiden negara X?")
        self.assertEqual(result["answer"], OUT_OF_SCOPE_FALLBACK)
        self.assertFalse(result["provider_used"])
        self.assertEqual(fake.calls, 0)

    def test_out_of_scope_tugas_teknis_ditolak_default(self):
        fake = FakeProvider("tidak boleh dipakai")
        with mock.patch("apps.ai.services.get_provider", return_value=fake):
            for question in (
                "buatkan koding python",
                "buatkan website landing page",
                "tolong buatkan program kasir",
                "tuliskan cerita horor untuk saya",
                "bagaimana cara coding game?",
                "buatkan coding python untuk menulis nama anda",
            ):
                with self.subTest(question=question):
                    result = ask(self.siswa, question)
                    self.assertEqual(result["answer"], OUT_OF_SCOPE_FALLBACK)
                    self.assertFalse(result["provider_used"])
        self.assertEqual(fake.calls, 0)

    def test_kemampuan_lain_tetap_utuh(self):
        fake = FakeProvider("tidak boleh dipakai")
        with mock.patch("apps.ai.services.get_provider", return_value=fake):
            self.assertEqual(
                ask(self.siswa, "Halo!")["source"], "smalltalk"
            )
            self.assertIn(
                "AI Assistant", ask(self.siswa, "Siapa anda?")["answer"]
            )
            result = ask(self.siswa, "Apakah ada buku Python?")
            self.assertNotEqual(result["answer"], OUT_OF_SCOPE_FALLBACK)
            self.assertTrue(result["provider_used"])
        # Hanya pertanyaan perpustakaan yang memakai provider.
        self.assertEqual(fake.calls, 1)

    def test_identitas_memperkenalkan_diri(self):
        fake = FakeProvider("tidak boleh dipakai")
        with mock.patch("apps.ai.services.get_provider", return_value=fake):
            for question in (
                "Siapa anda?",
                "Kamu siapa?",
                "Siapa nama kamu?",
                "Perkenalkan dirimu",
                "Kamu itu apa?",
            ):
                with self.subTest(question=question):
                    result = ask(self.siswa, question)
        self.assertEqual(result["source"], "smalltalk")
        self.assertIn("AI Assistant", result["answer"])
        self.assertIn("Digital Library", result["answer"])
        self.assertEqual(fake.calls, 0)

    def test_typo_tetap_dipahami(self):
        with mock.patch("apps.ai.services.get_provider", return_value=None):
            for question in (
                "Belajar Pithon",
                "buk python",
                "Belajar Pythoon?",
                "Pithon",
            ):
                with self.subTest(question=question):
                    result = ask(self.siswa, question)
                    self.assertIn("Python", result["answer"])
                    self.assertFalse(result["provider_used"])

    def test_variasi_ejaan_ebook(self):
        with mock.patch("apps.ai.services.get_provider", return_value=None):
            for question in (
                "apakah ada E-book Python?",
                "cari ebook python",
                "E-Book apa saja?",
            ):
                with self.subTest(question=question):
                    result = ask(self.siswa, question)
                    self.assertNotEqual(
                        result["answer"],
                        "Maaf, informasi tersebut belum tersedia "
                        "dalam sistem perpustakaan.",
                    )

    def test_fakta_jumlah_dari_sistem(self):
        with mock.patch("apps.ai.services.get_provider", return_value=None):
            siswa = ask(self.siswa, "berapa jumlah siswa?")
            self.assertRegex(siswa["answer"], r"\d+")
            self.assertIn("siswa", siswa["answer"].lower())
            buku = ask(self.siswa, "berapa banyak buku?")
            self.assertIn("judul", buku["answer"].lower())
            fitur = ask(self.siswa, "fitur apa saja yang tersedia?")
            self.assertIn("katalog", fitur["answer"])
