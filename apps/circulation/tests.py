from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import Book, BookCopy, Category
from apps.circulation.models import Loan, LoanItem

User = get_user_model()


class CirculationModelTests(TestCase):
    def setUp(self):
        self.pustakawan = User.objects.create_user(
            username="pus1",
            password="secret123",
            role=User.Role.PUSTAKAWAN,
            full_name="Pustakawan Satu",
        )
        self.siswa = User.objects.create_user(
            username="siswa1",
            password="secret123",
            role=User.Role.SISWA,
            full_name="Siswa Satu",
            nis="10001",
        )
        category = Category.objects.create(name="Fiksi")
        book = Book.objects.create(
            category=category, isbn="ISBN-CIRC-1", title="Sirkulasi"
        )
        self.copy = BookCopy.objects.create(
            book=book,
            copy_code="CIRC-001",
            status="TERSEDIA",
            acquired_at=timezone.localdate(),
            condition="BAIK",
            location="RAK-A1",
        )

    def test_11_processed_by_adalah_fk_user(self):
        field = LoanItem._meta.get_field("processed_by")
        self.assertTrue(field.is_relation)
        self.assertEqual(field.related_model, User)
        loan = Loan.objects.create(
            user=self.siswa,
            status="DIMINTA",
            requested_at=timezone.now(),
        )
        item = LoanItem.objects.create(
            loan=loan,
            book_copy=self.copy,
            return_status="BELUM_KEMBALI",
            processed_by=self.pustakawan,
            fine_rate_per_day=1000,
            fine_status="BELUM_BAYAR",
        )
        item.refresh_from_db()
        self.assertEqual(item.processed_by, self.pustakawan)

    def test_17_loan_pickup_exp_at_adalah_datetimefield(self):
        field = Loan._meta.get_field("pickup_exp_at")
        self.assertEqual(field.get_internal_type(), "DateTimeField")
        exp = timezone.now() + timezone.timedelta(minutes=30)
        loan = Loan.objects.create(
            user=self.siswa,
            status="DISETUJUI",
            requested_at=timezone.now(),
            pickup_exp_at=exp,
        )
        loan.refresh_from_db()
        self.assertEqual(loan.pickup_exp_at, exp)

    def test_loanitem_loan_on_delete_cascade(self):
        self.assertEqual(
            LoanItem._meta.get_field("loan").remote_field.on_delete.__name__,
            "CASCADE",
        )
