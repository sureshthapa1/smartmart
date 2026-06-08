"""Customer storefront routes for GoldKernel Dry Fruits."""
from __future__ import annotations

import re
import time
import uuid
from functools import wraps
from urllib.parse import urlparse

from flask import (
    Blueprint, Response, jsonify, redirect, render_template,
    request, session, url_for, flash, g
)
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ...extensions import db, limiter
from ...models.product import Product
from ...models.online_order import OnlineOrder, OnlineOrderItem
from ...models.customer import Customer
from ...models.shop_settings import ShopSettings
from ...models.customer_account import CustomerAccount
from ...models.ecommerce import StockReservation, EcommercePayment, SyncLog
from ...services.ecommerce_sync import (
    create_order as svc_create_order,
    available_quantity,
    expire_old_reservations,
    EcommerceSyncError,
)
from ...services.customer_auth import (
    get_current_customer, login_customer,
    logout_customer, register, authenticate,
)

store_bp = Blueprint("store", __name__, url_prefix="/store")

# ── Constants ─────────────────────────────────────────────────────────────────
FREE_DELIVERY_THRESHOLD = 2000.0
DELIVERY_CHARGE        = 100.0
MAX_QTY_PER_ITEM       = 50
MIN_ORDER_AMOUNT       = 200.0
NEPAL_PHONE_RE         = re.compile(r"^(97|98)\d{8}$")
VALID_PAYMENT_METHODS  = {"cod", "esewa", "khalti", "ime_pay"}

# ── Reservation expiry cooldown ───────────────────────────────────────────────
_last_expiry_run: float = 0.0
_EXPIRY_COOLDOWN = 60.0  # seconds


def _maybe_expire_reservations() -> None:
    global _last_expiry_run
    now = time.monotonic()
    if now - _last_expiry_run > _EXPIRY_COOLDOWN:
        _last_expiry_run = now
        expire_old_reservations()


# ── Slug helper ───────────────────────────────────────────────────────────────
def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_-]+', '-', text)
    return text[:120]

# ── Helpers ───────────────────────────────────────────────────────────────────

def _settings():
    try:
        return ShopSettings.get()
    except Exception:
        return None


def _get_categories():
    rows = db.session.execute(
        db.select(Product.category)
        .where(Product.is_active.isnot(False), Product.quantity > 0)
        .distinct()
        .order_by(Product.category)
    ).scalars().all()
    return [c for c in rows if c]


def _cart() -> dict:
    return session.get("cart", {})


def _save_cart(cart: dict):
    session["cart"] = cart
    session.modified = True


def _calc_delivery(subtotal: float) -> float:
    """Free delivery above threshold, NPR 100 otherwise."""
    return 0.0 if subtotal >= FREE_DELIVERY_THRESHOLD else DELIVERY_CHARGE


def _safe_next(next_url: str | None) -> str:
    """Block open-redirect: only allow relative paths within this app."""
    if not next_url:
        return url_for("store.home")
    parsed = urlparse(next_url)
    # Reject anything with a scheme or netloc (external URL)
    if parsed.scheme or parsed.netloc:
        return url_for("store.home")
    # Must start with /store/ to stay in the storefront
    if not parsed.path.startswith("/store"):
        return url_for("store.home")
    return next_url


def _validate_phone(phone: str) -> str | None:
    """Return cleaned phone or None if invalid Nepal number."""
    cleaned = re.sub(r"[\s\-\(\)+]", "", phone)
    # Strip country code if present
    if cleaned.startswith("977"):
        cleaned = cleaned[3:]
    if not NEPAL_PHONE_RE.match(cleaned):
        return None
    return cleaned


# Inject customer into g on every store request
@store_bp.before_request
def _load_customer():
    g.customer = get_current_customer()


def customer_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not g.customer:
            flash("Please log in to access that page.", "warning")
            return redirect(url_for("store.login", next=request.path))
        return f(*args, **kwargs)
    return decorated


# ── Home / Product Listing ────────────────────────────────────────────────────

