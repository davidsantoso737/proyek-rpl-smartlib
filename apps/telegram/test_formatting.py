import datetime
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from apps.telegram.formatting import (
    format_date,
    format_ebook_approved,
    format_loan_approved,
    format_loan_rejected,
    format_loan_returned,
    format_reminder,
    is_formatted_title,
)


def make_item(title, code):
    return SimpleNamespace(
        book_copy=SimpleNamespace(
            book=SimpleNamespace(title=title),
            copy_code=code,
        )
    )


def make_loan(**overrides):
    data = {
        "pk": 12,
        "user": SimpleNamespace(full_name="Budi Santoso"),
        "due_at": None,
        "pickup_exp_at": None,
        "ebook": SimpleNamespace(title="E-book Contoh"),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class TelegramFormattingTests(SimpleTestCase):
    def test_tanggal_indonesia_memakai_zona_waktu_lokal(self):
        value = datetime.datetime(
            2026, 10, 24, 3, 0, tzinfo=datetime.timezone.utc
        )

        with timezone.override("Asia/Jakarta"):
            result = format_date(value, include_time=True)

        self.assertEqual(result, "24 Oktober 2026 pukul 10:00")

    def test_judul_terformat_dikenali(self):
        message = format_loan_approved(make_loan(), [make_item("Buku", "A-1")])

        self.assertTrue(is_formatted_title(message.title))
        self.assertFalse(is_formatted_title("Pengajuan disetujui"))

    def test_nilai_dinamis_di_escape(self):
        message = format_loan_approved(
            make_loan(user=SimpleNamespace(full_name="Budi & <Admin>")),
            [make_item("<b>Hack</b> & \"Quotes\"", "A<1>")],
        )

        self.assertIn("Budi &amp; &lt;Admin&gt;", message.message)
        self.assertIn("&lt;b&gt;Hack&lt;/b&gt; &amp; &quot;Quotes&quot;", message.message)
        self.assertIn("A&lt;1&gt;", message.message)
        self.assertNotIn("<Admin>", message.message)

    def test_pengajuan_disetujui_membatasi_tiga_judul(self):
        items = [
            make_item(f"Buku {number}", f"COPY-{number}")
            for number in range(1, 5)
        ]

        message = format_loan_approved(make_loan(), items)

        self.assertEqual(message.message.count("<code>COPY-"), 3)
        self.assertIn("dan 1 buku lainnya", message.message)

    def test_alasan_ditolak_di_escape_dan_dipotong(self):
        message = format_loan_rejected(
            make_loan(),
            "<script>alasan</script>",
        )
        self.assertIn("&lt;script&gt;alasan&lt;/script&gt;", message.message)
        self.assertNotIn("<script>", message.message)

        long_message = format_loan_rejected(make_loan(), "x" * 600)
        self.assertIn(f"{'x' * 497}...", long_message.message)

    def test_pengembalian_tanpa_denda_tidak_memuat_kata_denda(self):
        item = make_item("Buku Kembali", "RET-1")
        item.returned_at = timezone.now()
        item.late_day = 0
        item.fine_amount = 0
        item.fine_status = "LUNAS"
        item.replacement_status = None

        message = format_loan_returned(make_loan(), item)

        self.assertIn("Dikembalikan", message.message)
        self.assertNotIn("denda", message.message.casefold())

    def test_pengembalian_dengan_denda_menampilkan_nominal(self):
        item = make_item("Buku Telat", "RET-2")
        item.returned_at = timezone.now()
        item.late_day = 2
        item.fine_amount = 2000
        item.fine_status = "BELUM_BAYAR"
        item.replacement_status = None

        message = format_loan_returned(make_loan(), item)

        self.assertIn("2 hari", message.message)
        self.assertIn("Rp2.000", message.message)
        self.assertIn("Belum dibayar", message.message)
        self.assertIn("denda", message.message.casefold())

    def test_buku_hilang_tidak_memuat_kata_denda(self):
        item = make_item("Buku Hilang", "RET-3")
        item.returned_at = timezone.now()
        item.late_day = 3
        item.fine_amount = 3000
        item.fine_status = "BELUM_BAYAR"
        item.replacement_status = "BELUM_DIGANTI"

        message = format_loan_returned(make_loan(), item, lost=True)

        self.assertIn("Hilang", message.message)
        self.assertIn("Belum diganti", message.message)
        self.assertNotIn("denda", message.message.casefold())

    def test_pengingat_membatasi_sepuluh_buku(self):
        entries = [
            (
                make_item(f"Buku {number}", f"REM-{number}"),
                datetime.date(2026, 10, 20),
                -2,
            )
            for number in range(1, 13)
        ]

        message = format_reminder(
            "OVERDUE",
            SimpleNamespace(full_name="Budi"),
            entries,
        )

        self.assertEqual(message.message.count("<code>REM-"), 10)
        self.assertIn("dan 2 buku lainnya", message.message)
        self.assertIn("terlambat 2 hari", message.message)
        self.assertIn("20 Oktober 2026", message.message)
        self.assertTrue(is_formatted_title(message.title))

    def test_ebook_disetujui_menampilkan_masa_akses(self):
        loan = make_loan(
            expires_at=datetime.datetime(
                2026, 10, 24, 3, 0, tzinfo=datetime.timezone.utc
            ),
            ebook=SimpleNamespace(title="Panduan <Python>"),
        )

        with timezone.override("Asia/Jakarta"):
            message = format_ebook_approved(loan)

        self.assertIn("Panduan &lt;Python&gt;", message.message)
        self.assertIn("24 Oktober 2026 pukul 10:00", message.message)
        self.assertIn("Aktif", message.message)
