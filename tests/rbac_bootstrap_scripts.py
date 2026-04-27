"""Inline Python scripts executed inside the RBAC pod via `manage.py shell -c`.

These scripts run inside the insights-rbac Django environment and have access
to the full ORM (api.models, management.models, etc.).  They are passed as
the `-c` argument to `manage.py shell` via exec_in_pod_raw().

Extraction rationale: keeps tests/conftest.py focused on fixture orchestration
while these module-level constants/functions own the ORM logic.
"""


def render_bootstrap_script(
    sa_usernames: list[str], org_id: str, acct_number: str
) -> str:
    """Return the bootstrap script that grants CI service account Cost Administrator.

    Creates (if needed):
      - Tenant for the given org_id
      - "CI Test Admin" group with Cost Administrator role
      - Principal entries for each SA username + the 'admin' user
    """
    return f"""
from api.models import Tenant
from management.models import Group, Policy, Role, Principal
from django.core.cache import cache

sa_usernames = {repr(sa_usernames)}
org_id = {repr(org_id)}
acct_number = {repr(acct_number)}

public_tenant = Tenant.objects.get(tenant_name='public')
cost_admin_role = Role.objects.filter(name='Cost Administrator', tenant=public_tenant).first()
if not cost_admin_role:
    print('RBAC bootstrap: Cost Administrator role not found')
else:
    tenant, created = Tenant.objects.get_or_create(
        org_id=org_id,
        defaults={{'tenant_name': 'acct' + acct_number, 'ready': True}}
    )
    if created:
        print(f'RBAC bootstrap: created tenant for org_id={{org_id}}')

    grp, _ = Group.objects.get_or_create(
        name='CI Test Admin', tenant=tenant,
        defaults={{'admin_default': False, 'system': True,
                  'description': 'CI service account admin access'}}
    )
    policy, _ = Policy.objects.get_or_create(
        name='CI Test Admin Policy', tenant=tenant, group=grp
    )
    policy.roles.add(cost_admin_role)

    for sa_name in sa_usernames:
        principal, _ = Principal.objects.get_or_create(
            username=sa_name, tenant=tenant,
            defaults={{'type': 'user'}}
        )
        grp.principals.add(principal)

    # Also grant the Keycloak "admin" user (used by UI/Playwright tests)
    admin_principal, _ = Principal.objects.get_or_create(
        username='admin', tenant=tenant,
        defaults={{'type': 'user'}}
    )
    grp.principals.add(admin_principal)

    cache.clear()
    print(f'RBAC bootstrap: CI Test Admin group with Cost Administrator created for org={{org_id}}, principals={{sa_usernames + ["admin"]}}')
"""


CLEANUP_SCRIPT = """
from management.models import Group, Policy, Role, Access
from django.db.models import Q
from django.core.cache import cache

group_removed = 0
for group in Group.objects.filter(platform_default=True):
    for policy in Policy.objects.filter(group=group):
        for role in policy.roles.all():
            has_cm = Access.objects.filter(role=role).filter(
                Q(permission__application='cost-management')
                | Q(permission__resource_type='*')
            ).exists()
            if has_cm:
                policy.roles.remove(role)
                group_removed += 1

role_cleared = 0
for role in Role.objects.filter(platform_default=True):
    has_cm = Access.objects.filter(role=role).filter(
        Q(permission__application='cost-management')
    ).exists()
    if has_cm:
        role.platform_default = False
        role.save(update_fields=['platform_default'])
        role_cleared += 1

cache.clear()
print(f'Platform default cleanup: removed {group_removed} role(s) from groups, '
      f'cleared platform_default flag on {role_cleared} role(s)')
"""


