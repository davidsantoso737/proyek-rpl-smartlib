from urllib.parse import urlsplit

from django.db import migrations, models


KNOWN_PROVIDER_NAMES = {
    "integrate.api.nvidia.com": "NVIDIA",
    "generativelanguage.googleapis.com": "Google Gemini",
    "api.juanrouter.com": "JuanRouter",
    "api.openai.com": "OpenAI",
}


def set_provider_names(apps, schema_editor):
    AIConfiguration = apps.get_model("ai", "AIConfiguration")
    for row in AIConfiguration.objects.all():
        host = (urlsplit(row.base_url or "").hostname or "").lower()
        name = KNOWN_PROVIDER_NAMES.get(host) or host
        if not name:
            name = f"Provider {row.pk}"
        row.name = name[:100]
        row.save(update_fields=["name"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("ai", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="aiconfiguration",
            name="name",
            field=models.CharField(default="", max_length=100),
            preserve_default=False,
        ),
        migrations.RunPython(set_provider_names, noop),
        migrations.AlterField(
            model_name="aiconfiguration",
            name="name",
            field=models.CharField(max_length=100),
        ),
    ]
