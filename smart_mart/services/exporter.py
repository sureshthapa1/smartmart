"""Exporter service — PDF and CSV report exports."""

from __future__ import annotations

import csv
import io
from io import BytesIO


def export_report_csv(report_data: list[dict], columns: list[str]) -> str:
    """Return CSV string for report_data with given column headers."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(report_data)
    return output.getvalue()


def export_report_pdf(report_data: list[dict], title: str, columns: list[str]) -> bytes:
    """Return PDF bytes for a tabular report using ReportLab."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                            leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)
    styles = getSampleStyleSheet()
    story = [Paragraph(title, styles["Title"]), Spacer(1, 0.4*cm)]

    table_data = [columns]
    for row in report_data:
        table_data.append([str(row.get(col, "")) for col in columns])

    col_width = (landscape(A4)[0] - 3*cm) / max(len(columns), 1)
    table = Table(table_data, colWidths=[col_width] * len(columns))
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#343a40")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
    ]))
    story.append(table)
    doc.build(story)
    return buffer.getvalue()


def export_invoice_pdf(sale) -> bytes:
    """Return PDF invoice bytes for a Sale object."""
    from smart_mart.services.sales_manager import generate_invoice_pdf
    return generate_invoice_pdf(sale.id)
