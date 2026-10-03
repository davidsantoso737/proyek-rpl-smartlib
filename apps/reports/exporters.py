"""Export PDF (reportlab) dan Excel (openpyxl) dari baris laporan."""

import io
from datetime import datetime


def build_pdf(title, columns, rows):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        title=title,
        leftMargin=24,
        rightMargin=24,
    )
    data = [columns] + [[str(cell) for cell in row] for row in rows]
    if not rows:
        data.append(["(tidak ada data)"] + [""] * (len(columns) - 1))
    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e40af")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                 [colors.white, colors.HexColor("#f1f5f9")]),
            ]
        )
    )
    doc.build([table])
    buffer.seek(0)
    return buffer.getvalue()


def build_xlsx(title, columns, rows):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title[:31]
    header_fill = PatternFill("solid", fgColor="1E40AF")
    header_font = Font(color="FFFFFF", bold=True)
    for col, name in enumerate(columns, start=1):
        cell = sheet.cell(row=1, column=col, value=name)
        cell.fill = header_fill
        cell.font = header_font
    for r, row in enumerate(rows, start=2):
        for col, value in enumerate(row, start=1):
            sheet.cell(row=r, column=col, value=str(value))
    for col in sheet.columns:
        sheet.column_dimensions[col[0].column_letter].width = max(
            12, min(40, max(len(str(c.value or "")) for c in col) + 2)
        )
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


def export_filename(title, ext):
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    safe = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in title
    )[:40]
    return f"{safe}-{stamp}.{ext}"
