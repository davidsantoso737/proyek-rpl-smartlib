import re

from django import forms

from .models import WhatsAppConnection
from .services import normalize_phone

INPUT_CLASS = (
    "min-h-[44px] w-full rounded-xl border border-slate-300 bg-white "
    "px-3 py-2 text-sm text-slate-900 placeholder:text-slate-500 "
    "focus:outline-none focus:ring-2 focus:ring-blue-500"
)
CHECKBOX_CLASS = "mt-0.5 h-4 w-4 shrink-0 accent-blue-600"

PREFERENCE_FIELDS = [
    "notifications_enabled",
    "notify_loans",
    "notify_returns",
    "notify_fines",
    "notify_due_soon",
    "notify_overdue",
]

PREFERENCE_LABELS = {
    "notifications_enabled": "Kirim notifikasi otomatis",
    "notify_loans": "Peminjaman berhasil",
    "notify_returns": "Pengembalian berhasil",
    "notify_fines": "Informasi atau perubahan denda",
    "notify_due_soon": "Pengingat jatuh tempo",
    "notify_overdue": "Informasi keterlambatan",
}

PREFERENCE_HELP_TEXTS = {
    "notifications_enabled": (
        "Jika dimatikan, semua notifikasi otomatis ke WhatsApp berhenti."
    ),
    "notify_loans": (
        "Persetujuan, penolakan, dan peminjaman yang aktif."
    ),
    "notify_returns": "Konfirmasi pengembalian buku.",
    "notify_fines": "Informasi denda yang tercatat saat pengembalian.",
    "notify_due_soon": "Pengingat sebelum tanggal jatuh tempo.",
    "notify_overdue": "Pemberitahuan setelah melewati jatuh tempo.",
}


class WhatsAppPhoneForm(forms.Form):
    phone = forms.CharField(
        label="Nomor WhatsApp",
        max_length=20,
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "type": "tel",
                "inputmode": "tel",
                "autocomplete": "tel-national",
                "placeholder": "08xxxxxxxxxx",
            }
        ),
        help_text=(
            "Gunakan nomor Indonesia yang aktif. Nomor akan diverifikasi "
            "lewat kode OTP setelah disetujui pustakawan."
        ),
    )

    def clean_phone(self):
        value = self.cleaned_data.get("phone", "")
        normalized = normalize_phone(value)
        if not normalized:
            raise forms.ValidationError(
                "Masukkan nomor WhatsApp Indonesia yang valid, "
                "misalnya 081234567890."
            )
        return normalized


class WhatsAppOtpForm(forms.Form):
    code = forms.CharField(
        label="Kode OTP",
        max_length=6,
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "inputmode": "numeric",
                "autocomplete": "one-time-code",
                "placeholder": "______",
                "aria-describedby": "whatsapp-otp-help",
            }
        ),
        help_text="Masukkan 6 digit kode dari pesan WhatsApp.",
    )

    def clean_code(self):
        value = self.cleaned_data.get("code", "")
        digits = re.sub(r"\s+", "", value or "")
        if len(digits) != 6 or not digits.isdigit():
            raise forms.ValidationError("Kode OTP harus 6 digit angka.")
        return digits


class WhatsAppPreferencesForm(forms.ModelForm):
    class Meta:
        model = WhatsAppConnection
        fields = PREFERENCE_FIELDS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.label = PREFERENCE_LABELS[name]
            field.help_text = PREFERENCE_HELP_TEXTS[name]
            field.widget.attrs.update(
                {
                    "class": CHECKBOX_CLASS,
                    "aria-describedby": f"whatsapp-{name}-help",
                }
            )


class RejectWhatsAppForm(forms.Form):
    note = forms.CharField(
        required=False,
        max_length=1000,
        widget=forms.Textarea(
            attrs={
                "class": INPUT_CLASS,
                "rows": 3,
                "placeholder": "Catatan penolakan (opsional)",
            }
        ),
    )
