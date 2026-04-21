# RBAC Setup and Operations Guide

Role-Based Access Control (RBAC) configuration, user management, and troubleshooting for Cost Management On-Premise.

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [How Authorization Works](#how-authorization-works)
- [Seeded Roles and Permissions](#seeded-roles-and-permissions)
- [User and Group Management](#user-and-group-management)
- [Creating Custom Access Policies](#creating-custom-access-policies)
- [Cache Behavior](#cache-behavior)
- [Troubleshooting](#troubleshooting)
- [Operational Runbook](#operational-runbook)

---

## Overview

Cost Management On-Premise uses [insights-rbac](https://github.com/RedHatInsights/insights-rbac) as its authorization backend. Every API request to Koku passes through insights-rbac to determine what resources the user can access.

Key properties:
- Authorization is **role-based**, not attribute-based
- `is_org_admin` is **always false** in the identity header — admin privileges come from RBAC roles only
- Permissions are scoped by `resourceDefinitions` (e.g., specific clusters, namespaces)
- The system seeds 5 built-in roles covering common access patterns

---

## Architecture

```
┌──────────┐     JWT      ┌─────────────┐   x-rh-identity   ┌──────────┐
│ Keycloak │────────────▶ │   Envoy GW   │──────────────────▶│ Koku API │
└──────────┘              └─────────────┘                    └────┬─────┘
                                                                  │
                                                    GET /access/  │
                                                                  ▼
                                                          ┌──────────────┐
                                                          │ insights-rbac│
                                                          │     API      │
                                                          └──────┬───────┘
                                                                 │
                                                          ┌──────┴───────┐
                                                          │  PostgreSQL  │
                                                          │  (rbac DB)   │
                                                          └──────────────┘
```

1. User authenticates via Keycloak and obtains a JWT
2. Envoy validates the JWT and constructs the `x-rh-identity` header (with `is_org_admin: false`)
3. Koku receives the request and calls insights-rbac's `/api/rbac/v1/access/` endpoint
4. insights-rbac resolves the user's groups → policies → roles → permissions
5. Koku applies any `resourceDefinitions` as SQL query filters

---

## Prerequisites

Before configuring RBAC, ensure:

1. **insights-rbac is deployed** — the Helm chart deploys it automatically when `rbac.enabled: true` (default)
2. **Migration job completed** — verify with:
   ```bash
   kubectl get jobs -n <namespace> | grep rbac-migration
   ```
3. **RBAC API is healthy**:
   ```bash
   kubectl exec -it deployment/cost-onprem-rbac-api -n <namespace> -- \
     curl -s http://localhost:8080/api/rbac/v1/status/
   ```
4. **Keycloak realm is configured** — users must exist in the Keycloak realm that the Envoy gateway validates against
5. **Valkey is running** — required as Celery broker for RBAC worker

---

## How Authorization Works

### Two-Layer Authorization in Koku

**Layer 1: Permission Gate (DRF)**

Koku checks that the user has at least one matching permission for the endpoint. For example, the OpenShift reports endpoint requires `cost-management:openshift.cluster:read` or `cost-management:openshift.project:read`.

If no matching permission exists → **HTTP 403**.

**Layer 2: Query Filtering**

If the user's permission includes `resourceDefinitions`, Koku applies them as SQL WHERE clauses:

- `operation: "equal"` with `value: "cluster-alpha"` → restricts to that specific cluster
- `operation: "in"` with `value: ["payment", "frontend"]` → restricts to those namespaces
- No `resourceDefinitions` (wildcard) → all data visible within the org

### Identity Header

The `x-rh-identity` header is constructed by the Envoy gateway and contains:

```json
{
  "org_id": "<from-keycloak>",
  "identity": {
    "org_id": "<from-keycloak>",
    "account_number": "<from-keycloak>",
    "type": "User",
    "user": {
      "username": "<from-jwt>",
      "email": "<from-jwt>",
      "is_org_admin": false
    }
  },
  "entitlements": {
    "cost_management": {
      "is_entitled": true
    }
  }
}
```

**Important**: `is_org_admin` is always `false`. This is a deliberate security decision — see the [PoC Analysis](https://gist.github.com/jordigilh/c81c73ba411637e24a30acd6a743e5fb) for rationale.

---

## Seeded Roles and Permissions

The migration job seeds the following roles into the `public` tenant:

| Role | Permissions | Use Case |
|------|------------|----------|
| **Cost Administrator** | `cost-management:*:*` | Full access to all cost data and settings |
| **Cost Price List Administrator** | `cost_model:*`, `settings:*` | Manage cost models and settings |
| **Cost Price List Viewer** | `cost_model:read`, `settings:read` | View cost models and settings |
| **Cost Cloud Viewer** | `aws.*:*`, `azure.*:*`, `gcp.*:*` | View cloud provider cost data |
| **Cost OpenShift Viewer** | `openshift.cluster:*` | View OpenShift cost data |

### Permission Format

Permissions follow the pattern: `cost-management:<resource_type>:<verb>`

Available resource types:
- `openshift.cluster`, `openshift.node`, `openshift.project`
- `aws.account`, `aws.organizational_unit`
- `azure.subscription_guid`
- `gcp.account`, `gcp.project`
- `cost_model`, `settings`
- `*` (wildcard — all resources)

Available verbs: `read`, `write`, `*`

---

## User and Group Management

### Creating a User in Keycloak

Users are managed in Keycloak. Each user must have:
- `org_id` attribute (determines which tenant/org they belong to)
- `account_number` attribute (customer account identifier)

### Assigning Roles via Groups

RBAC uses the following hierarchy:

```
Group → Policy → Role → Access → Permission + ResourceDefinition
```

To grant a user access:

1. **Create a Group** (or use an existing one)
2. **Create a Policy** binding the group to a role
3. **Add the user as a principal** to the group

### Example: Grant Full Admin Access

```bash
RBAC_POD=$(kubectl get pod -l app.kubernetes.io/component=rbac-api -n <namespace> -o jsonpath='{.items[0].metadata.name}')

kubectl exec -it $RBAC_POD -n <namespace> -- python manage.py shell <<'EOF'
from api.models import Tenant
from management.models import Group, Policy, Role, Principal

tenant = Tenant.objects.get(org_id='<your-org-id>')
public_tenant = Tenant.objects.get(tenant_name='public')

# Get the Cost Administrator role
admin_role = Role.objects.get(name='Cost Administrator', tenant=public_tenant)

# Create group
group, _ = Group.objects.get_or_create(
    name='Cost Admins', tenant=tenant,
    defaults={'description': 'Users with full cost management access'}
)

# Create policy binding group to role
policy, _ = Policy.objects.get_or_create(
    name='Cost Admin Policy', tenant=tenant, group=group
)
policy.roles.add(admin_role)

# Add user as principal
principal, _ = Principal.objects.get_or_create(
    username='admin-user', tenant=tenant
)
group.principals.add(principal)

print(f"User 'admin-user' now has Cost Administrator access")
EOF
```

### Example: Namespace-Scoped Access

Grant a user access only to specific namespaces across all clusters:

```bash
kubectl exec -it $RBAC_POD -n <namespace> -- python manage.py shell <<'EOF'
from api.models import Tenant
from management.models import (
    Group, Policy, Role, Access, Permission,
    ResourceDefinition, Principal
)

tenant = Tenant.objects.get(org_id='<your-org-id>')
public_tenant = Tenant.objects.get(tenant_name='public')

# Create a custom role with namespace filter
role, _ = Role.objects.get_or_create(
    name='Payment Team Viewer', tenant=public_tenant,
    defaults={
        'description': 'View payment namespace costs only',
        'system': False, 'version': 2
    }
)

# Add openshift.project:read permission with resource definition
perm = Permission.objects.get(
    application='cost-management',
    resource_type='openshift.project', verb='read'
)
access, _ = Access.objects.get_or_create(
    role=role, permission=perm, defaults={'tenant': public_tenant}
)

# Filter to "payment" namespace only
ResourceDefinition.objects.get_or_create(
    access=access, tenant=public_tenant,
    defaults={'attributeFilter': {
        'key': 'cost-management.openshift.project',
        'operation': 'in',
        'value': ['payment']
    }}
)

# Create group and bind
group, _ = Group.objects.get_or_create(
    name='Payment Team', tenant=tenant,
    defaults={'description': 'Payment namespace viewers'}
)
policy, _ = Policy.objects.get_or_create(
    name='Payment Team Policy', tenant=tenant, group=group
)
policy.roles.add(role)

# Add user
principal, _ = Principal.objects.get_or_create(
    username='payment-user', tenant=tenant
)
group.principals.add(principal)

print("User 'payment-user' now has read access to payment namespace only")
EOF
```

### Example: Cluster-Scoped Access

Grant a user access to a specific cluster:

```bash
kubectl exec -it $RBAC_POD -n <namespace> -- python manage.py shell <<'EOF'
from api.models import Tenant
from management.models import (
    Group, Policy, Role, Access, Permission,
    ResourceDefinition, Principal
)

tenant = Tenant.objects.get(org_id='<your-org-id>')
public_tenant = Tenant.objects.get(tenant_name='public')

role, _ = Role.objects.get_or_create(
    name='Cluster Alpha Ops', tenant=public_tenant,
    defaults={
        'description': 'View cluster-alpha costs only',
        'system': False, 'version': 2
    }
)

perm = Permission.objects.get(
    application='cost-management',
    resource_type='openshift.cluster', verb='read'
)
access, _ = Access.objects.get_or_create(
    role=role, permission=perm, defaults={'tenant': public_tenant}
)

ResourceDefinition.objects.get_or_create(
    access=access, tenant=public_tenant,
    defaults={'attributeFilter': {
        'key': 'cost-management.openshift.cluster',
        'operation': 'equal',
        'value': 'my-cluster-id'
    }}
)

group, _ = Group.objects.get_or_create(
    name='Cluster Alpha Team', tenant=tenant,
    defaults={'description': 'Cluster alpha operations team'}
)
policy, _ = Policy.objects.get_or_create(
    name='Cluster Alpha Policy', tenant=tenant, group=group
)
policy.roles.add(role)

principal, _ = Principal.objects.get_or_create(
    username='cluster-ops-user', tenant=tenant
)
group.principals.add(principal)

print("User 'cluster-ops-user' now has read access to cluster-alpha only")
EOF
```

**Known limitation**: Users with cluster-scoped access can browse their data via auto-injection, but explicit `?filter[cluster]=<id>` query parameters may return 403 due to a format mismatch in Koku's filter comparison logic.

---

## Creating Custom Access Policies

### Resource Definition Operations

| Operation | Value Type | Example | Behavior |
|-----------|-----------|---------|----------|
| `equal` | string | `"cluster-alpha"` | Exact match on single value |
| `in` | list | `["payment", "frontend"]` | Match any value in list |

### Resource Definition Keys

| Key | Filters On |
|-----|-----------|
| `cost-management.openshift.cluster` | OCP cluster identifier |
| `cost-management.openshift.node` | OCP node name |
| `cost-management.openshift.project` | OCP namespace/project name |
| `cost-management.aws.account` | AWS account ID |
| `cost-management.azure.subscription_guid` | Azure subscription GUID |
| `cost-management.gcp.account` | GCP billing account |
| `cost-management.gcp.project` | GCP project ID |

### Important Notes

1. **Custom roles must be created via Django ORM** — the RBAC API rejects custom roles for `cost-management` application via REST API
2. **Roles are created in the `public` tenant** — they are shared across all orgs
3. **Groups, policies, and principals are per-tenant** — each org has its own assignments
4. **Principal usernames must match Keycloak usernames** — RBAC resolves the principal by the `username` field from the identity header

---

## Cache Behavior

### RBAC Response Cache (Koku side)

Koku caches responses from the RBAC `/access/` endpoint in Valkey:
- **TTL**: 300 seconds (5 minutes) by default
- **Key**: Based on user identity + application
- **Effect**: Permission changes take up to 5 minutes to propagate

### Django Cache (insights-rbac side)

insights-rbac uses Django's cache framework for internal query results.

### Flushing Caches

After making permission changes that need immediate effect:

```bash
# 1. Flush insights-rbac Django cache
RBAC_POD=$(kubectl get pod -l app.kubernetes.io/component=rbac-api -n <namespace> -o jsonpath='{.items[0].metadata.name}')
kubectl exec -it $RBAC_POD -n <namespace> -- \
  python manage.py shell -c "from django.core.cache import cache; cache.clear(); print('RBAC cache cleared')"

# 2. Flush Koku's RBAC cache in Valkey
VALKEY_POD=$(kubectl get pod -l app.kubernetes.io/component=valkey -n <namespace> -o jsonpath='{.items[0].metadata.name}')
kubectl exec -it $VALKEY_POD -n <namespace> -- valkey-cli FLUSHALL

echo "Both caches flushed — permission changes are now active"
```

**Warning**: `FLUSHALL` clears ALL Valkey data including Celery task metadata. In production, consider using targeted key deletion instead.

---

## Troubleshooting

### User Gets 403 on All Endpoints

**Symptoms**: Authenticated user receives HTTP 403 on cost report endpoints.

**Diagnosis**:

```bash
# 1. Check what RBAC returns for this user
RBAC_POD=$(kubectl get pod -l app.kubernetes.io/component=rbac-api -n <namespace> -o jsonpath='{.items[0].metadata.name}')

# Create identity header for the user
IDENTITY=$(echo -n '{"org_id":"<org>","identity":{"org_id":"<org>","account_number":"<acct>","type":"User","user":{"username":"<user>","email":"<email>","is_org_admin":false}},"entitlements":{"cost_management":{"is_entitled":true}}}' | base64 | tr -d '\n')

# Query RBAC access endpoint
kubectl exec -it $RBAC_POD -n <namespace> -- \
  curl -s -H "X-Rh-Identity: $IDENTITY" \
  "http://localhost:8080/api/rbac/v1/access/?application=cost-management" | python3 -m json.tool
```

**Common causes**:
1. User has no principal record → Add principal to a group
2. User's group has no policy binding → Create policy with role
3. Cache is stale → Flush both caches
4. Tenant not bootstrapped → Check `TenantMapping` exists

### User Sees All Data (No Filtering)

**Symptoms**: User sees data from clusters/namespaces they shouldn't have access to.

**Diagnosis**:
1. Check if the user's role has `*:*` wildcard permissions
2. Check if `platform_default` groups grant broad access
3. Verify `resourceDefinitions` are correctly configured

```bash
kubectl exec -it $RBAC_POD -n <namespace> -- python manage.py shell <<'EOF'
from management.models import Access, ResourceDefinition
from api.models import Tenant

public = Tenant.objects.get(tenant_name='public')
for access in Access.objects.filter(tenant=public):
    rds = ResourceDefinition.objects.filter(access=access)
    if rds.exists():
        for rd in rds:
            print(f"Role: {access.role.name} | Perm: {access.permission} | Filter: {rd.attributeFilter}")
    else:
        print(f"Role: {access.role.name} | Perm: {access.permission} | Filter: WILDCARD (all data)")
EOF
```

### RBAC API Returns 500

**Symptoms**: Koku logs show 500 responses from RBAC, users get 424.

**Diagnosis**:
```bash
# Check RBAC API logs
kubectl logs deployment/cost-onprem-rbac-api -n <namespace> --tail=50

# Check RBAC worker logs
kubectl logs deployment/cost-onprem-rbac-worker -n <namespace> --tail=50

# Verify DB connectivity
kubectl exec -it $RBAC_POD -n <namespace> -- \
  python manage.py shell -c "from django.db import connection; connection.ensure_connection(); print('DB OK')"
```

### TenantNotBootstrappedError

**Symptoms**: RBAC returns errors about missing `TenantMapping`.

**Fix**:
```bash
kubectl exec -it $RBAC_POD -n <namespace> -- \
  python manage.py bootstrap_tenants --all -v 2
```

---

## Operational Runbook

### Adding a New Organization

When a new org is created in Keycloak:

1. The user's first request to a Koku endpoint triggers tenant creation
2. Run `bootstrap_tenants` to create the TenantMapping:
   ```bash
   kubectl exec -it $RBAC_POD -n <namespace> -- \
     python manage.py bootstrap_tenants --all -v 2
   ```
3. Create the admin_default group for the new tenant (if admin access is needed):
   ```bash
   kubectl exec -it $RBAC_POD -n <namespace> -- python manage.py shell <<'EOF'
   from api.models import Tenant
   from management.models import Group, Policy, Role

   public = Tenant.objects.get(tenant_name='public')
   tenant = Tenant.objects.get(org_id='<new-org-id>')
   admin_role = Role.objects.get(name='Cost Administrator', tenant=public)

   group, _ = Group.objects.get_or_create(
       name='Cost Admin Default', tenant=tenant,
       defaults={'admin_default': True, 'system': True,
                 'description': 'Admin default: Cost Administrator'}
   )
   group.admin_default = True
   group.save()

   policy, _ = Policy.objects.get_or_create(
       name='Cost Admin Default Policy', tenant=tenant, group=group
   )
   policy.roles.add(admin_role)
   print(f"Admin default group created for org {tenant.org_id}")
   EOF
   ```

### Listing All Users and Their Access

```bash
kubectl exec -it $RBAC_POD -n <namespace> -- python manage.py shell <<'EOF'
from management.models import Principal, Group

for p in Principal.objects.all():
    groups = Group.objects.filter(principals=p)
    group_names = [g.name for g in groups]
    print(f"User: {p.username} | Tenant: {p.tenant.org_id} | Groups: {group_names}")
EOF
```

### Revoking Access

```bash
kubectl exec -it $RBAC_POD -n <namespace> -- python manage.py shell <<'EOF'
from management.models import Principal, Group
from api.models import Tenant

tenant = Tenant.objects.get(org_id='<org-id>')
principal = Principal.objects.get(username='<username>', tenant=tenant)
group = Group.objects.get(name='<group-name>', tenant=tenant)

group.principals.remove(principal)
print(f"Removed {principal.username} from {group.name}")
EOF
```

After revoking, flush caches (see [Cache Behavior](#cache-behavior)).

### Upgrading RBAC

The migration job runs automatically on `helm upgrade`. It is idempotent:
- Migrations are safe to re-run
- Permission/role seeding uses `get_or_create`
- admin_default group creation is idempotent

Verify after upgrade:
```bash
# Check migration job completed
kubectl get jobs -n <namespace> | grep rbac-migration

# Verify roles are seeded
kubectl exec -it $RBAC_POD -n <namespace> -- \
  python manage.py shell -c "from management.models import Role; print(f'Roles: {Role.objects.count()}')"
```
