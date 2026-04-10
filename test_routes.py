from smart_mart.app import create_app
app = create_app()

ROUTES = [
    '/dashboard/', '/sales/', '/sales/create', '/inventory/',
    '/inventory/categories', '/purchases/', '/purchases/suppliers',
    '/purchase-orders/', '/returns/', '/expenses/', '/reports/cash-flow',
    '/reports/sales', '/reports/profitability', '/reports/inventory-valuation',
    '/reports/staff-efficiency', '/reports/credit-udharo', '/reports/dead-stock',
    '/operations/', '/operations/credits', '/operations/suppliers',
    '/operations/closing', '/operations/shifts', '/operations/loyalty',
    '/operations/branches', '/operations/reorders', '/operations/eod',
    '/operations/credit-risk', '/operations/notifications',
    '/stock-take/', '/supplier-returns/', '/promotions/',
    '/customers/', '/ai/insights', '/ai/chatbot', '/advisor/',
    '/alerts/', '/settings/', '/admin/users', '/admin/permissions',
    '/admin/staff-activity', '/admin/audit-log', '/admin/backup',
    '/admin/data-management', '/transfers/', '/online-orders/',
]

with app.test_client() as c:
    with app.app_context():
        from smart_mart.models.user import User
        from smart_mart.extensions import db
        u = db.session.execute(db.select(User).filter_by(role='admin').limit(1)).scalar_one_or_none()
        with c.session_transaction() as sess:
            sess['_user_id'] = str(u.id)
            sess['_fresh'] = True
        failed = []
        for r in ROUTES:
            resp = c.get(r)
            if resp.status_code not in (200, 302):
                failed.append((r, resp.status_code))
                body = resp.data.decode('utf-8', errors='replace')
                for kw in ['BuildError', 'TemplateNotFound', 'jinja2', 'AttributeError', 'OperationalError']:
                    idx = body.find(kw)
                    if idx >= 0:
                        print(f'  ERROR: {body[max(0,idx-30):idx+200]}')
                        break
        if failed:
            for r, s in failed:
                print(f'FAIL {s}: {r}')
        else:
            print(f'ALL {len(ROUTES)} routes OK')
