"""Sales management service — create sales, invoices, and stock reduction."""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO

from sqlalchemy import and_

from ..extensions import db
from ..models.product import Product
from ..models.sale import Sale, SaleItem
from ..models.stock_movement import StockMovement


class InsufficientStockError(ValueError):
    """Raised when a sale item quantity exceeds available product stock."""


def create_sale(items: list[dict], user_id: int,
                customer_name: str = None, customer_address: str = None,
                customer_phone: str = None, payment_mode: str = "cash",
                discount_amount: float = 0, discount_note: str = None,
                wallet_redeem_points: int = 0) -> Sale:
    """Create a confirmed sale."""
    products: dict[int, Product] = {}
    for item in items:
        pid = item["product_id"]
        if pid not in products:
            product = db.session.get(Product, pid)
            if product is None:
                raise ValueError(f"Product with id {pid} not found.")
            products[pid] = product
        product = products[pid]
        if item["quantity"] > product.quantity:
            raise InsufficientStockError(
                f"Insufficient stock for '{product.name}': "
                f"requested {item['quantity']}, available {product.quantity}."
            )

    try:
        invoice_number = None
        try:
            from ..models.shop_settings import ShopSettings
            settings = ShopSettings.get()
            invoice_number = settings.next_invoice_number()
        except Exception:
            pass

        gross_total_amount = sum(item["unit_price"] * item["quantity"] for item in items)
        total_amount = max(0, gross_total_amount - (discount_amount or 0))

        redeemed_points = 0
        try:
            from . import loyalty_wallet_service
            wallet = loyalty_wallet_service.get_or_create_wallet(customer_name, customer_phone)
            redeem_preview = loyalty_wallet_service.preview_redeem(
                wallet, int(wallet_redeem_points or 0), total_amount
            )
            redeemed_points = int(redeem_preview["redeemed_points"])
            total_amount = redeem_preview["payable_total"]
            if redeemed_points > 0 and not discount_note:
                discount_note = "Loyalty points redeemed"
        except Exception:
            wallet = None
        sale = Sale(
            user_id=user_id,
            total_amount=total_amount,
            sale_date=datetime.now(timezone.utc),
            invoice_number=invoice_number,
            customer_name=customer_name,
            customer_address=customer_address,
            customer_phone=customer_phone,
            payment_mode=payment_mode or "cash",
            discount_amount=discount_amount or 0,
            discount_note=discount_note,
        )
        db.session.add(sale)
        db.session.flush()

        for item in items:
            product = products[item["product_id"]]
            qty = item["quantity"]
            unit_price = item["unit_price"]
            db.session.add(SaleItem(
                sale_id=sale.id, product_id=product.id,
                quantity=qty, unit_price=unit_price,
                cost_price=product.cost_price,   # snapshot cost at time of sale
                subtotal=unit_price * qty,
            ))
            product.quantity -= qty
            db.session.add(StockMovement(
                product_id=product.id, change_amount=-qty, change_type="sale",
                reference_id=sale.id, created_by=user_id,
                timestamp=datetime.now(timezone.utc),
            ))

        try:
            from . import cash_flow_manager
            cash_flow_manager.record_income(sale)
        except ImportError:
            pass

        # Save customer for autofill on next visit
        try:
            from ..models.customer import Customer
            if customer_name and customer_name.strip().lower() != "walk-in customer":
                Customer.upsert(customer_name, customer_phone, customer_address)
                db.session.flush()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Customer upsert failed: %s", e)

        try:
            from . import loyalty_wallet_service
            loyalty_wallet_service.apply_sale_points(
                wallet=wallet,
                sale_id=sale.id,
                final_amount_paid=total_amount,
                redeemed_points=redeemed_points,
            )
        except Exception:
            pass

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    # Audit log (outside transaction)
    try:
        from . import audit_service
        audit_service.log("create", "Sale", sale.id,
                          f"Invoice {sale.invoice_number or sale.id}",
                          changes={"total_amount": [None, str(sale.total_amount)],
                                   "payment_mode": [None, sale.payment_mode]})
        db.session.commit()
    except Exception:
        pass

    return sale


