from django import forms
from django.contrib.auth import get_user_model

from apps.core.models import LibrarySetting

User = get_user_model()

INPUT_CLASS = (
    "w-full border border-slate-300 rounded-xl px-3.5 py-2.5 text-sm "
    "bg-white placeholder:text-slate-400 "
    "focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
)


class PolicyForm(forms.ModelForm):
    """Ubah 4 nilai kebijakan (FR-50 – FR-53E, NFR-39 – NFR-40).

    Efektivitas/window dan updated_by diisi service update_policy
    (versioned), bukan form ini.
    """

    class Meta:
        model = LibrarySetting
        fields = [
            "physical_loan_duration",
            "ebook_access_duration",
            "pickup_qr_validity",
            "fine_rate_per_day",
        ]
        labels = {
            "physical_loan_duration": "Durasi pinjam fisik (hari)",
            "ebook_access_duration": "Durasi akses e-book (hari)",
            "pickup_qr_validity": "Masa berlaku QR Pickup (menit)",
            "fine_rate_per_day": "Tarif denda per hari (Rp)",
        }
        help_texts = {
            "physical_loan_duration": "Berlaku untuk transaksi BARU; yang aktif tidak berubah.",
            "ebook_access_duration": "Berlaku untuk akses e-book BARU; yang aktif tidak berubah.",
            "pickup_qr_validity": "Berlaku untuk QR yang dibuat SETELAH perubahan.",
            "fine_rate_per_day": "Hanya untuk keterlambatan baru; denda tercatat tidak berubah.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({"class": INPUT_CLASS})
            field.min_value = 1
