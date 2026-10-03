from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.shortcuts import render

from apps.accounts.authorization import role_required

from .exporters import build_pdf, build_xlsx, export_filename
from .forms import ReportForm
from .services import REPORTS

User = get_user_model()


def _generate(report_type, filters):
    spec = REPORTS.get(report_type)
    if spec is None:
        return None, [], []
    return spec["title"], spec["columns"], spec["build"](filters)


@role_required(User.Role.PUSTAKAWAN)
def report_view(request):
    """Pilih jenis + filter + pratinjau (FR-90 – FR-93). Hanya Pustakawan."""
    params = request.GET if request.GET.get("report_type") else None
    form = ReportForm(params)
    title, columns, rows, active_type = "", [], [], "peminjaman"
    if params and form.is_valid():
        active_type = form.cleaned_data["report_type"]
        title, columns, rows = _generate(
            active_type, form.cleaned_data
        )
        export = request.GET.get("export")
        if export == "pdf":
            response = HttpResponse(
                build_pdf(title, columns, rows),
                content_type="application/pdf",
            )
            response["Content-Disposition"] = (
                f'attachment; filename="{export_filename(title, "pdf")}"'
            )
            return response
        if export == "xlsx":
            response = HttpResponse(
                build_xlsx(title, columns, rows),
                content_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
            )
            response["Content-Disposition"] = (
                f'attachment; filename="{export_filename(title, "xlsx")}"'
            )
            return response
    return render(
        request,
        "reports/report.html",
        {
            "form": form,
            "title": title,
            "columns": columns,
            "rows": rows,
            "generated": params is not None and form.is_valid(),
            "active_type": active_type,
            "querystring": request.GET.urlencode(),
        },
    )
