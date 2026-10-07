from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.db.models import Q

from .provisioning import normalize_identifier

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
    """Edit profil milik sendiri (FR-03).

    Username, role, dan NIS/NIP TIDAK ada di form sehingga tidak
    dapat diubah via self-service. Kelas dan Jurusan hanya untuk
    SISWA; GURU dan PUSTAKAWAN hanya full_name, email, phone.
    """

    class Meta:
        model = User
        fields = [
            "full_name",
            "email",
            "phone",
            "student_class",
            "department",
        ]
        labels = {
            "full_name": "Nama lengkap",
            "email": "Email",
            "phone": "No. HP / Telepon",
            "student_class": "Kelas",
            "department": "Jurusan / Departemen",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if getattr(self.instance, "role", None) != User.Role.SISWA:
            del self.fields["student_class"]
            del self.fields["department"]
        for field in self.fields.values():
            field.widget.attrs.update({"class": INPUT_CLASS})


# Role yang boleh dikelola Pustakawan via public UI (FR-04 – FR-06).
# Pustakawan dikelola via Django admin; tidak ada URL/form yang
# membuat atau mengubah role menjadi PUSTAKAWAN (scope: no new role,
# no pustakawan-via-public-UI).
MANAGEABLE_ROLES = (User.Role.SISWA, User.Role.GURU)


def validate_identity_for_role(role, cleaned_data):
    """Tegakkan aturan NIS/NIP di level form (FR-07, NFR-23).

    ModelForm melewatkan validasi model untuk field opsional yang
    kosong (blank=True), sehingga aturan User.clean() perlu
    ditegakkan eksplisit di sini agar menjadi form error, bukan 500.
    Mengembalikan dict {field: pesan}.
    """
    errors = {}
    if role == User.Role.SISWA:
        if not (cleaned_data.get("nis") or "").strip():
            errors["nis"] = "Siswa wajib memiliki NIS."
    elif role == User.Role.GURU:
        if not (cleaned_data.get("nip") or "").strip():
            errors["nip"] = "Guru wajib memiliki NIP."
    return errors


class ManagedUserCreateForm(forms.ModelForm):
    """Tambah akun Siswa/Guru oleh Pustakawan (FR-04, FR-07, NFR-23).

    P2 — manual provisioning: username TIDAK diinput (otomatis =
    NIS untuk Siswa, NIP untuk Guru), password awal otomatis =
    identifier yang sama (di-hash via set_password), dan
    ``must_change_password`` selalu True. Role TIDAK ada di form:
    ditentukan view dari URL sehingga tidak dapat dimanipulasi
    via POST (role restriction).
    """

    class Meta:
        model = User
        fields = [
            "full_name",
            "nis",
            "nip",
            "email",
            "phone",
            "student_class",
            "department",
        ]
        labels = {
            "full_name": "Nama lengkap",
            "nis": "NIS",
            "nip": "NIP",
            "email": "Email",
            "phone": "No. HP / Telepon",
            "student_class": "Kelas",
            "department": "Jurusan / Departemen",
        }
        help_texts = {
            "nis": "Username dan password awal otomatis sama dengan NIS.",
            "nip": "Username dan password awal otomatis sama dengan NIP.",
        }

    def __init__(self, *args, role=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.managed_role = role
        # Hanya tampilkan field identitas yang relevan untuk role
        # (aturan NIS/NIP model tetap ditegakkan via full_clean).
        if role == User.Role.SISWA:
            del self.fields["nip"]
            del self.fields["department"]
        elif role == User.Role.GURU:
            del self.fields["nis"]
            del self.fields["student_class"]
        for field in self.fields.values():
            field.widget.attrs.update({"class": INPUT_CLASS})

    def clean_nis(self):
        """Normalisasi NIS sebagai string (leading zero dipertahankan)."""
        value = self.cleaned_data.get("nis", "")
        if not value or not str(value).strip():
            return value
        try:
            return normalize_identifier(value)
        except ValueError as exc:
            raise ValidationError(str(exc))

    def clean_nip(self):
        """Normalisasi NIP sebagai string (leading zero dipertahankan)."""
        value = self.cleaned_data.get("nip", "")
        if not value or not str(value).strip():
            return value
        try:
            return normalize_identifier(value)
        except ValueError as exc:
            raise ValidationError(str(exc))

    def clean(self):
        cleaned = super().clean()
        for field, message in validate_identity_for_role(
            self.managed_role, cleaned
        ).items():
            self.add_error(field, message)
        # Duplikat: tolak sebelum membuat akun; jangan overwrite
        # account existing. Username otomatis = identifier sehingga
        # pengecekan username + NIS/NIP mencakup seluruh konflik.
        if self.managed_role == User.Role.SISWA:
            nis = cleaned.get("nis")
            if nis and not self.errors.get("nis"):
                if User.objects.filter(
                    Q(username=nis) | Q(nis=nis)
                ).exists():
                    self.add_error(
                        "nis",
                        "NIS ini sudah digunakan oleh akun lain.",
                    )
        elif self.managed_role == User.Role.GURU:
            nip = cleaned.get("nip")
            if nip and not self.errors.get("nip"):
                if User.objects.filter(
                    Q(username=nip) | Q(nip=nip)
                ).exists():
                    self.add_error(
                        "nip",
                        "NIP ini sudah digunakan oleh akun lain.",
                    )
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.managed_role == User.Role.SISWA:
            identifier = normalize_identifier(
                self.cleaned_data["nis"]
            )
        else:
            identifier = normalize_identifier(
                self.cleaned_data["nip"]
            )
        user.username = identifier
        user.role = self.managed_role
        user.set_password(identifier)
        user.must_change_password = True
        if commit:
            user.save()  # User.save() menjalankan full_clean (FR-07).
        return user


class ManagedUserUpdateForm(forms.ModelForm):
    """Ubah data Siswa/Guru oleh Pustakawan (FR-05, FR-07).

    Username/role tidak dapat diubah: username menjaga kestabilan
    login, role menjaga konsistensi NIS/NIP (role restriction).
    """

    class Meta:
        model = User
        fields = [
            "full_name",
            "nis",
            "nip",
            "email",
            "phone",
            "student_class",
            "department",
        ]
        labels = {
            "full_name": "Nama lengkap",
            "nis": "NIS",
            "nip": "NIP",
            "email": "Email",
            "phone": "No. HP / Telepon",
            "student_class": "Kelas",
            "department": "Jurusan / Departemen",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.role == User.Role.SISWA:
            del self.fields["nip"]
            del self.fields["department"]
        elif self.instance.role == User.Role.GURU:
            del self.fields["nis"]
            del self.fields["student_class"]
        for field in self.fields.values():
            field.widget.attrs.update({"class": INPUT_CLASS})

    def clean(self):
        cleaned = super().clean()
        for field, message in validate_identity_for_role(
            self.instance.role, cleaned
        ).items():
            self.add_error(field, message)
        return cleaned
