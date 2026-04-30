# Validate IQE Plugin Changes

Use this guide when validating changes to `iqe-cost-management-plugin` against the on-prem deployment.

## Prerequisites

- OpenShift cluster access configured (`oc login`)
- `NAMESPACE` environment variable set (default: `cost-onprem`)
- Local `iqe-cost-management-plugin` repo at `../iqe-cost-management-plugin`

## Quick Validation

For simple changes, run a targeted test:

```bash
# Single test
NAMESPACE=cost-onprem ./scripts/run-iqe-tests-local.sh --filter "test_name_here"

# Test category
NAMESPACE=cost-onprem ./scripts/run-iqe-tests-local.sh --filter "test_api_ocp_compute"
```

## Loop Testing for Stability

When validating changes that could introduce flakiness, use the loop runner:

```bash
# Run 5 iterations (default)
./scripts/qe/run-iqe-loop.sh --filter "test_api_ocp_compute_deltas_percentages"

# Run 12 iterations for thorough validation
./scripts/qe/run-iqe-loop.sh --iterations 12 --filter "test_api_ocp_compute_deltas_percentages"

# Verbose output to see full test logs
./scripts/qe/run-iqe-loop.sh --iterations 3 --filter "test_name" --verbose
```

## Cleanup Stale Sources

If tests fail with "duplicate accounts" errors, clean up stale test sources:

```bash
# Cleanup only
./scripts/qe/run-iqe-loop.sh --cleanup-only

# Or manually via API
KEYCLOAK_SECRET=$(kubectl get secret -n keycloak keycloak-client-secret-cost-management-operator -o jsonpath='{.data.CLIENT_SECRET}' | base64 -d)
TOKEN=$(curl -sk -X POST "https://$(kubectl get route -n keycloak keycloak -o jsonpath='{.spec.host}')/realms/kubernetes/protocol/openid-connect/token" \
  -d "grant_type=client_credentials" \
  -d "client_id=cost-management-operator" \
  -d "client_secret=$KEYCLOAK_SECRET" | jq -r '.access_token')

# List sources
curl -sk -H "Authorization: Bearer $TOKEN" \
  "https://$(kubectl get route -n cost-onprem cost-onprem-gateway -o jsonpath='{.spec.host}')/api/cost-management/v1/sources/" | jq '.data[] | {uuid, name}'

# Delete a specific source
curl -sk -X DELETE -H "Authorization: Bearer $TOKEN" \
  "https://$(kubectl get route -n cost-onprem cost-onprem-gateway -o jsonpath='{.spec.host}')/api/cost-management/v1/sources/UUID_HERE/"
```

## Common Test Patterns

### Delta/Percentage Tests
Tests that calculate deltas between metrics (usage vs limit, request vs capacity):

```bash
./scripts/qe/run-iqe-loop.sh --iterations 5 --filter "deltas_percentages"
```

**Known behavior**: Many OpenShift system namespaces have `limit=0` (no resource limits set), which causes `ZeroDivisionError` when calculating `usage_v_limit`. The helper function `validate_delta_percentage()` handles this gracefully by logging a warning and continuing.

### Volume Tests  
Tests for persistent volume reports:

```bash
./scripts/qe/run-iqe-loop.sh --iterations 5 --filter "test_api_ocp_volume"
```

**Known behavior**: Claimless PVs and certain cloud integrations produce resources with `No-node`, `No-project`, etc. naming patterns that legitimately have zero values.

## Interpreting Results

| Status | Meaning |
|--------|---------|
| PASSED | Test executed successfully |
| FAILED | Test assertion failed |
| ERROR | Test crashed (exception) |
| XFAIL | Expected failure (fixture issue, usually stale source) |

**XFAIL with "Source fixture failed"** indicates a stale test source exists. Run cleanup:
```bash
./scripts/qe/run-iqe-loop.sh --cleanup-only
```

## Validating ZeroDivisionError Handling

After changes to delta percentage handling, verify:

1. Tests that previously crashed with `ZeroDivisionError` now pass
2. Warnings are logged for zero denominator cases
3. No variance across multiple runs

```bash
# Run the specific tests that triggered ZeroDivisionError in CI
./scripts/qe/run-iqe-loop.sh --iterations 10 --filter "test_api_ocp_compute_deltas_percentages or test_api_ocp_memory_deltas_percentages"
```

Expected output should show:
- All iterations PASSED
- Warnings logged for `limit=0` resources
- Consistent results across all iterations
