"""Presentasi halaman rekomendasi: format narasi, sampul, dan render.

Fokus pengujian lapisan tampilan: penanda Markdown mentah tidak boleh
tampil, HTML dari respons AI harus tetap ter-escape, dan isi/urutan/alasan
rekomendasi tidak boleh berubah dari yang dibangun layanan AI.
"""

import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.ai.presentation import attach_covers, format_narration
from apps.ai.recommendations import recommend_for_user
from apps.catalog.models import (
    Author, Book, BookAuthor, BookCopy, Category, Ebook, EbookAuthor,
)
from apps.circulation.services import (
    activate_handover, approve_loan, request_loan,
)
from apps.core.models import LibrarySetting

User = get_user_model()
REC_URL = reverse("ai:recommendations")


class FormatNarrationTests(TestCase):
    def _text(self, blocks):
        return "".join(run["text"] for block in blocks for run in block["runs"])

    def test_teks_kosong_tidak_menghasilkan_blok(self):
        for value in (None, "", "   \n\n \t "):
            self.assertEqual(format_narration(value), [])

    def test_teks_biasa_menjadi_satu_paragraf(self):
        blocks = format_narration("Halo, ini rekomendasi untukmu.")
        self.assertEqual([b["type"] for b in blocks], ["paragraph"])
        self.assertEqual(self._text(blocks), "Halo, ini rekomendasi untukmu.")

    def test_butir_dengan_tebal_tanpa_penanda_mentah(self):
        raw = (
            "Berikut rekomendasi untukmu:\n\n"
            "- **Laskar Pelangi** (Fiksi): karena kamu suka fiksi\n"
            "- **Bumi Manusia**: penulis favoritmu\n\n"
            "Semoga bermanfaat!"
        )
        blocks = format_narration(raw)
        self.assertEqual(
            [b["type"] for b in blocks], ["paragraph", "list", "paragraph"]
        )
        items = blocks[1]["items"]
        self.assertEqual(len(items), 2)
        self.assertTrue(items[0][0]["strong"])
        self.assertEqual(items[0][0]["text"], "Laskar Pelangi")
        for text in self._all_text(blocks):
            self.assertNotIn("**", text)

    def _all_text(self, blocks):
        texts = []
        for block in blocks:
            for run in block.get("runs", []):
                texts.append(run["text"])
            for item in block.get("items", []):
                texts.extend(run["text"] for run in item)
        return texts

    def test_daftar_bernomor_dan_butir_bulat(self):
        blocks = format_narration("1. Satu\n2. Dua\n\u2022 Tiga")
        self.assertEqual([b["type"] for b in blocks], ["list"])
        self.assertEqual(
            [item[0]["text"] for item in blocks[0]["items"]],
            ["Satu", "Dua", "Tiga"],
        )

    def test_tautan_disisaikan_label_gambar_dibuang(self):
        blocks = format_narration("[Laskar](http://contoh/1) ![sampul](http://contoh/c.png)")
        text = self._text(blocks)
        self.assertEqual(text.strip(), "Laskar")
        self.assertNotIn("http", text)

    def test_garis_bawah_dalam_kata_tidak_diubah(self):
        blocks = format_narration("Judul bab_dua tetap utuh.")
        self.assertEqual(self._text(blocks), "Judul bab_dua tetap utuh.")

    def test_fallback_saat_pemecahan_gagal(self):
        with mock.patch(
            "apps.ai.presentation._parse_blocks",
            side_effect=RuntimeError("boom"),
        ):
            blocks = format_narration("Teks penting yang harus tetap tampil.")
        self.assertEqual([b["type"] for b in blocks], ["paragraph"])
        self.assertEqual(
            self._text(blocks), "Teks penting yang harus tetap tampil."
        )


class AttachCoversTests(TestCase):
    def setUp(self):
        self.fiksi = Category.objects.create(name="Fiksi")

    def test_sampul_tidak_tertukar_antar_kind(self):
        book = Book.objects.create(
            pk=7, category=self.fiksi, isbn="ISBN-7", title="Buku Tujuh",
            cover_url="https://contoh/buku-7.jpg",
        )
        BookCopy.objects.create(
            book=book, copy_code="RA-7", status="TERSEDIA",
            acquired_at=datetime.date(2026, 1, 10), condition="BAIK",
            location="RAK-A1",
        )
        Ebook.objects.create(
            pk=7, category=self.fiksi, eisbn="E-7", title="Ebook Tujuh",
            file_path="e7.pdf", file_name="e7.pdf", file_type="pdf",
            file_size=10, version="1.0",
            cover_url="https://contoh/ebook-7.jpg",
        )
        items = [
            {"kind": "book", "id": 7, "title": "Buku Tujuh", "reason": "r"},
            {"kind": "ebook", "id": 7, "title": "Ebook Tujuh", "reason": "r"},
        ]
        attach_covers(items)
        self.assertEqual(items[0]["cover_url"], "https://contoh/buku-7.jpg")
        self.assertEqual(items[1]["cover_url"], "https://contoh/ebook-7.jpg")
        # Isi rekomendasi tidak disentuh.
        self.assertEqual(items[0]["title"], "Buku Tujuh")
        self.assertEqual(items[1]["reason"], "r")

    def test_tanpa_sampul_bernilai_none(self):
        items = [{"kind": "book", "id": 999, "title": "Tak ada"}]
        attach_covers(items)
        self.assertIsNone(items[0]["cover_url"])


