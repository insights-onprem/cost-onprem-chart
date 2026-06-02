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
VALUES_FILE=""
USERNAME=""
ORG_ID=""
ACCOUNT_NUMBER=""

while [[ $# -gt 0 ]]; do
  case $1 in
    -f|--values) VALUES_FILE="$2"; shift 2 ;;
    --username) USERNAME="$2"; shift 2 ;;
    --org-id) ORG_ID="$2"; shift 2 ;;
    --account-number) ACCOUNT_NUMBER="$2"; shift 2 ;;
    --namespace) NAMESPACE="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [-f <values.yaml>] [--username USER] [--org-id ORG] [--account-number ACCT] [--namespace NS]"
      echo ""
      echo "When -f is provided, the admin identity is read from the first orgAdmin:true"
      echo "entry in jwtAuth.realmUsers. CLI flags override values-file fields."
      echo ""
      echo "Without -f, defaults to: username=admin, org-id=org1234567, account-number=7890123"
      exit 0
      ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# Read admin identity from values file if provided
if [ -n "$VALUES_FILE" ] && [ -f "$VALUES_FILE" ]; then
  admin_json=$(python3 -c "
import sys, json
try:
    import yaml
except ImportError:
    print('ERROR: pip3 install pyyaml is required when using -f', file=sys.stderr)
    sys.exit(1)
with open(sys.argv[1]) as f:
    vals = yaml.safe_load(f)
users = vals.get('jwtAuth', {}).get('realmUsers', [])
admin = next((u for u in users if u.get('orgAdmin')), None)
if admin:
    json.dump(admin, sys.stdout)
else:
    print('ERROR: no orgAdmin:true entry found in jwtAuth.realmUsers', file=sys.stderr)
    sys.exit(1)
" "$VALUES_FILE")
  if [ $? -ne 0 ] || [ -z "$admin_json" ]; then
    echo "ERROR: Failed to parse admin identity from $VALUES_FILE"
    exit 1
  fi
  USERNAME="${USERNAME:-$(echo "$admin_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('username','admin'))")}"
  ORG_ID="${ORG_ID:-$(echo "$admin_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('orgId','org1234567'))")}"
  ACCOUNT_NUMBER="${ACCOUNT_NUMBER:-$(echo "$admin_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('accountNumber','7890123'))")}"
elif [ -n "$VALUES_FILE" ]; then
  echo "ERROR: Values file not found: $VALUES_FILE"
  exit 1
fi

USERNAME="${USERNAME:-admin}"
ORG_ID="${ORG_ID:-org1234567}"
ACCOUNT_NUMBER="${ACCOUNT_NUMBER:-7890123}"

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
  env SYNC_USERNAME="${USERNAME}" SYNC_ORG_ID="${ORG_ID}" SYNC_ACCOUNT_NUMBER="${ACCOUNT_NUMBER}" \
  python /opt/rbac/rbac/manage.py shell -c "
import os
from api.models import Tenant
from management.models import Group, Policy, Role, Principal
from django.core.cache import cache

username = os.environ['SYNC_USERNAME']
org_id = os.environ['SYNC_ORG_ID']
acct_number = os.environ['SYNC_ACCOUNT_NUMBER']

public_tenant = Tenant.objects.get(tenant_name='public')
admin_default_roles = Role.objects.filter(admin_default=True, tenant=public_tenant)
if not admin_default_roles.exists():
    print('ERROR: No admin_default roles found')
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
              'description': 'Admin default: grants admin_default roles to bootstrap admin user'}
)
grp.admin_default = True
grp.save()

policy, _ = Policy.objects.get_or_create(
    name='Cost Admin Default Policy', tenant=tenant, group=grp
)
for role in admin_default_roles:
    policy.roles.add(role)

principal, _ = Principal.objects.get_or_create(
    username=username, tenant=tenant,
    defaults={'type': 'user'}
)
grp.principals.add(principal)

role_names = list(admin_default_roles.values_list('name', flat=True))
cache.clear()
print(f'User \"{username}\" granted {role_names} for org={org_id}')
"

echo ""
echo "Running bootstrap_tenants for TenantMapping/V2 records..."
set +e
kubectl exec -n "${NAMESPACE}" "${RBAC_POD}" -- \
  python /opt/rbac/rbac/manage.py bootstrap_tenants --org-id "${ORG_ID}" --force
bootstrap_rc=$?
set -e
if [ $bootstrap_rc -ne 0 ]; then
  echo "WARNING: bootstrap_tenants exited with code $bootstrap_rc (non-fatal)"
fi

echo ""
echo "=== RBAC admin sync complete ==="
echo "User '${USERNAME}' now has Cost Administrator access in org '${ORG_ID}'."
