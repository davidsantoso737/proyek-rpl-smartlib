from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class UserAdminTests(TestCase):
    def setUp(self):
        self.pus = self._make_user("pus1", User.Role.PUSTAKAWAN)
        self.siswa = self._make_user(
            "siswa1", User.Role.SISWA, nis="10001"
        )
        self.guru = self._make_user("guru1", User.Role.GURU, nip="20001")

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

    def _siswa_data(self, username="siswaBaru", **overrides):
        data = {
            "username": username,
            "full_name": "Siswa Baru",
            "nis": "99901",
            "email": "",
            "phone": "",
            "student_class": "XII-A",
            "password1": "rahasia-aman-123",
            "password2": "rahasia-aman-123",
        }
        data.update(overrides)
        return data

    def _guru_data(self, username="guruBaru", **overrides):
        data = {
            "username": username,
            "full_name": "Guru Baru",
            "nip": "88801",
            "email": "",
            "phone": "",
            "department": "Matematika",
            "password1": "rahasia-aman-123",
            "password2": "rahasia-aman-123",
        }
        data.update(overrides)
        return data

    # --- Pustakawan CRUD ---

    def test_pustakawan_melihat_daftar_siswa_guru(self):
        self.client.force_login(self.pus)
        response = self.client.get(reverse("accounts:user_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "siswa1")
        self.assertContains(response, "guru1")

    def test_pustakawan_menambah_siswa(self):
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("accounts:user_create", args=["SISWA"]),
            self._siswa_data(),
        )
        self.assertRedirects(response, reverse("accounts:user_list"))
        user = User.objects.get(username="siswaBaru")
        self.assertEqual(user.role, User.Role.SISWA)
        self.assertEqual(user.nis, "99901")
        self.assertTrue(user.check_password("rahasia-aman-123"))
        self.assertTrue(
            self.client.login(
                username="siswaBaru", password="rahasia-aman-123"
            )
        )

    def test_pustakawan_menambah_guru(self):
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("accounts:user_create", args=["GURU"]),
            self._guru_data(),
        )
        self.assertRedirects(response, reverse("accounts:user_list"))
        user = User.objects.get(username="guruBaru")
        self.assertEqual(user.role, User.Role.GURU)
        self.assertEqual(user.nip, "88801")

    def test_pustakawan_mengubah_data(self):
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("accounts:user_update", args=[self.siswa.pk]),
            {
                "full_name": "Siswa Satu Revisi",
                "nis": "10001",
                "email": "s1@sekolah.id",
                "phone": "",
                "student_class": "XI-B",
            },
        )
        self.assertRedirects(response, reverse("accounts:user_list"))
        self.siswa.refresh_from_db()
        self.assertEqual(self.siswa.full_name, "Siswa Satu Revisi")
        self.assertEqual(self.siswa.email, "s1@sekolah.id")

    def test_pustakawan_deactivate_lalu_activate(self):
        self.client.force_login(self.pus)
        toggle = reverse(
            "accounts:user_toggle_active", args=[self.siswa.pk]
        )
        self.client.post(toggle)
        self.siswa.refresh_from_db()
        self.assertFalse(self.siswa.is_active)
        # Akun nonaktif tidak bisa login (BR-02, BR-78).
        self.assertFalse(
            self.client.login(username="siswa1", password="secret123")
        )
        self.client.force_login(self.pus)
        self.client.post(toggle)
        self.siswa.refresh_from_db()
        self.assertTrue(self.siswa.is_active)
        self.assertTrue(
            self.client.login(username="siswa1", password="secret123")
        )

    # --- Forbidden untuk non-pustakawan ---

    def test_siswa_forbidden(self):
        self.client.force_login(self.siswa)
        urls = [
            reverse("accounts:user_list"),
            reverse("accounts:user_create", args=["SISWA"]),
            reverse("accounts:user_update", args=[self.guru.pk]),
        ]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(
            self.client.post(
                reverse("accounts:user_create", args=["SISWA"]),
                self._siswa_data(username="siswaX"),
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse(
                    "accounts:user_toggle_active", args=[self.guru.pk]
                )
            ).status_code,
            403,
        )
        self.assertFalse(User.objects.filter(username="siswaX").exists())

    def test_guru_forbidden(self):
        self.client.force_login(self.guru)
        self.assertEqual(
            self.client.get(reverse("accounts:user_list")).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                reverse("accounts:user_create", args=["GURU"]),
                self._guru_data(username="guruX"),
            ).status_code,
            403,
        )
        self.assertFalse(User.objects.filter(username="guruX").exists())

    def test_anonymous_diarahkan_ke_login(self):
        response = self.client.get(reverse("accounts:user_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(
            reverse("accounts:login"), response["Location"]
        )

    # --- Validasi & role restriction ---

    def test_invalid_data_siswa_tanpa_nis_ditolak(self):
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("accounts:user_create", args=["SISWA"]),
            self._siswa_data(username="siswaBad", nis=""),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            User.objects.filter(username="siswaBad").exists()
        )

    def test_invalid_data_username_ganda_ditolak(self):
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("accounts:user_create", args=["SISWA"]),
            self._siswa_data(username="siswa1"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(username="siswa1").count(), 1)

    def test_role_tidak_bisa_dimanipulasi_via_post(self):
        self.client.force_login(self.pus)
        # Upaya menyusupkan role pustakawan diabaikan: role dari URL.
        response = self.client.post(
            reverse("accounts:user_create", args=["SISWA"]),
            dict(self._siswa_data(username="siswaHack"), role="PUSTAKAWAN"),
        )
        self.assertRedirects(response, reverse("accounts:user_list"))
        self.assertEqual(
            User.objects.get(username="siswaHack").role, User.Role.SISWA
        )
        # Tidak ada URL untuk membuat Pustakawan via public UI.
        self.assertEqual(
            self.client.get(
                reverse("accounts:user_create", args=["PUSTAKAWAN"])
            ).status_code,
            403,
        )
        # Role tidak berubah lewat form edit + akun Pustakawan tak tersentuh.
        response = self.client.post(
            reverse("accounts:user_update", args=[self.siswa.pk]),
            {
                "full_name": "Tetap Siswa",
                "nis": "10001",
                "email": "",
                "phone": "",
                "student_class": "",
                "role": "GURU",
            },
        )
        self.assertRedirects(response, reverse("accounts:user_list"))
        self.siswa.refresh_from_db()
        self.assertEqual(self.siswa.role, User.Role.SISWA)
        self.assertEqual(
            self.client.get(
                reverse("accounts:user_update", args=[self.pus.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse("accounts:user_toggle_active", args=[self.pus.pk])
            ).status_code,
            404,
        )