def get_sale(sale_id: int) -> Sale:
    return db.get_or_404(Sale, sale_id)


def list_sales(filters: dict, page: int = 1, per_page: int = 20) -> list[Sale]:
    from sqlalchemy import or_
    stmt = db.select(Sale).order_by(Sale.sale_date.desc())
    conditions = []
    if filters.get("start_date"):
        conditions.append(Sale.sale_date >= filters["start_date"])
    if filters.get("end_date"):
        conditions.append(Sale.sale_date <= filters["end_date"])
    if filters.get("payment_mode"):
        conditions.append(Sale.payment_mode == filters["payment_mode"])
    if filters.get("search"):
        term = f"%{filters['search'].lower()}%"
        conditions.append(or_(
            db.func.lower(Sale.customer_name).like(term),
            db.func.lower(Sale.customer_phone).like(term),
            db.func.lower(Sale.invoice_number).like(term),
        ))
    if conditions:
        stmt = stmt.where(and_(*conditions))
    stmt = stmt.limit(per_page).offset((page - 1) * per_page)
    return db.session.execute(stmt).scalars().all()


def delete_sale(sale_id: int) -> None:
    sale = get_sale(sale_id)
    try:
        for item in sale.items:
            product = db.session.get(Product, item.product_id)
            if product:
                product.quantity += item.quantity
                db.session.add(StockMovement(
                    product_id=product.id,
                    change_amount=item.quantity,
                    change_type="adjustment_in",
                    reference_id=sale.id,
                    note=f"Sale #{sale.id} deleted/reversed",
                    created_by=sale.user_id,
                    timestamp=datetime.now(timezone.utc),
                ))
        db.session.delete(sale)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def generate_invoice_pdf(sale_id: int) -> bytes:
    """Generate a professional Tax Invoice PDF using ReportLab."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                     Paragraph, Spacer, HRFlowable)
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

    sale: Sale = get_sale(sale_id)
    buffer = BytesIO()

    # ── Shop settings ─────────────────────────────────────────────────────
    shop_name = "Smart Mart"
    shop_pan = shop_address = shop_phone = shop_email = ""
    footer_note = "Thank you for your business!"
    shop_logo_path = None
    try:
        from ..models.shop_settings import ShopSettings
        s = ShopSettings.get()
        shop_name = s.shop_name or shop_name
        shop_pan = s.pan_number or ""
        shop_address = s.address or ""
        shop_phone = s.phone or ""
        shop_email = s.email or ""
        footer_note = s.footer_note or footer_note
        if s.logo_data:
            # Use base64 data from DB (works on Render)
            shop_logo_path = s.logo_data  # will be handled as data URI
        elif s.logo_filename:
            import os
            from flask import current_app
            logo_path = os.path.join(current_app.static_folder, "uploads", "shop", s.logo_filename)
            if os.path.exists(logo_path):
                shop_logo_path = logo_path
    except Exception:
        pass

    invoice_num = sale.invoice_number or f"INV-{sale.id:05d}"
    customer_name = sale.customer_name or "Walk-in Customer"
    customer_address = sale.customer_address or ""
    customer_phone = sale.customer_phone or ""
    pm = (sale.payment_mode or "cash").upper()
    pm_labels = {"CASH": "Cash", "QR": "QR / Digital Wallet",
                 "CARD": "Card", "CREDIT": "Credit / Udharo", "OTHER": "Other"}
    payment_label = pm_labels.get(pm, pm)

    # ── Document setup ────────────────────────────────────────────────────
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm
    )
    W = A4[0] - 3.6*cm

    # Colors
    navy = colors.HexColor("#1e3a5f")
    blue = colors.HexColor("#2563eb")
    light_blue = colors.HexColor("#eff6ff")
    slate = colors.HexColor("#64748b")
    border = colors.HexColor("#cbd5e1")
    green = colors.HexColor("#16a34a")
    light_green = colors.HexColor("#f0fdf4")
    row_alt = colors.HexColor("#f8fafc")

    def S(name, **kw):
        return ParagraphStyle(name, **kw)

    story = []

    # ── HEADER: two-column layout ─────────────────────────────────────────
    # Left column: logo + shop name + details
    # Right column: TAX INVOICE + invoice number + date
    left_col = []

    # Add logo if available
    if shop_logo_path:
        try:
            from reportlab.platypus import Image as RLImage
            import io as _io
            if shop_logo_path.startswith("data:"):
                # base64 data URI — decode to bytes
                import base64
                header, b64data = shop_logo_path.split(",", 1)
                img_bytes = base64.b64decode(b64data)
                logo_img = RLImage(_io.BytesIO(img_bytes), width=3*cm, height=1.5*cm, kind='proportional')
            else:
                logo_img = RLImage(shop_logo_path, width=3*cm, height=1.5*cm, kind='proportional')
            left_col.append(logo_img)
            left_col.append(Spacer(1, 4))
        except Exception:
            pass

    left_col.append(
        Paragraph(f"<b>{shop_name}</b>",
                  S("sn", fontSize=16, fontName="Helvetica-Bold",
                    textColor=navy, spaceAfter=4, leading=20))
    )
    contact_parts = []
    if shop_address:
        contact_parts.append(shop_address)
    if shop_phone:
        contact_parts.append(f"Tel: {shop_phone}")
    if shop_email:
        contact_parts.append(shop_email)
    if shop_pan:
        contact_parts.append(f"PAN No: {shop_pan}")
    try:
        from ..models.shop_settings import ShopSettings as _SS
        _sv = _SS.get()
        if _sv.vat_enabled and _sv.vat_number:
            contact_parts.append(f"VAT No: {_sv.vat_number}")
    except Exception:
        pass
    for part in contact_parts:
        left_col.append(Paragraph(part, S(f"sc_{part[:5]}", fontSize=8,
                                          fontName="Helvetica", textColor=slate,
                                          spaceAfter=2, leading=11)))

    right_col = [
        Paragraph("TAX INVOICE",
                  S("ti", fontSize=16, fontName="Helvetica-Bold",
                    textColor=blue, alignment=TA_RIGHT, spaceAfter=5, leading=20)),
        Paragraph(f"<b>No: {invoice_num}</b>",
                  S("inv", fontSize=10, fontName="Helvetica-Bold",
                    textColor=navy, alignment=TA_RIGHT, spaceAfter=3, leading=13)),
        Paragraph((sale.sale_date + __import__('datetime').timedelta(hours=5, minutes=45)).strftime("%d %B %Y  %I:%M %p") if sale.sale_date else "",
                  S("dt", fontSize=8, fontName="Helvetica",
                    textColor=slate, alignment=TA_RIGHT, leading=11)),
    ]

    hdr = Table([[left_col, right_col]], colWidths=[W * 0.55, W * 0.45])
    hdr.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), light_blue),
        ("LEFTPADDING", (0, 0), (0, 0), 14),
        ("RIGHTPADDING", (0, 0), (0, 0), 10),
        ("LEFTPADDING", (1, 0), (1, 0), 10),
        ("RIGHTPADDING", (1, 0), (1, 0), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
        ("LINEBELOW", (0, 0), (-1, -1), 2.5, blue),
    ]))
    story.append(hdr)
    story.append(Spacer(1, 0.4 * cm))

    # ── BILL TO + PAYMENT DETAILS ─────────────────────────────────────────
    lbl_s = S("lbl", fontSize=7, fontName="Helvetica-Bold", textColor=slate,
               spaceAfter=3, leading=10)
    val_s = S("val", fontSize=9, fontName="Helvetica", textColor=navy,
               spaceAfter=2, leading=12)
    val_b = S("valb", fontSize=9, fontName="Helvetica-Bold", textColor=navy,
               spaceAfter=2, leading=12)

    bill_items = [Paragraph("BILL TO", lbl_s),
                  Paragraph(customer_name, val_b)]
    if customer_phone:
        bill_items.append(Paragraph(f"Tel: {customer_phone}", val_s))
    if customer_address:
        bill_items.append(Paragraph(customer_address, val_s))

    pay_items = [
        Paragraph("PAYMENT DETAILS", lbl_s),
        Paragraph(f"Mode:  <b>{payment_label}</b>", val_s),
        Paragraph("Status:  <b>Paid</b>", val_s),
        Paragraph(f"Served by:  <b>{sale.user.username if sale.user else '—'}</b>", val_s),
    ]

    meta = Table([[bill_items, pay_items]], colWidths=[W * 0.52, W * 0.48])
    meta.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (0, 0), 0.5, border),
        ("BOX", (1, 0), (1, 0), 0.5, border),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(meta)
    story.append(Spacer(1, 0.4 * cm))

    # ── ITEMS TABLE ───────────────────────────────────────────────────────
    col_w = [0.7*cm, 5.8*cm, 1.4*cm, 1.4*cm, 2.4*cm, 1.8*cm, 2.5*cm]
    th = S("th", fontSize=8, fontName="Helvetica-Bold",
            textColor=colors.white, alignment=TA_CENTER)
    th_r = S("thr", fontSize=8, fontName="Helvetica-Bold",
              textColor=colors.white, alignment=TA_RIGHT)

    rows = [[
        Paragraph("#", th),
        Paragraph("DESCRIPTION", th),
        Paragraph("UNIT", th),
        Paragraph("QTY", th),
        Paragraph("RATE (NPR)", th_r),
        Paragraph("DISC", th_r),
        Paragraph("AMOUNT (NPR)", th_r),
    ]]

    for i, si in enumerate(sale.items, 1):
        name = si.product.name if si.product else f"Product #{si.product_id}"
        sku = si.product.sku if si.product else ""
        unit = (si.product.unit if si.product else "pcs") or "pcs"
        desc = [
            Paragraph(f"<b>{name}</b>",
                      S(f"pn{i}", fontSize=9, fontName="Helvetica-Bold",
                        textColor=navy, leading=12)),
            Paragraph(f"SKU: {sku}",
                      S(f"sk{i}", fontSize=7, fontName="Helvetica",
                        textColor=slate, leading=10)),
        ]
        rows.append([
            Paragraph(str(i), S(f"n{i}", fontSize=9, fontName="Helvetica",
                                 textColor=slate, alignment=TA_CENTER)),
            desc,
            Paragraph(unit, S(f"u{i}", fontSize=9, fontName="Helvetica",
                               textColor=slate, alignment=TA_CENTER)),
            Paragraph(str(si.quantity), S(f"q{i}", fontSize=9, fontName="Helvetica",
                                           textColor=navy, alignment=TA_CENTER)),
            Paragraph(f"{float(si.unit_price):,.2f}",
                      S(f"up{i}", fontSize=9, fontName="Helvetica",
                        textColor=navy, alignment=TA_RIGHT)),
            Paragraph("—", S(f"d{i}", fontSize=9, fontName="Helvetica",
                              textColor=slate, alignment=TA_RIGHT)),
            Paragraph(f"{float(si.subtotal):,.2f}",
                      S(f"st{i}", fontSize=9, fontName="Helvetica-Bold",
                        textColor=navy, alignment=TA_RIGHT)),
        ])

    row_bgs = [("BACKGROUND", (0, i), (-1, i),
                colors.white if i % 2 == 1 else row_alt)
               for i in range(1, len(rows))]

    it = Table(rows, colWidths=col_w, repeatRows=1)
    it.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), navy),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 7),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
        ("LINEBELOW", (0, 0), (-1, 0), 1.5, blue),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("TOPPADDING", (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.3, border),
    ] + row_bgs))
    story.append(it)
    story.append(Spacer(1, 0.3 * cm))

    # ── TOTALS ────────────────────────────────────────────────────────────
    gross_total = sum(float(si.subtotal) for si in sale.items)
    discount = float(sale.discount_amount or 0)
    final_total = float(sale.total_amount)

    # VAT calculation
    vat_enabled = False
    vat_rate = 0.0
    vat_number = ""
    try:
        from ..models.shop_settings import ShopSettings
        _s = ShopSettings.get()
        vat_enabled = bool(_s.vat_enabled)
        vat_rate = float(_s.vat_rate or 0)
        vat_number = _s.vat_number or ""
    except Exception:
        pass

    taxable_amount = final_total
    vat_amount = 0.0
    if vat_enabled and vat_rate > 0:
        # VAT is inclusive in the total (extract it)
        vat_amount = round(taxable_amount * vat_rate / (100 + vat_rate), 2)
        taxable_amount = round(final_total - vat_amount, 2)

    tot_s = S("ts", fontSize=9, fontName="Helvetica", textColor=slate)
    tot_v = S("tv", fontSize=9, fontName="Helvetica", textColor=navy, alignment=TA_RIGHT)
    tot_red = S("tr", fontSize=9, fontName="Helvetica", textColor=colors.HexColor("#dc2626"), alignment=TA_RIGHT)

    totals_rows = [
        [Paragraph("Sub Total:", tot_s), Paragraph(f"NPR {gross_total:,.2f}", tot_v)],
    ]
    if discount > 0:
        disc_label = f"Discount ({sale.discount_note}):" if sale.discount_note else "Discount:"
        totals_rows.append([Paragraph(disc_label, tot_s), Paragraph(f"- NPR {discount:,.2f}", tot_red)])
    if vat_enabled and vat_rate > 0:
        totals_rows.append([Paragraph(f"Taxable Amount:", tot_s), Paragraph(f"NPR {taxable_amount:,.2f}", tot_v)])
        totals_rows.append([Paragraph(f"VAT ({vat_rate:.0f}%):", tot_s), Paragraph(f"NPR {vat_amount:,.2f}", tot_v)])
    else:
        totals_rows.append([Paragraph("Tax (0%):", tot_s), Paragraph("NPR 0.00", tot_v)])

    totals = Table(totals_rows, colWidths=[W * 0.8, W * 0.2])
    totals.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(totals)

    grand = Table([[
        Paragraph("TOTAL AMOUNT",
                  S("gt", fontSize=11, fontName="Helvetica-Bold",
                    textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph(f"NPR {final_total:,.2f}",
                  S("gv", fontSize=12, fontName="Helvetica-Bold",
                    textColor=colors.white, alignment=TA_RIGHT)),
    ]], colWidths=[W * 0.75, W * 0.25])
    grand.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), navy),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(grand)
    story.append(Spacer(1, 0.4 * cm))

    # ── PAYMENT CONFIRMATION ──────────────────────────────────────────────
    pay_conf = Table([[
        Paragraph(f"Payment Received  —  <b>{payment_label}</b>",
                  S("pc", fontSize=9, fontName="Helvetica-Bold", textColor=green)),
        Paragraph(f"NPR {final_total:,.2f}",
                  S("pv", fontSize=11, fontName="Helvetica-Bold",
                    textColor=green, alignment=TA_RIGHT)),
    ]], colWidths=[W * 0.72, W * 0.28])
    pay_conf.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), light_green),
        ("BOX", (0, 0), (-1, -1), 1, green),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(pay_conf)
    story.append(Spacer(1, 0.5 * cm))

    # ── FOOTER ────────────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=border, spaceAfter=5))
    story.append(Paragraph(footer_note,
                            S("fn", fontSize=8, fontName="Helvetica",
                              textColor=slate, alignment=TA_CENTER)))
    story.append(Paragraph(
        "This is a computer-generated Tax Invoice. No signature required.",
        S("fn2", fontSize=7, fontName="Helvetica",
          textColor=colors.HexColor("#94a3b8"), alignment=TA_CENTER)
    ))

    doc.build(story)
    return buffer.getvalue()