@override_settings(AI_PROVIDER_BASE_URL="", AI_API_KEY="", AI_MODEL="")
class RecommendationPageTests(TestCase):
    def setUp(self):
        self.pus = self._make_user("pus1", User.Role.PUSTAKAWAN)
        self.siswa = self._make_user("siswa1", User.Role.SISWA, nis="10001")
        LibrarySetting.objects.create(
            physical_loan_duration=7,
            ebook_access_duration=14,
            pickup_qr_validity=30,
            fine_rate_per_day=Decimal("1000"),
            effective_from=timezone.now(),
        )
        self.fiksi = Category.objects.create(name="Fiksi")
        self.andrea = Author.objects.create(name="Andrea Hirata")
        self.book_a = self._make_book("Laskar A", "RA-1")
        self.book_b = self._make_book(
            "Laskar B", "RA-2", cover_url="https://contoh/laskar-b.jpg"
        )
        self.ebook_d = Ebook.objects.create(
            category=self.fiksi, eisbn="E-R1", title="Cerita D",
            file_path="d.pdf", file_name="d.pdf", file_type="pdf",
            file_size=64, version="1.0",
        )
        EbookAuthor.objects.create(ebook=self.ebook_d, author=self.andrea)

    def _make_user(self, username, role, password="secret123", **extra):
        defaults = {"full_name": f"{username} Full"}
        defaults.update(extra)
        user = User(username=username, role=role, **defaults)
        user.set_password(password)
        user.full_clean()
        user.save()
        return user

    def _make_book(self, title, copy_code, cover_url=None):
        book = Book.objects.create(
            category=self.fiksi, isbn=f"ISBN-{copy_code}", title=title,
            cover_url=cover_url,
        )
        BookAuthor.objects.create(book=book, author=self.andrea)
        BookCopy.objects.create(
            book=book, copy_code=copy_code, status="TERSEDIA",
            acquired_at=datetime.date(2026, 1, 10), condition="BAIK",
            location="RAK-A1",
        )
        return book

    def _borrow(self, user, copy_code):
        copy = BookCopy.objects.get(copy_code=copy_code)
        loan = request_loan(user, [copy.pk])
        approve_loan(loan.pk, self.pus)
        loan.refresh_from_db()
        activate_handover(loan.pickup_token, self.pus)

    def _get(self, narration):
        with mock.patch(
            "apps.ai.views.get_provider", return_value=object()
        ):
            with mock.patch("apps.ai.views.narrate", return_value=narration):
                self.client.force_login(self.siswa)
                return self.client.get(REC_URL)

    def test_markdown_mentah_tidak_tampil(self):
        self._borrow(self.siswa, "RA-1")
        response = self._get(
            "Berikut rekomendasi untukmu:\n\n"
            "- **Laskar B** (Fiksi): karena kamu suka fiksi"
        )
        body = response.content.decode()
        self.assertNotIn("**", body)
        self.assertContains(response, "Laskar B")
        self.assertContains(response, "Ringkasan rekomendasi")
        self.assertContains(response, "Pilihan untukmu")

    def test_html_dari_ai_tetap_ter_escape(self):
        response = self._get("<script>alert(1)</script> halo")
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(response, "&lt;script&gt;")

    def test_tanpa_narasi_tetap_tampil_daftar(self):
        response = self._get(None)
        self.assertNotContains(response, "Ringkasan rekomendasi")
        self.assertContains(response, "Koleksi yang tersedia")
        self.assertContains(response, "Laskar B")

    def test_urutan_dan_alasan_sama_dengan_layanan(self):
        self._borrow(self.siswa, "RA-1")
        result = recommend_for_user(self.siswa)
        self.assertTrue(result["items"])
        self.client.force_login(self.siswa)
        response = self.client.get(REC_URL)
        body = response.content.decode()
        positions = [body.find(item["title"]) for item in result["items"]]
        self.assertNotIn(-1, positions)
        self.assertEqual(positions, sorted(positions))
        for item in result["items"]:
            self.assertContains(response, item["reason"])

    def test_sampul_item_muncul_di_halaman(self):
        self.client.force_login(self.siswa)
        response = self.client.get(REC_URL)
        self.assertContains(response, "https://contoh/laskar-b.jpg")

    def test_tautan_katalog_pertahankan_pola_lama(self):
        self.client.force_login(self.siswa)
        response = self.client.get(REC_URL)
        books_url = (
            "%s?q=Laskar%%20B" % reverse("catalog:catalog")
        )
        self.assertContains(response, books_url)
        self.assertContains(
            response,
            "%s?q=Cerita%%20D&type=ebook" % reverse("catalog:catalog"),
        )
