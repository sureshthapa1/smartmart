from io import BytesIO

from flask import Blueprint, Response, render_template, request
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet

from ...extensions import db
from ...models.customer import Customer
from ...models.shop_settings import ShopSettings
from ...services.decorators import login_required

loyalty_bp = Blueprint("loyalty", __name__, url_prefix="/loyalty")


@loyalty_bp.route("/")
@login_required
def index():
    customers = db.session.execute(
        db.select(Customer).order_by(Customer.total_spent.desc().nullslast(), Customer.name).limit(100)
    ).scalars().all()
    return render_template("loyalty/index.html", customers=customers)


@loyalty_bp.route("/<int:customer_id>/card")
@login_required
def card(customer_id):
    customer = db.get_or_404(Customer, customer_id)
    shop = ShopSettings.get()
    qr_data = request.host_url.rstrip("/") + f"/loyalty/{customer.id}/card"
    qr_image_b64 = _qr_b64(qr_data)
    return render_template("loyalty/card.html", customer=customer, shop=shop, qr_data=qr_data, qr_image_b64=qr_image_b64)


@loyalty_bp.route("/<int:customer_id>/card.pdf")
@login_required
def card_pdf(customer_id):
    customer = db.get_or_404(Customer, customer_id)
    shop = ShopSettings.get()
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    tier = (customer.loyalty_tier or "silver").title()
    shop_name = shop.name or shop.shop_name or "GoldKernel Dry Fruits & Treats"
    table = Table([
        [Paragraph(f"<b>{shop_name}</b>", styles["Heading1"])],
        [Paragraph(f"Loyalty Card for {customer.name}", styles["Heading2"])],
        [f"Tier: {tier}"],
        [f"Points: {int(customer.loyalty_points or 0)}"],
        [f"Total Spent: NPR {float(customer.total_spent or 0):,.2f}"],
    ])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1A5C3A")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#C9991A")),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    doc.build([table, Spacer(1, 12), Paragraph("Thank you for shopping at GoldKernel!", styles["Normal"])])
    return Response(
        buffer.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=loyalty_card_{customer.id}.pdf"},
    )


def _qr_b64(data):
    import base64
    import io
    import qrcode

    qr = qrcode.QRCode(version=1, box_size=5, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#1A5C3A", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