@store_bp.route("/")
@limiter.limit("120/minute")
def home():
    settings  = _settings()
    q         = request.args.get("q", "").strip()
    category  = request.args.get("category", "").strip()
    sort      = request.args.get("sort", "name")

    expire_old_reservations()

    stmt = db.select(Product).where(Product.is_active.isnot(False), Product.quantity > 0)
    if q:
        term = f"%{q.lower()}%"
        stmt = stmt.where(db.or_(
            func.lower(Product.name).like(term),
            func.lower(func.coalesce(Product.category, "")).like(term),
            func.lower(func.coalesce(Product.sku, "")).like(term),
        ))
    if category:
        stmt = stmt.where(
            func.lower(func.coalesce(Product.category, "")) == category.lower()
        )

    sort_map = {
        "name":       Product.name.asc(),
        "price_asc":  Product.selling_price.asc(),
        "price_desc": Product.selling_price.desc(),
        "newest":     Product.created_at.desc(),
    }
    stmt = stmt.order_by(sort_map.get(sort, Product.name.asc()))
    products   = db.session.execute(stmt).scalars().all()
    categories = _get_categories()

    # Featured products: is_featured first, then fall back to best sellers
    featured_products = db.session.execute(
        db.select(Product)
        .where(Product.is_active.isnot(False), Product.quantity > 0,
               Product.is_featured == True)
        .order_by(Product.name)
        .limit(6)
    ).scalars().all()

    if not featured_products:
        # Fall back to best-selling
        popular_ids = set()
        try:
            rows = db.session.execute(
                db.select(OnlineOrderItem.product_id, func.sum(OnlineOrderItem.quantity).label("sold"))
                .group_by(OnlineOrderItem.product_id)
                .order_by(func.sum(OnlineOrderItem.quantity).desc())
                .limit(6)
            ).all()
            popular_ids = {r.product_id for r in rows}
        except Exception:
            pass
        featured_products = [p for p in products if p.id in popular_ids][:6]

    return render_template(
        "store/home.html",
        products=products,
        categories=categories,
        featured_products=featured_products,
        settings=settings,
        q=q,
        selected_category=category,
        sort=sort,
        customer=g.customer,
        free_delivery_threshold=FREE_DELIVERY_THRESHOLD,
    )

# ── Product Detail ────────────────────────────────────────────────────────────

@store_bp.route("/product/<int:product_id>")
@limiter.limit("120/minute")
def product_detail(product_id):
    product = db.get_or_404(Product, product_id)
    if not getattr(product, "is_active", True):
        flash("This product is no longer available.", "warning")
        return redirect(url_for("store.home"))

    settings = _settings()
    _maybe_expire_reservations()
    avail = available_quantity(product)

    # Auto-populate slug if missing (Improvement 13)
    if product and not product.slug:
        candidate = _slugify(product.name)
        existing_slug = db.session.execute(
            db.select(Product).where(Product.slug == candidate, Product.id != product.id)
        ).scalar_one_or_none()
        if not existing_slug:
            product.slug = candidate
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

    related = db.session.execute(
        db.select(Product)
        .where(
            Product.is_active.isnot(False),
            Product.quantity > 0,
            Product.id != product.id,
            func.lower(func.coalesce(Product.category, "")) ==
            func.lower(func.coalesce(product.category, ""))
        )
        .limit(4)
    ).scalars().all()

    cart_qty = _cart().get(str(product_id), 0)

    return render_template(
        "store/product_detail.html",
        product=product,
        avail=avail,
        cart_qty=cart_qty,
        related=related,
        settings=settings,
        customer=g.customer,
        max_qty=MAX_QTY_PER_ITEM,
    )


# ── Cart ──────────────────────────────────────────────────────────────────────

