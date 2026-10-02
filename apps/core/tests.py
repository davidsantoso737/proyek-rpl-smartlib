from django.test import TestCase
from django.utils import timezone

from apps.core.models import LibrarySetting


class LibrarySettingModelTests(TestCase):
    def _make_setting(self):
        return LibrarySetting.objects.create(
            physical_loan_duration=7,
            ebook_access_duration=14,
            pickup_qr_validity=30,
            fine_rate_per_day=1000,
            effective_from=timezone.now(),
        )

    def test_14_physical_loan_duration_adalah_jumlah_hari(self):
        field = LibrarySetting._meta.get_field("physical_loan_duration")
        self.assertEqual(field.get_internal_type(), "PositiveIntegerField")
        setting = self._make_setting()
        self.assertEqual(setting.physical_loan_duration, 7)

    def test_15_ebook_access_duration_adalah_jumlah_hari(self):
        field = LibrarySetting._meta.get_field("ebook_access_duration")
        self.assertEqual(field.get_internal_type(), "PositiveIntegerField")
        setting = self._make_setting()
        self.assertEqual(setting.ebook_access_duration, 14)

    def test_16_pickup_qr_validity_adalah_jumlah_menit(self):
        field = LibrarySetting._meta.get_field("pickup_qr_validity")
        self.assertEqual(field.get_internal_type(), "PositiveIntegerField")
        # 30 berarti QR berlaku 30 MENIT; kedaluwarsa aktual per transaksi
        # disimpan di Loan.pickup_exp_at (DateTimeField).
        setting = self._make_setting()
        exp = setting.effective_from + timezone.timedelta(
            minutes=setting.pickup_qr_validity
        )
        self.assertEqual(
            (exp - setting.effective_from).total_seconds(), 30 * 60
        )
