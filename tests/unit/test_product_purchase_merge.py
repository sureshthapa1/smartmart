"""Tests for the merged product + purchase creation workflow.

Locks in the fix for a real double-counting bug: creating a product with
opening stock (quantity > 0) and then separately logging a Purchase for
the same units resulted in the product's quantity being counted twice
(e.g. 50 from product creation + 50 from the purchase = 100 actual units
recorded for only 50 physically received).

Now: providing opening stock + a supplier at product-creation time routes
the stock through a real Purchase record (creating the Purchase, the
PurchaseItem, a StockMovement, and an Expense entry) instead of setting
Product.quantity directly — so there is exactly one path by which stock
quantity increases, and it is always tracked as a real purchase expense.

Also locks in: editing an existing product can never silently change its
quantity — that must go through a new Purchase (restock) or a Stock Take
(count correction).
"""
from smart_mart.extensions import db
from smart_mart.models.product import Product
from smart_mart.models.purchase import Purchase, PurchaseItem
from smart_mart.models.expense import Expense
from smart_mart.models.supplier import Supplier
from smart_mart.models.stock_movement import StockMovement


def _admin_client(client, db):
    from tests.test_goldkernel_features import _admin, _login
    user = _admin("merge_test_admin")
    _login(client, user)
    return user


def _supplier(db, name="Test Supplier"):
    s = Supplier(name=name)
    db.session.add(s)
    db.session.commit()
    return s


def test_creating_product_with_opening_stock_and_supplier_creates_one_purchase(client, db):
    """The core regression test: quantity must be set exactly once, via the
    Purchase, not double-counted."""
    _admin_client(client, db)
    supplier = _supplier(db)

    resp = client.post("/inventory/create", data={
        "name": "Regression Test Cashews",
        "sku": "REGR-TEST-001",
        "cost_price": "500",
        "selling_price": "800",
        "quantity": "50",
        "supplier_id": str(supplier.id),
        "purchase_date": "2026-07-19",
        "unit": "kg",
    }, follow_redirects=True)
    assert resp.status_code == 200

    product = db.session.execute(
        db.select(Product).where(Product.sku == "REGR-TEST-001")
    ).scalar_one_or_none()
    assert product is not None
    assert product.quantity == 50, "quantity must be exactly 50, not double-counted to 100"

    purchases = db.session.execute(
        db.select(Purchase).where(Purchase.supplier_id == supplier.id)
    ).scalars().all()
    assert len(purchases) == 1, "exactly one Purchase should be created, not zero or two"

    items = db.session.execute(
        db.select(PurchaseItem).where(PurchaseItem.purchase_id == purchases[0].id)
    ).scalars().all()
    assert len(items) == 1
    assert items[0].product_id == product.id
    assert items[0].quantity == 50
    assert float(items[0].unit_cost) == 500.0

    # Expense must be tracked for accurate shop expense totals
    expenses = db.session.execute(
        db.select(Expense).where(Expense.expense_type == "purchase")
    ).scalars().all()
    assert len(expenses) == 1
    assert float(expenses[0].amount) == 25000.0  # 50 * 500


def test_creating_product_with_zero_quantity_creates_no_purchase(client, db):
    """Listing a product before receiving any stock must not create a
    spurious Purchase/expense record."""
    _admin_client(client, db)

    resp = client.post("/inventory/create", data={
        "name": "Zero Stock Product",
        "sku": "REGR-TEST-002",
        "cost_price": "100",
        "selling_price": "150",
        "quantity": "0",
        "supplier_id": "",
        "unit": "pcs",
    }, follow_redirects=True)
    assert resp.status_code == 200

    product = db.session.execute(
        db.select(Product).where(Product.sku == "REGR-TEST-002")
    ).scalar_one_or_none()
    assert product is not None
    assert product.quantity == 0

    purchase_count = db.session.execute(db.select(db.func.count(Purchase.id))).scalar()
    assert purchase_count == 0


def test_creating_product_with_quantity_but_no_supplier_still_tracks_stock_movement(client, db):
    """Opening stock without a supplier shouldn't create a Purchase (no one
    to attribute the expense to), but the quantity should still be set and
    ideally an audit trail (StockMovement) should exist for it."""
    _admin_client(client, db)

    resp = client.post("/inventory/create", data={
        "name": "No Supplier Product",
        "sku": "REGR-TEST-003",
        "cost_price": "100",
        "selling_price": "150",
        "quantity": "20",
        "supplier_id": "",
        "unit": "pcs",
    }, follow_redirects=True)
    assert resp.status_code == 200

    product = db.session.execute(
        db.select(Product).where(Product.sku == "REGR-TEST-003")
    ).scalar_one_or_none()
    assert product is not None
    assert product.quantity == 20

    purchase_count = db.session.execute(db.select(db.func.count(Purchase.id))).scalar()
    assert purchase_count == 0

    movements = db.session.execute(
        db.select(StockMovement).where(StockMovement.product_id == product.id)
    ).scalars().all()
    assert len(movements) >= 1, (
        "opening stock without a supplier should still leave an audit trail "
        "(StockMovement) even though there's no Purchase/expense to record"
    )


def test_editing_product_cannot_change_quantity(client, db):
    """Regression test for the reverse risk: the edit form must never be
    usable to silently overwrite stock counts."""
    _admin_client(client, db)
    supplier = _supplier(db, "Edit Test Supplier")

    client.post("/inventory/create", data={
        "name": "Edit Guard Product", "sku": "REGR-TEST-004",
        "cost_price": "200", "selling_price": "300", "quantity": "10",
        "supplier_id": str(supplier.id), "purchase_date": "2026-07-19", "unit": "pcs",
    })
    product = db.session.execute(
        db.select(Product).where(Product.sku == "REGR-TEST-004")
    ).scalar_one_or_none()
    assert product.quantity == 10

    # Attempt to smuggle a huge quantity through the edit form
    resp = client.post(f"/inventory/{product.id}/edit", data={
        "name": "Edit Guard Product", "sku": "REGR-TEST-004",
        "cost_price": "200", "selling_price": "350", "quantity": "999999", "unit": "pcs",
    }, follow_redirects=True)
    assert resp.status_code == 200

    db.session.refresh(product)
    assert product.quantity == 10, "quantity must be unchanged by the edit form"
    assert float(product.selling_price) == 350.0, "other fields should still update normally"
