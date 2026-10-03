import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Book, BookCopy, Category
from apps.circulation.services import (
    activate_handover,
    approve_loan,
    request_loan,
)
from apps.core.models import LibrarySetting
from apps.notifications.models import Notification
from apps.notifications.services import (
    mark_all_read,
    reference_url,
    sync_due_overdue,
    unread_count,
)

User = get_user_model()


class NotificationTests(TestCase):
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
        category = Category.objects.create(name="Fiksi")
        book = Book.objects.create(
            category=category, isbn="ISBN-NT-1", title="Notif A"
        )
        self.copy1 = BookCopy.objects.create(
            book=book,
            copy_code="NT-001",
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

    def _approved_active_loan(self):
        loan = request_loan(self.siswa, [self.copy1.pk])
        approve_loan(loan.pk, self.pus)
        loan.refresh_from_db()
        activate_handover(loan.pickup_token, self.pus)
        return loan

    # --- pembuatan & penerima ---

    def test_notification_created_correct_recipient(self):
        loan = request_loan(self.siswa, [self.copy1.pk])
        approve_loan(loan.pk, self.pus)
        notif = Notification.objects.get(
            user=self.siswa,
            type="LOAN_APPROVED",
            reference_type="loan",
            reference_id=loan.pk,
        )
        self.assertFalse(notif.is_read)
        self.assertIsNone(notif.read_at)
        self.assertEqual(
            Notification.objects.filter(user=self.siswa2).count(), 0
        )
        self.client.force_login(self.siswa)
        self.assertContains(
            self.client.get(reverse("notifications:list")),
            "Pengajuan disetujui",
        )
        self.client.force_login(self.siswa2)
        self.assertNotContains(
            self.client.get(reverse("notifications:list")),
            "Pengajuan disetujui",
        )

    # --- baca ---

    def test_read_menandai_dan_ikut_referensi(self):
        loan = request_loan(self.siswa, [self.copy1.pk])
        approve_loan(loan.pk, self.pus)
        notif = Notification.objects.get(user=self.siswa)
        self.client.force_login(self.siswa)
        response = self.client.post(
            reverse("notifications:read", args=[notif.pk])
        )
        self.assertRedirects(
            response,
            reverse("circulation:loan_detail", args=[loan.pk]),
        )
        notif.refresh_from_db()
        self.assertTrue(notif.is_read)
        self.assertIsNotNone(notif.read_at)
        self.assertEqual(unread_count(self.siswa), 0)

    def test_read_all(self):
        loan = request_loan(self.siswa, [self.copy1.pk])
        approve_loan(loan.pk, self.pus)
        self.assertEqual(unread_count(self.siswa), 1)
        self.client.force_login(self.siswa)
        self.client.post(reverse("notifications:read_all"))
        self.assertEqual(mark_all_read(self.siswa), 0)
        self.assertEqual(unread_count(self.siswa), 0)

    def test_unauthorized_notification_access(self):
        loan = request_loan(self.siswa, [self.copy1.pk])
        approve_loan(loan.pk, self.pus)
        notif = Notification.objects.get(user=self.siswa)
        self.client.force_login(self.siswa2)
        response = self.client.post(
            reverse("notifications:read", args=[notif.pk])
        )
        self.assertRedirects(response, reverse("notifications:list"))
        notif.refresh_from_db()
        self.assertFalse(notif.is_read)
        self.assertIsNone(notif.read_at)
        self.assertIsNone(reference_url(notif, self.siswa2))

    # --- jatuh tempo & terlambat ---

    def test_due_soon_terbuat_idempoten(self):
        loan = self._approved_active_loan()
        Loan = loan.__class__
        Loan.objects.filter(pk=loan.pk).update(
            due_at=timezone.now() + datetime.timedelta(days=1)
        )
        self.client.force_login(self.siswa)
        self.client.get(reverse("circulation:loan_list"))
        notif = Notification.objects.get(
            user=self.siswa, type="LOAN_DUE_SOON"
        )
        self.assertIn("Jatuh tempo", notif.title)
        self.client.get(reverse("circulation:loan_list"))
        self.assertEqual(
            Notification.objects.filter(
                user=self.siswa, type="LOAN_DUE_SOON"
            ).count(),
            1,
        )

    def test_overdue_terbuat_sekali(self):
        loan = self._approved_active_loan()
        Loan = loan.__class__
        Loan.objects.filter(pk=loan.pk).update(
            due_at=timezone.now() - datetime.timedelta(days=3)
        )
        due, overdue = sync_due_overdue()
        self.assertEqual((due, overdue), (0, 1))
        notif = Notification.objects.get(
            user=self.siswa, type="LOAN_OVERDUE"
        )
        self.assertIn("3 hari", notif.message)
        self.assertEqual(sync_due_overdue(), (0, 0))

    def test_tidak_ada_notif_bila_masih_lama(self):
        self._approved_active_loan()  # jatuh tempo +7 hari
        self.assertEqual(sync_due_overdue(), (0, 0))

    # --- referensi lain ---

    def test_reference_ebook_dan_material(self):
        from apps.catalog.models import Author, Ebook, EbookAuthor
        from apps.circulation.services import (
            approve_ebook_loan,
            request_ebook_loan,
        )

        author = Author.objects.create(name="Penulis N")
        category = Category.objects.first()
        ebook = Ebook.objects.create(
            category=category,
            eisbn="E-N1",
            title="Ebook N",
            file_path="n.pdf",
            file_name="n.pdf",
            file_type="pdf",
            file_size=64,
            version="1.0",
        )
        EbookAuthor.objects.create(ebook=ebook, author=author)
        eloan = request_ebook_loan(self.siswa, ebook.pk)
        approve_ebook_loan(eloan.pk, self.pus)
        notif = Notification.objects.get(
            user=self.siswa, type="EBOOK_APPROVED"
        )
        self.assertEqual(
            reference_url(notif, self.siswa),
            reverse("circulation:my_ebooks"),
        )
