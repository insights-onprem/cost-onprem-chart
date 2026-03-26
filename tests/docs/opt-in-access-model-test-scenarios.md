# Opt-In Access Model — Test Scenarios

| Field       | Value                                                                                      |
|-------------|--------------------------------------------------------------------------------------------|
| Document ID | TS-OPTIN-001                                                                               |
| ADR         | [onprem-workspace-management-adr.md](../../../../koku/docs/architecture/kessel-integration/onprem-workspace-management-adr.md) |
| Test Plan   | [kessel-e2e-test-plan.md](kessel-e2e-test-plan.md) — Scenario S-14                        |
| Created     | 2026-03-06                                                                                 |

---

## Table of Contents

1. [Purpose](#purpose)
2. [Reference Scenario](#reference-scenario)
3. [Verification Matrix](#verification-matrix)
4. [Test Mapping — API Tests](#test-mapping--api-tests)
5. [Test Mapping — UI Tests](#test-mapping--ui-tests)
6. [Bootstrap Prerequisites](#bootstrap-prerequisites)
7. [Running the Tests](#running-the-tests)
8. [Environment Variables](#environment-variables)
9. [Limitations](#limitations)
10. [Future Work](#future-work)

---

## Purpose

This document describes the test scenarios that validate the **opt-in access
model** for the on-prem Cost Management deployment.  The opt-in model means
users see nothing until an administrator explicitly grants access to specific
resources via team workspaces and group membership.

These tests prove that:

- Keycloak users created by `deploy-rhbk.sh` can authenticate through the
  full stack (Keycloak → Envoy gateway → Koku/ROS APIs → UI).
- The Kessel ReBAC authorization layer (SpiceDB + Relations API + Inventory
  API) correctly enforces workspace-based and group-based access boundaries.
- Each user's effective permissions derive from their group membership and
  direct workspace bindings — not from any default or org-wide grant.

The scenario is the **reference scenario** defined in the ADR and bootstrapped
by `kessel-admin.sh demo <org_id>`.

---

## Reference Scenario

### Resources

Created by Koku's `resource_reporter` via the Inventory API:

| Cluster   | Namespaces     | Primary workspace |
|-----------|----------------|-------------------|
| cluster-a | demo           | org123            |
| cluster-b | demo, payment  | org123            |
| cluster-c | test, payment  | org123            |

### Workspace Hierarchy

Created by `kessel-admin.sh demo` via the Relations API:

```
tenant:org123
└── workspace:org123
    ├── workspace:ws-infra
    ├── workspace:ws-demo
    ├── workspace:ws-payment
    └── workspace:ws-test1
```

### Groups and Membership

| Group   | Members | Workspace  | Role                    |
|---------|---------|------------|-------------------------|
| demo    | test1   | ws-demo    | cost-openshift-viewer   |
| infra   | test2   | ws-infra   | cost-openshift-viewer   |
| payment | test3   | ws-payment | cost-openshift-viewer   |

### Direct User Bindings

| User  | Workspace      | Role                    | Reason                      |
|-------|----------------|-------------------------|-----------------------------|
| admin | org123 + tenant| cost-administrator      | Org admin — sees everything |
| test1 | ws-test1       | cost-openshift-viewer   | Personal access to Cluster B|

### Resource-to-Workspace Assignments

Additional `t_workspace` tuples beyond the primary `org123`:

| Workspace  | Cluster-level         | Namespace-level                 | Rationale            |
|------------|-----------------------|---------------------------------|----------------------|
| ws-infra   | cluster-a, cluster-c  | demo-a, test-c, payment-c      | Full Clusters A + C  |
| ws-demo    | cluster-a             | demo-a                          | Full Cluster A       |
| ws-payment | —                     | payment-b, payment-c            | Namespace-level only |
| ws-test1   | cluster-b             | demo-b, payment-b               | Full Cluster B       |

---

## Verification Matrix

Each cell indicates whether the user can **read** the resource or is **DENIED**.
The parenthetical shows through which workspace or mechanism access is granted.

| Resource    | admin | test1           | test2           | test3               |
|-------------|-------|-----------------|-----------------|---------------------|
| cluster-a   | read  | read (ws-demo)  | read (ws-infra) | DENIED              |
| cluster-b   | read  | read (ws-test1) | DENIED          | read (has\_project) |
| cluster-c   | read  | DENIED          | read (ws-infra) | read (has\_project) |
| ns demo-a   | read  | read (ws-demo)  | read (ws-infra) | DENIED              |
| ns demo-b   | read  | read (ws-test1) | DENIED          | DENIED              |
| ns payment-b| read  | read (ws-test1) | DENIED          | read (ws-payment)   |
| ns test-c   | read  | DENIED          | read (ws-infra) | DENIED              |
| ns payment-c| read  | DENIED          | read (ws-infra) | read (ws-payment)   |

### Key Behaviors Demonstrated

1. **Workspace scoping** — users only see resources assigned to their workspace(s).
2. **Group access** — group members inherit workspace bindings via
   `role_binding#t_subject → group#member`.
3. **Direct access** — test1 has a personal workspace `ws-test1` for Cluster B.
4. **Namespace-level scoping** — test3 sees payment namespaces but not
   demo/test namespaces in the same clusters.
5. **`has_project` cascade** — test3 sees clusters B and C through namespace
   access (`openshift_cluster.read` includes `has_project->read`).

---

## Test Mapping — API Tests

**File:** `tests/suites/e2e/test_opt_in_authorization.py`

### TestTest1Access

| Test                                          | Matrix cell(s) verified                     |
|-----------------------------------------------|---------------------------------------------|
| `test_ocp_reports_allowed`                    | test1 can access OCP (via ws-demo, ws-test1)|
| `test_aws_reports_denied`                     | test1 denied AWS (no aws roles)             |
| `test_cost_models_denied`                     | test1 denied cost-models                    |
| `test_sources_empty`                          | test1 sees 0 sources                        |
| `test_user_access_has_ocp_permissions`        | test1 has partial OCP access                |
| `test_recommendations_allowed`                | test1 can access recommendations            |

### TestTest2Access

| Test                                          | Matrix cell(s) verified                     |
|-----------------------------------------------|---------------------------------------------|
| `test_ocp_reports_allowed`                    | test2 can access OCP (via ws-infra)         |
| `test_aws_reports_denied`                     | test2 denied AWS                            |
| `test_cost_models_denied`                     | test2 denied cost-models                    |
| `test_sources_empty`                          | test2 sees 0 sources                        |
| `test_user_access_has_ocp_permissions`        | test2 has partial OCP access                |
| `test_recommendations_allowed`                | test2 can access recommendations            |

### TestTest3Access

| Test                                          | Matrix cell(s) verified                     |
|-----------------------------------------------|---------------------------------------------|
| `test_ocp_reports_allowed`                    | test3 can access OCP (via ws-payment + has\_project) |
| `test_aws_reports_denied`                     | test3 denied AWS                            |
| `test_cost_models_denied`                     | test3 denied cost-models                    |
| `test_sources_empty`                          | test3 sees 0 sources                        |
| `test_user_access_has_ocp_permissions`        | test3 has partial OCP access                |
| `test_recommendations_allowed`                | test3 can access recommendations            |

### TestCrossBoundaries

| Test                                          | What it verifies                            |
|-----------------------------------------------|---------------------------------------------|
| `test_all_denied_aws`                         | All 3 users denied AWS (403)                |
| `test_all_denied_cost_models`                 | All 3 users denied cost-models (403)        |
| `test_all_see_zero_sources`                   | All 3 users see 0 sources                   |
| `test_all_allowed_ocp_reports`                | All 3 users get 200 on OCP reports          |
| `test_all_have_user_access`                   | All 3 users have access=true                |
| `test_all_allowed_recommendations`            | All 3 users get 200 on recommendations      |

**Total: 24 API tests.**

---

## Test Mapping — UI Tests

**File:** `tests/suites/ui/test_opt_in_authorization_ui.py`

| Test Class                    | Tests per user | What it validates                              |
|-------------------------------|----------------|------------------------------------------------|
| `TestOptInLogin`              | 3              | All three land on Overview after login         |
| `TestOpenShiftVisibility`     | 3              | OCP page loads with content for all three      |
| `TestAWSVisibility`           | 3              | AWS page shows empty/no-data for all three     |
| `TestSettingsVisibility`      | 3              | Settings page loads (restricted view)          |
| `TestOptimizationsVisibility` | 3              | Optimizations page visible for all three       |
| `TestCostExplorerVisibility`  | 3              | Cost Explorer loads for all three              |

**Total: 18 UI tests.**

---

## Bootstrap Prerequisites

The tests require the opt-in demo scenario to be active on the cluster:

### 1. Keycloak Users

Created by `deploy-rhbk.sh`:

| Username | Password | org\_id     | Role context        |
|----------|----------|-------------|---------------------|
| admin    | admin    | org1234567  | cost-administrator  |
| test     | test     | org1234567  | cost-openshift-viewer (legacy) |
| test1    | test1    | org1234567  | cost-openshift-viewer (demo group, ws-test1) |
| test2    | test2    | org1234567  | cost-openshift-viewer (infra group) |
| test3    | test3    | org1234567  | cost-openshift-viewer (payment group) |

### 2. Kessel Demo Bootstrap

Run by `deploy-test-cost-onprem.sh` or manually:

```bash
kessel-admin.sh demo org1234567
```

This creates all workspaces, groups, bindings, resource tuples, and structural
relationships described in the reference scenario.

### 3. Infrastructure

- Kessel stack (SpiceDB, Relations API, Inventory API) deployed and healthy
- `ENHANCED_ORG_ADMIN=False` on the Koku deployment
- Cost Management UI deployed and accessible via route

---

## Running the Tests

### API Tests

```bash
cd tests
source .venv/bin/activate

# Run all opt-in API tests
pytest suites/e2e/test_opt_in_authorization.py -m e2e -v

# Run a specific user's tests
pytest suites/e2e/test_opt_in_authorization.py -m e2e -v -k TestTest1Access
pytest suites/e2e/test_opt_in_authorization.py -m e2e -v -k TestTest3Access

# Run cross-boundary tests only
pytest suites/e2e/test_opt_in_authorization.py -m e2e -v -k TestCrossBoundaries
```

### UI Tests

```bash
# Ensure Playwright browsers are installed
playwright install chromium

# Run all opt-in UI tests
pytest suites/ui/test_opt_in_authorization_ui.py -m ui -v

# Run with visible browser (debugging)
PLAYWRIGHT_HEADLESS=false pytest suites/ui/test_opt_in_authorization_ui.py -m ui -v

# Run login tests only
pytest suites/ui/test_opt_in_authorization_ui.py -m ui -v -k TestOptInLogin
```

### All Kessel Authorization Tests Together

```bash
# Existing (admin/viewer/no-access) + opt-in (test1/test2/test3)
pytest suites/e2e/test_authorization.py suites/e2e/test_opt_in_authorization.py -m e2e -v

# All UI authorization tests
pytest suites/ui/test_authorization_ui.py suites/ui/test_opt_in_authorization_ui.py -m ui -v
```

---

## Environment Variables

| Variable        | Default  | Description                              |
|-----------------|----------|------------------------------------------|
| `OPTIN_USER1`   | `test1`  | Username for the demo-group user         |
| `OPTIN_USER2`   | `test2`  | Username for the infra-group user        |
| `OPTIN_USER3`   | `test3`  | Username for the payment-group user      |
| `OPTIN_PASS1`   | `test1`  | Password for test1                       |
| `OPTIN_PASS2`   | `test2`  | Password for test2                       |
| `OPTIN_PASS3`   | `test3`  | Password for test3                       |

---

## Limitations

### Status-Code Level Only

The current tests verify access at the HTTP status-code level:

- **200** = the user has *some* access to the resource type (Kessel grants
  permission through at least one workspace binding).
- **403** = the user is fully denied.

They do **not** verify which specific cluster or namespace data is returned
in the response body.  For example, the matrix says test1 can read cluster-a
(via ws-demo) but not cluster-c; however, the test only confirms test1 gets
HTTP 200 on `/reports/openshift/costs/`, not that the response contains
cluster-a data and excludes cluster-c data.

### Why

Verifying data-level scoping (e.g., "test1 sees rows for cluster-a but not
cluster-c") requires NISE-ingested cost data for clusters named `cluster-a`,
`cluster-b`, and `cluster-c`.  The current test environment does not have
this data.

### What the Tests Still Prove

Even at the status-code level, the tests validate the full authorization chain:

1. Keycloak user creation and JWT issuance
2. Envoy gateway JWT validation and `X-Rh-Identity` injection
3. Koku middleware identity parsing
4. Kessel `StreamedListObjects` / `Check` calls
5. SpiceDB graph traversal through workspace, group, and structural relations
6. Koku permission enforcement returning 200 or 403

If any link in this chain breaks (e.g., a group binding is missing, a workspace
tuple is deleted, a structural relation is misconfigured), the affected user
gets 403 and the test fails.

---

## Future Work

### Phase 2: Data-Scoped Assertions

Once NISE data is ingested for clusters A/B/C with the expected namespaces:

- `test_test1_sees_cluster_a_not_cluster_c` — parse `/reports/openshift/costs/`
  response and assert on the `cluster` dimension in the data rows.
- `test_test3_sees_payment_namespaces_only` — filter response by project
  dimension and verify only `payment` namespaces appear.
- `test_test2_sees_clusters_a_and_c` — verify data from both infra-workspace
  clusters appears.

### Phase 3: Lifecycle Tests

Test the dynamic add/remove of access:

- Grant test3 access to ws-demo → verify test3 now sees cluster-a data.
- Remove test1 from demo group → verify test1 loses cluster-a access but
  keeps cluster-b (direct ws-test1 binding).
- Delete ws-payment → verify test3 loses all access.

### Phase 4: UI Data Differentiation

With ingested data, the UI tests can verify:

- test1 sees "Cluster A" and "Cluster B" in the OCP page table.
- test3 only sees rows mentioning "payment" namespaces.
- test2 does not see "Cluster B" anywhere in the UI.
