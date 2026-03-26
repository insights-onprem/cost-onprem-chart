# Kessel ReBAC E2E Test Plan

| Field           | Value                                                                     |
|-----------------|---------------------------------------------------------------------------|
| Document ID     | TP-KESSEL-E2E-001                                                         |
| Jira            | [FLPATH-3294](https://issues.redhat.com/browse/FLPATH-3294)              |
| Author          | Jordi Gil                                                                 |
| Status          | Draft                                                                     |
| Created         | 2026-02-24                                                               |
| IEEE 829 Ref    | IEEE 829-2008 (Software and System Test Documentation)                   |

---

## Table of Contents

1. [Test Plan Identifier](#1-test-plan-identifier)
2. [Introduction](#2-introduction)
3. [Test Items](#3-test-items)
4. [Features To Be Tested](#4-features-to-be-tested)
5. [Features Not To Be Tested](#5-features-not-to-be-tested)
6. [Approach](#6-approach)
7. [Item Pass/Fail Criteria](#7-item-passfail-criteria)
8. [Suspension and Resumption Criteria](#8-suspension-and-resumption-criteria)
9. [Test Deliverables](#9-test-deliverables)
10. [Test Environment](#10-test-environment)
11. [Responsibilities](#11-responsibilities)
12. [Schedule](#12-schedule)
13. [Risks and Mitigations](#13-risks-and-mitigations)
14. [Blast Radius Analysis: ReBAC on Existing Tests](#14-blast-radius-analysis-rebac-on-existing-tests)
15. [Test Scenarios](#15-test-scenarios)
16. [Traceability Matrix](#16-traceability-matrix)
17. [Required Modifications to Existing Tests](#17-required-modifications-to-existing-tests)

---

## 1. Test Plan Identifier

`TP-KESSEL-E2E-001`

This plan covers E2E test scenarios for Kessel ReBAC integration within the
`ros-helm-chart` test suite (`tests/suites/`). It extends the existing pytest
infrastructure with a new `kessel` suite under `tests/suites/kessel/`.

---

## 2. Introduction

### 2.1 Purpose

Validate that the Kessel Relationship-Based Access Control (ReBAC) integration
with Koku works correctly end-to-end on an OpenShift cluster. The tests exercise
the full authorization chain: schema provisioning, role seeding, resource
reporting, role binding management, group membership, and data-plane filtering.

### 2.2 Scope

- Kessel infrastructure health (SpiceDB, Relations API, Inventory API)
- Schema and role provisioning via `deploy-kessel.sh` and migration job
- Authorization enforcement through the Koku API (gateway + internal)
- Role binding lifecycle (create, verify, revoke)
- Group membership and transitive permission resolution
- Interaction with existing data pipeline (OCP source, ingress, reports)

### 2.3 Relationship to Koku Test Plan

The Koku repo contains [kessel-ocp-test-plan.md](../../../koku/docs/architecture/kessel-integration/kessel-ocp-test-plan.md)
which defines 7 E2E scenarios (E2E-KESSEL-FLOW-001 through -007) at the
design level. This document operationalizes those scenarios as pytest test
cases within the ros-helm-chart E2E framework, adding infrastructure
verification and enhancing existing tests for Kessel awareness.

---

## 3. Test Items

| Item | Version | Source |
|------|---------|--------|
| Koku (ReBAC image) | `quay.io/insights-onprem/koku:kessel` | `FLPATH-3294/kessel-rebac-integration` branch |
| SpiceDB | `docker.io/authzed/spicedb:latest` | Deployed by `deploy-kessel.sh` |
| Kessel Relations API | Deployed by `deploy-kessel.sh` | `kessel` namespace |
| Kessel Inventory API | Deployed by `deploy-kessel.sh` | `kessel` namespace |
| ZED Schema | `scripts/kessel/schema.zed` | ros-helm-chart repo |
| Helm Chart | `cost-onprem` (local) | `feature/FLPATH-2685-external-services-byoi` branch |

---

## 4. Features To Be Tested

| ID | Feature | Priority |
|----|---------|----------|
| F-01 | Kessel infrastructure health | P0 |
| F-02 | Schema provisioning (deploy-kessel.sh + migration safety net) | P0 |
| F-03 | Role seeding (23 relationships for 5 standard roles) | P0 |
| F-04 | Authorized user sees filtered cost data via ReBAC | P0 |
| F-05 | Unauthorized user denied access (empty result set) | P0 |
| F-06 | Role binding creation via Access Management API | P1 |
| F-07 | Role binding revocation removes access | P1 |
| F-08 | Group creation and membership management | P1 |
| F-09 | Group-based transitive permission resolution | P1 |
| F-10 | Primary test user full access via cost-administrator binding | P0 |
| F-11 | OCP resource auto-discovery via pipeline | P1 |
| F-12 | Cost model resource authorization | P1 |

---

## 5. Features Not To Be Tested

| Feature | Rationale |
|---------|-----------|
| SpiceDB internal consistency | Covered by upstream SpiceDB tests |
| Kessel Relations API gRPC contract | Covered by koku_rebac contract tests (CT tier) |
| Koku RBAC (non-ReBAC) authorization | Existing tests cover this; ReBAC replaces it on-prem |
| UI rendering of authorization state | UI tests are separate; ReBAC is transparent to UI |
| Multi-cluster Kessel HA | Out of scope for single-cluster E2E |

---

## 6. Approach

### 6.1 Test Framework

- **Runner**: pytest via `scripts/run-pytest.sh` (all tests go through ReBAC)
- **Marker**: `@pytest.mark.kessel` for Kessel-specific scenarios
- **Suite**: `tests/suites/kessel/` (new directory for Kessel-specific tests)
- **Fixtures**: Kessel fixtures in `tests/suites/kessel/conftest.py` and
  session-level bootstrap fixture in root `tests/conftest.py`
- **Dependencies**: `grpcio`, `authzed` (for direct SpiceDB verification)

### 6.2 Authentication Strategy

All tests run with `ENHANCED_ORG_ADMIN=False`. Every API request is authorized
through the full Kessel ReBAC chain. A session-scoped bootstrap fixture
(`kessel_bootstrap`) creates role bindings for the primary test user before any
tests execute, ensuring existing tests continue to pass while exercising the
real authorization path.

| Actor | Method | Kessel State |
|-------|--------|--------------|
| Primary test user | JWT via Keycloak (`cost-management-operator`) | `cost-administrator` role binding created by bootstrap fixture |
| Test admin (Kessel mgmt) | X-Rh-Identity with `is_org_admin=False` | `cost-administrator` role binding (for Access Management API) |
| Restricted user | X-Rh-Identity with `is_org_admin=False` | Specific role bindings per scenario |
| No-access user | X-Rh-Identity with `is_org_admin=False` | No role bindings |

### 6.3 Bootstrap Fixture

A session-scoped `autouse` fixture runs before all tests:

1. Detects the primary test user's username from the Keycloak JWT
2. Creates a `cost-administrator` role binding for that user in Kessel
   via the Access Management API (using an initial admin identity)
3. Verifies the binding is active by calling the Koku status endpoint

This ensures every existing test (sources, cost models, reports, ingress,
complete flow, etc.) exercises the full ReBAC authorization chain rather
than bypassing it. If any existing test fails, it surfaces a real ReBAC
regression -- not a test configuration issue.

**Teardown**: The binding persists for the session. Cleanup runs at session end.

### 6.4 Kessel Interaction

Tests interact with Kessel through two paths:

1. **Koku Access Management API** (`/api/cost-management/v1/access-management/`)
   for role bindings, groups, and membership management
2. **Direct SpiceDB verification** (via `authzed` SDK from test-runner pod)
   for state verification when the Koku API is not the actor

### 6.5 Data Strategy

- Reuse existing NISE data generation from `e2e_helpers.py`
- Create Kessel-specific fixtures that register sources and create role bindings
- Verify authorization by comparing API responses between authorized and
  unauthorized users for the same data

### 6.6 Integration with Existing Tests

All existing tests run through ReBAC. The deployment sets
`ENHANCED_ORG_ADMIN=False` and the bootstrap fixture provisions the primary
test user with `cost-administrator` access. This means:

- **No test bypass**: Every API call goes through `KesselAccessProvider` ->
  `LookupResources` -> SpiceDB permission check
- **Existing tests validate ReBAC**: If the complete flow test
  (`test_complete_flow.py`) passes, it proves the full pipeline works
  under ReBAC authorization
- **Regressions surface immediately**: A broken schema, missing role seed,
  or Relations API failure will cause existing tests to fail with 403 or
  empty data, not silently pass via bypass

Tests that need different permission levels (restricted users, no-access users)
create their own role bindings per scenario.

---

## 7. Item Pass/Fail Criteria

| Criterion | Pass | Fail |
|-----------|------|------|
| Infrastructure health | All Kessel pods Running, Jobs Completed | Any pod in CrashLoopBackOff or Job Failed |
| Schema provisioning | Schema written to SpiceDB (non-empty ReadSchema) | WriteSchema gRPC error or empty schema |
| Role seeding | 23 relationships readable from SpiceDB | Missing relationships or count mismatch |
| Authorization grant | HTTP 200 with non-empty `data` array | HTTP 403/401 or empty `data` when access was granted |
| Authorization denial | HTTP 200 with empty `data` array or HTTP 403 | Non-empty `data` returned for unauthorized user |
| Role binding lifecycle | Create returns 201, revoke returns 204, effect immediate | Create/revoke fails or stale access persists |
| Group resolution | Group member inherits all group permissions | Group member sees empty data despite group binding |

---

## 8. Suspension and Resumption Criteria

### Suspension

- Kessel namespace pods are not running (SpiceDB, Relations API down)
- Koku API pods are in CrashLoopBackOff
- Keycloak is unreachable (no JWT obtainable)
- Database is unreachable

### Resumption

- All infrastructure health checks pass (`test_smoke.py` green)
- Kessel pods are in Running state with Ready condition

---

## 9. Test Deliverables

| Deliverable | Location |
|-------------|----------|
| Test plan (this document) | `tests/docs/kessel-e2e-test-plan.md` |
| Bootstrap fixture | `tests/conftest.py` (session-scoped `kessel_bootstrap`) |
| Kessel test code | `tests/suites/kessel/` |
| Kessel fixtures | `tests/suites/kessel/conftest.py` |
| JUnit XML report | `tests/reports/junit.xml` (standard pytest output) |

---

## 10. Test Environment

### 10.1 Infrastructure

| Component | Details |
|-----------|---------|
| Cluster | OpenShift 4.x (parodos-dev) |
| Kessel namespace | `kessel` (deployed by `deploy-kessel.sh`) |
| Cost Management namespace | `cost-onprem` (deployed by Helm chart) |
| Keycloak namespace | `keycloak` (RHBK operator) |
| Kafka namespace | `kafka` (Strimzi operator) |

### 10.2 Configuration

| Setting | Value | Purpose |
|---------|-------|---------|
| `ONPREM` | `True` | Enables ReBAC backend (implies `AUTHORIZATION_BACKEND=rebac`) |
| `SPICEDB_HOST` | `spicedb.kessel.svc.cluster.local` | Direct SpiceDB access |
| `KESSEL_RELATIONS_HOST` | `kessel-relations.kessel.svc.cluster.local` | Relations API |

> **Note**: `ONPREM=True` forces `AUTHORIZATION_BACKEND=rebac` unconditionally
> via `resolve_authorization_backend()` in `koku_rebac/config.py`. The
> `AUTHORIZATION_BACKEND` env var is ignored in on-prem deployments. There is
> no separate setting to configure.

### 10.3 Test Users

| User | org_id | is_org_admin | Kessel State |
|------|--------|--------------|--------------|
| `test` (Keycloak) | From Keycloak attributes | `False` | `cost-administrator` binding via bootstrap fixture |
| `kessel-user-a` | Same org | `False` | Managed by test fixtures (restricted roles) |
| `kessel-user-b` | Same org | `False` | Managed by test fixtures (restricted roles) |
| `kessel-no-access` | Same org | `False` | No role bindings |

---

## 11. Responsibilities

| Role | Responsibility |
|------|----------------|
| Test author | Implement scenarios, maintain fixtures |
| Reviewer | Validate coverage against DD scenarios |
| CI operator | Run `scripts/run-pytest.sh` on target cluster (all tests go through ReBAC) |

---

## 12. Schedule

| Milestone | Target |
|-----------|--------|
| Test plan approved | Current session |
| Infrastructure scenarios (S-01 to S-04) | Sprint 1 |
| Authorization scenarios (S-05 to S-08) | Sprint 1 |
| Group and lifecycle scenarios (S-09 to S-12) | Sprint 2 |
| Pipeline integration scenario (S-13) | Sprint 2 |

---

## 13. Risks and Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Bootstrap binding fails | All tests fail (no authorization) | High | Fail-fast in `kessel_bootstrap`; clear error message identifies the cause |
| Existing tests fail under ReBAC | Regressions surface as 403/empty | Medium | S-05 runs first as gate; if it fails, remaining tests are skipped with clear diagnostics |
| SpiceDB cold start delays | Tests timeout on first run | Medium | `backoffLimit: 3` on zed Job; retry in fixture |
| Kessel cache staleness | Revocation test sees stale data | Medium | Flush Valkey between tests; use `KESSEL_CACHE_TIMEOUT=0` |
| Bootstrap binding propagation delay | Early tests fail with 403 | Medium | Bootstrap fixture polls permission check before yielding |
| Keycloak token expiry | Long test suites get 401s | Low | `jwt_token` fixture is function-scoped (fresh per test) |
| NISE data generation fails | E2E data pipeline tests skip | Low | Fallback to simple CSV; separate infra tests from data tests |
| Relations API restart during test | gRPC calls fail mid-assertion | Low | Retry with backoff in test fixtures |

---

## 14. Blast Radius Analysis: ReBAC on Existing Tests

This section traces every API call made by existing E2E tests through the
ReBAC authorization chain when `ONPREM=True` and `ENHANCED_ORG_ADMIN=False`.
It identifies which tests break, why, and what the bootstrap fixture must
provide.

### 14.1 Prerequisites: PR #5895 Cherry-Pick

PR [#5895](https://github.com/project-koku/koku/pull/5895) (ELK4N4:
"Add Sources API compatibility endpoints to Koku for on-prem") must be
cherry-picked into our branch. It adds:

- `/source_types`, `/application_types`, `/applications` endpoints to Koku
- `AdminSourcesSerializer` with `source_type_id` and `source_ref` fields
- Full CRUD on sources (POST/PATCH/DELETE gated by `ONPREM`)
- Kafka event publishing on source deletion

Without this PR, the E2E test fixtures that call `/source_types` and
`/application_types` on the internal Koku API will get 404s.

**Catalog endpoint authentication**: PR #5895 adds `/application_types` and
`/source_types` to Koku's `is_no_auth` middleware list, matching the
sources-api-go behavior where these catalog endpoints skip app-level auth.
In on-prem, these endpoints are protected at the network level by the
existing `cost-api-access` NetworkPolicy, which restricts koku-api ingress
to only gateway, ingress, and housekeeper pods. The only unauthenticated
pod-to-pod caller is the ROS housekeeper, which calls `/application_types`
at startup without identity headers.

[FLPATH-3336](https://issues.redhat.com/browse/FLPATH-3336) tracks the
follow-up to route the housekeeper through the gateway with Keycloak JWT
auth, after which `/application_types` and `/source_types` can be removed
from `is_no_auth`.

### 14.2 Authorization Chain Summary

```
Request → IdentityHeaderMiddleware
  ├─ is_no_auth? (/status, /source_types, /application_types) → skip auth
  ├─ Parse identity header (JWT or X-Rh-Identity)
  ├─ _get_access(user):
  │     ENHANCED_ORG_ADMIN=False → always call KesselAccessProvider
  │     → LookupResources for each (resource_type, operation) pair
  │     → Returns access dict: {type: {read: [...], write: [...]}}
  ├─ KokuTenantMiddleware._check_user_has_access:
  │     access dict is always non-empty (structured) → passes
  └─ View permission class:
        ├─ AllowAny (sources, status) → always passes
        ├─ OpenShiftAccessPermission → requires openshift.cluster.read non-empty
        ├─ CostModelsAccessPermission → requires cost_model.read/write
        └─ SettingsAccessPermission → requires settings.read/write
```

### 14.3 Endpoint-by-Endpoint Impact

#### No-auth endpoints (unaffected)

| Endpoint | Why unaffected |
|----------|----------------|
| `GET /status/` | `is_no_auth` → True; `AllowAny` permission |
| `GET /source_types` | `is_no_auth` → True (PR #5895); NetworkPolicy protected; [FLPATH-3336] to add app-level auth |
| `GET /application_types` | `is_no_auth` → True (PR #5895); NetworkPolicy protected; [FLPATH-3336] to add app-level auth |
| Keycloak token endpoints | External service, not Koku |
| `GET /ingress/ready` | Gateway health, not Koku |

#### AllowAny endpoints (access-filtered, not permission-gated)

| Endpoint | Permission | ReBAC Impact |
|----------|------------|--------------|
| `GET /sources/` | `AllowAny` | **Queryset filtered** by `get_excludes()`. Without OCP read access, OCP sources excluded → empty `data` |
| `POST /sources/` | `AllowAny` | No permission gate, but user must have a customer/org_id |
| `DELETE /sources/{id}/` | `AllowAny` | Object must be in queryset (filtered by access) |
| `GET /sources/{id}/` | `AllowAny` | Object must be in queryset |
| `GET /applications` | `AllowAny` (PR #5895) | No filtering |

#### Permission-gated endpoints (will 403 without bindings)

| Endpoint | Permission Class | Required Access | Failure Mode |
|----------|-----------------|-----------------|--------------|
| `GET /reports/openshift/costs/` | `OpenShiftAccessPermission` | `openshift.cluster.read` non-empty | **403** |
| `GET /reports/openshift/compute/` | `OpenShiftAccessPermission` | `openshift.cluster.read` non-empty | **403** |
| `GET /reports/openshift/memory/` | `OpenShiftAccessPermission` | `openshift.cluster.read` non-empty | **403** |
| `GET /reports/openshift/volumes/` | `OpenShiftAccessPermission` | `openshift.cluster.read` non-empty | **403** |
| `GET /tags/openshift/` | `OpenShiftAccessPermission` | `openshift.cluster.read` non-empty | **403** |
| `GET /cost-models/` | `CostModelsAccessPermission` | `cost_model.read` non-empty | **403** |
| `POST /cost-models/` | `CostModelsAccessPermission` | `cost_model.write` contains `*` | **403** |
| `DELETE /cost-models/{uuid}/` | `CostModelsAccessPermission` | `cost_model.write` contains `*` or uuid | **403** |
| `GET /recommendations/openshift` | Proxied to ROS | Depends on ROS auth | May not be Koku-gated |

#### External service endpoints (not Koku-gated)

| Endpoint | Service | ReBAC Impact |
|----------|---------|--------------|
| `POST /ingress/v1/upload` | `insights-ingress-go` | JWT-only auth at gateway; **not gated by Koku ReBAC** |

### 14.4 Test-by-Test Blast Radius

#### Tests that PASS without changes (no ReBAC impact)

| Test File | Tests | Reason |
|-----------|-------|--------|
| `suites/e2e/test_smoke.py` | 4 | Only hits `/status/` (no-auth) and Keycloak |
| `suites/auth/test_keycloak.py` | 2 | Only hits Keycloak OIDC endpoints |
| `suites/auth/test_ui_oauth.py` | 2 | Only hits Keycloak token endpoint |
| `suites/auth/test_gateway_auth.py` (3 of 5) | 3 | Unauthenticated/malformed tests; expected 401 |
| `suites/e2e/test_scenarios.py` | all | No HTTP calls (YAML generation only) |
| `suites/ros/test_kruize.py` | all | Pod exec, no HTTP API |

#### Tests that BREAK without bootstrap binding

| Test File | Tests | Endpoint | Permission | Failure |
|-----------|-------|----------|------------|---------|
| **`suites/api/test_reports.py`** | 7 | `/reports/openshift/*` | `OpenShiftAccessPermission` | **403** |
| **`suites/api/test_tagging.py`** | 7+ | `/tags/openshift/`, `/reports/openshift/costs/` | `OpenShiftAccessPermission` | **403** |
| **`suites/api/test_cost_models.py`** | 6 | `/cost-models/` | `CostModelsAccessPermission` | **403** |
| **`suites/auth/test_gateway_auth.py`** (`test_sources_api_accessible`) | 1 | `/sources/` | `AllowAny` | **200 but empty `data`** (may fail assertion) |
| **`suites/interpod/test_koku_api.py`** (`test_reports_endpoint_with_identity`) | 1 | `/reports/openshift/costs/` | `OpenShiftAccessPermission` | **403** |
| **`suites/interpod/test_koku_api.py`** (`test_sources_list_with_identity`) | 1 | `/sources/` | `AllowAny` | **200 but empty** (may fail assertion on `data`) |
| **`suites/sources/test_sources_api.py`** (external via gateway) | 9 | `/sources/*` | `AllowAny` | **200 but filtered/empty** |
| **`suites/sources/test_sources_api.py`** (interpod) | 20+ | `/sources/*` | `AllowAny` | **200 but filtered/empty**; creates may succeed but reads may not see them |
| **`suites/e2e/test_complete_flow.py`** | multi-step | `/sources/`, `/reports/*`, `/recommendations/*` | Mixed | **Source invisible after create** (filtered out); **reports 403** |
| **`suites/cost_management/conftest.py`** | fixture | `/sources/`, ingress | Mixed | **Source registration may succeed but becomes invisible** |
| **`suites/ros/test_recommendations.py`** | 1 | `/recommendations/openshift` | ROS proxy | **Depends on ROS auth config** |

### 14.5 Bootstrap Fixture Requirements

The `kessel_bootstrap` fixture must create role bindings that grant the
test users the following access in SpiceDB:

| Kessel Resource Type | Relation | Grantee | Reason |
|---------------------|----------|---------|--------|
| `openshift_cluster` | `read` | Primary test user | Reports, tags, sources visibility |
| `openshift_node` | `read` | Primary test user | Inherited from cluster (OCP cascade) |
| `openshift_project` | `read` | Primary test user | Inherited from cluster (OCP cascade) |
| `cost_model` | `read` | Primary test user | Cost model list/get |
| `cost_model` | `write` | Primary test user | Cost model create/delete |
| `settings` | `read` | Primary test user | Ingress (if grace period off) |
| `settings` | `write` | Primary test user | Ingress POST |
| `aws_account` | `read` | Primary test user | Sources visibility (AWS type) |
| `gcp_account` | `read` | Primary test user | Sources visibility (GCP type) |
| `gcp_project` | `read` | Primary test user | Sources visibility (GCP type) |
| `azure_subscription_guid` | `read` | Primary test user | Sources visibility (Azure type) |

All of the above are granted by the `cost-administrator` role (seeded by
`kessel_seed_roles`). The bootstrap fixture creates a single
`cost-administrator` role binding for the primary test user, which covers
every permission above.

For interpod tests using X-Rh-Identity, the identity's username must also
have the `cost-administrator` binding. The fixture must ensure the
interpod identity's username matches a bound user.

### 14.6 Sources-Specific Concerns

The sources `get_excludes()` method iterates over `RESOURCE_TYPE_MAP` and
excludes provider types for which the user has no `read` access. With
`cost-administrator`, all resource types have `read` access, so no source
types are excluded.

However, without any binding:
- `get_excludes()` returns all provider types → queryset returns zero sources
- `POST /sources/` succeeds (creates the source) but subsequent `GET` cannot
  see it → tests that create-then-read will fail
- `DELETE /sources/{id}/` will 404 (object not in filtered queryset)

The `test_non_admin_source_creation_returns_424` test in interpod tests
calls POST with `is_org_admin=False` and expects 424. With ReBAC, the
behavior depends on whether the identity has `write` access to the source
type. This test may need adjustment.

### 14.7 Ingress Upload Endpoint

The E2E ingress tests (`test_ingress.py`, `test_complete_flow.py`) call
`/ingress/v1/upload` which routes to `insights-ingress-go`, not to Koku's
ingress endpoint. This is JWT-authenticated at the gateway level and
**not subject to Koku ReBAC**. These tests are unaffected.

If any test calls Koku's `/api/cost-management/v1/ingress/reports/`
directly, it requires `settings.write` via `IngressAccessPermission`
(unless the Unleash grace period flag is enabled).

### 14.8 Impact Summary

| Category | Count | Impact |
|----------|-------|--------|
| Unaffected (no-auth / external) | ~15 tests | None |
| Break with 403 (permission-gated) | ~20 tests | Need `cost-administrator` binding |
| Break with empty data (access-filtered) | ~30 tests | Need `cost-administrator` binding |
| **Total requiring bootstrap** | **~50 tests** | Single `cost-administrator` binding resolves all |

---

## 15. Test Scenarios

### Conventions

Scenario IDs follow `S-{NN}` format. Each maps to one or more E2E-KESSEL-FLOW
scenarios from the Koku test plan.

---

### S-01: Kessel infrastructure pods are healthy

| Field | Value |
|-------|-------|
| ID | S-01 |
| Priority | P0 |
| Maps to | (New -- infrastructure prerequisite) |
| Marker | `@pytest.mark.kessel`, `@pytest.mark.smoke` |
| Module | `tests/suites/kessel/test_infrastructure.py` |

**Prerequisites:**
- `deploy-kessel.sh` has been run
- `kessel` namespace exists

**Steps:**
- **Given** the Kessel stack has been deployed via `deploy-kessel.sh`
- **When** the test queries pod status in the `kessel` namespace
- **Then** `spicedb` deployment has 1/1 Ready replicas
- **And** `kessel-relations` deployment has 1/1 Ready replicas
- **And** `kessel-inventory` deployment has 1/1 Ready replicas
- **And** `kessel-db` statefulset has 1/1 Ready replicas
- **And** `spicedb-migrate` job is Complete
- **And** `spicedb-schema-init` job is Complete

**Acceptance Criteria:**
- All pods are Running with Ready condition
- All Jobs show `status.succeeded >= 1`

---

### S-02: SpiceDB schema is provisioned and readable

| Field | Value |
|-------|-------|
| ID | S-02 |
| Priority | P0 |
| Maps to | E2E-KESSEL-FLOW-006 (bootstrap prerequisite) |
| Marker | `@pytest.mark.kessel` |
| Module | `tests/suites/kessel/test_infrastructure.py` |

**Prerequisites:**
- S-01 passes

**Steps:**
- **Given** SpiceDB is running and the schema-init Job completed
- **When** the test reads the schema from SpiceDB via gRPC (ReadSchema)
- **Then** the schema text contains `definition rbac/role`
- **And** the schema text contains `definition rbac/role_binding`
- **And** the schema text contains `definition rbac/group`
- **And** the schema text contains `definition rbac/principal`
- **And** the schema text contains `definition cost_management/`

**Acceptance Criteria:**
- The schema is non-empty and contains all required definitions
- The schema matches the content of `scripts/kessel/schema.zed`

---

### S-03: Standard roles are seeded in SpiceDB

| Field | Value |
|-------|-------|
| ID | S-03 |
| Priority | P0 |
| Maps to | E2E-KESSEL-FLOW-006 (bootstrap prerequisite) |
| Marker | `@pytest.mark.kessel` |
| Module | `tests/suites/kessel/test_infrastructure.py` |

**Prerequisites:**
- S-01 passes, migration job completed

**Steps:**
- **Given** the Koku migration job has run `kessel_seed_roles`
- **When** the test reads relationships from SpiceDB for `rbac/role`
- **Then** exactly 5 roles exist: `cost-administrator`, `cost-cloud-viewer`,
  `cost-openshift-viewer`, `cost-price-list-administrator`, `cost-price-list-viewer`
- **And** the total relationship count is 23

**Acceptance Criteria:**
- All 5 standard roles have their expected permission relations
- The relationship count matches the expected 23 (from STANDARD_ROLES definition)

---

### S-04: Kessel services are reachable from cost-onprem namespace

| Field | Value |
|-------|-------|
| ID | S-04 |
| Priority | P0 |
| Maps to | (New -- connectivity prerequisite) |
| Marker | `@pytest.mark.kessel`, `@pytest.mark.smoke` |
| Module | `tests/suites/kessel/test_infrastructure.py` |

**Prerequisites:**
- S-01 passes

**Steps:**
- **Given** Kessel is deployed in the `kessel` namespace
- **And** Koku is deployed in the `cost-onprem` namespace
- **When** a TCP connection is attempted from a `cost-onprem` pod to:
  - `kessel-relations.kessel.svc.cluster.local:9000`
  - `kessel-inventory.kessel.svc.cluster.local:9000`
  - `spicedb.kessel.svc.cluster.local:50051`
- **Then** all three connections succeed

**Acceptance Criteria:**
- Cross-namespace gRPC connectivity is confirmed
- No NetworkPolicy blocks traffic between namespaces

---

### S-05: Primary test user has full access via cost-administrator binding

| Field | Value |
|-------|-------|
| ID | S-05 |
| Priority | P0 |
| Maps to | E2E-KESSEL-FLOW-006 (bootstrap phase) |
| Marker | `@pytest.mark.kessel` |
| Module | `tests/suites/kessel/test_authorization.py` |

**Prerequisites:**
- S-01 through S-04 pass
- OCP source registered and cost data ingested
- Bootstrap fixture has created a `cost-administrator` role binding for the
  primary test user (Keycloak JWT user)

**Steps:**
- **Given** `ENHANCED_ORG_ADMIN=False` is set in the Koku deployment
- **And** the primary test user has a `cost-administrator` role binding in Kessel
- **And** cost data exists for at least one OCP cluster
- **When** the primary test user (JWT via Keycloak) queries
  `/api/cost-management/v1/reports/openshift/costs/`
- **Then** the response status is 200
- **And** the `data` array is non-empty
- **And** the user can see all clusters (full access via cost-administrator role)

**Acceptance Criteria:**
- Full access is granted through Kessel ReBAC, not via bypass
- The `cost-administrator` role binding grants permissions equivalent to the
  previous `ENHANCED_ORG_ADMIN=True` behavior
- SpiceDB `LookupResources` returns all OCP cluster resources for this user

---

### S-06: Authorized user sees only permitted cost data

| Field | Value |
|-------|-------|
| ID | S-06 |
| Priority | P0 |
| Maps to | E2E-KESSEL-FLOW-001 |
| Marker | `@pytest.mark.kessel` |
| Module | `tests/suites/kessel/test_authorization.py` |

**Prerequisites:**
- S-01 through S-04 pass
- Cost data exists for at least one OCP cluster
- The test fixture creates a role binding in Kessel:
  `kessel-user-a` bound to `cost-openshift-viewer` for the test tenant

**Steps:**
- **Given** `kessel-user-a` has a role binding granting `cost-openshift-viewer`
- **When** `kessel-user-a` queries `/api/cost-management/v1/reports/openshift/costs/`
  via X-Rh-Identity (non-admin, `is_org_admin=False`)
- **Then** the response status is 200
- **And** the `data` array contains cost data for the authorized cluster
- **And** no data for unauthorized clusters is returned

**Acceptance Criteria:**
- Kessel LookupResources resolves the correct cluster IDs for the user
- The API response contains exactly the data the user is authorized to see
- The response shape matches what RBAC would return for equivalent permissions

---

### S-07: Unauthorized user sees empty cost data

| Field | Value |
|-------|-------|
| ID | S-07 |
| Priority | P0 |
| Maps to | E2E-KESSEL-FLOW-002 |
| Marker | `@pytest.mark.kessel` |
| Module | `tests/suites/kessel/test_authorization.py` |

**Prerequisites:**
- S-01 through S-04 pass
- Cost data exists in the system

**Steps:**
- **Given** `kessel-no-access` has no role bindings in Kessel
- **When** `kessel-no-access` queries `/api/cost-management/v1/reports/openshift/costs/`
  via X-Rh-Identity (non-admin)
- **Then** the response status is 200 (authenticated but not authorized)
- **And** the `data` array is empty
- **And** no cluster names, IDs, or cost amounts are leaked

**Acceptance Criteria:**
- Zero data returned for a user with no Kessel permissions
- No metadata leakage (cluster names, project names, etc.)
- Matches RBAC behavior for a user with no cost-management roles

---

### S-08: Role binding creation via Access Management API

| Field | Value |
|-------|-------|
| ID | S-08 |
| Priority | P1 |
| Maps to | E2E-KESSEL-FLOW-001 (setup), E2E-KESSEL-FLOW-006 (transition) |
| Marker | `@pytest.mark.kessel` |
| Module | `tests/suites/kessel/test_role_bindings.py` |

**Prerequisites:**
- S-01 through S-04 pass
- Org admin JWT available

**Steps:**
- **Given** the org admin is authenticated
- **When** the admin creates a role binding via
  `POST /api/cost-management/v1/access-management/role-bindings/`
  with body `{ "role": "cost-openshift-viewer", "subject": "kessel-user-a", "subject_type": "principal" }`
- **Then** the response status is 201
- **And** the role binding is readable via
  `GET /api/cost-management/v1/access-management/role-bindings/`
- **And** SpiceDB contains the corresponding `rbac/role_binding` tuple

**Acceptance Criteria:**
- The Access Management API creates the binding in Kessel
- The binding is immediately visible via the API and verifiable in SpiceDB

---

### S-09: Role binding revocation removes access immediately

| Field | Value |
|-------|-------|
| ID | S-09 |
| Priority | P1 |
| Maps to | E2E-KESSEL-FLOW-003 |
| Marker | `@pytest.mark.kessel` |
| Module | `tests/suites/kessel/test_role_bindings.py` |

**Prerequisites:**
- S-06 passes (user has active role binding with data access)

**Steps:**
- **Given** `kessel-user-a` can see cost data (verified by S-06)
- **When** the org admin deletes the role binding via
  `DELETE /api/cost-management/v1/access-management/role-bindings/{id}/`
- **And** the Kessel access cache is invalidated
- **And** `kessel-user-a` queries the reports endpoint again
- **Then** the `data` array is empty (access revoked)

**Acceptance Criteria:**
- Access revocation takes effect within one request cycle
- No stale cached access is served after deletion
- The SpiceDB tuple for the role binding no longer exists

---

### S-10: Group creation and member management

| Field | Value |
|-------|-------|
| ID | S-10 |
| Priority | P1 |
| Maps to | E2E-KESSEL-FLOW-007 (setup) |
| Marker | `@pytest.mark.kessel` |
| Module | `tests/suites/kessel/test_groups.py` |

**Prerequisites:**
- S-01 through S-04 pass
- Org admin authenticated

**Steps:**
- **Given** the org admin is authenticated
- **When** the admin creates a group `team-alpha` via
  `POST /api/cost-management/v1/access-management/groups/`
- **Then** the response status is 201
- **When** the admin adds `kessel-user-a` to `team-alpha` via
  `POST /api/cost-management/v1/access-management/groups/{id}/members/`
- **Then** the response status is 201
- **And** listing members of `team-alpha` includes `kessel-user-a`
- **And** SpiceDB contains `rbac/group:team-alpha#t_member@rbac/principal:kessel-user-a`

**Acceptance Criteria:**
- Group CRUD works via the Access Management API
- Member addition is reflected in both the API and SpiceDB
- Member removal (tested in teardown) also works correctly

---

### S-11: Group-based access grants transitive permissions

| Field | Value |
|-------|-------|
| ID | S-11 |
| Priority | P1 |
| Maps to | E2E-KESSEL-FLOW-007 |
| Marker | `@pytest.mark.kessel` |
| Module | `tests/suites/kessel/test_groups.py` |

**Prerequisites:**
- S-10 passes (group exists with `kessel-user-a` as member)
- Cost data exists for at least one cluster

**Steps:**
- **Given** group `team-alpha` exists with `kessel-user-a` as a member
- **And** `kessel-user-b` is NOT a member of any group
- **When** the admin creates a role binding granting `cost-openshift-viewer` to `team-alpha` (group)
- **And** `kessel-user-a` queries the OCP cost report
- **Then** `kessel-user-a` receives cost data (inherited via group membership)
- **When** `kessel-user-b` queries the same endpoint
- **Then** `kessel-user-b` receives empty data (not in group)
- **When** `kessel-user-b` is added to `team-alpha`
- **And** `kessel-user-b` queries the endpoint again
- **Then** `kessel-user-b` now receives cost data

**Acceptance Criteria:**
- Group membership provides transitive permission inheritance
- Adding a user to a group grants permissions immediately
- SpiceDB correctly resolves `rbac/group#t_member` in permission checks

---

### S-12: Cost model authorization via ReBAC

| Field | Value |
|-------|-------|
| ID | S-12 |
| Priority | P1 |
| Maps to | E2E-KESSEL-FLOW-005 |
| Marker | `@pytest.mark.kessel` |
| Module | `tests/suites/kessel/test_authorization.py` |

**Prerequisites:**
- S-01 through S-04 pass
- Role binding for `cost-price-list-administrator` exists for `kessel-user-a`

**Steps:**
- **Given** `kessel-user-a` has `cost-price-list-administrator` role binding
- **When** `kessel-user-a` creates a cost model via
  `POST /api/cost-management/v1/cost-models/`
- **Then** the response status is 201
- **When** `kessel-user-a` lists cost models
- **Then** the created cost model is visible
- **When** `kessel-no-access` lists cost models
- **Then** the result is empty (no cost_model:read permission)

**Acceptance Criteria:**
- Cost model write permission is enforced via ReBAC
- Cost model read filtering works for authorized vs unauthorized users

---

### S-13: OCP resources auto-discovered via data pipeline

| Field | Value |
|-------|-------|
| ID | S-13 |
| Priority | P1 |
| Maps to | E2E-KESSEL-FLOW-004 |
| Marker | `@pytest.mark.kessel`, `@pytest.mark.slow` |
| Module | `tests/suites/kessel/test_pipeline.py` |

**Prerequisites:**
- S-01 through S-04 pass
- NISE data available

**Steps:**
- **Given** an OCP source is registered and data is uploaded via ingress
- **When** Koku processes the data through the pipeline (MASU -> summary)
- **And** the ResourceReporter runs during pipeline completion
- **Then** the OCP cluster is reported to Kessel Inventory
- **And** OCP nodes from the data are reported as resources
- **And** OCP projects from the data are reported as resources
- **And** a role binding granting project-level access results in filtered data

**Acceptance Criteria:**
- Resources are auto-discovered without manual registration
- Fine-grained authorization (project-level) works after discovery
- `KesselSyncedResource` records exist with successful sync status

---

### S-14: Opt-in access model — multi-workspace, group-based authorization

| Field | Value |
|-------|-------|
| ID | S-14 |
| Priority | P0 |
| Maps to | ADR reference scenario (Business Case: Opt-In Access Model) |
| Marker | `@pytest.mark.e2e`, `@pytest.mark.kessel`, `@pytest.mark.ui` |
| Module (API) | `tests/suites/e2e/test_opt_in_authorization.py` |
| Module (UI) | `tests/suites/ui/test_opt_in_authorization_ui.py` |
| Documentation | [opt-in-access-model-test-scenarios.md](opt-in-access-model-test-scenarios.md) |

**Prerequisites:**
- S-01 through S-04 pass
- `deploy-rhbk.sh` has created users: test1, test2, test3
- `kessel-admin.sh demo <org_id>` has been run

**Steps:**
- **Given** the opt-in demo scenario is bootstrapped with 4 workspaces
  (ws-demo, ws-infra, ws-payment, ws-test1), 3 groups (demo, infra, payment),
  and 3 clusters (cluster-a, cluster-b, cluster-c)
- **And** test1 is member of group demo + has direct binding on ws-test1
- **And** test2 is member of group infra
- **And** test3 is member of group payment
- **When** each user queries the Koku API endpoints:
  - `/reports/openshift/costs/` (OCP reports)
  - `/reports/aws/costs/` (AWS reports)
  - `/cost-models/`
  - `/sources/`
  - `/user-access/?type=any`
  - `/recommendations/openshift`
- **Then** all three users get 200 on OCP reports and recommendations
- **And** all three users get 403 on AWS reports and cost-models
- **And** all three users see 0 sources
- **And** all three users have `access=true` on `/user-access/`
- **When** each user navigates the UI
- **Then** all three can login and see Overview, OCP, Optimizations, Cost Explorer
- **And** all three see empty/no-data on AWS
- **And** all three see restricted Settings view

**Acceptance Criteria:**
- 24 API tests pass (6 per user × 3 users + 6 cross-boundary)
- 18 UI tests pass (3 per user × 6 test classes)
- Group-based access works: workspace bindings inherited via group membership
- Direct workspace bindings work: test1's ws-test1 binding grants Cluster B
- No cross-workspace leakage: each user only accesses permitted resource types

---

## 16. Traceability Matrix

| Scenario | DD Reference | Koku Test Plan | Feature |
|----------|-------------|----------------|---------|
| S-01 | Infrastructure | (New) | F-01 |
| S-02 | Schema provisioning | E2E-KESSEL-FLOW-006 | F-02 |
| S-03 | Role seeding | E2E-KESSEL-FLOW-006 | F-03 |
| S-04 | Connectivity | (New) | F-01 |
| S-05 | Full access via binding | E2E-KESSEL-FLOW-006 | F-10 |
| S-06 | Authorized access | E2E-KESSEL-FLOW-001 | F-04 |
| S-07 | Unauthorized denial | E2E-KESSEL-FLOW-002 | F-05 |
| S-08 | Role binding CRUD | E2E-KESSEL-FLOW-001, -006 | F-06 |
| S-09 | Access revocation | E2E-KESSEL-FLOW-003 | F-07 |
| S-10 | Group management | E2E-KESSEL-FLOW-007 | F-08 |
| S-11 | Group transitive access | E2E-KESSEL-FLOW-007 | F-09 |
| S-12 | Cost model authz | E2E-KESSEL-FLOW-005 | F-12 |
| S-13 | Pipeline auto-discovery | E2E-KESSEL-FLOW-004 | F-11 |
| S-14 | Opt-in access model | ADR reference scenario | F-04, F-05, F-08, F-09 |

---

## 17. Required Modifications to Existing Tests

Since all tests now run under ReBAC (`ENHANCED_ORG_ADMIN=False`), the
following modifications are required to ensure existing tests work through
the full authorization chain.

### 16.1 `conftest.py` -- Bootstrap fixture and Kessel infrastructure

**Required changes:**
- Add session-scoped `kessel_bootstrap` autouse fixture that:
  1. Discovers the primary test user identity from the Keycloak JWT
  2. Creates a `cost-administrator` role binding via the Access Management API
  3. Waits for the binding to propagate (poll status endpoint)
- Add session-scoped `kessel_config` fixture: SpiceDB host/port, Relations
  API host/port (read from cluster config or environment)
- Add `kessel_healthy` fixture: Boolean that checks Kessel pod readiness
- Add `kessel_test_users` fixture: Creates test user identities with known
  usernames for authorization scenarios

### 16.2 `test_gateway_auth.py` -- ReBAC-aware auth validation

**Current**: Tests JWT validation (401/403 for bad tokens, 200 for valid).
**Required change**: The existing "valid JWT gets 200" test now implicitly
validates that ReBAC authorization passes (since the bootstrap fixture set up
the binding). Add a negative test: valid JWT for a user with no Kessel bindings
returns 200 with empty data (not 403 at gateway, but no authorized resources).

### 16.3 `test_sources_api.py` -- Verify Kessel resource creation on source CRUD

**Current**: Tests source CRUD via gateway and interpod, verifies HTTP responses.
**Required change**: After source creation, verify that the source was reported
to Kessel Inventory as a resource via SpiceDB check. After source deletion,
verify that the Kessel resource is retained (ONPREM policy: sources may be
re-registered).

### 16.4 `test_complete_flow.py` -- Authorization checkpoint

**Current**: 9-step pipeline test from source to recommendations.
**Required change**: After step 06 (summary tables populated), add a checkpoint
that verifies the OCP cluster/nodes/projects were reported to Kessel Inventory.
This validates F-11 as part of the existing flow. The test already passes
through ReBAC via the bootstrap fixture.

### 16.5 `test_cost_validation.py` -- Authorization-filtered validation

**Current**: Validates cost metrics against NISE expectations.
**Required change**: Add a companion scenario that runs the same validation as
a restricted user (e.g., `cost-viewer` binding for a single cluster) and
verifies that only authorized data appears in the metrics.

---

## Appendix A: Scenario Summary

| ID | Name | Priority | Type |
|----|------|----------|------|
| S-01 | Infrastructure health | P0 | Smoke |
| S-02 | Schema provisioned | P0 | Verification |
| S-03 | Roles seeded | P0 | Verification |
| S-04 | Cross-namespace connectivity | P0 | Smoke |
| S-05 | Full access via binding | P0 | Authorization |
| S-06 | Authorized user access | P0 | Authorization |
| S-07 | Unauthorized user denial | P0 | Authorization |
| S-08 | Role binding CRUD | P1 | Lifecycle |
| S-09 | Access revocation | P1 | Lifecycle |
| S-10 | Group management | P1 | Lifecycle |
| S-11 | Group transitive access | P1 | Authorization |
| S-12 | Cost model authz | P1 | Authorization |
| S-13 | Pipeline auto-discovery | P1 | Integration |
| S-14 | Opt-in access model | P0 | Authorization |

**Total: 14 scenarios (6 P0, 8 P1)**

---

## Appendix B: pytest Marker Configuration

Add to `tests/pytest.ini`:

```ini
kessel: Kessel-specific authorization and lifecycle scenarios
```

Run with:

```bash
./scripts/run-pytest.sh                           # All tests (ReBAC active)
./scripts/run-pytest.sh -m kessel                 # Kessel-specific scenarios only
./scripts/run-pytest.sh -m "kessel and smoke"     # Infrastructure only
./scripts/run-pytest.sh -m kessel -k "authorization"  # Auth scenarios only
```

All tests run through the ReBAC authorization chain regardless of marker.
The `kessel` marker is used to select Kessel-specific scenarios (S-01 through
S-13) when running a targeted subset.
