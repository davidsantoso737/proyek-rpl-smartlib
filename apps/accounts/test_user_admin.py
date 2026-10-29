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

    def _siswa_data(self, **overrides):
        data = {
            "full_name": "Siswa Baru",
            "nis": "99901",
            "email": "",
            "phone": "",
            "student_class": "XII-A",
        }
        data.update(overrides)
        return data

    def _guru_data(self, **overrides):
        data = {
            "full_name": "Guru Baru",
            "nip": "88801",
            "email": "",
            "phone": "",
            "department": "Matematika",
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
        user = User.objects.get(nis="99901")
        self.assertEqual(user.username, "99901")
        self.assertEqual(user.role, User.Role.SISWA)
        self.assertTrue(user.must_change_password)
        self.assertNotEqual(user.password, "99901")
        self.assertTrue(user.check_password("99901"))
        # Login pertama diarahkan ke password change (P1 enforcement).
        self.client.post(reverse("accounts:logout"))
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "99901", "password": "99901"},
        )
        self.assertRedirects(
            response, reverse("accounts:password_change")
        )

    def test_pustakawan_menambah_guru(self):
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("accounts:user_create", args=["GURU"]),
            self._guru_data(),
        )
        self.assertRedirects(response, reverse("accounts:user_list"))
        user = User.objects.get(nip="88801")
        self.assertEqual(user.username, "88801")
        self.assertEqual(user.role, User.Role.GURU)
        self.assertTrue(user.must_change_password)
        self.assertNotEqual(user.password, "88801")
        self.assertTrue(user.check_password("88801"))

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
                self._siswa_data(nis="99601"),
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
        self.assertFalse(User.objects.filter(nis="99601").exists())

    def test_guru_forbidden(self):
        self.client.force_login(self.guru)
        self.assertEqual(
            self.client.get(reverse("accounts:user_list")).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                reverse("accounts:user_create", args=["GURU"]),
                self._guru_data(nip="88701"),
            ).status_code,
            403,
        )
        self.assertFalse(User.objects.filter(nip="88701").exists())

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
            self._siswa_data(nis=""),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            User.objects.filter(full_name="Siswa Baru").exists()
        )

    def test_duplicate_nis_ditolak_tanpa_overwrite(self):
        self.client.force_login(self.pus)
        before = User.objects.count()
        response = self.client.post(
            reverse("accounts:user_create", args=["SISWA"]),
            self._siswa_data(nis="10001"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "sudah digunakan")
        self.assertEqual(User.objects.count(), before)
        # Account existing tidak tertimpa.
        original = User.objects.get(nis="10001")
        self.assertEqual(original.username, "siswa1")
        self.assertEqual(original.full_name, "siswa1 Full")
        self.assertTrue(original.check_password("secret123"))

    def test_duplicate_nip_ditolak_tanpa_overwrite(self):
        self.client.force_login(self.pus)
        before = User.objects.count()
        response = self.client.post(
            reverse("accounts:user_create", args=["GURU"]),
            self._guru_data(nip="20001"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "sudah digunakan")
        self.assertEqual(User.objects.count(), before)
        original = User.objects.get(nip="20001")
        self.assertEqual(original.username, "guru1")
        self.assertTrue(original.check_password("secret123"))

    def test_duplicate_username_ditolak(self):
        # Username otomatis = identifier; NIS yang sama dengan
        # username akun lain tetap ditolak.
        self.client.force_login(self.pus)
        before = User.objects.count()
        response = self.client.post(
            reverse("accounts:user_create", args=["SISWA"]),
            self._siswa_data(nis="guru1"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), before)

    def test_leading_zero_dipertahankan(self):
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("accounts:user_create", args=["SISWA"]),
            self._siswa_data(nis="006112"),
        )
        self.assertRedirects(response, reverse("accounts:user_list"))
        user = User.objects.get(nis="006112")
        self.assertEqual(user.username, "006112")
        self.assertTrue(user.check_password("006112"))
        self.assertIsNone(User.objects.filter(username="6112").first())
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("accounts:user_create", args=["GURU"]),
            self._guru_data(nip="007889"),
        )
        self.assertRedirects(response, reverse("accounts:user_list"))
        guru = User.objects.get(nip="007889")
        self.assertEqual(guru.username, "007889")
        self.assertTrue(guru.check_password("007889"))

    def test_username_tidak_bisa_diinput_bebas(self):
        self.client.force_login(self.pus)
        response = self.client.post(
            reverse("accounts:user_create", args=["SISWA"]),
            dict(
                self._siswa_data(nis="99902"),
                username="hacker-bebas",
                password1="apa-saja-123",
                password2="apa-saja-123",
            ),
        )
        self.assertRedirects(response, reverse("accounts:user_list"))
        user = User.objects.get(nis="99902")
        self.assertEqual(user.username, "99902")
        self.assertTrue(user.check_password("99902"))
        self.assertFalse(
            User.objects.filter(username="hacker-bebas").exists()
        )

    def test_form_create_tanpa_field_username_password(self):
        self.client.force_login(self.pus)
        for role in ("SISWA", "GURU"):
            response = self.client.get(
                reverse("accounts:user_create", args=[role])
            )
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'name="username"')
            self.assertNotContains(response, 'name="password1"')
            self.assertNotContains(response, 'name="password2"')

    def test_role_tidak_bisa_dimanipulasi_via_post(self):
        self.client.force_login(self.pus)
        # Upaya menyusupkan role pustakawan diabaikan: role dari URL.
        # Upaya menyusupkan username bebas juga diabaikan.
        response = self.client.post(
            reverse("accounts:user_create", args=["SISWA"]),
            dict(
                self._siswa_data(nis="99903"),
                role="PUSTAKAWAN",
                username="siswaHack",
            ),
        )
        self.assertRedirects(response, reverse("accounts:user_list"))
        user = User.objects.get(nis="99903")
        self.assertEqual(user.role, User.Role.SISWA)
        self.assertEqual(user.username, "99903")
        self.assertFalse(
            User.objects.filter(username="siswaHack").exists()
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

    def test_filter_role_menjaga_kata_kunci(self):
        self.client.force_login(self.pus)
        response = self.client.get(
            reverse("accounts:user_list"), {"role": "GURU", "q": "guru"}
        )
        self.assertEqual(response.status_code, 200)
        usernames = [
            account.username
            for account in response.context["page_obj"].object_list
        ]
        self.assertEqual(usernames, ["guru1"])
        self.assertContains(response, 'value="guru"')

    def test_daftar_pengguna_menyediakan_filter_role_realtime(self):
        self.client.force_login(self.pus)
        response = self.client.get(reverse("accounts:user_list"))
        self.assertContains(response, "data-live-filter")
        self.assertContains(response, "data-live-filter-select")
        self.assertContains(response, 'aria-label="Ubah akun siswa1"')
        self.assertContains(response, 'aria-label="Reset kredensial akun siswa1"')
        self.assertContains(response, 'aria-label="Nonaktifkan akun siswa1"')
