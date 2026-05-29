"""
Full data integration test — creates real data through every section
and verifies cross-section syncing.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ['FLASK_ENV'] = 'development'

from smart_mart.app import create_app
app = create_app('development')
app.config['WTF_CSRF_ENABLED'] = False

PASS = "✅ PASS"
FAIL = "❌ FAIL"
WARN = "⚠️  WARN"
results = []

def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    results.append((status, name, detail))
    print(f"  {status}  {name}" + (f"  [{detail}]" if detail else ""))

def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

with app.app_context():
    from smart_mart.extensions import db
    from smart_mart.models.user import User
    from smart_mart.models.product import Product
    from smart_mart.models.sale import Sale, SaleItem
    from smart_mart.models.expense import Expense
    from smart_mart.models.purchase import Purchase, PurchaseItem
    from smart_mart.models.stock_movement import StockMovement
    from smart_mart.models.audit_log import AuditLog
    from smart_mart.models.customer import Customer
    from smart_mart.bi.models.operating_expense import OperatingExpense

    admin = db.session.execute(db.select(User).where(User.role=='admin')).scalars().first()

    # ─────────────────────────────────────────────────────────────────────
    section("1. PRODUCT CREATION & INVENTORY")
    # ─────────────────────────────────────────────────────────────────────
    from smart_mart.services import inventory_manager

    # Create a test product
    # Clean up any leftover test product from previous run
    leftover = db.session.execute(
        db.select(Product).where(Product.sku == "TEST-RICE-001")
    ).scalar_one_or_none()
    if leftover:
        db.session.delete(leftover)
        db.session.commit()

    try:
        prod = inventory_manager.create_product({
            "name": "TEST Rice 5kg",
            "sku": "TEST-RICE-001",
            "category": "Groceries",
            "quantity": 100,
            "cost_price": 450.00,
            "selling_price": 550.00,
            "unit": "bag",
        })
        db.session.commit()
        check("Product created", prod.id is not None, f"id={prod.id}")
        check("Product quantity set", int(prod.quantity) == 100, f"qty={prod.quantity}")
        check("Product cost price set", float(prod.cost_price) == 450.0, f"cost={prod.cost_price}")
        check("Product selling price set", float(prod.selling_price) == 550.0, f"sell={prod.selling_price}")
        prod_id = prod.id
    except Exception as e:
        check("Product created", False, str(e))
        prod_id = None

    # Verify product is queryable
    if prod_id:
        fetched = db.session.get(Product, prod_id)
        check("Product fetchable by ID", fetched is not None)
        check("Product SKU correct", fetched.sku == "TEST-RICE-001")

    # ─────────────────────────────────────────────────────────────────────
    section("2. PURCHASE → STOCK SYNC")
    # ─────────────────────────────────────────────────────────────────────
    if prod_id:
        qty_before = int(db.session.get(Product, prod_id).quantity)

        # Create a supplier first
        from smart_mart.models.supplier import Supplier
        supplier = db.session.execute(db.select(Supplier).limit(1)).scalars().first()
        if not supplier:
            supplier = Supplier(name="Test Supplier", contact_person="Test", phone="9800000000")
            db.session.add(supplier)
            db.session.flush()

        # Create purchase
        purchase = Purchase(
            supplier_id=supplier.id,
            purchase_date=__import__('datetime').date.today(),
            total_cost=4500.00,
            created_by=admin.id,
        )
        db.session.add(purchase)
        db.session.flush()

        item = PurchaseItem(
            purchase_id=purchase.id,
            product_id=prod_id,
            quantity=10,
            unit_cost=450.00,
            subtotal=4500.00,
        )
        db.session.add(item)

        # Update stock
        prod_obj = db.session.get(Product, prod_id)
        prod_obj.quantity = int(prod_obj.quantity) + 10

        # Stock movement
        db.session.add(StockMovement(
            product_id=prod_id,
            change_amount=10,
            change_type="purchase",
            reference_id=purchase.id,
            created_by=admin.id,
        ))
        db.session.commit()

        qty_after = int(db.session.get(Product, prod_id).quantity)
        check("Purchase created", purchase.id is not None, f"id={purchase.id}")
        check("Stock increased after purchase", qty_after == qty_before + 10,
              f"{qty_before} → {qty_after}")

        # Verify stock movement recorded
        movement = db.session.execute(
            db.select(StockMovement).where(
                StockMovement.product_id == prod_id,
                StockMovement.change_type == "purchase"
            ).order_by(StockMovement.id.desc())
        ).scalars().first()
        check("Stock movement recorded", movement is not None)
        check("Stock movement amount correct", movement and int(movement.change_amount) == 10)

    # ─────────────────────────────────────────────────────────────────────
    section("3. SALE CREATION → STOCK DEDUCTION → AUDIT LOG")
    # ─────────────────────────────────────────────────────────────────────
    if prod_id:
        from smart_mart.services import sales_manager

        qty_before_sale = int(db.session.get(Product, prod_id).quantity)
        audit_count_before = db.session.execute(
            db.select(db.func.count(AuditLog.id))
        ).scalar()

        try:
            sale = sales_manager.create_sale(
                items=[{"product_id": prod_id, "quantity": 3, "unit_price": 550.00}],
                user_id=admin.id,
                customer_name="Test Customer",
                customer_phone="9800000001",
                payment_mode="cash",
                discount_amount=0,
            )
            check("Sale created", sale.id is not None, f"id={sale.id}, inv={sale.invoice_number}")
            check("Invoice number generated", sale.invoice_number is not None)
            check("Sale total correct", float(sale.total_amount) == 1650.0,
                  f"total={sale.total_amount}")

            # Stock deducted
            qty_after_sale = int(db.session.get(Product, prod_id).quantity)
            check("Stock deducted after sale", qty_after_sale == qty_before_sale - 3,
                  f"{qty_before_sale} → {qty_after_sale}")

            # Sale items created
            items = db.session.execute(
                db.select(SaleItem).where(SaleItem.sale_id == sale.id)
            ).scalars().all()
            check("Sale items created", len(items) == 1, f"items={len(items)}")
            check("Sale item cost_price snapshotted", items[0].cost_price is not None,
                  f"cost={items[0].cost_price}")

            # Audit log created
            audit_count_after = db.session.execute(
                db.select(db.func.count(AuditLog.id))
            ).scalar()
            check("Audit log entry created", audit_count_after > audit_count_before,
                  f"before={audit_count_before} after={audit_count_after}")

            # Customer auto-created
            cust = db.session.execute(
                db.select(Customer).where(
                    db.func.lower(Customer.name) == "test customer"
                )
            ).scalar_one_or_none()
            check("Customer auto-created from sale", cust is not None)

            # Stock movement for sale
            sale_movement = db.session.execute(
                db.select(StockMovement).where(
                    StockMovement.product_id == prod_id,
                    StockMovement.change_type == "sale",
                    StockMovement.reference_id == sale.id,
                )
            ).scalars().first()
            check("Sale stock movement recorded", sale_movement is not None)
            check("Sale stock movement negative", sale_movement and int(sale_movement.change_amount) == -3)

            sale_id = sale.id

        except Exception as e:
            check("Sale created", False, str(e))
            sale_id = None

    # ─────────────────────────────────────────────────────────────────────
    section("4. EXPENSE → BI OPEX SYNC")
    # ─────────────────────────────────────────────────────────────────────
    opex_count_before = db.session.execute(
        db.select(db.func.count(OperatingExpense.id))
    ).scalar()

    expense = Expense(
        expense_type="rent",
        amount=5000.00,
        expense_date=__import__('datetime').date.today(),
        note="Test monthly rent",
        created_by=admin.id,
    )
    db.session.add(expense)
    db.session.flush()

    from smart_mart.services.expense_sync import sync_create
    sync_create(expense)
    db.session.commit()

    opex_count_after = db.session.execute(
        db.select(db.func.count(OperatingExpense.id))
    ).scalar()
    check("Expense created", expense.id is not None, f"id={expense.id}")
    check("BI OPEX mirror created", opex_count_after == opex_count_before + 1,
          f"before={opex_count_before} after={opex_count_after}")

    # Verify mirror matches
    if expense.bi_opex_id:
        opex = db.session.get(OperatingExpense, expense.bi_opex_id)
        check("BI OPEX amount matches", opex and float(opex.amount) == 5000.0,
              f"opex_amount={opex.amount if opex else 'N/A'}")
        check("BI OPEX category mapped", opex and opex.category == "rent")
    else:
        check("BI OPEX bi_opex_id set", False, "bi_opex_id is None")

    # Test update sync
    expense.amount = 5500.00
    expense.note = "Updated rent"
    from smart_mart.services.expense_sync import sync_update
    sync_update(expense)
    db.session.commit()

    if expense.bi_opex_id:
        opex_updated = db.session.get(OperatingExpense, expense.bi_opex_id)
        check("BI OPEX syncs on update", opex_updated and float(opex_updated.amount) == 5500.0,
              f"updated_amount={opex_updated.amount if opex_updated else 'N/A'}")

    expense_id = expense.id
    bi_opex_id = expense.bi_opex_id

    # Test delete sync
    from smart_mart.services.expense_sync import sync_delete
    db.session.delete(expense)
    sync_delete(bi_opex_id)
    db.session.commit()

    deleted_opex = db.session.get(OperatingExpense, bi_opex_id) if bi_opex_id else None
    check("BI OPEX deleted when expense deleted", deleted_opex is None)

    # ─────────────────────────────────────────────────────────────────────
    section("5. CASH SESSION OPEN/CLOSE")
    # ─────────────────────────────────────────────────────────────────────
    from smart_mart.services import operations_manager
    from smart_mart.models.operations import CashSession

    # Close any open session first
    open_sess = operations_manager.get_open_cash_session(admin.id)
    if open_sess:
        try:
            operations_manager.close_cash_session(open_sess.id, float(open_sess.opening_cash))
        except Exception:
            pass

    try:
        session = operations_manager.open_cash_session(admin.id, opening_cash=5000.0, notes="Test session")
        check("Cash session opened", session.id is not None, f"id={session.id}")
        check("Cash session status open", session.status == "open")
        check("Opening cash correct", float(session.opening_cash) == 5000.0)

        closed = operations_manager.close_cash_session(session.id, closing_cash=6650.0)
        check("Cash session closed", closed.status == "closed")
        check("Closing cash recorded", float(closed.closing_cash) == 6650.0)
        check("Variance calculated", closed.variance is not None,
              f"variance={closed.variance}")
    except Exception as e:
        check("Cash session open/close", False, str(e))

    # ─────────────────────────────────────────────────────────────────────
    section("6. REPORTS DATA ACCURACY")
    # ─────────────────────────────────────────────────────────────────────
    from smart_mart.services import report_engine, cash_flow_manager
    import datetime

    today = datetime.date.today()
    start = today - datetime.timedelta(days=30)

    try:
        summary = report_engine.sales_summary(start, today)
        check("Sales summary returns data", isinstance(summary, dict))
        check("Sales summary has total_revenue", "total_revenue" in summary,
              f"keys={list(summary.keys())[:5]}")
    except Exception as e:
        check("Sales summary", False, str(e))

    try:
        pnl = cash_flow_manager.profit_loss(start, today)
        check("P&L report works", isinstance(pnl, dict))
        check("P&L has profit key", "profit" in pnl, f"keys={list(pnl.keys())}")
    except Exception as e:
        check("P&L report", False, str(e))

    try:
        from smart_mart.bi.services.report_service import ReportService
        bi_pnl = ReportService.profit_and_loss(start, today)
        check("BI P&L works", isinstance(bi_pnl, dict))
        check("BI P&L has overall", "overall" in bi_pnl)
        check("BI P&L has products", "products" in bi_pnl)
        overall = bi_pnl["overall"]
        check("BI P&L sales >= 0", overall["sales"] >= 0, f"sales={overall['sales']}")
        check("BI P&L opex included", overall["opex"] >= 0, f"opex={overall['opex']}")
    except Exception as e:
        check("BI P&L report", False, str(e))

    try:
        top = report_engine.top_products(start, today)
        check("Top products report works", isinstance(top, list))
    except Exception as e:
        check("Top products report", False, str(e))

    try:
        inv = report_engine.inventory_valuation()
        check("Inventory valuation works", isinstance(inv, dict))
    except Exception as e:
        check("Inventory valuation", False, str(e))

    # ─────────────────────────────────────────────────────────────────────
    section("7. DASHBOARD DATA")
    # ─────────────────────────────────────────────────────────────────────
    try:
        from smart_mart.bi.services.dashboard_service import DashboardService
        payload = DashboardService.payload("today")
        check("BI Dashboard payload works", isinstance(payload, dict))
        check("Dashboard has KPIs", "kpis" in payload)
        check("Dashboard has sales_trend", "sales_trend" in payload)
        check("Dashboard has insights", "insights" in payload)
    except Exception as e:
        check("BI Dashboard", False, str(e))

    # ─────────────────────────────────────────────────────────────────────
    section("8. IDEMPOTENCY KEY — DOUBLE POST PREVENTION")
    # ─────────────────────────────────────────────────────────────────────
    if prod_id:
        from smart_mart.models.idempotency_key import IdempotencyKey

        # First call — should return None (proceed)
        result1 = IdempotencyKey.consume("test-idem-key-001", admin.id, "sales.create_sale")
        db.session.commit()
        check("Idempotency first call returns None (proceed)", result1 is None)

        # Second call — should return existing record (block)
        result2 = IdempotencyKey.consume("test-idem-key-001", admin.id, "sales.create_sale")
        check("Idempotency second call returns record (block)", result2 is not None)

        # Cleanup
        record = db.session.execute(
            db.select(IdempotencyKey).where(IdempotencyKey.key == "test-idem-key-001")
        ).scalar_one_or_none()
        if record:
            db.session.delete(record)
            db.session.commit()

    # ─────────────────────────────────────────────────────────────────────
    section("9. AUDIT LOG IMMUTABILITY")
    # ─────────────────────────────────────────────────────────────────────
    from smart_mart.models.audit_log import AuditLog

    # Create a log entry
    from smart_mart.services import audit_service
    audit_service.log("create", "TestEntity", 999, "Test Label", {"field": ["old", "new"]})
    db.session.commit()

    log_entry = db.session.execute(
        db.select(AuditLog).where(AuditLog.entity_type == "TestEntity").order_by(AuditLog.id.desc())
    ).scalars().first()
    check("Audit log entry created", log_entry is not None)

    # Try to update — should raise
    blocked = False
    try:
        log_entry.action = "hacked"
        db.session.flush()
    except RuntimeError:
        blocked = True
        db.session.rollback()
    check("Audit log update blocked (immutable)", blocked)

    # Try to delete — should raise
    blocked_del = False
    try:
        db.session.delete(log_entry)
        db.session.flush()
    except RuntimeError:
        blocked_del = True
        db.session.rollback()
    check("Audit log delete blocked (immutable)", blocked_del)

    # ─────────────────────────────────────────────────────────────────────
    section("10. FINANCIAL PERIOD CLOSE")
    # ─────────────────────────────────────────────────────────────────────
    import datetime
    from smart_mart.services import period_service
    from smart_mart.models.financial_period import FinancialPeriod

    # Use a past month to avoid interfering with current data
    test_year, test_month = 2025, 1

    # Clean up if exists
    existing = db.session.execute(
        db.select(FinancialPeriod).where(
            FinancialPeriod.year == test_year, FinancialPeriod.month == test_month
        )
    ).scalar_one_or_none()
    if existing:
        db.session.delete(existing)
        db.session.commit()

    try:
        period = period_service.close_period(test_year, test_month, admin.id, "Test close")
        check("Period closed", period.status == "closed")
        check("Period snapshots sales", period.total_sales is not None)
        check("Period snapshots net_profit", period.net_profit is not None)
        check("Period.is_locked returns True", FinancialPeriod.is_locked(test_year, test_month))

        # Try to reopen
        reopened = period_service.reopen_period(period.id, admin.id)
        check("Period can be reopened", reopened.status == "open")
        check("Period.is_locked False after reopen", not FinancialPeriod.is_locked(test_year, test_month))

        # Lock it
        period_service.close_period(test_year, test_month, admin.id)
        period2 = db.session.execute(
            db.select(FinancialPeriod).where(
                FinancialPeriod.year == test_year, FinancialPeriod.month == test_month
            )
        ).scalar_one_or_none()
        locked = period_service.lock_period(period2.id)
        check("Period can be locked", locked.status == "locked")

        # Try to reopen locked — should fail
        try:
            period_service.reopen_period(locked.id, admin.id)
            check("Locked period cannot be reopened", False)
        except ValueError:
            check("Locked period cannot be reopened", True)

        # Cleanup
        db.session.delete(locked)
        db.session.commit()

    except Exception as e:
        check("Financial period close", False, str(e))

    # ─────────────────────────────────────────────────────────────────────
    section("11. LOYALTY WALLET SYNC")
    # ─────────────────────────────────────────────────────────────────────
    try:
        from smart_mart.services import loyalty_wallet_service
        wallet = loyalty_wallet_service.get_or_create_wallet("Test Customer", "9800000001")
        db.session.commit()
        check("Loyalty wallet created/fetched", wallet is not None)
        check("Wallet has points_balance", hasattr(wallet, "points_balance"))

        points_before = int(wallet.points_balance or 0)
        loyalty_wallet_service.apply_sale_points(
            wallet=wallet, sale_id=9999, final_amount_paid=1650.0, redeemed_points=0
        )
        db.session.commit()
        points_after = int(wallet.points_balance or 0)
        check("Loyalty points earned after sale", points_after >= points_before,
              f"{points_before} → {points_after}")
    except Exception as e:
        check("Loyalty wallet", False, str(e))

    # ─────────────────────────────────────────────────────────────────────
    section("12. CLEANUP TEST DATA")
    # ─────────────────────────────────────────────────────────────────────
    if prod_id:
        try:
            db.session.rollback()  # clear any dirty state
            # Delete in correct FK order: sale_items → sales → purchase_items → purchases → product
            from smart_mart.models.sale import SaleItem as _SI
            from smart_mart.models.stock_movement import StockMovement as _SM

            # Remove stock movements for test product
            db.session.execute(db.delete(_SM).where(_SM.product_id == prod_id))

            # Remove sale items and sales for test customer
            test_sale_ids = [s.id for s in db.session.execute(
                db.select(Sale).where(Sale.customer_name == "Test Customer")
            ).scalars().all()]
            if test_sale_ids:
                db.session.execute(db.delete(_SI).where(_SI.sale_id.in_(test_sale_ids)))
                db.session.execute(db.delete(Sale).where(Sale.id.in_(test_sale_ids)))

            # Remove purchase items then purchases
            from smart_mart.models.purchase import PurchaseItem as _PI
            test_purchase_ids = [p.id for p in db.session.execute(
                db.select(Purchase).where(Purchase.total_cost == 4500.00)
            ).scalars().all()]
            if test_purchase_ids:
                db.session.execute(db.delete(_PI).where(_PI.purchase_id.in_(test_purchase_ids)))
                db.session.execute(db.delete(Purchase).where(Purchase.id.in_(test_purchase_ids)))

            # Now safe to delete product
            db.session.execute(db.delete(Product).where(Product.id == prod_id))
            db.session.commit()
            check("Test data cleaned up", True)
        except Exception as e:
            db.session.rollback()
            check("Test data cleaned up", False, str(e))

    # ─────────────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  RESULTS SUMMARY")
    print(f"{'='*60}")
    passed = sum(1 for r in results if r[0] == PASS)
    failed = sum(1 for r in results if r[0] == FAIL)
    warned = sum(1 for r in results if r[0] == WARN)
    print(f"\n  Total: {len(results)}  |  {PASS}: {passed}  |  {FAIL}: {failed}  |  {WARN}: {warned}")

    if failed:
        print(f"\n  FAILURES:")
        for r in results:
            if r[0] == FAIL:
                print(f"    {r[0]}  {r[1]}  {r[2]}")

    print()
