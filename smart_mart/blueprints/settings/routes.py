"""Settings blueprint — shop configuration (admin only)."""

import os
import uuid

from flask import Blueprint, flash, redirect, render_template, request, url_for, current_app
from ...models.shop_settings import ShopSettings
from ...extensions import db
from ...services.decorators import admin_required

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")

ALLOWED_IMG = {"jpg", "jpeg", "png", "gif", "webp"}


def _save_logo(file) -> str | None:
    if not file or file.filename == "":
        return None
    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_IMG:
        return None
    filename = f"shop_logo_{uuid.uuid4().hex[:8]}.{ext}"
    upload_dir = os.path.join(current_app.static_folder, "uploads", "shop")
    os.makedirs(upload_dir, exist_ok=True)
    file.save(os.path.join(upload_dir, filename))
    return filename


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

        # Logo upload
        logo_file = request.files.get("logo")
        new_logo = _save_logo(logo_file)
        if new_logo:
            # Delete old logo
            if s.logo_filename:
                try:
                    old_path = os.path.join(current_app.static_folder, "uploads", "shop", s.logo_filename)
                    if os.path.exists(old_path):
                        os.remove(old_path)
                except Exception:
                    pass
            s.logo_filename = new_logo
        if request.form.get("remove_logo") == "1":
            if s.logo_filename:
                try:
                    old_path = os.path.join(current_app.static_folder, "uploads", "shop", s.logo_filename)
                    if os.path.exists(old_path):
                        os.remove(old_path)
                except Exception:
                    pass
            s.logo_filename = None

        db.session.commit()
        flash("Settings saved successfully.", "success")
        return redirect(url_for("settings.index"))
    return render_template("settings/index.html", s=s)
