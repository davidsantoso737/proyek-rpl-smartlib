from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class AuthTestMixin:
    def make_user(self, username, role, password="secret123", **extra):
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

    def login_as(self, username, password="secret123"):
        return self.client.post(
            reverse("accounts:login"),
            {"username": username, "password": password},
        )


class LoginTests(AuthTestMixin, TestCase):
    def setUp(self):
        self.make_user("siswa1", User.Role.SISWA)
        self.make_user("guru1", User.Role.GURU)
        self.make_user("pus1", User.Role.PUSTAKAWAN)

    def test_valid_login_redirects_to_role_dashboard(self):
        response = self.login_as("siswa1")
        self.assertRedirects(
            response,
            reverse("accounts:dashboard"),
            fetch_redirect_response=False,
        )
        self.assertIn("_auth_user_id", self.client.session)
        response = self.client.get(reverse("accounts:dashboard"))
        self.assertRedirects(
            response, reverse("accounts:dashboard_siswa")
        )

    def test_role_identification_guru_dan_pustakawan(self):
        self.login_as("guru1")
        self.assertRedirects(
            self.client.get(reverse("accounts:dashboard")),
            reverse("accounts:dashboard_guru"),
        )
        self.client.post(reverse("accounts:logout"))
        self.login_as("pus1")
        self.assertRedirects(
            self.client.get(reverse("accounts:dashboard")),
            reverse("accounts:dashboard_pustakawan"),
        )

    def test_invalid_login_ditolak(self):
        response = self.login_as("siswa1", password="salah")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertContains(
            response, "Username atau password salah"
        )

    def test_login_next_dihormati(self):
        response = self.client.post(
            reverse("accounts:login"),
            {
                "username": "siswa1",
                "password": "secret123",
                "next": reverse("accounts:profile"),
            },
        )
        self.assertRedirects(response, reverse("accounts:profile"))


class InactiveAccountTests(AuthTestMixin, TestCase):
    def setUp(self):
        self.user = self.make_user(
            "siswaX", User.Role.SISWA, is_active=False
        )

    def test_inactive_tidak_bisa_login(self):
        response = self.login_as("siswaX")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertContains(response, "tidak aktif")

    def test_inactive_ditolak_dari_fitur_authenticated(self):
        # Simulasi sesi lama yang akunnya dinonaktifkan setelah login.
        # Django menganggap sesi tersebut anonim (ModelBackend.get_user
        # menolak user non-aktif); yang penting request ditolak dan
        # tidak ada request terautentikasi yang lolos.
        self.client.force_login(self.user)
        for url in (
            reverse("accounts:dashboard"),
            reverse("accounts:dashboard_siswa"),
            reverse("accounts:profile"),
            reverse("accounts:password_change"),
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("accounts:login"), response["Location"])
            self.assertFalse(response.wsgi_request.user.is_authenticated)


