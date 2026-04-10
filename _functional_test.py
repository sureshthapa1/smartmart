"""Functional POST flow tests."""
from datetime import date
from smart_mart.app import create_app
from smart_mart.services.authenticator import hash_password

app = create_app()

with app.app_context():
    from smart_mart.extensions import db
    from smart_mart.models.user import User
    from smart_mart.models.product import Product
    from smart_mart.models.supplier import Supplier
    u = db.session.execute(db.select(User).where(User.role == 'admin').limit(1)).scalar_one()
    u.password_hash = hash_password('admin123')
    db.session.commit()
    uname = u.username
    p = db.session.execute(db.select(Product).where(Product.quantity > 5).limit(1)).scalar_one_or_none()
    s = db.session.execute(db.select(Supplier).limit(1)).scalar_one_or_none()
    pid = p.id if p else None
    pprice = float(p.selling_price) if p else 10.0
    sid = s.id if s else None
    print(f"Product: {p.name if p else 'NONE'} id={pid} qty={p.quantity if p else 0}")
    print(f"Supplier: {s.name if s else 'NONE'} id={sid}")

client = app.test_client()
fails = []

def check(label, r, expected=(200, 302)):
    if r.status_code in expected:
        loc = r.headers.get('Location', '')
        print(f"  OK {r.status_code}  {label}  {loc}")
    else:
        body = r.data.decode('utf-8', errors='ignore')
        # grab first error line
        import re
        errs = re.findall(r'alert-danger[^>]*>([^<]{5,80})', body)
        hint = errs[0].strip() if errs else body[200:300].replace('\n', ' ')
        print(f"  FAIL {r.status_code}  {label}  hint: {hint[:120]}")
        fails.append(f"{r.status_code} {label}")

with client:
    r = client.post('/auth/login', data={'username': uname, 'password': 'admin123'}, follow_redirects=True)
    print(f"Login: {r.status_code}")

    # ── Sale creation ──────────────────────────────────────────────────────
    if pid:
        r = client.post('/sales/create', data={
            'customer_name': 'Audit Customer',
            'payment_mode': 'cash',
            'discount_amount': '0',
            'items[0][product_id]': str(pid),
            'items[0][quantity]': '1',
            'items[0][unit_price]': str(pprice),
        }, follow_redirects=False)
        check('Create sale', r)

    # ── Expense creation ───────────────────────────────────────────────────
    r = client.post('/expenses/create', data={
        'expense_type': 'miscellaneous',
        'amount': '250',
        'expense_date': str(date.today()),
        'note': 'Audit test',
    }, follow_redirects=False)
    check('Create expense', r)

    # ── Customer creation ──────────────────────────────────────────────────
    r = client.post('/customers/create', data={
        'name': 'Audit Customer New',
        'phone': '9800000088',
        'address': 'Kathmandu',
        'email': '',
    }, follow_redirects=False)
    check('Create customer', r)

    # ── Promotion creation ─────────────────────────────────────────────────
    r = client.post('/promotions/create', data={
        'name': 'Audit Promo',
        'promo_type': 'percentage',
        'discount_value': '10',
        'scope': 'all',
        'is_active': 'on',
        'start_date': str(date.today()),
        'end_date': str(date.today()),
    }, follow_redirects=False)
    check('Create promotion', r)

    # ── Stock take creation ────────────────────────────────────────────────
    r = client.post('/stock-take/create', data={
        'scope': 'all',
        'notes': 'Audit stock take',
    }, follow_redirects=False)
    check('Create stock take', r)

    # ── Purchase creation ──────────────────────────────────────────────────
    if sid and pid:
        r = client.post('/purchases/create', data={
            'supplier_id': str(sid),
            'purchase_date': str(date.today()),
            'items[0][product_id]': str(pid),
            'items[0][quantity]': '10',
            'items[0][unit_cost]': '50',
        }, follow_redirects=False)
        check('Create purchase', r)

    # ── Product creation ───────────────────────────────────────────────────
    if sid:
        r = client.post('/inventory/create', data={
            'name': 'Audit Test Product',
            'category': 'Test',
            'sku': 'AUDIT-SKU-001',
            'cost_price': '40',
            'selling_price': '70',
            'quantity': '20',
            'supplier_id': str(sid),
        }, follow_redirects=False)
        check('Create product', r)

    # ── Supplier return creation ───────────────────────────────────────────
    if sid and pid:
        r = client.post('/supplier-returns/create', data={
            'supplier_id': str(sid),
            'reason': 'Damaged',
            'items[0][product_id]': str(pid),
            'items[0][quantity]': '1',
            'items[0][unit_cost]': '50',
        }, follow_redirects=False)
        check('Create supplier return', r)

    # ── Online order creation ──────────────────────────────────────────────
    if pid:
        r = client.post('/online-orders/create', data={
            'customer_name': 'Online Test',
            'customer_phone': '9800000077',
            'delivery_address': 'Lalitpur',
            'payment_mode': 'cod',
            'items[0][product_id]': str(pid),
            'items[0][quantity]': '1',
            'items[0][unit_price]': str(pprice),
        }, follow_redirects=False)
        check('Create online order', r)

    # ── Settings save ──────────────────────────────────────────────────────
    r = client.post('/settings/', data={
        'shop_name': 'Smart Mart Test',
        'currency_symbol': 'NPR',
        'low_stock_threshold': '10',
        'vat_enabled': '',
        'vat_rate': '13',
    }, follow_redirects=False)
    check('Save settings', r)

    # ── Change password ────────────────────────────────────────────────────
    r = client.post('/auth/change-password', data={
        'current_password': 'admin123',
        'new_password': 'admin123',
        'confirm_password': 'admin123',
    }, follow_redirects=False)
    check('Change password', r)

print()
if fails:
    print(f"=== {len(fails)} FAILURES ===")
    for f in fails:
        print(f"  {f}")
else:
    print("ALL FUNCTIONAL TESTS PASSED")
