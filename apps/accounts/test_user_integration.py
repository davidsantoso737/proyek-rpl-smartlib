"""P6 — User Management integration regression tests.

Akun hasil manual/bulk provisioning adalah user normal: muncul di
daftar/search/filter, bisa diedit/diaktifkan/dinonaktifkan/direset,
profil self-service tidak dapat mengubah username/role/NIS/NIP,
dan first-login enforcement tetap berlaku.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.provisioning import provision_guru, provision_siswa

User = get_user_model()


class ProvisionedUserManagementTests(TestCase):
    def setUp(self):
        self.pus = self._make_user("pus1", User.Role.PUSTAKAWAN)
        self.siswa = provision_siswa(
            "006112",
            "Siswa Nol",
            email="siswa@sekolah.id",
            student_class="XII-A",
        )
        self.guru = provision_guru(
            "007889",
            "Guru Nol",
            email="guru@sekolah.id",
            department="Matematika",
        )
        # Akun lama dengan username berbeda dari identifier.
        self.legacy = self._make_user(
            "siswa-legacy", User.Role.SISWA, nis="55501"
        )

    def _make_user(self, username, role, password="secret123", **extra):
        defaults = {"full_name": f"{username} Full"}
        if role == User.Role.SISWA:
            defaults.setdefault("nis", f"nis-{username}")
        elif role == User.Role.GURU:
            defaults.setdefault("nip", f"nip-{username}")
        defaults.update(extra)
        user = User(username=username, role=role, **defaults)
        user.set_password(password)
        user.full_clean()
        user.save()
        return user

    def test_imported_student_muncul_di_daftar(self):
        self.client.force_login(self.pus)
        response = self.client.get(reverse("accounts:user_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "006112")
        self.assertContains(response, "Siswa Nol")

    def test_imported_teacher_muncul_di_daftar(self):
        self.client.force_login(self.pus)
        response = self.client.get(reverse("accounts:user_list"))
        self.assertContains(response, "007889")
        self.assertContains(response, "Guru Nol")

    def test_search_berdasarkan_username(self):
        self.client.force_login(self.pus)
        response = self.client.get(
            reverse("accounts:user_list"), {"q": "006112"}
        )
        self.assertContains(response, "006112")
        self.assertNotContains(response, "007889")

    def test_search_berdasarkan_nis_nip(self):
        self.client.force_login(self.pus)
        response = self.client.get(
            reverse("accounts:user_list"), {"q": "55501"}
        )
        self.assertContains(response, "siswa-legacy")
        self.assertNotContains(response, "006112")
        # Filter role existing tetap bekerja.
        response = self.client.get(
            reverse("accounts:user_list"), {"role": "GURU"}
        )
        self.assertContains(response, "007889")
        self.assertNotContains(response, "006112")

    def test_edit_profile_siswa_dan_guru(self):
        # Status normal pasca first-login (enforcement diuji terpisah).
        for account in (self.siswa, self.guru):
            account.must_change_password = False
            account.save(update_fields=["must_change_password", "updated_at"])
        self.client.force_login(self.siswa)
        response = self.client.post(
            reverse("accounts:profile"),
            {
                "full_name": "Siswa Nol Baru",
                "email": "baru@sekolah.id",
                "phone": "0810000000",
                "student_class": "XII-B",
            },
        )
        self.assertRedirects(response, reverse("accounts:profile"))
        self.siswa.refresh_from_db()
        self.assertEqual(self.siswa.full_name, "Siswa Nol Baru")
        self.assertEqual(self.siswa.email, "baru@sekolah.id")
        self.assertEqual(self.siswa.student_class, "XII-B")
        self.client.force_login(self.guru)
        response = self.client.post(
            reverse("accounts:profile"),
            {
                "full_name": "Guru Nol Baru",
                "email": "",
                "phone": "",
                "department": "Fisika",
            },
        )
        self.assertRedirects(response, reverse("accounts:profile"))
        self.guru.refresh_from_db()
        self.assertEqual(self.guru.department, "Fisika")

    def test_activate_deactivate_imported_account(self):
        self.client.force_login(self.pus)
        toggle = reverse(
            "accounts:user_toggle_active", args=[self.siswa.pk]
        )
        self.client.post(toggle)
        self.siswa.refresh_from_db()
        self.assertFalse(self.siswa.is_active)
        self.assertFalse(
            self.client.login(username="006112", password="006112")
        )
        self.client.force_login(self.pus)
        self.client.post(toggle)
        self.siswa.refresh_from_db()
        self.assertTrue(self.siswa.is_active)
        self.assertTrue(
            self.client.login(username="006112", password="006112")
        )

    def test_username_role_identifier_immutable_via_profile(self):
        # Flag False agar POST benar-benar sampai ke ProfileForm.
        self.siswa.must_change_password = False
        self.siswa.save(update_fields=["must_change_password", "updated_at"])
        self.client.force_login(self.siswa)
        self.client.post(
            reverse("accounts:profile"),
            {
                "full_name": "Tetap",
                "email": "",
                "phone": "",
                "student_class": "",
                "username": "hacker",
                "role": User.Role.PUSTAKAWAN,
                "nis": "99999",
                "nip": "88888",
            },
        )
        self.siswa.refresh_from_db()
        self.assertEqual(self.siswa.username, "006112")
        self.assertEqual(self.siswa.role, User.Role.SISWA)
        self.assertEqual(self.siswa.nis, "006112")
        self.assertIsNone(self.siswa.nip)
        self.assertFalse(
            User.objects.filter(username="hacker").exists()
        )

    def test_first_login_enforcement_untuk_imported(self):
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "006112", "password": "006112"},
        )
        self.assertRedirects(
            response, reverse("accounts:password_change")
        )
        response = self.client.get(reverse("accounts:dashboard"))
        self.assertRedirects(
            response, reverse("accounts:password_change")
        )
        self.client.post(
            reverse("accounts:password_change"),
            {
                "old_password": "006112",
                "new_password1": "rahasia-baru-456",
                "new_password2": "rahasia-baru-456",
            },
        )
        self.siswa.refresh_from_db()
        self.assertFalse(self.siswa.must_change_password)
        self.assertRedirects(
            self.client.get(reverse("accounts:dashboard")),
            reverse("accounts:dashboard_siswa"),
        )

    def test_librarian_workflow_tidak_berubah(self):
        self.client.force_login(self.pus)
        self.assertEqual(
            self.client.get(reverse("accounts:user_list")).status_code, 200
        )
        self.assertEqual(
            self.client.get(
                reverse("accounts:user_create", args=["SISWA"])
            ).status_code,
            200,
        )
        response = self.client.post(
            reverse("accounts:user_update", args=[self.siswa.pk]),
            {
                "full_name": "Siswa Nol Revisi",
                "nis": "006112",
                "email": "",
                "phone": "",
                "student_class": "XII-A",
            },
        )
        self.assertRedirects(response, reverse("accounts:user_list"))


class LibrarianResetPasswordTests(TestCase):
    def setUp(self):
        self.pus = self._make_user("pus1", User.Role.PUSTAKAWAN)
        self.siswa = provision_siswa("006112", "Siswa Nol")
        self.guru = provision_guru("007889", "Guru Nol")
        # Akun yang sudah mengganti password (flag False).
        self.siswa.set_password("rahasia-pribadi-789")
        self.siswa.must_change_password = False
        self.siswa.save()
        self.guru.set_password("rahasia-guru-321")
        self.guru.must_change_password = False
        self.guru.save()

    def _make_user(self, username, role, password="secret123", **extra):
        defaults = {"full_name": f"{username} Full"}
        defaults.update(extra)
        user = User(username=username, role=role, **defaults)
        user.set_password(password)
        user.full_clean()
        user.save()
        return user

    def _reset_url(self, user):
        return reverse("accounts:user_reset_password", args=[user.pk])

    def test_reset_siswa_ke_nis_dan_flag_true(self):
        self.client.force_login(self.pus)
        response = self.client.post(self._reset_url(self.siswa))
        self.assertRedirects(response, reverse("accounts:user_list"))
        self.siswa.refresh_from_db()
        self.assertTrue(self.siswa.check_password("006112"))
        self.assertFalse(
            self.siswa.check_password("rahasia-pribadi-789")
        )
        self.assertTrue(self.siswa.must_change_password)
        self.assertNotEqual(self.siswa.password, "006112")
        # Identitas dan status tidak berubah.
        self.assertEqual(self.siswa.username, "006112")
        self.assertEqual(self.siswa.role, User.Role.SISWA)
        self.assertEqual(self.siswa.nis, "006112")
        self.assertTrue(self.siswa.is_active)
        # Login berikutnya dipaksa ganti password.
        self.client.post(reverse("accounts:logout"))
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "006112", "password": "006112"},
        )
        self.assertRedirects(
            response, reverse("accounts:password_change")
        )

    def test_reset_guru_ke_nip(self):
        self.guru.set_password("lama-123")
        self.guru.must_change_password = False
        self.guru.save()
        self.client.force_login(self.pus)
        self.client.post(self._reset_url(self.guru))
        self.guru.refresh_from_db()
        self.assertTrue(self.guru.check_password("007889"))
        self.assertTrue(self.guru.must_change_password)
        self.assertEqual(self.guru.username, "007889")
        self.assertEqual(self.guru.role, User.Role.GURU)

    def test_reset_hanya_pustakawan_dan_post(self):
        self.client.force_login(self.siswa)
        self.assertEqual(
            self.client.post(self._reset_url(self.guru)).status_code, 403
        )
        self.client.force_login(self.guru)
        self.assertEqual(
            self.client.post(self._reset_url(self.siswa)).status_code, 403
        )
        self.assertTrue(
            self.siswa.check_password("rahasia-pribadi-789")
        )
        self.client.force_login(self.pus)
        # GET ditolak method (konsisten dengan logout/toggle),
        # POST ke akun PUSTAKAWAN tidak ditemukan dalam scope.
        self.assertEqual(
            self.client.get(self._reset_url(self.siswa)).status_code, 405
        )
        self.assertEqual(
            self.client.post(
                reverse(
                    "accounts:user_reset_password",
                    args=[self.pus.pk],
                )
            ).status_code,
            404,
        )
        self.client.logout()
        response = self.client.post(self._reset_url(self.siswa))
        self.assertEqual(response.status_code, 302)
        self.assertIn(
            reverse("accounts:login"), response["Location"]
        )

    def test_reset_akun_nonaktif_tetap_nonaktif(self):
        self.siswa.is_active = False
        self.siswa.save(update_fields=["is_active", "updated_at"])
        self.client.force_login(self.pus)
        self.client.post(self._reset_url(self.siswa))
        self.siswa.refresh_from_db()
        self.assertFalse(self.siswa.is_active)
        self.assertTrue(self.siswa.check_password("006112"))
        self.assertFalse(
            self.client.login(username="006112", password="006112")
        )