def render_diag_script(
    sa_username: str, org_id: str, acct_number: str, koku_svc_host: str
) -> str:
    """Return the diagnostic script for post-bootstrap RBAC state checks.

    This script performs ORM diagnostics, direct RBAC API checks, and direct
    Koku API checks from inside the RBAC pod.  It uses f-string interpolation
    for runtime values and contains nested dict literals that require careful
    brace escaping.

    TODO(DES-2 follow-up): Consider further refactoring the xrhid JSON
    construction to use host-side json.dumps() with a single placeholder,
    reducing the nested brace complexity.
    """
    return f"""
import json, base64, urllib.request, urllib.error

sa_username = {repr(sa_username)}
org_id = {repr(org_id)}
acct_number = {repr(acct_number)}

# --- V1 ORM diagnostics ---
from api.models import Tenant
from management.models import Group, Policy, Role, Principal, Access

public_tenant = Tenant.objects.get(tenant_name='public')

# Check CI Test Admin group
try:
    tenant = Tenant.objects.get(org_id=org_id)
    ci_grp = Group.objects.filter(name='CI Test Admin', tenant=tenant).first()
    if ci_grp:
        principals = list(ci_grp.principals.values_list('username', flat=True))
        policies = Policy.objects.filter(group=ci_grp)
        roles = []
        for p in policies:
            roles.extend(list(p.roles.values_list('name', flat=True)))
        print(f"DIAG V1: CI Test Admin group found. principals={{principals}}, roles={{roles}}")
    else:
        print("DIAG V1: CI Test Admin group NOT found")
except Tenant.DoesNotExist:
    print(f"DIAG V1: Tenant org_id={{org_id}} not found")

# Check platform_default groups
pd_groups = Group.objects.filter(platform_default=True)
for g in pd_groups:
    policies = Policy.objects.filter(group=g)
    for p in policies:
        for r in p.roles.all():
            cm_access = Access.objects.filter(role=r).filter(
                permission__application='cost-management'
            ).exists()
            wildcard_access = Access.objects.filter(role=r).filter(
                permission__resource_type='*'
            ).exists()
            if cm_access or wildcard_access:
                print(f"DIAG V1: platform_default group '{{g.name}}' (tenant={{g.tenant.org_id or g.tenant.tenant_name}}) has role '{{r.name}}' with cm_access={{cm_access}}, wildcard={{wildcard_access}}")

# Check roles with platform_default=True flag
pd_roles = Role.objects.filter(platform_default=True)
for r in pd_roles:
    perms = list(Access.objects.filter(role=r).values_list('permission__permission', flat=True))
    print(f"DIAG V1: Role '{{r.name}}' has platform_default=True, permissions={{perms[:5]}}")

# --- V2 diagnostics ---
try:
    from management.models import BindingMapping
    bm_count = BindingMapping.objects.filter(resource_id=str(tenant.id)).count()
    print(f"DIAG V2: BindingMapping count for tenant={{bm_count}}")
except Exception as e:
    print(f"DIAG V2: BindingMapping check error: {{e}}")

try:
    from management.relation_replicator.logging_replicator import get_relations_for_org
    # V2 might not have this, ignore errors
    pass
except ImportError:
    pass

# --- Direct RBAC /access/ query (use 'admin' user, not SA) ---
xrhid = json.dumps({{
    "org_id": org_id,
    "identity": {{
        "org_id": org_id,
        "account_number": acct_number,
        "type": "User",
        "user": {{
            "username": "admin",
            "email": "admin@test.com",
            "is_org_admin": False
        }}
    }},
    "entitlements": {{"cost_management": {{"is_entitled": True}}}}
}})
xrhid_b64 = base64.b64encode(xrhid.encode()).decode()

try:
    req = urllib.request.Request(
        "http://localhost:8000/api/rbac/v1/access/?application=cost-management&limit=50",
        headers={{"X-Rh-Identity": xrhid_b64, "Accept": "application/json"}}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode()
        data = json.loads(body)
        perm_count = data.get("meta", {{}}).get("count", 0)
        perms = [d.get("permission") for d in data.get("data", [])[:10]]
        print(f"DIAG RBAC-API: /access/ returned {{perm_count}} permissions: {{perms}}")
except urllib.error.HTTPError as e:
    body = e.read().decode()[:300]
    print(f"DIAG RBAC-API: /access/ returned HTTP {{e.code}}: {{body}}")
except Exception as e:
    print(f"DIAG RBAC-API: /access/ error: {{e}}")

# --- Direct Koku query (bypass gateway) ---
koku_host = {repr(koku_svc_host)}
try:
    req = urllib.request.Request(
        f"http://{{koku_host}}:8000/api/cost-management/v1/cost-models/",
        headers={{"X-Rh-Identity": xrhid_b64, "Accept": "application/json"}}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(f"DIAG KOKU-DIRECT: /cost-models/ returned HTTP {{resp.status}}")
except urllib.error.HTTPError as e:
    body = e.read().decode()[:300]
    print(f"DIAG KOKU-DIRECT: /cost-models/ returned HTTP {{e.code}}: {{body}}")
except Exception as e:
    print(f"DIAG KOKU-DIRECT: /cost-models/ error: {{e}}")
"""
