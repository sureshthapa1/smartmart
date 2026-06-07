"""Customer authentication helpers for the storefront."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from flask import session

from ..extensions import db
from ..models.customer_account import CustomerAccount


_SESSION_KEY = "cust_id"


def get_current_customer() -> Optional[CustomerAccount]:
    """Return the logged-in CustomerAccount or None."""
    cid = session.get(_SESSION_KEY)
    if not cid:
        return None
    try:
        account = db.session.get(CustomerAccount, int(cid))
        if not account or not account.is_active:
            session.pop(_SESSION_KEY, None)
            return None
        return account
    except Exception:
        session.pop(_SESSION_KEY, None)
        return None


def login_customer(account: CustomerAccount) -> None:
    """Log in a customer — rotate session to prevent fixation."""
    # Preserve cart across login so items aren't lost
    cart = session.get("cart", {})
    last_order = session.get("last_order")

    session.clear()                    # ← session fixation fix
    session["cart"] = cart
    if last_order:
        session["last_order"] = last_order

    session[_SESSION_KEY] = account.id
    session.permanent = True
    account.touch_login()
    db.session.commit()


def logout_customer() -> None:
    """Log out customer, preserving cart so items aren't lost on logout."""
    cart = session.get("cart", {})
    session.clear()
    if cart:
        session["cart"] = cart


def register(name: str, phone: str, password: str,
             email: str = "", address: str = "", area: str = "") -> CustomerAccount:
    """Create and persist a new customer account. Raises ValueError on conflict."""
    name  = name.strip()
    phone = phone.strip()
    email = email.strip() or None

    if not name or not phone or not password:
        raise ValueError("Name, phone and password are required.")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")

    existing_phone = db.session.execute(
        db.select(CustomerAccount).where(CustomerAccount.phone == phone)
    ).scalar_one_or_none()
    if existing_phone:
        raise ValueError("An account with this phone number already exists.")

    if email:
        existing_email = db.session.execute(
            db.select(CustomerAccount).where(CustomerAccount.email == email)
        ).scalar_one_or_none()
        if existing_email:
            raise ValueError("An account with this email already exists.")

    account = CustomerAccount(
        name=name,
        phone=phone,
        email=email,
        address=address.strip() or None,
        area=area.strip() or None,
    )
    account.set_password(password)
    db.session.add(account)
    db.session.commit()
    return account


def authenticate(phone: str, password: str) -> Optional[CustomerAccount]:
    """Return account if credentials match, else None."""
    phone   = phone.strip()
    account = db.session.execute(
        db.select(CustomerAccount).where(CustomerAccount.phone == phone)
    ).scalar_one_or_none()
    if account and account.is_active and account.check_password(password):
        return account
    return None
