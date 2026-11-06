from urllib.parse import urlsplit

from django import forms

INPUT_CLASS = (
    "w-full border border-slate-300 rounded-xl px-3.5 py-2.5 text-sm "
    "bg-white placeholder:text-slate-400 "
    "focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
)


class _AIProviderConnectionForm(forms.Form):
    """Field koneksi yang dipakai bersama oleh uji dan discovery model."""

    configuration_id = forms.IntegerField(
        required=False,
        widget=forms.HiddenInput(),
    )
    base_url = forms.URLField(
        label="URL endpoint provider",
        max_length=255,
        help_text=(
            "URL dasar endpoint kompatibel OpenAI, tanpa /chat/completions. "
            "Contoh: https://api.openai.com/v1"
        ),
    )
    api_key = forms.CharField(
        label="API key",
        required=False,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={
                "autocomplete": "new-password",
                "spellcheck": "false",
                "placeholder": "Kosongkan untuk memakai key tersimpan",
            },
        ),
        help_text=(
            "Biarkan kosong untuk memakai API key tersimpan, termasuk saat "
            "memuat model."
        ),
    )

    def __init__(self, *args, provider_name="", **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("base_url", "api_key"):
            self.fields[name].widget.attrs.update({"class": INPUT_CLASS})
        if provider_name:
            self.fields["api_key"].widget.attrs["placeholder"] = (
                "Kosongkan untuk memakai API key tersimpan "
                f"{provider_name}"
            )

    def clean_base_url(self):
        value = (self.cleaned_data.get("base_url") or "").strip().rstrip("/")
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise forms.ValidationError(
                "Gunakan URL http atau https yang valid."
            )
        if parsed.username or parsed.password:
            raise forms.ValidationError(
                "URL tidak boleh memuat kredensial."
            )
        if parsed.query or parsed.fragment:
            raise forms.ValidationError(
                "URL tidak boleh memuat query atau fragmen."
            )
        return value


class AIConfigurationForm(_AIProviderConnectionForm):
    """Form konfigurasi AI. API key tidak pernah diisi ulang dari server."""

    name = forms.CharField(
        label="Nama provider",
        max_length=100,
        required=False,
        help_text=(
            "Nama yang mudah dikenali, misalnya NVIDIA, Google Gemini, "
            "atau JuanRouter."
        ),
    )
    model = forms.CharField(
        label="ID model",
        max_length=120,
        help_text=(
            "Pilih dari daftar model atau isi ID model persis seperti yang "
            "didukung penyedia."
        ),
    )
    is_enabled = forms.BooleanField(
        label="Jadikan provider aktif setelah disimpan",
        required=False,
        initial=False,
        help_text=(
            "Hanya satu provider yang aktif. Bila tidak dicentang, provider "
            "disimpan sebagai nonaktif; Assistant memakai provider aktif "
            "lain atau fallback environment."
        ),
    )
    create_new = forms.BooleanField(
        required=False,
        widget=forms.HiddenInput(),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.update(
            {
                "class": INPUT_CLASS,
                "placeholder": "Contoh: NVIDIA",
                "autocomplete": "off",
            }
        )
        self.fields["model"].widget.attrs.update({"class": INPUT_CLASS})
        self.fields["is_enabled"].widget.attrs.update(
            {
                "class": (
                    "h-4 w-4 rounded border-slate-300 text-blue-600 "
                    "focus:ring-2 focus:ring-blue-500"
                )
            }
        )

    def clean_name(self):
        return (self.cleaned_data.get("name") or "").strip()

    def clean_model(self):
        value = (self.cleaned_data.get("model") or "").strip()
        if not value:
            raise forms.ValidationError("ID model wajib diisi.")
        return value


class AIModelDiscoveryForm(_AIProviderConnectionForm):
    """Form discovery model; tidak menyimpan konfigurasi apa pun."""