@store_bp.route("/cart")
def cart():
    settings   = _settings()
    raw_cart   = _cart()
    items      = []
    subtotal   = 0.0

    for pid_str, qty in raw_cart.items():
        try:
            product = db.session.get(Product, int(pid_str))
        except Exception:
            continue
        if not product or not getattr(product, "is_active", True):
            continue
        avail      = available_quantity(product)
        line_qty   = min(qty, avail, MAX_QTY_PER_ITEM)
        if line_qty < 1:
            continue
        line_total  = float(product.selling_price) * line_qty
        subtotal   += line_total
        items.append({"product": product, "qty": line_qty,
                       "line_total": line_total, "avail": avail})

    delivery    = _calc_delivery(subtotal)
    grand_total = subtotal + delivery

    return render_template(
        "store/cart.html",
        items=items, subtotal=subtotal, delivery=delivery,
        grand_total=grand_total, settings=settings, customer=g.customer,
        free_delivery_threshold=FREE_DELIVERY_THRESHOLD,
        raw_cart_count=len(raw_cart),
    )


@store_bp.route("/cart/add", methods=["POST"])
@limiter.limit("60/minute")
def cart_add():
    try:
        product_id = int(request.form.get("product_id", 0))
        qty        = max(1, min(int(request.form.get("qty", 1)), MAX_QTY_PER_ITEM))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Invalid input"}), 400

    product = db.session.get(Product, product_id)
    if not product or not getattr(product, "is_active", True):
        return jsonify({"ok": False, "error": "Product not found"}), 404

    expire_old_reservations()
    avail       = available_quantity(product)
    cart        = _cart()
    current_qty = cart.get(str(product_id), 0)
    new_qty     = min(current_qty + qty, avail, MAX_QTY_PER_ITEM)

    if new_qty < 1:
        return jsonify({"ok": False, "error": "Out of stock"}), 409
    if new_qty == current_qty:
        return jsonify({"ok": False,
                        "error": f"Maximum available quantity is {avail}"}), 409

    cart[str(product_id)] = new_qty
    _save_cart(cart)
    return jsonify({
        "ok": True,
        "cart_count": sum(cart.values()),
        "product_name": product.name,
        "qty_in_cart": new_qty,
        "capped": new_qty < current_qty + qty,
    })


@store_bp.route("/cart/update", methods=["POST"])
@limiter.limit("30/minute")
def cart_update():
    product_id = str(request.form.get("product_id", ""))
    try:
        qty = int(request.form.get("qty", 0))
    except (ValueError, TypeError):
        qty = 0

    cart = _cart()
    if qty <= 0:
        cart.pop(product_id, None)
    else:
        try:
            product = db.session.get(Product, int(product_id))
            if product:
                avail = available_quantity(product)
                cart[product_id] = min(qty, avail, MAX_QTY_PER_ITEM)
        except Exception:
            pass
    _save_cart(cart)
    return redirect(url_for("store.cart"))


@store_bp.route("/cart/remove", methods=["POST"])
@limiter.limit("30/minute")
def cart_remove():
    product_id = str(request.form.get("product_id", ""))
    cart = _cart()
    cart.pop(product_id, None)
    _save_cart(cart)
    return redirect(url_for("store.cart"))


@store_bp.route("/cart/count")
def cart_count():
    return jsonify({"count": sum(_cart().values())})


# ── Checkout ──────────────────────────────────────────────────────────────────

