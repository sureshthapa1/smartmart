"""
ESC/POS direct printing service for TVS RP3200 Lite (and compatible printers).

How it works:
  Flask route → this service → ESC/POS commands → TVS RP3200 Lite

The printer name is read from the THERMAL_PRINTER_NAME environment variable.
Default: "TVS RP3200 Lite"

To find your exact Windows printer name:
  python -c "import win32print; [print(p[2]) for p in win32print.EnumPrinters(2)]"

Dependencies (already installed):
  python-escpos==3.1
  pywin32 (for win32print on Windows)
"""

from __future__ import annotations

import os
import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

# ── Printer name (configurable via env var) ───────────────────────────────────
PRINTER_NAME = os.environ.get("THERMAL_PRINTER_NAME", "TVS RP3200 Lite")


def _get_printer():
    """Return an escpos Printer instance connected to the Windows printer."""
    try:
        from escpos.printer import Win32Raw
        return Win32Raw(PRINTER_NAME)
    except Exception as exc:
        raise RuntimeError(
            f"Cannot connect to printer '{PRINTER_NAME}'. "
            f"Check THERMAL_PRINTER_NAME env var and that the printer is installed. "
            f"Error: {exc}"
        )


def _fmt(amount) -> str:
    """Format a number as 2dp string."""
    try:
        return f"{float(amount):.2f}"
    except Exception:
        return "0.00"


def _trunc(text: str, width: int) -> str:
    """Truncate text to fit in given character width."""
    if len(text) > width:
        return text[: width - 1] + "…"
    return text


def _row(left: str, right: str, width: int = 42) -> str:
    """Build a left-right padded row string."""
    gap = width - len(left) - len(right)
    if gap < 1:
        gap = 1
    return left + " " * gap + right


def print_receipt(sale, shop=None, loyalty_txns=None, loyalty_balance=None) -> None:
    """
    Print a receipt directly to the thermal printer via ESC/POS.

    Parameters
    ----------
    sale            : Sale ORM object with .items, .user, etc.
    shop            : ShopSettings ORM object (optional)
    loyalty_txns    : list of LoyaltyWalletTransaction objects (optional)
    loyalty_balance : int points balance (optional)
    """
    p = _get_printer()

    shop_name = (shop.shop_name if shop else None) or "Goldkernel"
    address   = (shop.address if shop else None) or ""
    phone     = (shop.phone if shop else None) or ""
    pan       = (shop.pan_number if shop else None) or ""
    vat_no    = (shop.vat_number if shop and shop.vat_enabled else None) or ""
    footer    = (shop.footer_note if shop else None) or "Thank you for shopping with us!"

    inv_no    = sale.invoice_number or f"#{sale.id:05d}"
    date_str  = sale.sale_date.strftime("%Y-%m-%d %H:%M") if sale.sale_date else ""
    cashier   = sale.user.username if sale.user else "—"
    customer  = sale.customer_name or "Walk-in"
    pay_mode  = (sale.payment_mode or "cash").upper()

    # ── Header ───────────────────────────────────────────────────────────────
    p.set(align="center", bold=True, height=2, width=2)
    p.text(shop_name + "\n")

    p.set(align="center", bold=False, height=1, width=1)
    if address:
        p.text(address + "\n")
    if phone:
        p.text("Tel: " + phone + "\n")
    if pan:
        p.text("PAN: " + pan + "\n")
    if vat_no:
        p.text("VAT: " + vat_no + "\n")

    p.text("-" * 42 + "\n")

    # ── Invoice info ─────────────────────────────────────────────────────────
    p.set(align="left", bold=False)
    p.text(_row("Invoice:", inv_no) + "\n")
    p.text(_row("Date:", date_str) + "\n")
    p.text(_row("Cashier:", cashier) + "\n")
    p.text(_row("Customer:", _trunc(customer, 24)) + "\n")
    p.text(_row("Payment:", pay_mode) + "\n")

    p.text("-" * 42 + "\n")

    # ── Column headers ────────────────────────────────────────────────────────
    p.set(bold=True)
    p.text(f"{'Item':<22}{'Qty':>4}{'Price':>8}{'Total':>8}\n")
    p.set(bold=False)
    p.text("-" * 42 + "\n")

    # ── Items ─────────────────────────────────────────────────────────────────
    subtotal = Decimal("0")
    for item in sale.items:
        name     = item.product.name if item.product else (item.custom_label or "Custom Item")
        qty      = item.quantity
        price    = Decimal(str(item.unit_price))
        line_tot = Decimal(str(item.subtotal))
        subtotal += line_tot

        # First line: name (truncated to 22 chars)
        name_trunc = _trunc(name, 22)
        p.text(
            f"{name_trunc:<22}{qty:>4}{float(price):>8.2f}{float(line_tot):>8.2f}\n"
        )

        # If name is longer than 22, print remainder on next line
        if len(name) > 22:
            p.text(f"  {name[22:44]}\n")

    p.text("-" * 42 + "\n")

    # ── Totals ────────────────────────────────────────────────────────────────
    p.set(bold=False)
    p.text(_row("Subtotal:", "NPR " + _fmt(subtotal)) + "\n")

    discount = Decimal(str(sale.discount_amount or 0))
    if discount > 0:
        note = f" ({sale.discount_note[:12]})" if sale.discount_note else ""
        p.text(_row(f"Discount{note}:", "- NPR " + _fmt(discount)) + "\n")

    # VAT
    tax_amount = Decimal(str(sale.tax_amount or 0))
    if tax_amount > 0:
        rate = int(sale.tax_rate or 0)
        p.text(_row(f"VAT ({rate}%):", "NPR " + _fmt(tax_amount)) + "\n")
    elif shop and shop.vat_enabled and shop.vat_rate:
        rate = float(shop.vat_rate)
        total_amt = float(sale.total_amount)
        vat = total_amt * rate / (100 + rate)
        p.text(_row(f"VAT ({int(rate)}%):", "NPR " + _fmt(vat)) + "\n")

    p.text("=" * 42 + "\n")

    # Grand total — large text
    p.set(align="right", bold=True, height=2, width=2)
    p.text(f"NPR {_fmt(sale.total_amount)}\n")

    p.set(align="left", bold=False, height=1, width=1)
    p.text("=" * 42 + "\n")

    # ── Loyalty points ────────────────────────────────────────────────────────
    if loyalty_txns:
        p.text("\n")
        p.set(align="center", bold=True)
        p.text("LOYALTY POINTS\n")
        p.set(align="left", bold=False)
        for txn in loyalty_txns:
            sign  = "+" if txn.points_change > 0 else ""
            label = "Earned" if txn.points_change > 0 else "Redeemed"
            p.text(_row(label + ":", f"{sign}{txn.points_change} pts") + "\n")
        if loyalty_balance is not None:
            p.text(_row("Balance:", f"{loyalty_balance} pts") + "\n")

    # ── Footer ────────────────────────────────────────────────────────────────
    p.set(align="center", bold=False)
    p.text("\n" + footer + "\n")
    p.text(date_str + "\n")

    # Feed and cut — always cut even if an earlier line raised
    try:
        p.ln(3)
        p.cut()
    except Exception:
        pass

    logger.info("ESC/POS receipt printed: invoice=%s printer=%s", inv_no, PRINTER_NAME)


def list_printers() -> list[str]:
    """Return all installed Windows printer names (for settings/debug)."""
    try:
        import win32print
        return [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL, None, 2)]
    except Exception:
        return []


def is_printer_available() -> bool:
    """Return True if the configured printer is installed and reachable."""
    try:
        names = list_printers()
        return PRINTER_NAME in names
    except Exception:
        return False
