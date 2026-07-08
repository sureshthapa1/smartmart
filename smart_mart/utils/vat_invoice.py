from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO


def generate_vat_invoice(sale, shop_settings):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from .nepali_date import ad_to_bs, format_bs

    forest = colors.HexColor("#1A5C3A")
    gold = colors.HexColor("#C9991A")
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="HeaderWhite", parent=styles["Heading1"], textColor=colors.white))
    styles.add(ParagraphStyle(name="SmallMuted", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#4B5563")))

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
    )
    width = A4[0] - 3 * cm

    shop_name = (
        getattr(shop_settings, "name", None)
        or getattr(shop_settings, "shop_name", None)
        or "GoldKernel Dry Fruits & Treats"
    )
    address = getattr(shop_settings, "address", None) or "Dhangadhi, Sudurpashchim, Nepal"
    phone = getattr(shop_settings, "phone", None) or ""
    pan = getattr(shop_settings, "pan_number", None) or "TBD"
    vat = getattr(shop_settings, "vat_number", None) or "TBD"

    sale_dt = sale.sale_date
    ad_date = sale_dt.date() if hasattr(sale_dt, "date") else sale_dt
    bs_date = format_bs(*ad_to_bs(ad_date))
    payment_method = getattr(sale, "payment_method", None) or getattr(sale, "payment_mode", None) or "cash"

    story = []
    header = Table([[
        Paragraph(f"<b>{shop_name}</b><br/>{address}<br/>Phone: {phone}<br/>PAN: {pan} | VAT: {vat}", styles["HeaderWhite"]),
        Paragraph("<b>VAT INVOICE</b>", styles["HeaderWhite"]),
    ]], colWidths=[width * 0.65, width * 0.35])
    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), forest),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
        ("LINEBELOW", (0, 0), (-1, -1), 3, gold),
    ]))
    story.append(header)
    story.append(Spacer(1, 12))

    meta = [
        ["Invoice No.", sale.invoice_number or f"INV-{sale.id:05d}", "Customer", sale.customer_name or "Walk-in Customer"],
        ["AD Date", ad_date.strftime("%Y-%m-%d"), "Phone", sale.customer_phone or ""],
        ["BS Date", bs_date, "Payment", payment_method.replace("_", " ").title()],
    ]
    meta_table = Table(meta, colWidths=[width * 0.18, width * 0.32, width * 0.18, width * 0.32])
    meta_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D1D5DB")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F3F4F6")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#F3F4F6")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 14))

    rows = [["#", "Product Name", "Quantity (g)", "Unit Price (NPR)", "Amount (NPR)"]]
    subtotal = 0.0
    for idx, item in enumerate(sale.items, 1):
        amount = float(item.subtotal or 0)
        subtotal += amount
        rows.append([
            idx,
            item.product.name if item.product else f"Product #{item.product_id}",
            f"{float(item.quantity):,.0f}",
            f"{float(item.unit_price):,.2f}",
            f"{amount:,.2f}",
        ])

    item_table = Table(rows, colWidths=[width * 0.07, width * 0.43, width * 0.17, width * 0.16, width * 0.17])
    item_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), forest),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D1D5DB")),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(item_table)
    story.append(Spacer(1, 12))

    discount = float(sale.discount_amount or 0)
    taxable = max(0.0, subtotal - discount)
    vat_amount = (Decimal(str(taxable)) * Decimal('0.13')).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    grand_total = Decimal(str(taxable)) + vat_amount
    totals = [
        ["Subtotal", f"NPR {subtotal:,.2f}"],
        ["Discount", f"NPR {discount:,.2f}"],
        ["Taxable Amount", f"NPR {taxable:,.2f}"],
        ["VAT 13%", f"NPR {vat_amount:,.2f}"],
        ["GRAND TOTAL", f"NPR {grand_total:,.2f}"],
    ]
    totals_table = Table(totals, colWidths=[width * 0.75, width * 0.25])
    totals_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), gold),
        ("TEXTCOLOR", (0, -1), (-1, -1), colors.white),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(totals_table)
    story.append(Spacer(1, 18))
    story.append(Paragraph("Thank you for shopping at GoldKernel!", styles["Normal"]))

    doc.build(story)
    return buffer.getvalue()