@store_bp.route("/checkout", methods=["GET", "POST"])
@limiter.limit("20/minute")
def checkout():
    settings = _settings()
    raw_cart = _cart()
    if not raw_cart:
        flash("Your cart is empty.", "warning")
        return redirect(url_for("store.home"))

    items    = []
    subtotal = 0.0
    _maybe_expire_reservations()

    for pid_str, qty in raw_cart.items():
        try:
            product = db.session.get(Product, int(pid_str))
        except Exception:
            continue
        if not product or not getattr(product, "is_active", True):
            continue
        avail = available_quantity(product)
        if avail < 1:
            continue
        line_qty   = min(qty, avail, MAX_QTY_PER_ITEM)
        line_total = float(product.selling_price) * line_qty
        subtotal  += line_total
        items.append({"product": product, "qty": line_qty, "line_total": line_total})

    if not items:
        flash("Some items in your cart are no longer available.", "warning")
        return redirect(url_for("store.cart"))

    delivery    = _calc_delivery(subtotal)
    grand_total = subtotal + delivery
    cust        = g.customer

    if request.method == "POST":
        name    = request.form.get("name", "").strip()
        phone   = request.form.get("phone", "").strip()
        email   = request.form.get("email", "").strip()
        address = request.form.get("address", "").strip()
        area    = request.form.get("area", "").strip()
        method  = request.form.get("payment_method", "cod")
        if method not in VALID_PAYMENT_METHODS:
            method = "cod"
        notes   = request.form.get("notes", "").strip()

        errors = []
        if not name:
            errors.append("Full name is required.")
        if not phone:
            errors.append("Phone number is required.")
        else:
            clean_phone = _validate_phone(phone)
            if not clean_phone:
                errors.append("Enter a valid Nepal phone number (98XXXXXXXX or 97XXXXXXXX).")
            else:
                phone = clean_phone
        if not address:
            errors.append("Delivery address is required.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "store/checkout.html",
                items=items, subtotal=subtotal, delivery=delivery,
                grand_total=grand_total, settings=settings,
                form_data=request.form, customer=cust,
                free_delivery_threshold=FREE_DELIVERY_THRESHOLD,
            )

        # Minimum order check
        if subtotal < MIN_ORDER_AMOUNT:
            flash(f"Minimum order amount is NPR {MIN_ORDER_AMOUNT:.0f}. Add more items to continue.", "warning")
            return redirect(url_for("store.cart"))

        # Promo code handling
        discount_amount = 0.0
        promo_code = request.form.get("promo_code", "").strip().upper()
        if promo_code:
            try:
                from ...models.promotion import Promotion
                from datetime import date
                promo = db.session.execute(
                    db.select(Promotion).where(
                        Promotion.code == promo_code,
                        Promotion.is_active == True,
                    )
                ).scalar_one_or_none()
                if promo is None:
                    flash(f"Promo code '{promo_code}' is not valid.", "warning")
                elif hasattr(promo, 'end_date') and promo.end_date and promo.end_date < date.today():
                    flash(f"Promo code '{promo_code}' has expired.", "warning")
                elif hasattr(promo, 'discount_pct') and promo.discount_pct:
                    discount_amount = round(float(subtotal) * float(promo.discount_pct) / 100, 2)
                    flash(f"Code '{promo_code}' applied: {promo.discount_pct}% off", "success")
                elif hasattr(promo, 'discount_amount') and promo.discount_amount:
                    discount_amount = min(float(promo.discount_amount), float(subtotal))
                    flash(f"Code '{promo_code}' applied: NPR {discount_amount:.0f} off", "success")
            except Exception:
                pass  # promo system not available

        grand_total = subtotal + delivery - discount_amount

        payload = {
            "customer": {"name": name, "phone": phone, "email": email,
                         "address": address, "area": area},
            "items": [{"product_id": it["product"].id, "quantity": it["qty"],
                       "unit_price": float(it["product"].selling_price)} for it in items],
            "payment": {"method": method, "status": "pending", "provider": method},
            "delivery_charge": delivery,
            "discount_amount": discount_amount,
            "reservation_minutes": 30,
            "notes": notes,
            "order_source": "website",
        }

        try:
            response, _ = svc_create_order(
                payload, idempotency_key=f"store-{uuid.uuid4().hex}"
            )
            order_number = response["order"]["order_number"]

            # Save address back to account on first order
            if cust:
                changed = False
                if not cust.address and address:
                    cust.address = address
                    changed = True
                if not cust.area and area:
                    cust.area = area
                    changed = True
                if changed:
                    db.session.commit()

            # Store order number in session so success page can verify ownership
            session["last_order"] = order_number
            session.modified = True
            session.pop("cart", None)

            payment_redirect = {"esewa": True, "khalti": True, "ime_pay": True}.get(method, False)
            if payment_redirect:
                return redirect(url_for("store.payment_pending", order_number=order_number))
            else:
                return redirect(url_for("store.order_success", order_number=order_number))

        except EcommerceSyncError as exc:
            flash(str(exc), "danger")
            return render_template(
                "store/checkout.html",
                items=items, subtotal=subtotal, delivery=delivery,
                grand_total=grand_total, settings=settings,
                form_data=request.form, customer=cust,
                free_delivery_threshold=FREE_DELIVERY_THRESHOLD,
            )

    # Pre-fill from account
    form_data = {}
    if cust:
        form_data = {"name": cust.name, "phone": cust.phone,
                     "email": cust.email or "", "address": cust.address or "",
                     "area": cust.area or ""}

    return render_template(
        "store/checkout.html",
        items=items, subtotal=subtotal, delivery=delivery,
        grand_total=grand_total, settings=settings,
        form_data=form_data, customer=cust,
        free_delivery_threshold=FREE_DELIVERY_THRESHOLD,
    )


