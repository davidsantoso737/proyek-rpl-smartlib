from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.notifications.models import Notification

User = get_user_model()


class NotificationModelTests(TestCase):
    def test_13_reference_id_bukan_fk(self):
        field = Notification._meta.get_field("reference_id")
        self.assertEqual(field.get_internal_type(), "BigIntegerField")
        self.assertFalse(field.is_relation)
        # reference_type + reference_id menunjuk entity apa pun tanpa FK.
        siswa = User.objects.create_user(
            username="siswa1",
            password="secret123",
            role=User.Role.SISWA,
            full_name="Siswa Satu",
            nis="10001",
        )
        notif = Notification.objects.create(
            user=siswa,
            type="LOAN",
            title="Peminjaman disetujui",
            message="Silakan ambil buku.",
            reference_type="loan",
            reference_id=999,
        )
        notif.refresh_from_db()
        self.assertEqual(notif.reference_type, "loan")
        self.assertEqual(notif.reference_id, 999)

    def test_notification_user_on_delete_cascade(self):
        self.assertEqual(
            Notification._meta.get_field("user").remote_field.on_delete.__name__,
            "CASCADE",
        )
