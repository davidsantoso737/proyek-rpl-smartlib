"""P1 — Account Provisioning foundation tests.

Mencakup: default flag existing (False), provisioning SISWA/GURU
(username = identifier, password awal = identifier ter-hash,
flag True, leading zero utuh), first-login enforcement
(login -> password change, tidak bisa bypass via direct URL),
penuntasan flag setelah ganti password, password lama hangus,
serta tidak ada regression role/authorization PUSTAKAWAN.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.provisioning import provision_guru, provision_siswa

User = get_user_model()


class ProvisioningBehaviorTests(TestCase):
    def test_provision_siswa_default(self):
        user = provision_siswa("006112", "Siswa Nol")
        self.assertEqual(user.username, "006112")
        self.assertEqual(user.nis, "006112")
        self.assertEqual(user.role, User.Role.SISWA)
        self.assertTrue(user.must_change_password)
        # Password ter-hash, bukan plaintext.
        self.assertNotEqual(user.password, "006112")
        self.assertTrue(user.check_password("006112"))

    def test_provision_guru_leading_zero(self):
        user = provision_guru("007889", "Guru Nol")
        self.assertEqual(user.username, "007889")
        self.assertEqual(user.nip, "007889")
        self.assertEqual(user.role, User.Role.GURU)
        self.assertTrue(user.must_change_password)
        self.assertNotEqual(user.password, "007889")
        self.assertTrue(user.check_password("007889"))

    def test_existing_style_user_default_flag_false(self):
        user = User(
            username="lama1",
            role=User.Role.SISWA,
            full_name="Lama Satu",
            nis="55501",
        )
        user.set_password("secret123")
        user.full_clean()
        user.save()
        self.assertFalse(user.must_change_password)


class FirstLoginEnforcementTests(TestCase):
    def setUp(self):
        self.siswa = provision_siswa("006112", "Siswa Nol")
        self.guru = provision_guru("007889", "Guru Nol")
        self.pus = User(
            username="pus1",
            role=User.Role.PUSTAKAWAN,
            full_name="Pustakawan Satu",
        )
        self.pus.set_password("secret123")
        self.pus.full_clean()
        self.pus.save()

    def _login(self, username, password):
        return self.client.post(
            reverse("accounts:login"),
            {"username": username, "password": password},
        )

    def test_existing_user_flag_false_login_normal(self):
        normal = User(
            username="siswaLama",
            role=User.Role.SISWA,
            full_name="Siswa Lama",
            nis="55502",
        )
        normal.set_password("secret123")
        normal.full_clean()
        normal.save()
        response = self._login("siswaLama", "secret123")
        self.assertRedirects(
            response, reverse("accounts:dashboard"),
            fetch_redirect_response=False,
        )
        response = self.client.get(reverse("accounts:dashboard"))
        self.assertRedirects(
            response, reverse("accounts:dashboard_siswa")
        )

    def test_provisioned_login_diarahkan_ke_password_change(self):
        response = self._login("006112", "006112")
        self.assertRedirects(
            response, reverse("accounts:password_change")
        )

    def test_next_diabaikan_selama_flag_true(self):
        response = self.client.post(
            reverse("accounts:login"),
            {
                "username": "006112",
                "password": "006112",
                "next": reverse("accounts:profile"),
            },
        )
        self.assertRedirects(
            response, reverse("accounts:password_change")
        )

    def test_tidak_bisa_bypass_via_direct_url(self):
        self._login("006112", "006112")
        for url in (
            reverse("accounts:dashboard"),
            reverse("accounts:dashboard_siswa"),
            reverse("accounts:profile"),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(
                    reverse("accounts:password_change"),
                    response["Location"],
                )
        # Halaman ganti password dan logout tetap dapat dibuka.
        self.assertEqual(
            self.client.get(
                reverse("accounts:password_change")
            ).status_code,
            200,
        )

    def test_full_flow_ganti_password_lalu_normal(self):
        self._login("006112", "006112")
        response = self.client.post(
            reverse("accounts:password_change"),
            {
                "old_password": "006112",
                "new_password1": "rahasia-baru-456",
                "new_password2": "rahasia-baru-456",
            },
        )
        self.assertRedirects(response, reverse("accounts:profile"))
        self.siswa.refresh_from_db()
        self.assertFalse(self.siswa.must_change_password)
        # Dashboard normal kini dapat diakses.
        self.assertRedirects(
            self.client.get(reverse("accounts:dashboard")),
            reverse("accounts:dashboard_siswa"),
        )
        self.assertEqual(
            self.client.get(
                reverse("accounts:dashboard_siswa")
            ).status_code,
            200,
        )
        # Password lama hangus, password baru berlaku.
        self.client.post(reverse("accounts:logout"))
        self.assertFalse(
            self.client.login(username="006112", password="006112")
        )
        self.assertTrue(
            self.client.login(
                username="006112", password="rahasia-baru-456"
            )
        )

    def test_role_authorization_tetap_setelah_flag_clear(self):
        self._login("006112", "006112")
        self.client.post(
            reverse("accounts:password_change"),
            {
                "old_password": "006112",
                "new_password1": "rahasia-baru-456",
                "new_password2": "rahasia-baru-456",
            },
        )
        # Siswa tetap tidak boleh masuk dashboard guru/pustakawan.
        self.assertEqual(
            self.client.get(
                reverse("accounts:dashboard_guru")
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse("accounts:dashboard_pustakawan")
            ).status_code,
            403,
        )

    def test_pustakawan_tidak_terkena_regression(self):
        self.assertFalse(self.pus.must_change_password)
        response = self._login("pus1", "secret123")
        self.assertRedirects(
            response, reverse("accounts:dashboard"),
            fetch_redirect_response=False,
        )
        self.assertRedirects(
            self.client.get(reverse("accounts:dashboard")),
            reverse("accounts:dashboard_pustakawan"),
        )
        self.assertEqual(
            self.client.get(
                reverse("accounts:dashboard_pustakawan")
            ).status_code,
            200,
        )
        # Pustakawan tetap bisa mengelola daftar pengguna.
        self.assertEqual(
            self.client.get(reverse("accounts:user_list")).status_code,
            200,
        )