# ── Order Success ─────────────────────────────────────────────────────────────

@store_bp.route("/order/<order_number>/success")
def order_success(order_number):
    settings = _settings()

    # Security: only the session that placed the order can see full details
    last_order = session.get("last_order", "")
    owns_order = (last_order == order_number)

    # Logged-in customer who owns the order by phone also qualifies
    cust = g.customer

    order = db.session.execute(
        db.select(OnlineOrder).where(OnlineOrder.order_number == order_number)
    ).scalar_one_or_none()

    if not order:
        flash("Order not found.", "warning")
        return redirect(url_for("store.home"))

    # If neither the session token nor logged-in account matches, show minimal info
    if cust and order.customer_phone == cust.phone:
        owns_order = True

    if not owns_order:
        # Show safe redirect — don't expose PII
        flash("Order placed successfully! Use your order number to track it.", "success")
        return redirect(url_for("store.track", order_number=order_number))

    return render_template("store/order_success.html", order=order,
                           settings=settings, customer=cust)


# ── Order Tracking ────────────────────────────────────────────────────────────

@store_bp.route("/track", methods=["GET"])
def track():
    settings = _settings()
    order    = None
    order_number = (
        request.args.get("order_number") or ""
    ).strip().upper()

    if order_number:
        order = db.session.execute(
            db.select(OnlineOrder).where(OnlineOrder.order_number == order_number)
        ).scalar_one_or_none()

    return render_template(
        "store/track.html",
        order=order, order_number=order_number,
        settings=settings, customer=g.customer,
    )


# ── Customer Auth ─────────────────────────────────────────────────────────────

@store_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10/minute", methods=["POST"])
def login():
    if g.customer:
        return redirect(url_for("store.my_account"))

    settings  = _settings()
    next_url  = _safe_next(request.args.get("next") or request.form.get("next"))

    if request.method == "POST":
        phone    = request.form.get("phone", "").strip()
        password = request.form.get("password", "")

        account = authenticate(phone, password)
        if account:
            login_customer(account)
            flash(f"Welcome back, {account.name}! 👋", "success")
            return redirect(next_url)
        flash("Incorrect phone number or password.", "danger")

    return render_template("store/login.html", settings=settings,
                           next=next_url, customer=None)


@store_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("5/minute", methods=["POST"])
def register_view():
    if g.customer:
        return redirect(url_for("store.my_account"))

    settings = _settings()

    if request.method == "POST":
        name     = request.form.get("name", "").strip()
        phone    = request.form.get("phone", "").strip()
        email    = request.form.get("email", "").strip()
        address  = request.form.get("address", "").strip()
        area     = request.form.get("area", "").strip()
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm_password", "")

        # Validate phone
        clean_phone = _validate_phone(phone)
        if not clean_phone:
            flash("Enter a valid Nepal phone number (98XXXXXXXX or 97XXXXXXXX).", "danger")
            return render_template("store/register.html", settings=settings,
                                   form_data=request.form, customer=None)

        if password != confirm:
            flash("Passwords do not match.", "danger")
            return render_template("store/register.html", settings=settings,
                                   form_data=request.form, customer=None)

        try:
            account = register(name, clean_phone, password,
                               email=email, address=address, area=area)
            login_customer(account)
            flash(f"Account created! Welcome, {account.name} 🎉", "success")
            return redirect(url_for("store.home"))
        except ValueError as exc:
            flash(str(exc), "danger")
            return render_template("store/register.html", settings=settings,
                                   form_data=request.form, customer=None)

    return render_template("store/register.html", settings=settings,
                           form_data={}, customer=None)


