"""Regression tests for stock paths that moved units without a ledger row.

`products.quantity` is the number the shop trusts; `stock_movements` is the
audit trail that explains how it got there. Several paths mutated the first
without writing the second, so the two drifted apart permanently and the
shrinkage report counted the difference as unexplained loss. These tests pin
each of those paths to writing its movement.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from smart_mart.extensions import db as _db
from smart_mart.models.operations import Branch, ProductBatch, SupplierPayment
from smart_mart.models.product import Product
from smart_mart.models.purchase import Purchase
from smart_mart.models.stock_movement import StockMovement
from smart_mart.models.supplier import Supplier
from smart_mart.models.user import User
from smart_mart.services import purchase_manager, transfer_manager


def _user(username: str = "ledger_user") -> User:
    user = User(username=username, password_hash="x", role="admin")
    _db.session.add(user)
    _db.session.commit()
    return user


def _product(sku: str, qty: int = 100, cost: str = "50.00") -> Product:
    product = Product(
        name=f"Product {sku}",
        sku=sku,
        category="Grain",
        cost_price=Decimal(cost),
        selling_price=Decimal("80.00"),
        quantity=qty,
    )
    _db.session.add(product)
    _db.session.commit()
    return product


def _movements(product_id: int, change_type: str | None = None):
    stmt = _db.select(StockMovement).where(StockMovement.product_id == product_id)
    if change_type:
        stmt = stmt.where(StockMovement.change_type == change_type)
    return _db.session.execute(stmt).scalars().all()


# ── Stock transfers ───────────────────────────────────────────────────────

def _branches(db) -> tuple[Branch, Branch]:
    a = Branch(name="Kathmandu")
    b = Branch(name="Pokhara")
    db.session.add_all([a, b])
    db.session.commit()
    return a, b


def test_completing_a_transfer_writes_a_transfer_out_movement(db):
    """The deduction was invisible to the ledger — the BI ledger had a
    "Transfer Out" filter that nothing ever populated."""
    user = _user("transferrer")
    src, dst = _branches(db)
    product = _product("TR-RICE", qty=100)

    transfer = transfer_manager.create_transfer(
        src.id, dst.id, user.id,
        [{"product_id": product.id, "quantity": 30}],
    )
    assert _movements(product.id) == []

    transfer_manager.complete_transfer(transfer.id, user.id)

    assert db.session.get(Product, product.id).quantity == 70
    moves = _movements(product.id, "transfer_out")
    assert len(moves) == 1
    assert moves[0].change_amount == -30
    assert moves[0].reference_id == transfer.id
    assert moves[0].created_by == user.id
    assert str(src.id) in moves[0].note and str(dst.id) in moves[0].note


def test_transfer_movement_falls_back_to_the_creator_when_no_actor_given(db):
    """created_by is NOT NULL on stock_movements, so the fallback has to hold."""
    creator = _user("transfer_creator")
    src, dst = _branches(db)
    product = _product("TR-DAL", qty=40)

    transfer = transfer_manager.create_transfer(
        src.id, dst.id, creator.id, [{"product_id": product.id, "quantity": 10}]
    )
    transfer_manager.complete_transfer(transfer.id)

    moves = _movements(product.id, "transfer_out")
    assert len(moves) == 1
    assert moves[0].created_by == creator.id


def test_a_failed_transfer_leaves_no_movement_behind(db):
    """Insufficient stock must not half-apply: no deduction, no ledger row."""
    user = _user("transfer_short")
    src, dst = _branches(db)
    product = _product("TR-OIL", qty=5)

    transfer = transfer_manager.create_transfer(
        src.id, dst.id, user.id, [{"product_id": product.id, "quantity": 5}]
    )
    # Stock drops away underneath the pending transfer.
    db.session.get(Product, product.id).quantity = 2
    db.session.commit()

    with pytest.raises(ValueError, match="Insufficient stock"):
        transfer_manager.complete_transfer(transfer.id, user.id)

    db.session.rollback()
    assert _movements(product.id, "transfer_out") == []


# ── Purchase deletion ─────────────────────────────────────────────────────

def _purchase(user, product, qty=100, cost="50.00") -> Purchase:
    supplier = Supplier(name=f"Supplier {product.sku}")
    _db.session.add(supplier)
    _db.session.commit()
    return purchase_manager.create_purchase(
        supplier_id=supplier.id,
        items=[{"product_id": product.id, "quantity": qty, "unit_cost": Decimal(cost)}],
        purchase_date=date.today(),
        user_id=user.id,
    )


def test_deleting_an_untouched_purchase_reverses_stock_and_clears_its_batches(db):
    user = _user("pur_clean")
    product = _product("PUR-CLEAN", qty=0)
    purchase = _purchase(user, product, qty=100)
    assert db.session.get(Product, product.id).quantity == 100

    purchase_manager.delete_purchase(purchase.id, user.id)

    assert db.session.get(Product, product.id).quantity == 0
    assert db.session.get(Purchase, purchase.id) is None
    # Batches carry no cascade from Purchase, so they used to survive with a
    # dangling purchase_id and their full quantity_remaining.
    orphans = db.session.execute(
        db.select(ProductBatch).where(ProductBatch.purchase_id == purchase.id)
    ).scalars().all()
    assert orphans == []
    assert [m.change_amount for m in _movements(product.id, "purchase_reversal")] == [-100]


def test_deleting_a_purchase_whose_stock_was_sold_is_refused(db):
    """The old max(0, ...) clamp silently destroyed the units still on the shelf."""
    user = _user("pur_sold")
    product = _product("PUR-SOLD", qty=0)
    purchase = _purchase(user, product, qty=100)

    # 60 units go out the door.
    db.session.get(Product, product.id).quantity = 40
    db.session.commit()

    with pytest.raises(ValueError, match="negative stock"):
        purchase_manager.delete_purchase(purchase.id, user.id)

    db.session.rollback()
    # Refused outright: the 40 real units survive and the purchase still exists.
    assert db.session.get(Product, product.id).quantity == 40
    assert db.session.get(Purchase, purchase.id) is not None
    assert _movements(product.id, "purchase_reversal") == []


def test_deleting_a_purchase_with_a_drawn_down_batch_is_refused(db):
    user = _user("pur_batch")
    product = _product("PUR-BATCH", qty=0)
    purchase = _purchase(user, product, qty=100)

    batch = db.session.execute(
        db.select(ProductBatch).where(ProductBatch.purchase_id == purchase.id)
    ).scalars().first()
    assert batch is not None, "create_purchase should have opened a batch"
    batch.quantity_remaining = 70
    db.session.commit()

    with pytest.raises(ValueError, match="already been used"):
        purchase_manager.delete_purchase(purchase.id, user.id)

    db.session.rollback()
    assert db.session.get(Purchase, purchase.id) is not None


def test_deleting_a_purchase_with_a_supplier_payment_is_refused(db):
    """supplier_payments.purchase_id has no cascade — deleting orphaned it."""
    user = _user("pur_paid")
    product = _product("PUR-PAID", qty=0)
    purchase = _purchase(user, product, qty=10)

    db.session.add(SupplierPayment(
        supplier_id=purchase.supplier_id, purchase_id=purchase.id,
        user_id=user.id, amount=Decimal("500.00"), payment_mode="cash",
    ))
    db.session.commit()

    with pytest.raises(ValueError, match="supplier payments"):
        purchase_manager.delete_purchase(purchase.id, user.id)

    db.session.rollback()
    assert db.session.get(Purchase, purchase.id) is not None


def test_a_refused_delete_reverses_no_line_of_a_multi_line_purchase(db):
    """All-or-nothing: one bad line must not let the others deduct."""
    user = _user("pur_multi")
    good = _product("PUR-MULTI-OK", qty=0)
    bad = _product("PUR-MULTI-BAD", qty=0)
    supplier = Supplier(name="Multi Supplier")
    db.session.add(supplier)
    db.session.commit()

    purchase = purchase_manager.create_purchase(
        supplier_id=supplier.id,
        items=[
            {"product_id": good.id, "quantity": 20, "unit_cost": Decimal("10")},
            {"product_id": bad.id, "quantity": 20, "unit_cost": Decimal("10")},
        ],
        purchase_date=date.today(), user_id=user.id,
    )
    db.session.get(Product, bad.id).quantity = 5   # some already sold
    db.session.commit()

    with pytest.raises(ValueError, match="negative stock"):
        purchase_manager.delete_purchase(purchase.id, user.id)

    db.session.rollback()
    assert db.session.get(Product, good.id).quantity == 20
    assert db.session.get(Product, bad.id).quantity == 5
    assert _movements(good.id, "purchase_reversal") == []
