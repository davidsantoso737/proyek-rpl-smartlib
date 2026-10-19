from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class PopupTests(TestCase):
    def _make_user(self, username="siswa1", role=None, **extra):
        role = role or User.Role.SISWA
        defaults = {"full_name": f"{username} Full", "nis": "90001"}
        defaults.update(extra)
        user = User(username=username, role=role, **defaults)
        user.set_password("secret123")
        user.full_clean()
        user.save()
        return user

    def test_popup_muncul_untuk_pengguna_login(self):
        self.client.force_login(self._make_user())
        for url in (
            reverse("accounts:dashboard"),
            reverse("catalog:catalog"),
            reverse("circulation:loan_list"),
        ):
            with self.subTest(url=url):
                response = self.client.get(url, follow=True)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'id="ai-fab"')
                self.assertContains(response, 'id="ai-popup-panel"')
                self.assertContains(response, 'id="ai-popup-form"')
                self.assertContains(response, 'maxlength="500"')
                self.assertContains(response, reverse("ai:assistant_ask"))

    def test_popup_tidak_muncul_di_halaman_login(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="ai-fab"')
        self.assertNotContains(response, 'id="ai-popup-panel"')

    def test_tautan_nav_ai_dihapus_endpoint_tetap_ada(self):
        self.client.force_login(self._make_user())
        response = self.client.get(
            reverse("accounts:dashboard"), follow=True
        )
        # Tautan navigasi ke halaman AI dihapus; endpoint fetch popup
        # (/ai/asisten/tanya/) tetap dipertahankan.
        self.assertNotContains(response, 'href="/ai/asisten/"')
        # Endpoint tetap hidup dan butuh auth + CSRF-safe POST.
        ask_url = reverse("ai:assistant_ask")
        self.client.logout()
        anonymous = self.client.post(ask_url, {"question": "Halo"})
        self.assertEqual(anonymous.status_code, 302)
        self.client.force_login(self._make_user(username="siswa9", nis="90009"))
        logged = self.client.post(ask_url, {"question": "Halo"})
        self.assertEqual(logged.status_code, 200)
        self.assertIn("answer", logged.json())

    def test_halaman_ai_lama_tidak_error(self):
        self.client.force_login(self._make_user())
        response = self.client.get(reverse("ai:assistant"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="chat-form"')

    def test_pustakawan_tanpa_akses_ai(self):
        pus = self._make_user(
            username="pus1", role=User.Role.PUSTAKAWAN, nis=None
        )
        self.client.force_login(pus)
        dashboard = self.client.get(
            reverse("accounts:dashboard"), follow=True
        )
        self.assertNotContains(dashboard, 'id="ai-fab"')
        self.assertNotContains(dashboard, 'id="ai-popup-panel"')
        self.assertEqual(
            self.client.get(reverse("ai:assistant")).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                reverse("ai:assistant_ask"), {"question": "Halo"}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse("ai:recommendations")).status_code, 403
        )

    def test_guru_mendapat_popup(self):
        guru = self._make_user(
            username="guru1", role=User.Role.GURU, nis=None, nip="20001"
        )
        self.client.force_login(guru)
        response = self.client.get(
            reverse("accounts:dashboard"), follow=True
        )
        self.assertContains(response, 'id="ai-fab"')
        self.assertContains(response, 'id="ai-popup-panel"')

    def test_css_terkompilasi_memuat_class_popup(self):
        """Regresi: ikon tak terlihat bila utilitas positioning belum dibuild."""
        from pathlib import Path

        from django.conf import settings

        css = (
            Path(settings.BASE_DIR) / "static" / "css" / "tailwind.css"
        ).read_text(encoding="utf-8")
        for selector in (
            ".bottom-5",
            ".bottom-24",
            ".z-40",
            ".h-14",
            ".max-w-sm",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, css)
