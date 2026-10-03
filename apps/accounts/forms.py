from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

User = get_user_model()

INPUT_CLASS = (
    "w-full border border-slate-300 rounded-xl px-3.5 py-2.5 text-sm "
    "bg-white placeholder:text-slate-400 "
    "focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
)


class LoginForm(AuthenticationForm):
    """Form login dengan pesan error berbahasa Indonesia (NFR-33)."""

    error_messages = {
        "invalid_login": (
            "Username atau password salah. Silakan coba lagi."
        ),
        "inactive": (
            "Akun ini tidak aktif. Hubungi pustakawan untuk bantuan."
        ),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Username"
        self.fields["password"].label = "Password"
        self.fields["username"].widget.attrs.update(
            {"class": INPUT_CLASS, "autofocus": True}
        )
        self.fields["password"].widget.attrs.update({"class": INPUT_CLASS})

    def clean(self):
        # authenticate() mengembalikan None untuk akun non-aktif,
        # sehingga tanpa pemeriksaan ini pengguna non-aktif hanya
        # mendapat pesan "salah" yang generik (NFR-33).
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if username and password:
            try:
                user = User.objects.get_by_natural_key(username)
            except User.DoesNotExist:
                user = None
            if (
                user is not None
                and not user.is_active
                and user.check_password(password)
            ):
                raise ValidationError(
                    self.error_messages["inactive"],
                    code="inactive",
                )
        return super().clean()


class ProfileForm(forms.ModelForm):
    """Edit profil milik sendiri: tanpa role/username/NIS/NIP (FR-03)."""

    class Meta:
        model = User
        fields = ["full_name", "email", "phone"]
        labels = {
            "full_name": "Nama lengkap",
            "email": "Email",
            "phone": "No. HP / Telepon",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({"class": INPUT_CLASS})
