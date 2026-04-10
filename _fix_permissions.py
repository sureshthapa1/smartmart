"""
Patch all blueprints to use permission-based access instead of @admin_required.
Run once: python _fix_permissions.py
"""
import re

HELPER = '''

def _require_perm(perm: str):
    """Abort 403 if staff user lacks the given permission."""
    from flask import abort
    from flask_login import current_user as _cu
    if _cu.role != "admin":
        from ...models.user_permissions import UserPermissions
        p = UserPermissions.get_or_create(_cu.id)
        if not getattr(p, perm, False):
            abort(403)

'''

# (file, [(old_decorator+def, new_decorator+def+perm_call), ...])
PATCHES = {
    "smart_mart/blueprints/reports/routes.py": [
        # All report routes — replace @admin_required with @login_required + perm check inline
        # We'll do a bulk replace of the decorator pattern
    ],
    "smart_mart/blueprints/customers/routes.py": [],
    "smart_mart/blueprints/promotions/routes.py": [],
    "smart_mart/blueprints/transfers/routes.py": [],
    "smart_mart/blueprints/stock_take/routes.py": [],
    "smart_mart/blueprints/supplier_returns/routes.py": [],
    "smart_mart/blueprints/purchase_orders/routes.py": [],
    "smart_mart/blueprints/advisor/routes.py": [],
    "smart_mart/blueprints/ai/routes.py": [],
}

# For each file, map: route function name -> permission key
ROUTE_PERMS = {
    # reports
    "sales_report": "can_view_sales_report",
    "export_sales_csv": "can_view_sales_report",
    "profitability": "can_view_profit_report",
    "export_profitability_csv": "can_view_profit_report",
    "cash_flow": "can_view_reports",
    "dead_stock": "can_view_stock_report",
    "inventory_valuation": "can_view_stock_report",
    "export_inventory_csv": "can_view_stock_report",
    "top_products": "can_view_sales_report",
    "export_top_products_csv": "can_view_sales_report",
    "least_products": "can_view_sales_report",
    "stock_analysis": "can_view_stock_report",
    "category_performance": "can_view_reports",
    "export_category_csv": "can_view_reports",
    "staff_efficiency": "can_view_reports",
    "export_staff_csv": "can_view_reports",
    "credit_udharo": "can_view_credit_report",
    "mark_credit_collected": "can_view_credit_report",
    "set_credit_due_date": "can_view_credit_report",
    # customers
    "list_customers": "can_view_customers",
    "customer_profile": "can_view_customers",
    "create_customer": "can_manage_customers",
    "edit_customer": "can_manage_customers",
    "delete_customer": "can_manage_customers",
    "export_csv": "can_view_customers",
    # promotions
    "list_promotions": "can_view_promotions",
    "create_promotion": "can_manage_promotions",
    "edit_promotion": "can_manage_promotions",
    "delete_promotion": "can_manage_promotions",
    "check_promotions": "can_view_promotions",
    # transfers
    "list_transfers": "can_view_transfers",
    "create_transfer": "can_manage_transfers",
    "transfer_detail": "can_view_transfers",
    "complete_transfer": "can_manage_transfers",
    "cancel_transfer": "can_manage_transfers",
    # stock_take
    "list_takes": "can_view_stock_take",
    "create_take": "can_manage_stock_take",
    "count": "can_manage_stock_take",
    "complete": "can_manage_stock_take",
    "cancel": "can_manage_stock_take",
    "view": "can_view_stock_take",
    "api_save_count": "can_manage_stock_take",
    # supplier_returns
    "list_returns": "can_view_supplier_returns",
    "create_return": "can_manage_supplier_returns",
    "view_return": "can_view_supplier_returns",
    "update_status": "can_manage_supplier_returns",
    # purchase_orders
    "list_pos": "can_view_purchase_orders",
    "create_po": "can_manage_purchase_orders",
    "po_detail": "can_view_purchase_orders",
    "send_po": "can_manage_purchase_orders",
    "receive_po": "can_manage_purchase_orders",
    "cancel_po": "can_manage_purchase_orders",
    # advisor
    "index": "can_view_advisor",
    "api_kpis": "can_view_advisor",
    "api_forecast": "can_view_advisor",
    "api_report": "can_view_advisor",
    # ai (all)
    "insights": "can_view_ai_insights",
    "chatbot": "can_view_ai_insights",
    "chatbot_query": "can_view_ai_insights",
    "advanced_dashboard": "can_view_ai_insights",
    "anomalies_page": "can_view_ai_insights",
    "cashflow_page": "can_view_ai_insights",
    "customer_intelligence": "can_view_ai_insights",
    "customer_profile": "can_view_ai_insights",
    "product_analysis": "can_view_ai_insights",
    "voice_assistant": "can_view_ai_insights",
    "competitor_pricing": "can_view_ai_insights",
    "add_competitor_price": "can_view_ai_insights",
    "get_price_suggestion": "can_view_ai_insights",
    "feedback_loop": "can_view_ai_insights",
    "feedback_action": "can_view_ai_insights",
    "trigger_retrain": "can_view_ai_insights",
}

FILES_TO_PATCH = [
    "smart_mart/blueprints/reports/routes.py",
    "smart_mart/blueprints/customers/routes.py",
    "smart_mart/blueprints/promotions/routes.py",
    "smart_mart/blueprints/transfers/routes.py",
    "smart_mart/blueprints/stock_take/routes.py",
    "smart_mart/blueprints/supplier_returns/routes.py",
    "smart_mart/blueprints/purchase_orders/routes.py",
    "smart_mart/blueprints/advisor/routes.py",
    "smart_mart/blueprints/ai/routes.py",
]

for fpath in FILES_TO_PATCH:
    with open(fpath, encoding='utf-8') as f:
        content = f.read()

    original = content

    # 1. Add login_required import if not present
    if 'login_required' not in content:
        content = content.replace(
            'from ...services.decorators import admin_required',
            'from ...services.decorators import admin_required, login_required'
        )

    # 2. Add helper after Blueprint definition if not present
    if '_require_perm' not in content:
        # Insert after the Blueprint(...) line
        bp_match = re.search(r'(\w+_bp\s*=\s*Blueprint\([^\)]+\))', content)
        if bp_match:
            insert_pos = bp_match.end()
            content = content[:insert_pos] + HELPER + content[insert_pos:]

    # 3. Replace @admin_required -> @login_required + perm check for each function
    def replace_decorator(m):
        func_name = m.group(2)
        perm = ROUTE_PERMS.get(func_name)
        if perm:
            return f'@login_required\ndef {func_name}({m.group(3)}):\n    _require_perm("{perm}")'
        # Keep admin_required for functions not in our map
        return m.group(0)

    content = re.sub(
        r'@admin_required\ndef (\w+)\(([^)]*)\):',
        lambda m: (
            f'@login_required\ndef {m.group(1)}({m.group(2)}):\n    _require_perm("{ROUTE_PERMS[m.group(1)]}")'
            if m.group(1) in ROUTE_PERMS
            else m.group(0)
        ),
        content
    )

    if content != original:
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f'  Patched: {fpath}')
    else:
        print(f'  No changes: {fpath}')

print('Done.')
