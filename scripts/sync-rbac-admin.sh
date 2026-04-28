#!/usr/bin/env bash
# sync-rbac-admin.sh — Grant Cost Administrator to a Keycloak user in insights-rbac.
#
# This is a manual fallback for environments where the Helm post-install hook
# (rbac.bootstrapAdmin) is not used. It execs into the running RBAC pod and
# creates the Tenant, Principal, Group, and Policy via Django ORM.
#
# Usage:
#   NAMESPACE=cost-onprem ./scripts/sync-rbac-admin.sh
#   NAMESPACE=cost-onprem ./scripts/sync-rbac-admin.sh --username alice --org-id myorg --account-number 9999
#
# Prerequisites:
#   - kubectl access to the cluster
#   - The Helm chart is installed (RBAC pod is running, migration job completed)

set -euo pipefail

NAMESPACE="${NAMESPACE:-cost-onprem}"
USERNAME="admin"
ORG_ID="org1234567"
ACCOUNT_NUMBER="7890123"

while [[ $# -gt 0 ]]; do
  case $1 in
    --username) USERNAME="$2"; shift 2 ;;
    --org-id) ORG_ID="$2"; shift 2 ;;
    --account-number) ACCOUNT_NUMBER="$2"; shift 2 ;;
    --namespace) NAMESPACE="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--username USER] [--org-id ORG] [--account-number ACCT] [--namespace NS]"
      echo ""
      echo "Defaults: username=admin, org-id=org1234567, account-number=7890123, namespace=cost-onprem"
      exit 0
      ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

echo "=== RBAC Admin User Sync ==="
echo "Namespace:      ${NAMESPACE}"
echo "Username:       ${USERNAME}"
echo "Org ID:         ${ORG_ID}"
echo "Account Number: ${ACCOUNT_NUMBER}"
echo ""

RBAC_POD=$(kubectl get pod -l app.kubernetes.io/component=rbac-api -n "${NAMESPACE}" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "${RBAC_POD}" ]; then
  echo "ERROR: No RBAC API pod found in namespace ${NAMESPACE}"
  echo "  Ensure the Helm chart is installed and the RBAC pod is running."
  exit 1
fi
echo "RBAC pod: ${RBAC_POD}"

echo "Creating Tenant, Principal, Group, and Policy..."
kubectl exec -n "${NAMESPACE}" "${RBAC_POD}" -- \
  python /opt/rbac/rbac/manage.py shell -c "
from api.models import Tenant
from management.models import Group, Policy, Role, Principal
from django.core.cache import cache

username = '${USERNAME}'
org_id = '${ORG_ID}'
acct_number = '${ACCOUNT_NUMBER}'

public_tenant = Tenant.objects.get(tenant_name='public')
cost_admin_role = Role.objects.filter(name='Cost Administrator', tenant=public_tenant).first()
if not cost_admin_role:
    print('ERROR: Cost Administrator role not found')
    raise SystemExit(1)

tenant, created = Tenant.objects.get_or_create(
    org_id=org_id,
    defaults={'tenant_name': 'acct' + acct_number, 'ready': True}
)
status = 'created' if created else 'exists'
print(f'Tenant org_id={org_id}: {status}')

grp, _ = Group.objects.get_or_create(
    name='Cost Admin Default', tenant=tenant,
    defaults={'admin_default': True, 'system': True,
              'description': 'Admin default: grants Cost Administrator to bootstrap admin user'}
)
grp.admin_default = True
grp.save()

policy, _ = Policy.objects.get_or_create(
    name='Cost Admin Default Policy', tenant=tenant, group=grp
)
policy.roles.add(cost_admin_role)

principal, _ = Principal.objects.get_or_create(
    username=username, tenant=tenant,
    defaults={'type': 'user'}
)
grp.principals.add(principal)

cache.clear()
print(f'User \"{username}\" granted Cost Administrator for org={org_id}')
"

echo ""
echo "Running bootstrap_tenants for TenantMapping/V2 records..."
kubectl exec -n "${NAMESPACE}" "${RBAC_POD}" -- \
  python /opt/rbac/rbac/manage.py bootstrap_tenants --org-id "${ORG_ID}" --force || \
  echo "WARNING: bootstrap_tenants returned non-zero (non-fatal)"

echo ""
echo "=== RBAC admin sync complete ==="
echo "User '${USERNAME}' now has Cost Administrator access in org '${ORG_ID}'."
