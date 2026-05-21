# RBAC gateway tests

**Module:** `tests/suites/auth/test_rbac_gateway.py`  
**Markers:** `auth`, `integration`  
**Flow:** Keycloak password grant → Envoy gateway → Koku / ROS / insights-rbac

Complements `tests/suites/e2e/test_rbac_access.py` (in-cluster `X-Rh-Identity`).

---

## Fixtures and setup

| Fixture | Scope | Role |
|---------|-------|------|
| `rbac_gateway_test_user_password` | session | From `RBAC_GATEWAY_TEST_USER_PASSWORD`, or a random ephemeral secret per session (no hardcoded default) |
| `_provision_gateway_nobody_user` | module | Keycloak `nobody-unassigned` |
| `_provision_rbac_iam_reader_gateway` | module | Keycloak `rbac-iam-admin` + in-cluster IAM reader bootstrap |
| `gateway_nobody_user_jwt` / `gateway_rbac_iam_user_jwt` | function | Password-grant JWTs |

Helpers: `tests/jwt_forge.py` (forged JWTs for 401 tests), `tests/rbac_keycloak_users.py`.

---

## `TestRBACGateway`

| Test | Request | Auth | Expected |
|------|---------|------|----------|
| `test_gateway_openshift_costs_unauthenticated_returns_401` | `GET …/reports/openshift/costs/` | none | `401` |
| `test_gateway_rbac_status_unauthenticated_returns_401` | `GET …/rbac/v1/status/` | none | `401` |
| `test_gateway_openshift_costs_user_without_rbac_returns_403` | `GET …/reports/openshift/costs/` | `nobody-unassigned` | `403` or `424` |
| `test_gateway_rbac_groups_unauthenticated_returns_401` | `GET …/rbac/v1/groups/` | none | `401` |
| `test_gateway_rbac_groups_user_without_iam_returns_403` | `GET …/rbac/v1/groups/` | `nobody-unassigned` | `403` or `424` |
| `test_gateway_rbac_principals_iam_reader_returns_200` | `GET …/rbac/v1/principals/` | `rbac-iam-admin` | `200` |
| `test_gateway_rbac_groups_post_iam_reader_forbidden` | `POST …/rbac/v1/groups/` | `rbac-iam-admin` | `403` or `400` |
| `test_gateway_ros_recommendations_unauthenticated_returns_401` | `GET …/recommendations/openshift` | none | `401` |
| `test_gateway_ros_recommendations_user_without_rbac_returns_403` | `GET …/recommendations/openshift` | `nobody-unassigned` | `403` or `424` |

---

## `test_rbac_migration_job_completed`

Helm `rbac-migration` Job `succeeded == 1` in the chart namespace (skips if missing).

---

## `TestRBACSecurityBoundaries`

| Test | What it does | Assertions |
|------|----------------|------------|
| `test_rbac_service_unavailable_denies_access_fail_closed` | Scale `rbac-api` to 0, `GET` cost report, restore replicas | Not `200`; in `403`, `424`, `503`, `504` |
| `test_expired_jwt_rejected` | Forged JWT with past `exp` | `401` on `GET …/status/` |
| `test_jwt_without_required_claims_rejected` | Forged JWT without `sub` | `401` |
| `test_org_id_tenant_isolation_boundary_cases` | Keycloak user per case with malicious `org_id` attribute, real password-grant JWT, `GET` cost report | Not `200`; status in expected set (`400`/`403`/`424`) — parametrized: `empty`, `overflow`, `path-traversal`, `sql-injection`, `wrong-tenant` |
| `test_permission_revocation_honored_after_cache_clear` | Revoke IAM reader from groups, detach `rbac:principal:read` roles from platform-default groups (e.g. `Default access`), disable `role.platform_default`, `cache.clear()`, new token | `403` or `424`; **`finally`** re-runs IAM reader bootstrap and restores seeded platform-default IAM read |
| `test_rbac_iam_reader_cannot_modify_own_permissions` | IAM reader `POST`/`PUT` on groups | `403`, `405`, or `400` |
| `test_concurrent_jwt_sessions_no_resource_exhaustion` | 6 tokens, warm-up, 3 workers, staggered `GET`s | No `500`; **≥4** of 6 return `200` |
| `test_rbac_cache_ttl_configuration_exists` | Read `rbac-api` deployment env | `ACCESS_CACHE_ENABLED=true` |

---

## Running

```bash
pytest tests/suites/auth/test_rbac_gateway.py -v
pytest tests/suites/auth/test_rbac_gateway.py::TestRBACSecurityBoundaries -v
```

Requires deployed chart, Keycloak (`deploy-rhbk.sh` with `roles` client scope on `cost-management-ui`), `python-jose[cryptography]` from `tests/requirements.txt`, and `oc` (cluster-admin for fail-closed scale test).

---

## Related code

| Path | Purpose |
|------|---------|
| `tests/jwt_forge.py` | `forge_jwt`, `forge_expired_jwt`, `forge_jwt_missing_sub` |
| `tests/rbac_keycloak_users.py` | Keycloak Admin API helpers |
| `tests/suites/e2e/test_rbac_access.py` | Persona isolation (in-cluster + gateway JWT) |
