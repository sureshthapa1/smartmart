"""Quick route audit script."""
from smart_mart.app import create_app
app = create_app()

with app.app_context():
    from smart_mart.extensions import db
    from smart_mart.models.user import User
    from smart_mart.services.authenticator import hash_password
    db.create_all()
    u = db.session.execute(db.select(User).where(User.role=='admin').limit(1)).scalar_one_or_none()
    if not u:
        u = User(username='audit_admin', password_hash=hash_password('admin123'), role='admin')
        db.session.add(u)
        db.session.commit()
    u.password_hash = hash_password('admin123')
    db.session.commit()
    uname = u.username

client = app.test_client()
client.testing = True

with client:
    r = client.post('/auth/login', data={'username': uname, 'password': 'admin123'}, follow_redirects=True)
    print('Login:', r.status_code)

    routes = [
        '/dashboard/',
        '/inventory/',
        '/inventory/categories',
        '/sales/',
        '/sales/create',
        '/purchases/',
        '/purchases/suppliers',
        '/reports/sales',
        '/reports/profitability',
        '/reports/cash-flow',
        '/reports/credit-udharo',
        '/reports/dead-stock',
        '/reports/inventory-valuation',
        '/reports/top-products',
        '/reports/stock-analysis',
        '/reports/staff-efficiency',
        '/reports/category-performance',
        '/reports/least-products',
        '/alerts/',
        '/expenses/',
        '/customers/',
        '/operations/',
        '/operations/credit-risk',
        '/operations/credits',
        '/operations/shifts',
        '/operations/loyalty',
        '/operations/notifications',
        '/operations/reorders',
        '/operations/branches',
        '/operations/closing',
        '/operations/eod',
        '/operations/suppliers',
        '/operations/inventory-tools',
        '/promotions/',
        '/stock-take/',
        '/supplier-returns/',
        '/transfers/',
        '/purchase-orders/',
        '/online-orders/',
        '/online-orders/analytics',
        '/admin/users',
        '/admin/permissions',
        '/admin/audit-log',
        '/admin/backup',
        '/admin/staff-activity',
        '/admin/data-management',
        '/settings/',
        '/ai/insights',
        '/ai/chatbot',
        '/ai/cashflow',
        '/ai/customers',
        '/ai/anomalies',
        '/ai/advanced',
        '/ai/competitor-pricing',
        '/ai/feedback',
        '/ai/voice',
        '/advisor/',
    ]

    fails = []
    for route in routes:
        r = client.get(route)
        if r.status_code not in (200, 302):
            fails.append((r.status_code, route))
        else:
            print(f'  OK {r.status_code}  {route}')

    print()
    if fails:
        print('=== FAILURES ===')
        for code, route in fails:
            print(f'  FAIL {code}  {route}')
    else:
        print('ALL ROUTES OK')
