from django import forms

from .services import REPORTS, category_choices

INPUT_CLASS = (
    "w-full min-w-0 border border-slate-300 rounded-xl px-3.5 py-2.5 text-sm bg-white "
    "focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
)


class ReportForm(forms.Form):
    report_type = forms.ChoiceField(
        label="Jenis laporan",
        choices=[(key, spec["title"]) for key, spec in REPORTS.items()],
    )
    status = forms.ChoiceField(label="Status", required=False)
    category = forms.ChoiceField(label="Kategori", required=False)
    role = forms.ChoiceField(
        label="Role",
        required=False,
        choices=[
            ("", "Semua role"),
            ("SISWA", "Siswa"),
            ("GURU", "Guru"),
            ("PUSTAKAWAN", "Pustakawan"),
        ],
    )
    active = forms.ChoiceField(
        label="Keaktifan",
        required=False,
        choices=[("", "Semua"), ("yes", "Aktif"), ("no", "Nonaktif")],
    )
    date_from = forms.DateField(
        label="Dari tanggal", required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        label="Sampai tanggal", required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    q = forms.CharField(label="Kata kunci", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        spec = REPORTS.get(
            (self.data.get("report_type") if self.data else None)
            or "peminjaman"
        )
        statuses = [("", "Semua status")] + [
            (status, status) for status in spec["statuses"]
        ]
        self.fields["status"].choices = statuses
        self.fields["category"].choices = [("", "Semua kategori")] + category_choices()
        for field in self.fields.values():
            field.widget.attrs.update({"class": INPUT_CLASS})
