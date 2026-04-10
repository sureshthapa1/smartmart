import re

with open('smart_mart/blueprints/inventory/routes.py', encoding='utf-8') as f:
    content = f.read()

inv_perms = {
    'list_categories': 'can_manage_categories',
    'create_category': 'can_manage_categories',
    'edit_category': 'can_manage_categories',
    'delete_category': 'can_manage_categories',
    'category_detail': 'can_manage_categories',
    'product_variants': 'can_manage_variants',
    'create_variant': 'can_manage_variants',
    'edit_variant': 'can_manage_variants',
    'delete_variant': 'can_manage_variants',
    'print_labels': 'can_print_labels',
}

helper = """

def _require_perm(perm: str):
    from flask import abort
    from flask_login import current_user as _cu
    if _cu.role != 'admin':
        from ...models.user_permissions import UserPermissions
        p = UserPermissions.get_or_create(_cu.id)
        if not getattr(p, perm, False):
            abort(403)

"""

if '_require_perm' not in content:
    bp_match = re.search(r'(inventory_bp\s*=\s*Blueprint\([^\)]+\))', content)
    if bp_match:
        content = content[:bp_match.end()] + helper + content[bp_match.end():]

def replacer(m):
    fname = m.group(1)
    args = m.group(2)
    if fname in inv_perms:
        return f'@login_required\ndef {fname}({args}):\n    _require_perm("{inv_perms[fname]}")'
    return m.group(0)

content = re.sub(r'@admin_required\ndef (\w+)\(([^)]*)\):', replacer, content)

with open('smart_mart/blueprints/inventory/routes.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Inventory routes patched.')
