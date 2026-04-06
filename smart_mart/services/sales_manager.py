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
                customer_phone: str = None) -> Sale:
    """Create a confirmed sale. Raises InsufficientStockError before any DB write if stock is insufficient."""
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
        # Auto-generate invoice number from shop settings
        invoice_number = None
        try:
            from ..models.shop_settings import ShopSettings
            settings = ShopSettings.get()
            invoice_number = settings.next_invoice_number()
        except Exception:
            pass

        total_amount = sum(item["unit_price"] * item["quantity"] for item in items)
        sale = Sale(
            user_id=user_id,
            total_amount=total_amount,
            sale_date=datetime.now(timezone.utc),
            invoice_number=invoice_number,
            customer_name=customer_name,
            customer_address=customer_address,
            customer_phone=customer_phone,
        )
        db.session.add(sale)
        db.session.flush()

        for item in items:
            product = products[item["product_id"]]
            qty = item["quantity"]
            unit_price = item["unit_price"]
            db.session.add(SaleItem(
                sale_id=sale.id, product_id=product.id,
                quantity=qty, unit_price=unit_price, subtotal=unit_price * qty,
            ))
            product.quantity -= qty
            db.session.add(StockMovement(
                product_id=product.id, change_amount=-qty, change_type="sale",
                reference_id=sale.id, created_by=user_id, timestamp=datetime.now(timezone.utc),
            ))

        try:
            from . import cash_flow_manager
            cash_flow_manager.record_income(sale)
        except ImportError:
            pass

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return sale


def get_sale(sale_id: int) -> Sale:
    """Return a Sale by ID or raise 404."""
    return db.get_or_404(Sale, sale_id)


def list_sales(filters: dict, page: int = 1, per_page: int = 20) -> list[Sale]:
    """Return paginated sales, optionally filtered by start_date/end_date."""
    stmt = db.select(Sale).order_by(Sale.sale_date.desc())
    conditions = []
    if filters.get("start_date"):
        conditions.append(Sale.sale_date >= filters["start_date"])
    if filters.get("end_date"):
        conditions.append(Sale.sale_date <= filters["end_date"])
    if conditions:
        stmt = stmt.where(and_(*conditions))
    stmt = stmt.limit(per_page).offset((page - 1) * per_page)
    return db.session.execute(stmt).scalars().all()


