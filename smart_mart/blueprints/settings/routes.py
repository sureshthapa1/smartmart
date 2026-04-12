"""Settings blueprint — shop configuration (admin only)."""

import os
import uuid

from flask import Blueprint, flash, redirect, render_template, request, url_for, current_app
from ...models.shop_settings import ShopSettings
from ...extensions import db
from ...services.decorators import admin_required

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")

ALLOWED_IMG = {"jpg", "jpeg", "png", "gif", "webp"}


def _save_logo(file) -> tuple[str | None, str | None]:
    """Save logo to filesystem AND return base64 data URI for DB storage."""
    if not file or file.filename == "":
        return None, None
    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_IMG:
        return None, None
    import base64
    file_bytes = file.read()
    # Save to filesystem (local dev)
    filename = f"shop_logo_{uuid.uuid4().hex[:8]}.{ext}"
    try:
        upload_dir = os.path.join(current_app.static_folder, "uploads", "shop")
        os.makedirs(upload_dir, exist_ok=True)
        with open(os.path.join(upload_dir, filename), "wb") as f:
            f.write(file_bytes)
    except Exception:
        pass
    # Also encode as base64 for DB (works on Render)
    mime = f"image/{ext}" if ext != "jpg" else "image/jpeg"
    b64 = base64.b64encode(file_bytes).decode("utf-8")
    logo_data = f"data:{mime};base64,{b64}"
    return filename, logo_data


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
        # Loyalty rates
        try:
            s.loyalty_points_per_rupee = float(request.form.get("loyalty_points_per_rupee", "0.01") or 0.01)
            s.loyalty_rupee_per_point = float(request.form.get("loyalty_rupee_per_point", "1.00") or 1.00)
        except (ValueError, TypeError):
            pass

        # Logo upload
        logo_file = request.files.get("logo")
        new_filename, new_logo_data = _save_logo(logo_file)
        if new_filename:
            # Delete old filesystem logo
            if s.logo_filename:
                try:
                    old_path = os.path.join(current_app.static_folder, "uploads", "shop", s.logo_filename)
                    if os.path.exists(old_path):
                        os.remove(old_path)
                except Exception:
                    pass
            s.logo_filename = new_filename
            s.logo_data = new_logo_data  # store in DB for Render persistence
        if request.form.get("remove_logo") == "1":
            if s.logo_filename:
                try:
                    old_path = os.path.join(current_app.static_folder, "uploads", "shop", s.logo_filename)
                    if os.path.exists(old_path):
                        os.remove(old_path)
                except Exception:
                    pass
            s.logo_filename = None
            s.logo_data = None

        db.session.commit()
        flash("Settings saved successfully.", "success")
        return redirect(url_for("settings.index"))
    return render_template("settings/index.html", s=s)