@store_bp.route("/logout")
def logout():
    logout_customer()
    flash("You've been logged out.", "info")
    return redirect(url_for("store.home"))


# ── My Account ────────────────────────────────────────────────────────────────

@store_bp.route("/account")
@customer_login_required
def my_account():
    settings = _settings()
    cust     = g.customer
    page     = max(1, int(request.args.get("page", 1)))
    per_page = 10
    offset   = (page - 1) * per_page
    orders   = db.session.execute(
        db.select(OnlineOrder)
        .where(OnlineOrder.customer_phone == cust.phone)
        .order_by(OnlineOrder.created_at.desc())
        .limit(per_page).offset(offset)
    ).scalars().all()
    total_orders = db.session.execute(
        db.select(func.count(OnlineOrder.id))
        .where(OnlineOrder.customer_phone == cust.phone)
    ).scalar() or 0
    total_pages = (total_orders + per_page - 1) // per_page
    return render_template("store/account.html", settings=settings,
                           customer=cust, orders=orders,
                           page=page, total_pages=total_pages,
                           total_orders=total_orders)


@store_bp.route("/account/update", methods=["POST"])
@customer_login_required
def account_update():
    cust = g.customer
    # Only update fields that were actually submitted with a value
    new_name    = request.form.get("name", "").strip()
    new_email   = request.form.get("email", "").strip()
    new_address = request.form.get("address", "").strip()
    new_area    = request.form.get("area", "").strip()

    if new_name:    cust.name    = new_name
    if new_email:   cust.email   = new_email
    if new_address: cust.address = new_address
    # area can be cleared intentionally — treat empty as clear
    cust.area = new_area if new_area else cust.area

    try:
        db.session.commit()
        flash("Profile updated successfully.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("That email is already in use.", "danger")
    return redirect(url_for("store.my_account"))


@store_bp.route("/account/change-password", methods=["POST"])
@customer_login_required
def account_change_password():
    cust       = g.customer
    current_pw = request.form.get("current_password", "")
    new_pw     = request.form.get("new_password", "")
    confirm_pw = request.form.get("confirm_password", "")

    if not cust.check_password(current_pw):
        flash("Current password is incorrect.", "danger")
    elif len(new_pw) < 6:
        flash("New password must be at least 6 characters.", "danger")
    elif new_pw != confirm_pw:
        flash("Passwords do not match.", "danger")
    else:
        cust.set_password(new_pw)
        db.session.commit()
        flash("Password changed successfully.", "success")
    return redirect(url_for("store.my_account"))


# ── API: live stock check ─────────────────────────────────────────────────────

@store_bp.route("/api/stock/<int:product_id>")
@limiter.limit("60/minute")
def api_stock(product_id):
    product = db.session.get(Product, product_id)
    if not product:
        return jsonify({"ok": False}), 404
    expire_old_reservations()
    avail = available_quantity(product)
    return jsonify({"ok": True, "available": avail,
                    "price": float(product.selling_price)})


# ── Payment Pending (Improvement 1) ──────────────────────────────────────────

@store_bp.route("/order/<order_number>/payment")
def payment_pending(order_number):
    settings = _settings()
    last_order = session.get("last_order", "")
    owns_order = (last_order == order_number)
    cust = g.customer
    order = db.session.execute(
        db.select(OnlineOrder).where(OnlineOrder.order_number == order_number)
    ).scalar_one_or_none()
    if not order:
        flash("Order not found.", "warning")
        return redirect(url_for("store.home"))
    if cust and order.customer_phone == cust.phone:
        owns_order = True
    if not owns_order:
        return redirect(url_for("store.track", order_number=order_number))
    return render_template("store/payment_pending.html", order=order,
                           settings=settings, customer=cust)


# ── Customer Order Cancellation (Improvement 5) ──────────────────────────────

@store_bp.route("/order/<order_number>/cancel", methods=["POST"])
@customer_login_required
@limiter.limit("5/minute")
def cancel_order(order_number):
    cust = g.customer
    order = db.session.execute(
        db.select(OnlineOrder).where(OnlineOrder.order_number == order_number)
    ).scalar_one_or_none()
    if not order:
        flash("Order not found.", "danger")
        return redirect(url_for("store.my_account"))
    if order.customer_phone != cust.phone:
        flash("You don't have permission to cancel this order.", "danger")
        return redirect(url_for("store.my_account"))
    if order.status not in ("pending",):
        flash("Only pending orders can be cancelled. Please call us for other requests.", "warning")
        return redirect(url_for("store.my_account"))
    try:
        from ...services.ecommerce_sync import apply_order_status
        apply_order_status(order, "cancelled", note="Cancelled by customer", actor=cust.name)
        db.session.commit()
        flash(f"Order {order.order_number} has been cancelled.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Could not cancel order: {exc}", "danger")
    return redirect(url_for("store.my_account"))


# ── Back-in-Stock Notification (Improvement 9) ───────────────────────────────

@store_bp.route("/product/<int:product_id>/notify", methods=["POST"])
@limiter.limit("5/minute")
def notify_stock(product_id):
    product = db.session.get(Product, product_id)
    if not product:
        return jsonify({"ok": False}), 404
    phone = request.form.get("phone", "").strip()
    email = request.form.get("email", "").strip()
    name = request.form.get("name", "").strip()
    if not phone and not email:
        flash("Please provide a phone number or email.", "warning")
        return redirect(url_for("store.product_detail", product_id=product_id))
    from ...models.stock_notification import StockNotification
    # Avoid duplicate
    existing = db.session.execute(
        db.select(StockNotification).where(
            StockNotification.product_id == product_id,
            StockNotification.phone == phone,
            StockNotification.notified == False,
        )
    ).scalar_one_or_none()
    if not existing:
        db.session.add(StockNotification(
            product_id=product_id, phone=phone, email=email or None, name=name or None
        ))
        db.session.commit()
    flash(f"We'll notify you when {product.name} is back in stock! 🔔", "success")
    return redirect(url_for("store.product_detail", product_id=product_id))


# ── SEO: Product by Slug (Improvement 13) ────────────────────────────────────

@store_bp.route("/p/<slug>")
@limiter.limit("120/minute")
def product_by_slug(slug):
    product = db.session.execute(
        db.select(Product).where(Product.slug == slug)
    ).scalar_one_or_none()
    if not product:
        from flask import abort
        abort(404)
    return redirect(url_for("store.product_detail", product_id=product.id), 301)


# ── Sitemap (Improvement 12) ──────────────────────────────────────────────────

@store_bp.route("/sitemap.xml")
def sitemap():
    from datetime import date
    products = db.session.execute(
        db.select(Product)
        .where(Product.is_active.isnot(False), Product.quantity > 0)
        .order_by(Product.updated_at.desc())
    ).scalars().all()

    base = request.url_root.rstrip("/")
    urls = [
        f"<url><loc>{base}/store/</loc><changefreq>daily</changefreq><priority>1.0</priority></url>",
        f"<url><loc>{base}/store/track</loc><changefreq>monthly</changefreq><priority>0.3</priority></url>",
    ]
    for p in products:
        lastmod = p.updated_at.strftime("%Y-%m-%d") if p.updated_at else date.today().isoformat()
        urls.append(
            f"<url><loc>{base}/store/product/{p.id}</loc>"
            f"<lastmod>{lastmod}</lastmod>"
            f"<changefreq>weekly</changefreq><priority>0.8</priority></url>"
        )
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml += "\n".join(urls)
    xml += "\n</urlset>"
    return Response(xml, mimetype="application/xml")
