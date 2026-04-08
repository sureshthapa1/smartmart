"""Settings blueprint — shop configuration (admin only)."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from ...models.shop_settings import ShopSettings
from ...extensions import db
from ...services.decorators import admin_required

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")


@settings_bp.route("/", methods=["GET", "POST"])
@admin_required
def index():
    s = ShopSettings.get()
    if request.method == "POST":
        s.shop_name = request.form.get("shop_name", "").strip() or "Smart Mart"
        s.pan_number = request.form.get("pan_number", "").strip() or None
        s.address = request.form.get("address", "").strip() or None
        s.phone = request.form.get("phone", "").strip() or None
        s.email = request.form.get("email", "").strip() or None
        s.website = request.form.get("website", "").strip() or None
        s.invoice_prefix = request.form.get("invoice_prefix", "INV").strip() or "INV"
        s.footer_note = request.form.get("footer_note", "").strip() or "Thank you for shopping with us!"
        s.vat_enabled = request.form.get("vat_enabled") == "on"
        s.vat_rate = float(request.form.get("vat_rate", "13") or 13)
        s.vat_number = request.form.get("vat_number", "").strip() or None
        s.currency_symbol = request.form.get("currency_symbol", "NPR").strip() or "NPR"
        s.low_stock_threshold = int(request.form.get("low_stock_threshold", "10") or 10)
        db.session.commit()
        flash("Settings saved successfully.", "success")
        return redirect(url_for("settings.index"))
    return render_template("settings/index.html", s=s)
