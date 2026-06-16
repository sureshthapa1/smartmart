"""Customer storefront routes for GoldKernel Dry Fruits."""
from __future__ import annotations

import base64
import hashlib
import hmac
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
from ...services.store_ai_service import (
    selling_fast_ids as _selling_fast_ids,
    get_velocity_map,
    personalise_products,
    search_suggestions,
)

store_bp = Blueprint("store", __name__, url_prefix="/store")

# ── Nepali/common name search aliases ────────────────────────────────────────
SEARCH_ALIASES = {
    "badam": "almond", "badaam": "almond",
    "okhar": "walnut", "akhrot": "walnut",
    "kaju": "cashew", "kew": "cashew",
    "pista": "pistachio",
    "kismis": "raisin", "kishmish": "raisin", "munakka": "raisin",
    "khajur": "date", "khajoor": "date",
    "khubani": "apricot",
    "nariyal": "coconut", "nariwal": "coconut",
    "anjeer": "fig",
    "mungphali": "peanut", "groundnut": "peanut",
    "chiya": "tea", "chai": "tea",
    "bhat": "rice", "chawal": "rice",
    "dal": "lentil", "daal": "lentil",
    "tel": "oil", "tori": "mustard",
    "sabun": "soap",
    "dudh": "milk", "dahi": "yogurt",
}

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
    min_price = request.args.get("min_price", "")
    max_price = request.args.get("max_price", "")

    _maybe_expire_reservations()

    stmt = db.select(Product).where(Product.is_active.isnot(False), Product.quantity > 0)

    # Nepali/common name alias expansion (FIX 10)
    if q:
        q_expanded = SEARCH_ALIASES.get(q.lower(), q)
        term = f"%{q_expanded.lower()}%"
        term_orig = f"%{q.lower()}%"
        stmt = stmt.where(db.or_(
            func.lower(Product.name).like(term),
            func.lower(Product.name).like(term_orig),
            func.lower(func.coalesce(Product.category, "")).like(term),
            func.lower(func.coalesce(Product.category, "")).like(term_orig),
            func.lower(func.coalesce(Product.sku, "")).like(term_orig),
            func.lower(func.coalesce(Product.description, "")).like(term_orig),
        ))

    if category:
        stmt = stmt.where(
            func.lower(func.coalesce(Product.category, "")) == category.lower()
        )

    # Price filter (FIX 7)
    if min_price:
        try:
            stmt = stmt.where(Product.selling_price >= float(min_price))
        except ValueError:
            pass
    if max_price:
        try:
            stmt = stmt.where(Product.selling_price <= float(max_price))
        except ValueError:
            pass

    sort_map = {
        "name":       Product.name.asc(),
        "price_asc":  Product.selling_price.asc(),
        "price_desc": Product.selling_price.desc(),
        "newest":     Product.created_at.desc(),
    }
    stmt = stmt.order_by(sort_map.get(sort, Product.name.asc()))

    # Pagination (FIX 6)
    page = max(1, int(request.args.get("page", 1)))
    per_page = 48

    # Count total for pagination
    count_stmt = db.select(func.count(Product.id)).where(
        Product.is_active.isnot(False), Product.quantity > 0
    )
    if q:
        q_expanded = SEARCH_ALIASES.get(q.lower(), q)
        term = f"%{q_expanded.lower()}%"
        term_orig = f"%{q.lower()}%"
        count_stmt = count_stmt.where(db.or_(
            func.lower(Product.name).like(term),
            func.lower(Product.name).like(term_orig),
            func.lower(func.coalesce(Product.category, "")).like(term),
            func.lower(func.coalesce(Product.category, "")).like(term_orig),
            func.lower(func.coalesce(Product.sku, "")).like(term_orig),
            func.lower(func.coalesce(Product.description, "")).like(term_orig),
        ))
    if category:
        count_stmt = count_stmt.where(
            func.lower(func.coalesce(Product.category, "")) == category.lower()
        )
    if min_price:
        try:
            count_stmt = count_stmt.where(Product.selling_price >= float(min_price))
        except ValueError:
            pass
    if max_price:
        try:
            count_stmt = count_stmt.where(Product.selling_price <= float(max_price))
        except ValueError:
            pass
    total_products = db.session.execute(count_stmt).scalar() or 0

    offset = (page - 1) * per_page
    products   = db.session.execute(stmt.limit(per_page).offset(offset)).scalars().all()
    categories = _get_categories()

    # AI: velocity + personalisation
    _fast_ids = _selling_fast_ids()

    # FEATURE 6: Recently viewed products
    _rv_ids = session.get("recently_viewed", [])
    _recently_viewed = []
    if _rv_ids:
        _rv_map = {p.id: p for p in db.session.execute(
            db.select(Product).where(Product.id.in_(_rv_ids), Product.is_active.isnot(False), Product.quantity > 0)
        ).scalars().all()}
        _recently_viewed = [_rv_map[pid] for pid in _rv_ids if pid in _rv_map][:6]
    cust_phone = g.customer.phone if g.customer else None
    _personalised = False
    if cust_phone:
        products = personalise_products(list(products), cust_phone)
        _personalised = True

    # FEATURE 1: Active promo codes for banner
    from datetime import date as _date2
    try:
        from ...models.promotion import Promotion as _Promo
        _active_promos = db.session.execute(
            db.select(_Promo).where(
                _Promo.is_active == True,
                _Promo.start_date <= _date2.today(),
                _Promo.end_date >= _date2.today(),
            ).limit(5)
        ).scalars().all()
    except Exception:
        _active_promos = []

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
        selling_fast_ids=_fast_ids,
        personalised=_personalised,
        recently_viewed=_recently_viewed,
        active_promos=_active_promos,
        settings=settings,
        q=q,
        selected_category=category,
        sort=sort,
        min_price=min_price,
        max_price=max_price,
        page=page,
        per_page=per_page,
        total_products=total_products,
        customer=g.customer,
        free_delivery_threshold=FREE_DELIVERY_THRESHOLD,
    )

