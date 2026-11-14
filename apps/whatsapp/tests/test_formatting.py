import datetime
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from apps.whatsapp.formatting import (
    FOOTER,
    format_date,
    format_ebook_approved,
    format_ebook_rejected,
    format_loan_active,
    format_loan_approved,
    format_loan_rejected,
    format_loan_returned,
    format_reminder,
    format_rupiah,
)


def _user(name="Budi Santoso"):
    return SimpleNamespace(full_name=name)


def _item(title="Buku Contoh", code="C-01"):
    return SimpleNamespace(
        book_copy=SimpleNamespace(
            copy_code=code,
            book=SimpleNamespace(title=title),
        )
    )


def _loan(**overrides):
    data = {
        "pk": 7,
        "user": _user(),
        "due_at": None,
        "pickup_exp_at": None,
        "ebook": SimpleNamespace(title="E-book Contoh"),
        "expires_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class FormattingTests(SimpleTestCase):
    def test_format_tanggal_indonesia(self):
        value = timezone.make_aware(
            datetime.datetime(2026, 10, 24, 9, 5)
        )
        with timezone.override("Asia/Jakarta"):
            self.assertEqual(format_date(value), "24 Oktober 2026")
            self.assertEqual(
                format_date(value, include_time=True),
                "24 Oktober 2026 pukul 09:05",
            )
        self.assertEqual(format_date(None), "-")

    def test_format_rupiah(self):
        self.assertEqual(format_rupiah(0), "Rp0")
        self.assertEqual(format_rupiah(15000), "Rp15.000")
        self.assertEqual(format_rupiah("2500000"), "Rp2.500.000")

    def test_pengajuan_disetujui(self):
        loan = _loan(
            pickup_exp_at=timezone.make_aware(
                datetime.datetime(2026, 10, 26, 15, 30)
            )
        )
        result = format_loan_approved(loan, [_item(), _item("Buku B", "C-02")])

        self.assertIn("Pengajuan disetujui", result.title)
        self.assertIn("Halo *Budi Santoso*", result.message)
        self.assertIn("Nomor pengajuan: #7", result.message)
        self.assertIn("Jumlah buku: 2", result.message)
        self.assertIn("Buku Contoh (C-01)", result.message)
        self.assertIn("QR Pickup", result.message)
        self.assertIn("26 Oktober 2026 pukul 15:30", result.message)
        self.assertIn(FOOTER, result.message)

    def test_pengajuan_disetujui_tanpa_item(self):
        result = format_loan_approved(_loan())
        self.assertNotIn("Jumlah buku", result.message)
        self.assertNotIn("QR berlaku", result.message)

    def test_pengajuan_ditolak_memotong_alasan(self):
        result = format_loan_rejected(_loan(), "x" * 600)
        self.assertIn("Pengajuan ditolak", result.title)
        self.assertIn("Status: *Ditolak*", result.message)
        self.assertIn("...", result.message)
        self.assertLessEqual(len(result.message), 1200)

    def test_pengajuan_ditolak_tanpa_alasan(self):
        result = format_loan_rejected(_loan(), "")
        self.assertIn("Tidak disebutkan.", result.message)

    def test_peminjaman_aktif(self):
        loan = _loan(
            due_at=timezone.make_aware(
                datetime.datetime(2026, 11, 1, 16, 0)
            )
        )
        result = format_loan_active(loan, [_item()])
        self.assertIn("Peminjaman aktif", result.title)
        self.assertIn("Jatuh tempo: *1 November 2026 pukul 16:00*", result.message)
        self.assertIn("Status: *Aktif*", result.message)

    def test_pengembalian_normal(self):
        item = _item()
        item.returned_at = timezone.make_aware(
            datetime.datetime(2026, 11, 3, 10, 15)
        )
        item.late_day = 2
        item.fine_amount = 2000
        item.fine_status = "BELUM_BAYAR"

        result = format_loan_returned(_loan(), item)

        self.assertIn("Buku dikembalikan", result.title)
        self.assertIn("Status: *Dikembalikan*", result.message)
        self.assertIn("Keterlambatan: 2 hari", result.message)
        self.assertIn("Rp2.000", result.message)
        self.assertIn("Belum dibayar", result.message)
        self.assertIn("Lunasi denda", result.message)

    def test_pengembalian_tanpa_denda(self):
        item = _item()
        item.returned_at = None
        item.late_day = 0
        item.fine_amount = 0
        item.fine_status = ""

        result = format_loan_returned(_loan(), item)

        self.assertNotIn("Keterlambatan", result.message)
        self.assertNotIn("Denda", result.message)
        self.assertNotIn("Lunasi denda", result.message)

    def test_buku_hilang(self):
        item = _item()
        item.replacement_status = "SUDAH_DIGANTI"

        result = format_loan_returned(_loan(), item, lost=True)

        self.assertIn("Buku hilang tercatat", result.title)
        self.assertIn("Status: *Hilang*", result.message)
        self.assertIn("Penggantian: *SUDAH_DIGANTI*", result.message)
        self.assertIn("proses penggantian", result.message)

    def test_buku_hilang_belum_diganti(self):
        item = _item()
        item.replacement_status = "BELUM_DIGANTI"

        result = format_loan_returned(_loan(), item, lost=True)

        self.assertIn("Penggantian: *Belum diganti*", result.message)

    def test_ebook_disetujui(self):
        loan = _loan(
            expires_at=timezone.make_aware(
                datetime.datetime(2026, 12, 31, 23, 59)
            )
        )
        result = format_ebook_approved(loan)
        self.assertIn("E-book siap dibaca", result.title)
        self.assertIn("E-book Contoh", result.message)
        self.assertIn("31 Desember 2026 pukul 23:59", result.message)

    def test_ebook_ditolak(self):
        result = format_ebook_rejected(_loan(), "Lisensi habis.")
        self.assertIn("E-book ditolak", result.title)
        self.assertIn("Lisensi habis.", result.message)

    def test_pengingat_dibatasi_sepuluh_baris(self):
        entries = [
            (_item(f"Buku {index}", f"C-{index}"), timezone.now(), 3)
            for index in range(12)
        ]
        result = format_reminder("H3", _user(), entries)

        self.assertIn("Jatuh tempo H-3", result.title)
        self.assertIn("dalam 3 hari", result.message)
        self.assertIn("*Buku 0* (C-0)", result.message)
        self.assertIn("*Buku 9* (C-9)", result.message)
        self.assertNotIn("*Buku 10* (C-10)", result.message)
        self.assertIn("dan 2 buku lainnya", result.message)

    def test_pengingat_terlambat(self):
        entries = [(_item(), timezone.now(), -4)]
        result = format_reminder("OVERDUE", _user(), entries)
        self.assertIn("Peminjaman terlambat", result.title)
        self.assertIn("terlambat 4 hari", result.message)
        self.assertIn("Kembalikan buku segera", result.message)

    def test_pengingat_besok(self):
        result = format_reminder(
            "H1", _user(), [(_item(), timezone.now(), 1)]
        )
        self.assertIn("Jatuh tempo besok", result.title)
        self.assertIn("jatuh tempo besok", result.message)

    def test_nama_dan_judul_panjang_dipotong(self):
        loan = _loan(user=_user("N" * 100))
        item = _item("T" * 300)
        result = format_loan_approved(loan, [item])
        self.assertIn("...", result.message)
        self.assertLessEqual(len(result.message), 2000)
