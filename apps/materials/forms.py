from django import forms

INPUT_CLASS = (
    "w-full border border-slate-300 rounded-xl px-3.5 py-2.5 text-sm "
    "bg-white placeholder:text-slate-400 "
    "focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
)


class MaterialUploadForm(forms.Form):
    title = forms.CharField(label="Judul", max_length=255)
    description = forms.CharField(
        label="Deskripsi", widget=forms.Textarea(attrs={"rows": 3}),
        required=False,
    )
    file = forms.FileField(
        label="File (pdf/doc/docx/ppt/pptx/xls/xlsx/txt, maks 20 MB)",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({"class": INPUT_CLASS})


class ReasonForm(forms.Form):
    reason = forms.CharField(
        label="Alasan", widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({"class": INPUT_CLASS})
