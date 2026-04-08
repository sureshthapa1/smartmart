"""Admin data management — clear data sections."""

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from ...extensions import db
from ...models.sale import Sale, SaleItem
from ...models.purchase import Purchase, PurchaseItem
from ...models.expense import Expense
from ...models.stock_movement import StockMovement
from ...models.product import Product
from ...models.supplier import Supplier
from ...models.category import Category
from ...services.decorators import admin_required


@admin_required
def data_management():
    """Show data management page with record counts."""
    counts = {
        "sales": db.session.execute(db.select(db.func.count(Sale.id))).scalar() or 0,
        "purchases": db.session.execute(db.select(db.func.count(Purchase.id))).scalar() or 0,
        "expenses": db.session.execute(db.select(db.func.count(Expense.id))).scalar() or 0,
        "stock_movements": db.session.execute(db.select(db.func.count(StockMovement.id))).scalar() or 0,
        "products": db.session.execute(db.select(db.func.count(Product.id))).scalar() or 0,
        "suppliers": db.session.execute(db.select(db.func.count(Supplier.id))).scalar() or 0,
        "categories": db.session.execute(db.select(db.func.count(Category.id))).scalar() or 0,
    }
    return render_template("admin/data_management.html", counts=counts)


@admin_required
def clear_data():
    """Clear a specific data section or all data."""
    section = request.form.get("section", "")
    confirm = request.form.get("confirm", "")

    if confirm != "DELETE":
        flash("Confirmation text did not match. Type DELETE to confirm.", "danger")
        return redirect(url_for("admin.data_management"))

    try:
        if section == "sales" or section == "all":
            db.session.execute(db.delete(SaleItem))
            db.session.execute(db.delete(Sale))
            if section == "sales":
                db.session.commit()
                flash("All sales and sale items have been cleared.", "success")
                return redirect(url_for("admin.data_management"))

        if section == "purchases" or section == "all":
            db.session.execute(db.delete(PurchaseItem))
            db.session.execute(db.delete(Purchase))
            if section == "purchases":
                db.session.commit()
                flash("All purchases and purchase items have been cleared.", "success")
                return redirect(url_for("admin.data_management"))

        if section == "expenses" or section == "all":
            db.session.execute(db.delete(Expense))
            if section == "expenses":
                db.session.commit()
                flash("All expense records have been cleared.", "success")
                return redirect(url_for("admin.data_management"))

        if section == "stock_movements" or section == "all":
            db.session.execute(db.delete(StockMovement))
            if section == "stock_movements":
                db.session.commit()
                flash("All stock movement history has been cleared.", "success")
                return redirect(url_for("admin.data_management"))

        if section == "products" or section == "all":
            # Must clear related records first
            db.session.execute(db.delete(SaleItem))
            db.session.execute(db.delete(Sale))
            db.session.execute(db.delete(PurchaseItem))
            db.session.execute(db.delete(Purchase))
            db.session.execute(db.delete(StockMovement))
            db.session.execute(db.delete(Product))
            if section == "products":
                db.session.commit()
                flash("All products and related records have been cleared.", "success")
                return redirect(url_for("admin.data_management"))

        if section == "suppliers" or section == "all":
            db.session.execute(db.delete(PurchaseItem))
            db.session.execute(db.delete(Purchase))
            db.session.execute(db.delete(Supplier))
            if section == "suppliers":
                db.session.commit()
                flash("All suppliers have been cleared.", "success")
                return redirect(url_for("admin.data_management"))

        if section == "categories" or section == "all":
            db.session.execute(db.delete(Category))
            if section == "categories":
                db.session.commit()
                flash("All categories have been cleared.", "success")
                return redirect(url_for("admin.data_management"))

        if section == "all":
            db.session.commit()
            flash("All data has been cleared. Products, sales, purchases, expenses, stock movements, suppliers and categories removed.", "success")
            return redirect(url_for("admin.data_management"))

        flash(f"Unknown section: {section}", "danger")

    except Exception as e:
        db.session.rollback()
        flash(f"Error clearing data: {e}", "danger")

    return redirect(url_for("admin.data_management"))
