from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.materials.models import TeachingMaterial

User = get_user_model()


class TeachingMaterialModelTests(TestCase):
    def test_12_teachingmaterial_tidak_memiliki_mime_type(self):
        field_names = [f.name for f in TeachingMaterial._meta.get_fields()]
        self.assertNotIn("mime_type", field_names)
        # Pastikan juga tidak ada duplikat file_path/file_name/file_size.
        self.assertEqual(field_names.count("file_path"), 1)
        self.assertEqual(field_names.count("file_name"), 1)
        self.assertEqual(field_names.count("file_size"), 1)

    def test_teachingmaterial_terkait_user_protect(self):
        guru = User.objects.create_user(
            username="guru1",
            password="secret123",
            role=User.Role.GURU,
            full_name="Guru Satu",
            nip="20001",
        )
        material = TeachingMaterial.objects.create(
            uploaded_by=guru,
            title="Materi IPA",
            file_path="/files/ipa.pdf",
            file_name="ipa.pdf",
            file_type="pdf",
            file_size=2048,
            status="MENUNGGU",
        )
        self.assertEqual(material.uploaded_by, guru)
