"""
Reset bikram (and any existing staff) to minimal default permissions.
Run once: python reset_staff_permissions.py
"""
from smart_mart.app import create_app
from smart_mart.extensions import db
from smart_mart.models.user import User
from smart_mart.models.user_permissions import UserPermissions

MINIMAL_DEFAULTS = dict(
    can_view_inventory=True,
    can_add_product=False,
    can_edit_product=False,
    can_delete_product=False,
    can_adjust_stock=False,
    can_bulk_upload_products=False,
    can_view_sales=True,
    can_create_sale=True,
    can_delete_sale=False,
    can_give_discount=False,
    can_download_invoice=True,
    can_view_purchases=False,
    can_create_purchase=False,
    can_bulk_upload_purchases=False,
    can_manage_suppliers=False,
    can_view_returns=True,
    can_create_return=False,
    can_view_online_orders=False,
    can_manage_online_orders=False,
    can_view_alerts=True,
    can_view_dashboard=True,
)

app = create_app("development")

with app.app_context():
    staff_users = db.session.execute(
        db.select(User).filter_by(role="staff")
    ).scalars().all()

    if not staff_users:
        print("No staff users found.")
    else:
        for user in staff_users:
            p = db.session.execute(
                db.select(UserPermissions).filter_by(user_id=user.id)
            ).scalar_one_or_none()

            if p is None:
                p = UserPermissions(user_id=user.id)
                db.session.add(p)

            for field, value in MINIMAL_DEFAULTS.items():
                setattr(p, field, value)

            db.session.commit()
            print(f"  Reset: {user.username} ({user.role})")

    print("Done.")
