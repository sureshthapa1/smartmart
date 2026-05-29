import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ['FLASK_ENV'] = 'development'
from smart_mart.app import create_app
app = create_app('development')
app.config['WTF_CSRF_ENABLED'] = False

with app.app_context():
    from smart_mart.extensions import db
    from smart_mart.models.user import User
    admin = db.session.execute(db.select(User).where(User.role=='admin')).scalars().first()
    admin_id = admin.id

client = app.test_client()
with client.session_transaction() as sess:
    sess['_user_id'] = str(admin_id)
    sess['_fresh'] = True

print("=== AI ROUTE TESTS ===")
routes = ['/dashboard/', '/ai/chatbot', '/ai/insights', '/ai/advanced',
          '/ai/anomalies', '/ai/cashflow', '/ai/voice', '/advisor/']
all_ok = True
for r in routes:
    resp = client.get(r, follow_redirects=True)
    flag = ' ERROR' if resp.status_code == 500 else ''
    print(f'  {resp.status_code}  {r}{flag}')
    if resp.status_code == 500:
        all_ok = False
        print(resp.data.decode('utf-8', 'replace')[:300])

print()
print("=== AI SERVICE TESTS ===")
with app.app_context():
    # Cash flow prediction with day-of-week
    from smart_mart.services.ai_cashflow_prediction import predict_cashflow
    cf = predict_cashflow(7)
    print(f"  Cashflow: {cf['outlook']} | best_day={cf.get('best_day')} | forecasts={len(cf['forecasts'])}")
    dow = cf.get('dow_multipliers', {})
    print(f"  DoW multipliers: {dow}")

    # Product action recommendations
    from smart_mart.services.ai_business_advisor import product_action_recommendations
    actions = product_action_recommendations()
    print(f"  Product actions: {len(actions)} recommendations")
    for a in actions[:3]:
        print(f"    [{a['action_label']}] {a['product_name']}: {a['reason'][:60]}")

    # Chatbot
    from smart_mart.services.ai_engine import chatbot_query
    tests = [
        ('today sales', 'Today'),
        ('low stock', 'Low Stock'),
        ('profit this month', 'Profit'),
        ('forecast', 'Forecast'),
        ('help', 'Help'),
        ('aaj kati becha', 'Nepali query'),
        ('credit outstanding', 'Credit'),
        ('top product', 'Top product'),
    ]
    print()
    print("  Chatbot responses:")
    for q, label in tests:
        r = chatbot_query(q)
        first_line = r.split('\n')[0][:70]
        print(f"    [{label}] {first_line}")

    # Customer segmentation
    from smart_mart.services.ai_customer_segmentation import segment_customers
    seg = segment_customers()
    print(f"\n  Customer segments: {seg['total_customers']} customers, {seg['segments']}")

    # Anomaly detection
    from smart_mart.services.ai_anomaly_detection import full_anomaly_report
    anomalies = full_anomaly_report(30)
    print(f"  Anomalies: {anomalies['total_anomalies']} detected, risk={anomalies['risk_level']}")

print()
print("ALL OK" if all_ok else "SOME ERRORS FOUND")
