import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ['FLASK_ENV'] = 'development'
from smart_mart.app import create_app
app = create_app('development')
with app.app_context():
    from smart_mart.extensions import db
    from sqlalchemy import text
    with db.engine.begin() as conn:
        conn.execute(text("DELETE FROM stock_movements WHERE product_id IN (SELECT id FROM products WHERE sku='TEST-RICE-001')"))
        conn.execute(text("DELETE FROM sale_items WHERE sale_id IN (SELECT id FROM sales WHERE customer_name='Test Customer')"))
        conn.execute(text("DELETE FROM sales WHERE customer_name='Test Customer'"))
        conn.execute(text("DELETE FROM purchase_items WHERE purchase_id IN (SELECT id FROM purchases WHERE total_cost=4500.00)"))
        conn.execute(text("DELETE FROM purchases WHERE total_cost=4500.00"))
        conn.execute(text("DELETE FROM products WHERE sku='TEST-RICE-001'"))
    print('Cleaned up test data')