# ── Category Browse ──────────────────────────────────────────────────────────

@store_bp.route("/category/<slug>")
def category_browse(slug: str):
    """Dedicated category page — /store/category/dry-fruits etc."""
    settings   = _settings()
    sort       = request.args.get("sort", "name")
    min_price  = request.args.get("min_price", "")
    max_price  = request.args.get("max_price", "")
    page       = max(1, int(request.args.get("page", 1)))
    per_page   = 48

    # Normalise slug back to a display name via the categories list
    categories = _get_categories()
    # Find the matching category name (case-insensitive, slug = name.lower().replace(" ", "-"))
    category_name = None
    for cat in categories:
        cat_slug = cat.lower().replace(" ", "-").replace("/", "-")
        if cat_slug == slug.lower() or cat.lower() == slug.lower():
            category_name = cat
            break
    if category_name is None:
        # Treat the slug directly as a category name if no match
        category_name = slug.replace("-", " ").title()

    stmt = (
        db.select(Product)
        .where(
            Product.is_active.isnot(False),
            Product.quantity > 0,
            func.lower(func.coalesce(Product.category, "")) == category_name.lower(),
        )
    )
    if min_price:
        try: stmt = stmt.where(Product.selling_price >= float(min_price))
        except ValueError: pass
    if max_price:
        try: stmt = stmt.where(Product.selling_price <= float(max_price))
        except ValueError: pass

    sort_map = {
        "name":       Product.name.asc(),
        "price_asc":  Product.selling_price.asc(),
        "price_desc": Product.selling_price.desc(),
        "newest":     Product.created_at.desc(),
    }
    stmt = stmt.order_by(sort_map.get(sort, Product.name.asc()))

    total = db.session.execute(
        db.select(func.count(Product.id))
        .where(Product.is_active.isnot(False), Product.quantity > 0,
               func.lower(func.coalesce(Product.category, "")) == category_name.lower())
    ).scalar() or 0

    products = db.session.execute(
        stmt.limit(per_page).offset((page - 1) * per_page)
    ).scalars().all()

    _fast_ids = _selling_fast_ids()

    return render_template(
        "store/category.html",
        products=products,
        categories=categories,
        category_name=category_name,
        category_slug=slug,
        selling_fast_ids=_fast_ids,
        settings=settings,
        sort=sort,
        min_price=min_price,
        max_price=max_price,
        page=page,
        per_page=per_page,
        total_products=total,
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

    # FEATURE 7: Cart abandonment recovery flash for returning visitors
    if not session.get("cart_recovery_shown") and session.get("cart"):
        _cart_data = {k: v for k, v in session["cart"].items()
                      if str(v).isdigit() and int(v) > 0}
        if _cart_data and not g.customer:
            session["cart_recovery_shown"] = True
            flash("👜 You left some items in your cart!", "info")
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

    # AI recommendations (co-purchase affinity + collaborative filtering, falls back to same-category)
    from ...services.recommendation_service import get_product_recommendations
    from ...services.store_ai_service import selling_fast_ids as _sfi
    from flask_login import current_user as _cu
    _phone = g.customer.phone if g.customer else None
    recommendations = get_product_recommendations(product.id, customer_phone=_phone, limit=4)

    # Fallback related (same category) for the elif in template
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

    # FEATURE 9: Estimated delivery date
    from datetime import date as _date, timedelta as _td
    _today = _date.today()
    _dow = _today.weekday()  # 0=Mon..6=Sun
    _days_to_add = 1 if _dow < 4 else (8 - _dow)  # next business day
    est_delivery = _today + _td(days=_days_to_add)

    # FEATURE 6: Recently viewed — store in session
    rv = session.get("recently_viewed", [])
    if product.id not in rv:
        rv.insert(0, product.id)
    session["recently_viewed"] = rv[:10]

    # Reviews data
    from ...models.product_review import ProductReview
    reviews = db.session.execute(
        db.select(ProductReview)
        .where(ProductReview.product_id == product.id, ProductReview.is_approved == True)
        .order_by(ProductReview.created_at.desc())
    ).scalars().all()
    avg_rating = round(sum(r.rating for r in reviews) / len(reviews), 1) if reviews else 0
    user_reviewed = any(r.customer_phone == (g.customer.phone if g.customer else "") for r in reviews)

    # Wishlist status
    wishlisted = False
    if g.customer:
        from ...models.wishlist_item import WishlistItem
        wishlisted = db.session.execute(
            db.select(WishlistItem).where(
                WishlistItem.customer_phone == g.customer.phone,
                WishlistItem.product_id == product.id,
            )
        ).scalar_one_or_none() is not None

    return render_template(
        "store/product_detail.html",
        product=product,
        avail=avail,
        cart_qty=cart_qty,
        recommendations=recommendations,
        related=related,
        est_delivery=est_delivery,
        reviews=reviews,
        avg_rating=avg_rating,
        user_reviewed=user_reviewed,
        wishlisted=wishlisted,
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

    # AI: cart recommendations (advanced co-purchase + collab)
    _cart_pids = [int(pid) for pid in raw_cart.keys() if str(pid).isdigit()]
    from ...services.recommendation_service import get_cart_recommendations
    cart_recs = get_cart_recommendations(_cart_pids, limit=4) if items else []

    return render_template(
        "store/cart.html",
        items=items, subtotal=subtotal, delivery=delivery,
        grand_total=grand_total, settings=settings, customer=g.customer,
        free_delivery_threshold=FREE_DELIVERY_THRESHOLD,
        raw_cart_count=len(raw_cart),
        cart_recommendations=cart_recs,
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
        method  = request.form.get("payment_mode", "cod")
        if method not in VALID_PAYMENT_METHODS:
            method = "cod"
        notes        = request.form.get("notes", "").strip()
        gift_wrap    = request.form.get("gift_wrap") == "1"
        gift_message = request.form.get("gift_message", "").strip()
        if gift_wrap:
            gift_note = f"🎁 GIFT WRAP REQUESTED. Message: {gift_message}" if gift_message else "🎁 GIFT WRAP REQUESTED"
            notes = f"{gift_note}. {notes}".strip(". ")

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

        gift_wrap_charge = 50.0 if gift_wrap else 0.0
        grand_total = subtotal + delivery + gift_wrap_charge - discount_amount

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

    # FEATURE 2: Send order receipt email if customer provided one
    if order.customer_email and not session.get(f"email_sent_{order_number}"):
        try:
            _send_order_receipt_email(order, settings)
            session[f"email_sent_{order_number}"] = True
        except Exception:
            pass

    # FEATURE 3: Build WhatsApp confirmation link
    wa_link = None
    if settings and settings.phone:
        import urllib.parse as _up
        wa_msg = (
            f"Hi! I just placed order *{order.order_number}* on {settings.shop_name or 'GoldKernel'}. "
            f"Total: NPR {float(order.grand_total):.0f}. "
            f"Please confirm. Thank you!"
        )
        wa_num = re.sub(r"\D", "", settings.phone)
        if not wa_num.startswith("977"):
            wa_num = "977" + wa_num
        wa_link = f"https://wa.me/{wa_num}?text={_up.quote(wa_msg)}"

    return render_template("store/order_success.html", order=order,
                           settings=settings, customer=cust,
                           wa_link=wa_link)


# ── Order Tracking ────────────────────────────────────────────────────────────

@store_bp.route("/track", methods=["GET", "POST"])
def track():
    settings     = _settings()
    order        = None
    orders       = []
    searched     = False
    order_number = ""

    if request.method == "POST":
        searched     = True
        order_number = request.form.get("order_number", "").strip().upper()
        phone        = request.form.get("phone", "").strip()

        if order_number:
            order = db.session.execute(
                db.select(OnlineOrder).where(OnlineOrder.order_number == order_number)
            ).scalar_one_or_none()
        elif phone:
            clean_phone = _validate_phone(phone)
            if clean_phone:
                orders = db.session.execute(
                    db.select(OnlineOrder)
                    .where(OnlineOrder.customer_phone == clean_phone)
                    .order_by(OnlineOrder.created_at.desc())
                    .limit(10)
                ).scalars().all()
    elif request.args.get("order_number"):
        order_number = request.args.get("order_number", "").strip().upper()
        if order_number:
            order = db.session.execute(
                db.select(OnlineOrder).where(OnlineOrder.order_number == order_number)
            ).scalar_one_or_none()

    return render_template(
        "store/track.html",
        order=order, orders=orders, searched=searched,
        order_number=order_number,
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


# ── eSewa signature helper ────────────────────────────────────────────────────

def _esewa_product_code() -> str:
    """Return the eSewa product code from env (EPAYTEST for sandbox, live code for production)."""
    import os as _os
    return _os.environ.get("ESEWA_PRODUCT_CODE", "EPAYTEST")


def _esewa_secret() -> str:
    """Return the eSewa secret key from env (sandbox default if not set)."""
    import os as _os
    return _os.environ.get("ESEWA_SECRET_KEY", "8gBm/:&EnhH.1/q")


def _esewa_signature(total_amount: str, transaction_uuid: str, product_code: str | None = None) -> str:
    """Generate eSewa HMAC-SHA256 signature for payment initiation."""
    if product_code is None:
        product_code = _esewa_product_code()
    secret = _esewa_secret()
    message = f"total_amount={total_amount},transaction_uuid={transaction_uuid},product_code={product_code}"
    sig = hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(sig).decode()


def _verify_esewa_callback(args: dict) -> bool:
    """
    Verify eSewa v2 callback signature.
    eSewa sends: transaction_code, status, total_amount, transaction_uuid,
                 product_code, signed_field_names, signature
    We recompute the HMAC over the signed fields and compare.
    """
    try:
        signed_fields = args.get("signed_field_names", "")
        if not signed_fields:
            return False
        field_names = [f.strip() for f in signed_fields.split(",")]
        message = ",".join(f"{k}={args.get(k, '')}" for k in field_names)
        secret = _esewa_secret()
        expected = base64.b64encode(
            hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
        ).decode()
        received = args.get("signature", "")
        return hmac.compare_digest(expected, received)
    except Exception:
        return False


def _verify_khalti_callback(token: str, amount_paisa: int) -> bool:
    """
    Verify Khalti payment by calling Khalti's verification API.
    amount_paisa is the expected amount in paisa (NPR * 100).
    Returns True only if Khalti confirms the payment as Completed.
    """
    import os as _os, urllib.request as _req, json as _json
    secret_key = _os.environ.get("KHALTI_SECRET_KEY", "")
    if not secret_key:
        # No Khalti key configured — log and fail secure
        import logging as _log
        _log.getLogger(__name__).warning(
            "KHALTI_SECRET_KEY not configured — cannot verify Khalti payment"
        )
        return False
    try:
        verify_url = "https://khalti.ebanking.com.np/api/v2/payment/verify/"
        payload = _json.dumps({"token": token, "amount": amount_paisa}).encode()
        req = _req.Request(verify_url, data=payload, method="POST")
        req.add_header("Authorization", f"Key {secret_key}")
        req.add_header("Content-Type", "application/json")
        with _req.urlopen(req, timeout=15) as resp:
            data = _json.loads(resp.read())
            return data.get("state", {}).get("name") == "Completed"
    except Exception:
        return False


# ── Payment Pending (FIX 1) ───────────────────────────────────────────────────

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
    esewa_signature = _esewa_signature(
        f"{order.grand_total:.2f}", order.order_number, _esewa_product_code()
    )
    esewa_product_code = _esewa_product_code()
    return render_template("store/payment_pending.html", order=order,
                           settings=settings, customer=cust,
                           esewa_signature=esewa_signature,
                           esewa_product_code=esewa_product_code)


@store_bp.route("/payment/<order_number>/callback/<provider>")
def payment_callback(order_number, provider):
    """
    Handle payment gateway callback — VERIFY signature before marking order paid.

    eSewa v2: verifies HMAC-SHA256 over signed_field_names.
    Khalti:   calls Khalti's server-side verification API with the token.
    COD/other: not routed here (COD goes straight to order_success).
    """
    import logging as _log
    _logger = _log.getLogger(__name__)

    order = db.session.execute(
        db.select(OnlineOrder).where(OnlineOrder.order_number == order_number)
    ).scalar_one_or_none()

    if not order:
        flash("Order not found.", "danger")
        return redirect(url_for("store.home"))

    # Already paid — idempotent redirect
    if order.payment_status == "paid":
        return redirect(url_for("store.order_success", order_number=order_number))

    args = request.args.to_dict()
    verified = False
    gateway_ref = ""

    if provider == "esewa":
        # eSewa v2 sends signed_field_names + signature
        if "signed_field_names" in args and "signature" in args:
            verified = _verify_esewa_callback(args)
            gateway_ref = args.get("transaction_code", args.get("refId", ""))
            if not verified:
                _logger.warning(
                    "eSewa signature mismatch for order %s. Args: %s",
                    order_number, {k: v for k, v in args.items() if k != "signature"}
                )
        else:
            # eSewa v1 fallback (sandbox) — accept only in non-production
            import os as _os
            if _os.environ.get("FLASK_ENV", "production") != "production":
                verified = True
                gateway_ref = args.get("refId", "")
                _logger.warning(
                    "eSewa v1 callback accepted in dev/sandbox for order %s", order_number
                )
            else:
                _logger.error(
                    "eSewa callback missing signed_field_names for order %s in production",
                    order_number
                )

    elif provider == "khalti":
        token = args.get("token") or args.get("pidx", "")
        try:
            amount_paisa = int(float(order.grand_total) * 100)
        except Exception:
            amount_paisa = 0
        if token:
            verified = _verify_khalti_callback(token, amount_paisa)
            gateway_ref = token
            if not verified:
                _logger.warning(
                    "Khalti verification failed for order %s (token=%s)",
                    order_number, token[:8] + "..."
                )
        else:
            _logger.warning("Khalti callback missing token for order %s", order_number)

    elif provider in ("cod", "cash"):
        # COD should not hit this route, but handle gracefully
        verified = True
        gateway_ref = "cod"

    if verified:
        order.payment_status = "paid"
        from ...models.ecommerce import EcommercePayment
        payment = db.session.execute(
            db.select(EcommercePayment).where(EcommercePayment.order_id == order.id)
        ).scalars().first()
        if payment:
            payment.status = "paid"
            payment.gateway_reference = gateway_ref
        db.session.commit()
        session["last_order"] = order_number
        flash(f"Payment successful! Order {order_number} confirmed.", "success")
        _logger.info("Payment verified for order %s via %s", order_number, provider)

        # ── Notify admin of new paid online order ─────────────────────────
        try:
            from ...services.notification_service import send_notification
            from ...models.shop_settings import ShopSettings
            settings = ShopSettings.get()
            admin_phone = getattr(settings, "phone", None) or getattr(settings, "contact_phone", None)
            if admin_phone:
                msg = (
                    f"[GoldKernel] New order {order_number} PAID via {provider.upper()}. "
                    f"Amount: NPR {order.grand_total:.0f}. "
                    f"Customer: {order.customer_name} ({order.customer_phone})."
                )
                send_notification(admin_phone, msg)
        except Exception:
            pass  # Never block the payment confirmation on notification failure

        return redirect(url_for("store.order_success", order_number=order_number))
    else:
        flash(
            "Payment verification failed. Your order has been saved — please try again "
            "or contact us if the amount was deducted.",
            "danger"
        )
        return redirect(url_for("store.payment_pending", order_number=order_number))


@store_bp.route("/payment/<order_number>/failed")
def payment_failed(order_number):
    """Handle failed payment — keep order pending, let customer retry."""
    flash("Payment was not completed. Your order is saved — you can try again or choose Cash on Delivery.", "warning")
    return redirect(url_for("store.payment_pending", order_number=order_number))


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


# ── Back-in-Stock Notification (Improvement 9) ────────────────────────────────

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


# ── Order Detail (FIX 8) ─────────────────────────────────────────────────────

@store_bp.route("/order/<order_number>")
def order_detail(order_number):
    settings = _settings()
    cust = g.customer
    order = db.session.execute(
        db.select(OnlineOrder).where(OnlineOrder.order_number == order_number)
    ).scalar_one_or_none()
    if not order:
        flash("Order not found.", "warning")
        return redirect(url_for("store.home"))
    owns = False
    if cust and order.customer_phone == cust.phone:
        owns = True
    if not owns:
        last_order = session.get("last_order", "")
        if last_order == order_number:
            owns = True
    if not owns:
        flash("You don't have permission to view this order.", "danger")
        return redirect(url_for("store.home"))
    return render_template("store/order_detail.html", order=order,
                           settings=settings, customer=cust)


# ── Password Reset (FIX 2) ────────────────────────────────────────────────────

@store_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("3/minute", methods=["POST"])
def forgot_password():
    settings = _settings()
    if request.method == "POST":
        phone = request.form.get("phone", "").strip()
        clean = _validate_phone(phone)
        if not clean:
            flash("Enter a valid Nepal phone number.", "danger")
        else:
            account = db.session.execute(
                db.select(CustomerAccount).where(CustomerAccount.phone == clean)
            ).scalar_one_or_none()
            if account:
                import secrets as _secrets
                token = _secrets.token_urlsafe(32)
                session[f"pwd_reset_{clean}"] = token
                reset_url = url_for("store.reset_password", phone=clean, token=token, _external=True)
                from flask import current_app
                current_app.logger.warning(
                    "CUSTOMER PASSWORD RESET for %s — Reset URL: %s", clean, reset_url
                )
            flash("If that phone number has an account, a reset link has been generated. "
                  "Please call us to get your reset link.", "info")
    return render_template("store/forgot_password.html", settings=settings, customer=None)


@store_bp.route("/reset-password", methods=["GET", "POST"])
@limiter.limit("5/minute", methods=["POST"])
def reset_password():
    settings = _settings()
    phone = request.args.get("phone", "").strip()
    token = request.args.get("token", "").strip()
    stored = session.get(f"pwd_reset_{phone}")
    valid = stored and stored == token
    if not valid:
        flash("This reset link is invalid or has expired.", "danger")
        return redirect(url_for("store.login"))
    if request.method == "POST":
        new_pw = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if len(new_pw) < 6:
            flash("Password must be at least 6 characters.", "danger")
        elif new_pw != confirm:
            flash("Passwords do not match.", "danger")
        else:
            account = db.session.execute(
                db.select(CustomerAccount).where(CustomerAccount.phone == phone)
            ).scalar_one_or_none()
            if account:
                account.set_password(new_pw)
                db.session.commit()
                session.pop(f"pwd_reset_{phone}", None)
                flash("Password reset successfully. Please log in.", "success")
                return redirect(url_for("store.login"))
    return render_template("store/reset_password.html", settings=settings,
                           phone=phone, token=token, customer=None)


# ── About / Contact / FAQ (FIX 3) ─────────────────────────────────────────────

@store_bp.route("/about")
def about():
    settings = _settings()
    return render_template("store/about.html", settings=settings, customer=g.customer)


@store_bp.route("/contact", methods=["GET", "POST"])
def contact():
    settings = _settings()
    if request.method == "POST":
        flash("Thank you for your message! We'll get back to you within 24 hours.", "success")
        return redirect(url_for("store.contact"))
    return render_template("store/contact.html", settings=settings, customer=g.customer)


@store_bp.route("/faq")
def faq():
    settings = _settings()
    return render_template("store/faq.html", settings=settings, customer=g.customer)


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



# ── AI: Live Search Suggestions ───────────────────────────────────────────────

@store_bp.route("/api/search-suggestions")
@limiter.limit("60/minute")
def search_suggestions_api():
    """JSON endpoint for live search dropdown."""
    q = request.args.get("q", "").strip()
    from ...services.store_ai_service import search_suggestions
    results = search_suggestions(q, limit=6)
    return jsonify({"ok": True, "results": results})


# ── AI: Store Chatbot ─────────────────────────────────────────────────────────

@store_bp.route("/api/chat", methods=["POST"])
@limiter.limit("30/minute")
def store_chat_api():
    """JSON endpoint for the store AI chatbot widget."""
    data    = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    history = data.get("history") or []
    if not message:
        return jsonify({"ok": False, "error": "Empty message"}), 400
    cust_name = g.customer.name if g.customer else None
    from ...services.store_ai_service import chatbot_reply
    reply = chatbot_reply(message, history=history, customer_name=cust_name)
    return jsonify({"ok": True, "reply": reply})



# ── FEATURE 1: Active promo codes display ─────────────────────────────────────

@store_bp.route("/promos")
def promos():
    """Show currently active promo codes."""
    from datetime import date
    from ...models.promotion import Promotion
    settings = _settings()
    today = date.today()
    active = db.session.execute(
        db.select(Promotion)
        .where(
            Promotion.is_active == True,
            Promotion.start_date <= today,
            Promotion.end_date >= today,
        )
        .order_by(Promotion.end_date)
    ).scalars().all()
    return render_template("store/promos.html", promos=active,
                           settings=settings, customer=g.customer)


# ── FEATURE 4: Product reviews ────────────────────────────────────────────────

@store_bp.route("/product/<int:product_id>/review", methods=["POST"])
@limiter.limit("5/hour")
def submit_review(product_id):
    """Submit a product review (requires login or verified order)."""
    from ...models.product_review import ProductReview
    product = db.get_or_404(Product, product_id)
    rating  = int(request.form.get("rating", 0))
    title   = request.form.get("title", "").strip()[:120]
    body    = request.form.get("body", "").strip()[:1000]
    order_number = request.form.get("order_number", "").strip().upper()

    cust = g.customer
    if not cust:
        flash("Please sign in to leave a review.", "warning")
        return redirect(url_for("store.product_detail", product_id=product_id))

    if not (1 <= rating <= 5):
        flash("Please select a rating between 1 and 5.", "danger")
        return redirect(url_for("store.product_detail", product_id=product_id))

    # Verify purchase if order number provided
    if order_number:
        order = db.session.execute(
            db.select(OnlineOrder).where(
                OnlineOrder.order_number == order_number,
                OnlineOrder.customer_phone == cust.phone,
            )
        ).scalar_one_or_none()
        if not order:
            flash("Order number not found for your account.", "danger")
            return redirect(url_for("store.product_detail", product_id=product_id))

    try:
        review = ProductReview(
            product_id=product_id,
            customer_phone=cust.phone,
            customer_name=cust.name,
            rating=rating,
            title=title or None,
            body=body or None,
            order_number=order_number or None,
        )
        db.session.add(review)
        db.session.commit()
        flash("Thank you for your review! ⭐ It will appear after moderation.", "success")
    except Exception:
        db.session.rollback()
        flash("You have already reviewed this product.", "info")

    return redirect(url_for("store.product_detail", product_id=product_id) + "#reviews")


# ── FEATURE 5: Wishlist ───────────────────────────────────────────────────────

@store_bp.route("/wishlist/toggle", methods=["POST"])
@limiter.limit("30/minute")
def wishlist_toggle():
    """Add or remove a product from the customer's wishlist."""
    from ...models.wishlist_item import WishlistItem
    cust = g.customer
    if not cust:
        return jsonify({"ok": False, "error": "Login required", "redirect": url_for("store.login")}), 401

    product_id = int(request.form.get("product_id", 0) or request.json.get("product_id", 0) if request.is_json else request.form.get("product_id", 0))
    if not product_id:
        return jsonify({"ok": False, "error": "Missing product_id"}), 400

    existing = db.session.execute(
        db.select(WishlistItem).where(
            WishlistItem.customer_phone == cust.phone,
            WishlistItem.product_id == product_id,
        )
    ).scalar_one_or_none()

    if existing:
        db.session.delete(existing)
        db.session.commit()
        return jsonify({"ok": True, "action": "removed", "wishlisted": False})
    else:
        item = WishlistItem(customer_phone=cust.phone, product_id=product_id)
        db.session.add(item)
        db.session.commit()
        return jsonify({"ok": True, "action": "added", "wishlisted": True})


@store_bp.route("/wishlist")
def wishlist():
    """View customer's wishlist — returns Product objects for the template."""
    from ...models.wishlist_item import WishlistItem
    settings = _settings()
    cust = g.customer
    if not cust:
        flash("Please sign in to view your wishlist.", "info")
        return redirect(url_for("store.login", next=url_for("store.wishlist")))
    # Join Product so template can access p.name, p.selling_price, p.image_filename etc.
    rows = db.session.execute(
        db.select(WishlistItem, Product)
        .join(Product, Product.id == WishlistItem.product_id)
        .where(WishlistItem.customer_phone == cust.phone)
        .order_by(WishlistItem.created_at.desc())
    ).all()
    # Pass Product objects directly — template iterates `for p in items`
    items = [row.Product for row in rows]
    selling_fast_ids = _selling_fast_ids()
    return render_template("store/wishlist.html", items=items,
                           selling_fast_ids=selling_fast_ids,
                           settings=settings, customer=cust)


# ── FEATURE 10: Improved sitemap with image tags ──────────────────────────────



# ── Admin: Trigger bulk autofill for all existing products ────────────────────

@store_bp.route("/api/autofill-all-products", methods=["POST"])
def autofill_all_products():
    """
    Trigger AI autofill for all products missing description or image.
    Protected by admin session — callable from admin panel or directly.
    """
    from flask_login import current_user
    if not (current_user.is_authenticated and getattr(current_user, "role", "") in ("admin", "manager")):
        return jsonify({"ok": False, "error": "Admin only"}), 403

    try:
        from ...services.product_autofill import autofill_all_empty
        results = autofill_all_empty(limit=200)
        return jsonify({"ok": True, "results": results})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

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
        f'<url><loc>{base}/store/</loc><changefreq>daily</changefreq><priority>1.0</priority></url>',
        f'<url><loc>{base}/store/track</loc><changefreq>monthly</changefreq><priority>0.3</priority></url>',
        f'<url><loc>{base}/store/about</loc><changefreq>monthly</changefreq><priority>0.5</priority></url>',
        f'<url><loc>{base}/store/faq</loc><changefreq>monthly</changefreq><priority>0.5</priority></url>',
        f'<url><loc>{base}/store/contact</loc><changefreq>monthly</changefreq><priority>0.4</priority></url>',
        f'<url><loc>{base}/store/promos</loc><changefreq>daily</changefreq><priority>0.6</priority></url>',
    ]
    for p in products:
        lastmod = p.updated_at.strftime("%Y-%m-%d") if p.updated_at else date.today().isoformat()
        loc = f"{base}/store/p/{p.slug}" if getattr(p, 'slug', None) else f"{base}/store/product/{p.id}"
        # Image tag for Google image search
        img_tag = ""
        if p.image_filename and not p.image_filename.startswith("cld:"):
            img_url = f"{base}/static/uploads/products/{p.image_filename}"
            img_caption = (p.name or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            img_tag = f'<image:image><image:loc>{img_url}</image:loc><image:caption>{img_caption}</image:caption></image:image>'
        # Boost priority for featured or fast-selling products
        priority = "0.9" if getattr(p, "is_featured", False) else "0.8"
        urls.append(
            f"<url><loc>{loc}</loc>"
            f"<lastmod>{lastmod}</lastmod>"
            f"<changefreq>weekly</changefreq><priority>{priority}</priority>"
            f"{img_tag}</url>"
        )
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"\n'
    xml += '  xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">\n'
    xml += "\n".join(urls)
    xml += "\n</urlset>"
    return Response(xml, mimetype="application/xml")


# ── SEO: robots.txt ───────────────────────────────────────────────────────────

@store_bp.route("/robots.txt")
def robots():
    """robots.txt — allow crawlers, block private pages."""
    base = request.url_root.rstrip("/")
    content = (
        "User-agent: *\n"
        "Allow: /store/\n"
        "Disallow: /store/checkout\n"
        "Disallow: /store/account\n"
        "Disallow: /store/cart\n"
        "Disallow: /dashboard/\n"
        "Disallow: /admin/\n"
        "Disallow: /api/\n"
        f"Sitemap: {base}/store/sitemap.xml\n"
    )
    return Response(content, mimetype="text/plain")
