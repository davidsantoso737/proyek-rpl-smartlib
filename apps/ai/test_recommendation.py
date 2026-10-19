import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.ai.recommendations import recommend_for_user
from apps.catalog.models import (
    Author, Book, BookAuthor, BookCopy, Category, Ebook, EbookAuthor,
)
from apps.circulation.models import EbookLoan, Loan
from apps.circulation.services import (
    activate_handover,
    approve_loan,
    request_loan,
)
from apps.core.models import LibrarySetting

User = get_user_model()
REC_URL = reverse("ai:recommendations")


@override_settings(AI_PROVIDER_BASE_URL="", AI_API_KEY="", AI_MODEL="")
class RecommendationTests(TestCase):
    def setUp(self):
        self.siswa = self._make_user("siswa1", User.Role.SISWA, nis="10001")
        self.guru = self._make_user("guru1", User.Role.GURU, nip="20001")
        self.pus = self._make_user("pus1", User.Role.PUSTAKAWAN)
        LibrarySetting.objects.create(
            physical_loan_duration=7,
            ebook_access_duration=14,
            pickup_qr_validity=30,
            fine_rate_per_day=Decimal("1000"),
            effective_from=timezone.now(),
        )
        self.fiksi = Category.objects.create(name="Fiksi")
        self.sains = Category.objects.create(name="Sains")
        self.andrea = Author.objects.create(name="Andrea Hirata")
        self.book_a = self._make_book("Laskar A", self.fiksi, self.andrea, "RA-1")
        self.book_b = self._make_book("Laskar B", self.fiksi, self.andrea, "RA-2")
        self.book_c = self._make_book("Fisika C", self.sains, None, "RA-3")
        self.ebook_d = Ebook.objects.create(
            category=self.fiksi,
            eisbn="E-R1",
            title="Cerita D",
            file_path="d.pdf",
            file_name="d.pdf",
            file_type="pdf",
            file_size=64,
            version="1.0",
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

    def _make_book(self, title, category, author, copy_code):
        book = Book.objects.create(
            category=category, isbn=f"ISBN-{copy_code}", title=title
        )
        if author:
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

    def _borrow(self, user, copy_code):
        copy = BookCopy.objects.get(copy_code=copy_code)
        loan = request_loan(user, [copy.pk])
        approve_loan(loan.pk, self.pus)
        loan.refresh_from_db()
        activate_handover(loan.pickup_token, self.pus)

    def test_authenticated_user(self):
        self.assertEqual(self.client.get(REC_URL).status_code, 302)
        for user in (self.siswa, self.guru):
            self.client.force_login(user)
            self.assertEqual(self.client.get(REC_URL).status_code, 200)
        # Pustakawan tidak memiliki akses AI.
        self.client.force_login(self.pus)
        self.assertEqual(self.client.get(REC_URL).status_code, 403)

    def test_recommendation_generated_personal(self):
        self._borrow(self.siswa, "RA-1")
        result = recommend_for_user(self.siswa)
        self.assertEqual(result["basis"], "personal")
        titles = [item["title"] for item in result["items"]]
        self.assertIn("Laskar B", titles)
        self.assertNotIn("Laskar A", titles)  # sedang dipinjam
        reason = next(
            item["reason"] for item in result["items"] if item["title"] == "Laskar B"
        )
        self.assertIn("Fiksi", reason)
        self.client.force_login(self.siswa)
        response = self.client.get(REC_URL)
        self.assertContains(response, "Laskar B")
        self.assertContains(response, "Berdasarkan")

    def test_insufficient_history_fallback_umum(self):
        result = recommend_for_user(self.guru)
        self.assertEqual(result["basis"], "general")
        self.assertTrue(result["items"])
        self.client.force_login(self.guru)
        self.assertContains(
            self.client.get(REC_URL), "belum cukup untuk personalisasi"
        )

    def test_no_available_collection(self):
        Book.objects.update(is_active=False)
        Ebook.objects.update(is_active=False)
        result = recommend_for_user(self.siswa)
        self.assertEqual(result["items"], [])
        self.client.force_login(self.siswa)
        self.assertContains(
            self.client.get(REC_URL), "Belum ada koleksi"
        )

    def test_information_only_tanpa_mutasi(self):
        self._borrow(self.siswa, "RA-1")
        before = (Loan.objects.count(), EbookLoan.objects.count())
        self.client.force_login(self.siswa)
        self.client.get(REC_URL)
        self.assertEqual(
            (Loan.objects.count(), EbookLoan.objects.count()), before
        )

    def test_ai_unavailable_tetap_deterministik(self):
        self._borrow(self.siswa, "RA-1")
        with mock.patch(
            "apps.ai.views.get_provider", return_value=object()
        ):
            with mock.patch(
                "apps.ai.views.narrate",
                side_effect=Exception("provider down"),
            ):
                self.client.force_login(self.siswa)
                response = self.client.get(REC_URL)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Laskar B")