class LogoutTests(AuthTestMixin, TestCase):
    def setUp(self):
        self.make_user("siswa1", User.Role.SISWA)

    def test_logout_post_mengakhiri_sesi(self):
        self.login_as("siswa1")
        self.assertIn("_auth_user_id", self.client.session)
        response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(
            response, "/accounts/login/?logged_out=1"
        )
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_get_ditolak_sesi_utuh(self):
        self.login_as("siswa1")
        response = self.client.get(reverse("accounts:logout"))
        self.assertEqual(response.status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)

    def test_setelah_logout_area_tertutup(self):
        self.login_as("siswa1")
        self.client.post(reverse("accounts:logout"))
        response = self.client.get(reverse("accounts:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])


class DashboardAuthorizationTests(AuthTestMixin, TestCase):
    DASHBOARDS = {
        User.Role.SISWA: "accounts:dashboard_siswa",
        User.Role.GURU: "accounts:dashboard_guru",
        User.Role.PUSTAKAWAN: "accounts:dashboard_pustakawan",
    }

    def setUp(self):
        self.users = {
            role: self.make_user(f"u_{role.lower()}", role)
            for role in self.DASHBOARDS
        }

    def test_anonymous_diarahkan_ke_login(self):
        for name in self.DASHBOARDS.values():
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302)
            self.assertIn(
                reverse("accounts:login"), response["Location"]
            )

    def test_siswa_authorization(self):
        self.client.force_login(self.users[User.Role.SISWA])
        self.assertEqual(
            self.client.get(
                reverse("accounts:dashboard_siswa")
            ).status_code,
            200,
        )
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

    def test_guru_authorization(self):
        self.client.force_login(self.users[User.Role.GURU])
        self.assertEqual(
            self.client.get(
                reverse("accounts:dashboard_guru")
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse("accounts:dashboard_siswa")
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse("accounts:dashboard_pustakawan")
            ).status_code,
            403,
        )

    def test_pustakawan_authorization(self):
        self.client.force_login(self.users[User.Role.PUSTAKAWAN])
        self.assertEqual(
            self.client.get(
                reverse("accounts:dashboard_pustakawan")
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse("accounts:dashboard_siswa")
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse("accounts:dashboard_guru")
            ).status_code,
            403,
        )

    def test_dashboard_sesuai_role_menampilkan_konten_role(self):
        self.client.force_login(self.users[User.Role.PUSTAKAWAN])
        response = self.client.get(
            reverse("accounts:dashboard_pustakawan")
        )
        self.assertContains(response, "Dashboard Pustakawan")
        self.assertContains(response, "Administrasi")


class ProfileTests(AuthTestMixin, TestCase):
    def setUp(self):
        self.siswa = self.make_user("siswa1", User.Role.SISWA)
        self.other = self.make_user("siswa2", User.Role.SISWA)

    def test_profile_get_menampilkan_data_sendiri(self):
        self.client.force_login(self.siswa)
        response = self.client.get(reverse("accounts:profile"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.siswa.username)

    def test_profile_update_berhasil(self):
        self.client.force_login(self.siswa)
        response = self.client.post(
            reverse("accounts:profile"),
            {
                "full_name": "Siswa Satu Baru",
                "email": "siswa1@sekolah.id",
                "phone": "08123456789",
            },
        )
        self.assertRedirects(response, reverse("accounts:profile"))
        self.siswa.refresh_from_db()
        self.assertEqual(self.siswa.full_name, "Siswa Satu Baru")
        self.assertEqual(self.siswa.email, "siswa1@sekolah.id")
        self.assertEqual(self.siswa.phone, "08123456789")

    def test_profile_tidak_mengubah_role_dan_akun_lain(self):
        self.client.force_login(self.siswa)
        old_role = self.siswa.role
        self.client.post(
            reverse("accounts:profile"),
            {
                "full_name": "Diubah",
                "email": "",
                "phone": "",
                # Upaya jahat: field di luar form diabaikan.
                "role": User.Role.PUSTAKAWAN,
                "username": "hacker",
            },
        )
        self.siswa.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.siswa.role, old_role)
        self.assertEqual(self.siswa.username, "siswa1")
        self.assertEqual(self.other.full_name, "siswa2 Full")

    def test_profile_anonymous_ditolak(self):
        response = self.client.get(reverse("accounts:profile"))
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            reverse("accounts:profile"), {"full_name": "X"}
        )
        self.assertEqual(response.status_code, 302)


class PasswordChangeTests(AuthTestMixin, TestCase):
    def setUp(self):
        self.make_user("guru1", User.Role.GURU)

    def test_password_change_berhasil_sesi_terjaga(self):
        self.login_as("guru1")
        response = self.client.post(
            reverse("accounts:password_change"),
            {
                "old_password": "secret123",
                "new_password1": "rahasia-baru-456",
                "new_password2": "rahasia-baru-456",
            },
        )
        self.assertRedirects(response, reverse("accounts:profile"))
        # Sesi tidak terputus setelah ganti password.
        self.assertIn("_auth_user_id", self.client.session)
        self.client.post(reverse("accounts:logout"))
        self.assertTrue(
            self.client.login(
                username="guru1", password="rahasia-baru-456"
            )
        )
        self.assertFalse(
            self.client.login(username="guru1", password="secret123")
        )

    def test_password_change_password_lama_salah_ditolak(self):
        self.login_as("guru1")
        response = self.client.post(
            reverse("accounts:password_change"),
            {
                "old_password": "keliru",
                "new_password1": "rahasia-baru-456",
                "new_password2": "rahasia-baru-456",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            self.client.login(username="guru1", password="secret123")
        )

    def test_password_change_anonymous_ditolak(self):
        response = self.client.post(
            reverse("accounts:password_change"),
            {
                "old_password": "secret123",
                "new_password1": "rahasia-baru-456",
                "new_password2": "rahasia-baru-456",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(
            reverse("accounts:login"), response["Location"]
        )