def delete_sale(sale_id: int) -> None:
    """Delete a sale and reverse all stock changes. Admin only."""
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
    """Generate a professional PDF invoice for a sale using ReportLab."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                     Paragraph, Spacer, HRFlowable)
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT

    sale: Sale = get_sale(sale_id)
    buffer = BytesIO()

    # Load shop settings
    shop_name = "Smart Mart"
    shop_pan = ""
    shop_address = ""
    shop_phone = ""
    shop_email = ""
    footer_note = "Thank you for shopping with us!"
    try:
        from ..models.shop_settings import ShopSettings
        s = ShopSettings.get()
        shop_name = s.shop_name or shop_name
        shop_pan = s.pan_number or ""
        shop_address = s.address or ""
        shop_phone = s.phone or ""
        shop_email = s.email or ""
        footer_note = s.footer_note or footer_note
    except Exception:
        pass

    invoice_num = sale.invoice_number or f"INV-{sale.id:05d}"
    customer_name = sale.customer_name or "Walk-in Customer"
    customer_address = sale.customer_address or ""
    customer_phone = sale.customer_phone or ""

    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            leftMargin=1.8*cm, rightMargin=1.8*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)

    accent = colors.HexColor("#6366f1")
    dark = colors.HexColor("#0f172a")
    light_gray = colors.HexColor("#f8fafc")
    mid_gray = colors.HexColor("#64748b")

    def ps(name, **kw):
        return ParagraphStyle(name, **kw)

    story = []

    # Header
    header_data = [[
        Paragraph(f"<b>{shop_name}</b>", ps("h1", fontSize=20, fontName="Helvetica-Bold", textColor=dark)),
        Paragraph("<b>INVOICE</b>", ps("h2", fontSize=20, fontName="Helvetica-Bold", textColor=accent, alignment=TA_RIGHT)),
    ]]
    ht = Table(header_data, colWidths=[9*cm, 9*cm])
    ht.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "MIDDLE")]))
    story.append(ht)

    shop_sub_parts = []
    if shop_address:
        shop_sub_parts.append(shop_address)
    if shop_phone:
        shop_sub_parts.append(f"Tel: {shop_phone}")
    if shop_email:
        shop_sub_parts.append(shop_email)
    if shop_pan:
        shop_sub_parts.append(f"PAN: {shop_pan}")
    story.append(Paragraph("  |  ".join(shop_sub_parts) if shop_sub_parts else "Inventory & Sales Management System",
                            ps("sub", fontSize=8, fontName="Helvetica", textColor=mid_gray)))
    story.append(HRFlowable(width="100%", thickness=2, color=accent, spaceAfter=8))

    # Meta
    sale_date_str = sale.sale_date.strftime("%B %d, %Y  %H:%M") if sale.sale_date else "N/A"
    served_by = sale.user.username if sale.user else "—"
    lbl = ps("lbl", fontSize=8, fontName="Helvetica-Bold", textColor=mid_gray)
    val = ps("val", fontSize=9, fontName="Helvetica", textColor=dark)

    bill_to_lines = [Paragraph("BILL TO", lbl), Paragraph(f"<b>{customer_name}</b>", val)]
    if customer_address:
        bill_to_lines.append(Paragraph(customer_address, val))
    if customer_phone:
        bill_to_lines.append(Paragraph(f"Tel: {customer_phone}", val))

    meta_data = [[
        [Paragraph("INVOICE DETAILS", lbl),
         Paragraph(f"Invoice No: <b>{invoice_num}</b>", val),
         Paragraph(f"Date: <b>{sale_date_str}</b>", val),
         Paragraph(f"Served by: <b>{served_by}</b>", val)],
        bill_to_lines,
    ]]
    mt = Table(meta_data, colWidths=[9*cm, 9*cm])
    mt.setStyle(TableStyle([
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("BACKGROUND", (0,0), (-1,-1), light_gray),
        ("BOX", (0,0), (0,0), 0.5, colors.HexColor("#e2e8f0")),
        ("BOX", (1,0), (1,0), 0.5, colors.HexColor("#e2e8f0")),
        ("LEFTPADDING", (0,0), (-1,-1), 10),
        ("TOPPADDING", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
    ]))
    story.append(mt)
    story.append(Spacer(1, 0.5*cm))

    # Items table
    col_widths = [1*cm, 6.5*cm, 2*cm, 2.5*cm, 2.5*cm, 3.5*cm]
    th = ps("th", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white)
    th_r = ps("thr", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)
    th_c = ps("thc", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_CENTER)

    table_data = [[
        Paragraph("#", th_c),
        Paragraph("PRODUCT", th),
        Paragraph("QTY", th_c),
        Paragraph("UNIT PRICE", th_r),
        Paragraph("DISCOUNT", th_r),
        Paragraph("SUBTOTAL", th_r),
    ]]

    for i, si in enumerate(sale.items, 1):
        name = si.product.name if si.product else f"Product #{si.product_id}"
        sku = si.product.sku if si.product else ""
        cat = si.product.category if si.product else ""
        product_cell = [
            Paragraph(f"<b>{name}</b>", ps("pn", fontSize=9, fontName="Helvetica-Bold", textColor=dark)),
            Paragraph(f"SKU: {sku}  |  {cat}", ps("ps", fontSize=7, fontName="Helvetica", textColor=mid_gray)),
        ]
        table_data.append([
            Paragraph(str(i), ps("n", fontSize=9, fontName="Helvetica", textColor=mid_gray, alignment=TA_CENTER)),
            product_cell,
            Paragraph(str(si.quantity), ps("q", fontSize=9, fontName="Helvetica", textColor=dark, alignment=TA_CENTER)),
            Paragraph(f"NPR {float(si.unit_price):,.2f}", ps("up", fontSize=9, fontName="Helvetica", textColor=dark, alignment=TA_RIGHT)),
            Paragraph("—", ps("d", fontSize=9, fontName="Helvetica", textColor=mid_gray, alignment=TA_RIGHT)),
            Paragraph(f"NPR {float(si.subtotal):,.2f}", ps("st", fontSize=9, fontName="Helvetica-Bold", textColor=dark, alignment=TA_RIGHT)),
        ])

    it = Table(table_data, colWidths=col_widths, repeatRows=1)
    it.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), dark),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,0), 8),
        ("TOPPADDING", (0,0), (-1,0), 8),
        ("BOTTOMPADDING", (0,0), (-1,0), 8),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f8fafc")]),
        ("FONTSIZE", (0,1), (-1,-1), 9),
        ("TOPPADDING", (0,1), (-1,-1), 6),
        ("BOTTOMPADDING", (0,1), (-1,-1), 6),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
        ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#e2e8f0")),
        ("LINEBELOW", (0,0), (-1,0), 1.5, accent),
    ]))
    story.append(it)
    story.append(Spacer(1, 0.4*cm))

    # Totals
    subtotal = float(sale.total_amount)
    totals_data = [
        ["", "Subtotal:", f"NPR {subtotal:,.2f}"],
        ["", "Discount:", "NPR 0.00"],
        ["", "Tax (0%):", "NPR 0.00"],
    ]
    tt = Table(totals_data, colWidths=[10*cm, 4*cm, 4*cm])
    tt.setStyle(TableStyle([
        ("ALIGN", (1,0), (-1,-1), "RIGHT"),
        ("FONTNAME", (0,0), (-1,-1), "Helvetica"),
        ("FONTSIZE", (0,0), (-1,-1), 9),
        ("TEXTCOLOR", (1,0), (-1,-1), mid_gray),
        ("TOPPADDING", (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
    ]))
    story.append(tt)

    grand_data = [["", "TOTAL AMOUNT:", f"NPR {subtotal:,.2f}"]]
    gt = Table(grand_data, colWidths=[10*cm, 4*cm, 4*cm])
    gt.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), dark),
        ("TEXTCOLOR", (0,0), (-1,-1), colors.white),
        ("FONTNAME", (0,0), (-1,-1), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 11),
        ("ALIGN", (1,0), (-1,-1), "RIGHT"),
        ("TOPPADDING", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("RIGHTPADDING", (0,0), (-1,-1), 8),
    ]))
    story.append(gt)
    story.append(Spacer(1, 0.6*cm))

    # Footer
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e2e8f0"), spaceAfter=6))
    footer = ps("footer", fontSize=8, fontName="Helvetica", textColor=mid_gray, alignment=TA_CENTER)
    story.append(Paragraph(footer_note, footer))
    story.append(Paragraph("This is a computer-generated invoice and does not require a signature.", footer))

    doc.build(story)
    return buffer.getvalue()
