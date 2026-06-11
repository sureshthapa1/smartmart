# smart_mart/utils/vat_invoice.py
# ==================================
# Generates a Nepal-compliant VAT invoice PDF.
# Uses ReportLab (already in requirements.txt).
#
# Usage:
#   from smart_mart.utils.vat_invoice import generate_vat_invoice
#   pdf_bytes = generate_vat_invoice(sale, shop_settings)
#   return send_file(BytesIO(pdf_bytes), mimetype="application/pdf",
#                    download_name=f"invoice_{sale.id}.pdf")

from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph,
    Spacer, HRFlowable,
)
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
import datetime
from smart_mart.utils.nepali_date import ad_to_bs, _NEPALI_MONTHS


VAT_RATE = 0.13   # Nepal 13% VAT


def generate_vat_invoice(sale, shop) -> bytes:
    """
    sale   — your Sale ORM object; must have:
               sale.id, sale.created_at, sale.items (list),
               sale.customer (optional), sale.payment_method,
               sale.discount_amount (default 0)
    shop   — your ShopSettings ORM object; must have:
               shop.name, shop.address, shop.phone,
               shop.pan_number, shop.vat_number (optional)

    Returns raw PDF bytes.
    """
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        rightMargin=15*mm, leftMargin=15*mm,
        topMargin=15*mm, bottomMargin=15*mm,
    )

    styles = getSampleStyleSheet()
    GOLD   = colors.HexColor("#C9991A")
    GREEN  = colors.HexColor("#1A5C3A")
    LIGHT  = colors.HexColor("#F5F5F0")

    h1 = ParagraphStyle("h1", fontSize=18, textColor=GREEN,
                         alignment=TA_CENTER, spaceAfter=2)
    h2 = ParagraphStyle("h2", fontSize=11, textColor=GREEN,
                         alignment=TA_CENTER, spaceAfter=1)
    small_c = ParagraphStyle("sc", fontSize=8, alignment=TA_CENTER,
                              textColor=colors.grey, spaceAfter=2)
    label = ParagraphStyle("lbl", fontSize=8, textColor=colors.grey)
    value = ParagraphStyle("val", fontSize=9, textColor=colors.black)
    right = ParagraphStyle("right", fontSize=9, alignment=TA_RIGHT)

    # ── BS date ───────────────────────────────────────────────────────────
    sale_date = sale.created_at
    if isinstance(sale_date, datetime.datetime):
        sale_date = sale_date.date()
    bs_y, bs_m, bs_d = ad_to_bs(sale_date)
    bs_str = f"{bs_d} {_NEPALI_MONTHS[bs_m-1]} {bs_y} BS"
    ad_str = sale_date.strftime("%d %b %Y")

    story = []

    # ── Header ────────────────────────────────────────────────────────────
    story.append(Paragraph(getattr(shop, "name", "GoldKernel Dry Fruits & Treats"), h1))
    story.append(Paragraph(getattr(shop, "address", "Dhangadhi, Sudurpashchim, Nepal"), h2))
    story.append(Paragraph(
        f"Phone: {getattr(shop, 'phone', '')}  |  PAN: {getattr(shop, 'pan_number', 'N/A')}",
        small_c,
    ))
    if getattr(shop, "vat_number", None):
        story.append(Paragraph(f"VAT Reg. No.: {shop.vat_number}", small_c))

    story.append(HRFlowable(width="100%", thickness=1, color=GOLD, spaceAfter=4))
    story.append(Paragraph("<b>TAX INVOICE</b>", ParagraphStyle(
        "ti", fontSize=13, alignment=TA_CENTER, textColor=GOLD, spaceAfter=6)))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey, spaceAfter=6))

    # ── Meta row ──────────────────────────────────────────────────────────
    customer_name = "Walk-in Customer"
    customer_phone = ""
    if getattr(sale, "customer", None):
        customer_name  = sale.customer.name  or customer_name
        customer_phone = getattr(sale.customer, "phone", "") or ""

    meta = [
        [Paragraph(f"<b>Invoice #:</b>", label),  Paragraph(str(sale.id), value),
         Paragraph(f"<b>Date (AD):</b>", label),  Paragraph(ad_str, value)],
        [Paragraph(f"<b>Customer:</b>", label),    Paragraph(customer_name, value),
         Paragraph(f"<b>Date (BS):</b>", label),  Paragraph(bs_str, value)],
        [Paragraph(f"<b>Phone:</b>", label),       Paragraph(customer_phone, value),
         Paragraph(f"<b>Payment:</b>", label),
         Paragraph(getattr(sale, "payment_method", "Cash"), value)],
    ]
    meta_table = Table(meta, colWidths=[28*mm, 62*mm, 28*mm, 62*mm])
    meta_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 4*mm))

    # ── Items table ───────────────────────────────────────────────────────
    header = ["#", "Product", "Qty", "Unit Price (NPR)", "Amount (NPR)"]
    rows   = [header]

    subtotal = 0.0
    for i, item in enumerate(sale.items, 1):
        qty   = getattr(item, "quantity", 1)
        price = float(getattr(item, "unit_price", 0))
        amt   = qty * price
        subtotal += amt
        rows.append([
            str(i),
            getattr(item, "product_name", getattr(item, "product", {}).name if hasattr(item, "product") else ""),
            str(qty),
            f"{price:,.2f}",
            f"{amt:,.2f}",
        ])

    discount = float(getattr(sale, "discount_amount", 0) or 0)
    taxable  = subtotal - discount
    vat_amt  = taxable * VAT_RATE
    grand    = taxable + vat_amt

    rows += [
        ["", "", "", "Subtotal:",        f"{subtotal:,.2f}"],
        ["", "", "", "Discount:",        f"({discount:,.2f})"],
        ["", "", "", "Taxable Amount:",  f"{taxable:,.2f}"],
        ["", "", "", f"VAT (13%):",      f"{vat_amt:,.2f}"],
        ["", "", "", "TOTAL (NPR):",     f"{grand:,.2f}"],
    ]

    col_w = [10*mm, 70*mm, 15*mm, 35*mm, 30*mm]
    item_table = Table(rows, colWidths=col_w, repeatRows=1)
    item_table.setStyle(TableStyle([
        # Header
        ("BACKGROUND",   (0, 0), (-1, 0), GREEN),
        ("TEXTCOLOR",    (0, 0), (-1, 0), colors.white),
        ("FONTSIZE",     (0, 0), (-1, 0), 9),
        ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN",        (2, 0), (-1, -1), "RIGHT"),
        ("ALIGN",        (0, 0), (0, -1), "CENTER"),
        # Body
        ("FONTSIZE",     (0, 1), (-1, -1), 8),
        ("TOPPADDING",   (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS",(0, 1), (-1, -6), [colors.white, LIGHT]),
        ("LINEBELOW",    (0, 0), (-1, 0), 0.5, GREEN),
        # Summary rows
        ("FONTNAME",     (3, -5), (-1, -5), "Helvetica-Bold"),
        ("LINEABOVE",    (3, -5), (-1, -5), 0.5, colors.lightgrey),
        ("BACKGROUND",   (0, -1), (-1, -1), GOLD),
        ("TEXTCOLOR",    (0, -1), (-1, -1), colors.white),
        ("FONTNAME",     (3, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE",     (0, -1), (-1, -1), 9),
    ]))
    story.append(item_table)
    story.append(Spacer(1, 6*mm))

    # ── Footer ────────────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey, spaceAfter=4))
    story.append(Paragraph(
        "This is a computer-generated invoice. Thank you for shopping at GoldKernel!",
        small_c,
    ))
    story.append(Paragraph(
        "Premium Himalayan Dry Fruits &amp; Wellness Products",
        ParagraphStyle("tag", fontSize=7, alignment=TA_CENTER,
                       textColor=GOLD, spaceAfter=0),
    ))

    doc.build(story)
    return buf.getvalue()
