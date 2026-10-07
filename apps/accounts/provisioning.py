"""Account Provisioning foundation (P1) untuk akun SISWA dan GURU.

Aturan:
- SISWA: identifier = NIS (string), username = NIS,
  initial password = NIS, must_change_password = True.
- GURU: identifier = NIP (string), username = NIP,
  initial password = NIP, must_change_password = True.

NIS/NIP diperlakukan sebagai string; leading zero dipertahankan
(mis. "006112" tetap "006112"). Password selalu di-hash via
mekanisme Django (set_password); tidak ada plaintext di database.

Fungsi ini menjadi default provisioning behavior untuk pembuatan
manual via public UI (ManagedUserCreateForm) maupun tahap
Excel import berikutnya.
"""

from django.contrib.auth import get_user_model

User = get_user_model()


def normalize_identifier(value):
    """Normalisasi NIS/NIP sebagai string tanpa menghilangkan leading zero.

    Hanya strip whitespace di ujung; tidak ada konversi ke int/float
    agar "006112" tidak menjadi "6112".
    """
    if value is None:
        raise ValueError("NIS/NIP wajib diisi.")
    text = str(value).strip()
    if not text:
        raise ValueError("NIS/NIP wajib diisi.")
    if any(ch.isspace() for ch in text):
        raise ValueError("NIS/NIP tidak boleh mengandung spasi di tengah.")
    return text


def provision_siswa(nis, full_name, **extra_fields):
    """Buat akun SISWA hasil provisioning.

    username = NIS, password awal = NIS (di-hash), flag True.
    """
    nis = normalize_identifier(nis)
    extra_fields.setdefault("must_change_password", True)
    return User.objects.create_user(
        username=nis,
        password=nis,
        role=User.Role.SISWA,
        full_name=full_name,
        nis=nis,
        **extra_fields,
    )


def provision_guru(nip, full_name, **extra_fields):
    """Buat akun GURU hasil provisioning.

    username = NIP, password awal = NIP (di-hash), flag True.
    """
    nip = normalize_identifier(nip)
    extra_fields.setdefault("must_change_password", True)
    return User.objects.create_user(
        username=nip,
        password=nip,
        role=User.Role.GURU,
        full_name=full_name,
        nip=nip,
        **extra_fields,
    )
