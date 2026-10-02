from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

User = get_user_model()


class UserRoleValidationTests(TestCase):
    def test_auth_user_model_is_accounts_user(self):
        from django.conf import settings

        self.assertEqual(settings.AUTH_USER_MODEL, "accounts.User")
        self.assertEqual(User._meta.db_table, "users")

    def test_1_siswa_valid_dengan_nis(self):
        user = User(
            username="siswa1",
            role=User.Role.SISWA,
            full_name="Siswa Satu",
            nis="12345",
        )
        user.set_password("secret123")
        user.full_clean()
        user.save()
        self.assertEqual(User.objects.count(), 1)

    def test_2_guru_valid_dengan_nip(self):
        user = User(
            username="guru1",
            role=User.Role.GURU,
            full_name="Guru Satu",
            nip="98765",
        )
        user.set_password("secret123")
        user.full_clean()
        user.save()
        self.assertEqual(User.objects.count(), 1)

    def test_3_pustakawan_tanpa_nis_nip(self):
        user = User(
            username="pus1",
            role=User.Role.PUSTAKAWAN,
            full_name="Pustakawan Satu",
        )
        user.set_password("secret123")
        user.full_clean()
        user.save()
        self.assertIsNone(user.nis)
        self.assertIsNone(user.nip)

    def test_4_siswa_tanpa_nis_ditolak(self):
        user = User(
            username="siswa2", role=User.Role.SISWA, full_name="Siswa Dua"
        )
        user.set_password("secret123")
        with self.assertRaises(ValidationError):
            user.full_clean()

    def test_5_guru_tanpa_nip_ditolak(self):
        user = User(
            username="guru2", role=User.Role.GURU, full_name="Guru Dua"
        )
        user.set_password("secret123")
        with self.assertRaises(ValidationError):
            user.full_clean()

    def test_6_siswa_dengan_nip_ditolak(self):
        user = User(
            username="siswa3",
            role=User.Role.SISWA,
            full_name="Siswa Tiga",
            nis="111",
            nip="222",
        )
        user.set_password("secret123")
        with self.assertRaises(ValidationError):
            user.full_clean()

    def test_7_guru_dengan_nis_ditolak(self):
        user = User(
            username="guru3",
            role=User.Role.GURU,
            full_name="Guru Tiga",
            nis="333",
            nip="444",
        )
        user.set_password("secret123")
        with self.assertRaises(ValidationError):
            user.full_clean()

    def test_password_terhash_bukan_plaintext(self):
        user = User(
            username="hash1",
            role=User.Role.PUSTAKAWAN,
            full_name="Hash Satu",
        )
        user.set_password("rahasia123")
        user.full_clean()
        user.save()
        user.refresh_from_db()
        self.assertNotEqual(user.password, "rahasia123")
        self.assertTrue(user.check_password("rahasia123"))
