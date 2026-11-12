from django import forms

from .models import TelegramAccount, TelegramConnection

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
        "Jika dimatikan, semua notifikasi otomatis ke Telegram "
        "berhenti."
    ),
    "notify_loans": (
        "Persetujuan, penolakan, dan peminjaman yang aktif."
    ),
    "notify_returns": "Konfirmasi pengembalian buku.",
    "notify_fines": (
        "Informasi denda yang tercatat saat pengembalian."
    ),
    "notify_due_soon": (
        "Pengingat sebelum tanggal jatuh tempo."
    ),
    "notify_overdue": (
        "Pemberitahuan setelah melewati jatuh tempo."
    ),
}


def _configure_preferences_form(form):
    for name, field in form.fields.items():
        field.label = PREFERENCE_LABELS[name]
        field.help_text = PREFERENCE_HELP_TEXTS[name]
        field.widget.attrs.update(
            {
                "class": CHECKBOX_CLASS,
                "aria-describedby": f"telegram-{name}-help",
            }
        )
    return form


class TelegramPreferencesForm(forms.ModelForm):
    class Meta:
        model = TelegramAccount
        fields = PREFERENCE_FIELDS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _configure_preferences_form(self)


class TelegramConnectionPreferencesForm(forms.ModelForm):
    class Meta:
        model = TelegramConnection
        fields = PREFERENCE_FIELDS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _configure_preferences_form(self)
