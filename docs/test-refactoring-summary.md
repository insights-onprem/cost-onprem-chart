# Test Refactoring Summary

## Overview

This document summarizes the test infrastructure refactoring completed as part of the proposal outlined in `Proposal: External API Tests.md`. The goal was to modernize the test architecture, reduce code duplication, and improve test maintainability.

## Phases Completed

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Add shared fixtures | ✅ Complete |
| 2 | Create external API test suite | ✅ Complete |
| 3 | Create internal test suite | ✅ Complete |
| 4 | Add Ingress API tests | ✅ Complete |
| 5 | Simplify E2E orchestration | ✅ Complete |

---

## Phase 1: Shared Fixtures

### What Changed
Added reusable fixtures to `tests/conftest.py` for both external and internal test access.

### New Fixtures

| Fixture | Scope | Purpose |
|---------|-------|---------|
| `authenticated_session` | function | Pre-configured requests.Session with JWT auth |
| `test_runner_pod` | session | Dedicated pod for internal cluster commands |
| `internal_api_url` | session | Internal Koku API URL (ClusterIP) |
| `internal_ros_api_url` | session | Internal ROS API URL (ClusterIP) |
| `database_config` | session | Dynamic database configuration |

### Why
- **Consistency**: All tests use the same authentication and API access patterns
- **Isolation**: Dedicated test runner pod prevents interference with application pods
- **Dynamic Config**: Database names are detected from deployment, not hardcoded

---

## Phase 2: External API Test Suite (`tests/suites/api/`)

### What Changed
Created new test suite for external API testing via the gateway route.

### New Files
- `tests/suites/api/__init__.py`
- `tests/suites/api/conftest.py` - Suite-specific fixtures
- `tests/suites/api/README.md` - Documentation
- `tests/suites/api/test_reports.py` - Cost report endpoint tests

### Tests Added (10 total)

| Test | Description |
|------|-------------|
| `test_ocp_costs_report` | Verify OCP costs endpoint returns valid response |
| `test_ocp_compute_report` | Verify compute report endpoint |
| `test_ocp_memory_report` | Verify memory report endpoint |
| `test_ocp_volumes_report` | Verify volumes report endpoint |
| `test_report_with_project_filter` | Verify project filtering works |
| `test_report_with_cluster_filter` | Verify cluster filtering works |
| `test_report_with_group_by_project` | Verify project grouping |
| `test_report_with_group_by_cluster` | Verify cluster grouping |
| `test_report_with_date_range` | Verify date range filtering |
| `test_report_pagination` | Verify pagination parameters |

### Why
- **Realistic Testing**: Tests go through the actual gateway route with JWT auth
- **API Coverage**: Validates report endpoints that users actually call
- **Filtering/Grouping**: Ensures query parameters work correctly

---

## Phase 3: Internal Test Suite (`tests/suites/internal/`)

### What Changed
Created new test suite for internal cluster testing via the test runner pod.

### New Files
- `tests/suites/internal/__init__.py`
- `tests/suites/internal/conftest.py` - `internal_curl` helper fixture
- `tests/suites/internal/README.md` - Documentation
- `tests/suites/internal/test_koku_api.py` - Internal Koku API tests

### Tests Added (5 total)

| Test | Description |
|------|-------------|
| `test_status_endpoint` | Verify Koku status endpoint is healthy |
| `test_reports_endpoint_with_identity` | Verify reports work with X-Rh-Identity header |
| `test_internal_service_routing` | Verify internal service DNS resolution |
| `test_api_version_in_response` | Verify API version is returned |
| `test_internal_health_check` | Verify health check endpoint |

### Why
- **Internal Validation**: Tests internal service networking without gateway
- **X-Rh-Identity Testing**: Validates the internal auth mechanism
- **Service Discovery**: Ensures Kubernetes DNS routing works

---

## Phase 4: Ingress API Tests

### What Changed
Added external API tests for the Ingress upload endpoint.

### New File
- `tests/suites/api/test_ingress.py`

### Tests Added (3 total)

| Test | Description |
|------|-------------|
| `test_upload_endpoint_accessible` | Verify ingress endpoint is routable |
| `test_upload_invalid_payload` | Verify proper error handling |
| `test_upload_without_auth` | Verify auth is required |

### Removed (Redundant)
- `tests/suites/api/test_ros.py` - Duplicated `tests/suites/ros/test_recommendations.py`

### Why
- **Upload Validation**: Ensures the ingress upload path works
- **Error Handling**: Validates proper HTTP error responses
- **No Redundancy**: Removed duplicate ROS tests

---

## Phase 5: E2E Orchestration Simplification

### What Changed
Refactored `test_complete_flow.py` from a monolithic 1,495-line file to a focused 500-line orchestrator.

### Before
```
test_complete_flow.py (1,495 lines)
├── Data generation utilities (230 lines)
├── NISE configuration (100 lines)
├── Source registration fixture (300 lines)
├── Test data fixture (150 lines)
└── 9 test methods (715 lines)
```

### After
```
test_complete_flow.py (500 lines)
└── 9 test methods only

e2e/conftest.py (350 lines)
├── e2e_cluster_id fixture
├── e2e_test_data fixture
├── registered_source fixture
├── rh_identity_header fixture
├── koku_api_reads_url fixture
├── koku_api_writes_url fixture
├── ingress_pod fixture
└── _generate_simple_data() helper

e2e_helpers.py (+40 lines)
└── generate_dynamic_static_report()
```

### Why
- **Separation of Concerns**: Fixtures in conftest, tests in test file
- **Reusability**: Fixtures can be used by other E2E tests
- **Maintainability**: Smaller files are easier to understand and modify
- **66% Reduction**: From 1,495 to 500 lines in the main test file

---

## Bug Fixes

### Hardcoded Database Names

**Problem**: Tests used hardcoded database names (`costonprem_koku`, `costonprem_kruize`) that didn't match actual deployments (`koku`, `kruize_db`).

**Solution**: 
1. Updated `database_config` fixture to detect database name from Koku deployment's `DATABASE_NAME` environment variable
2. Changed all hardcoded references:
   - `costonprem_koku` → `database_config.database` (dynamic)
   - `costonprem_kruize` → `kruize_db` (consistent)

**Files Changed**:
- `tests/conftest.py`
- `tests/suites/infrastructure/test_preflight.py`
- `tests/suites/infrastructure/test_database.py`
- `tests/suites/ros/conftest.py`
- `tests/suites/e2e/test_complete_flow.py`

---

## New Pytest Markers

Added markers to `pytest.ini`:

```ini
markers =
    api: External API tests via gateway route (requires network access)
    internal: Tests that execute inside the cluster via test-runner pod
```

### Usage

```bash
# Run only external API tests
pytest -m api

# Run only internal tests
pytest -m internal

# Run both
pytest -m "api or internal"
```

---

## Test Results

All tests pass after refactoring:

```
============================= test session starts ==============================
collected 191 items

E2E Tests:           50 passed
Infrastructure:      35 passed
API Tests:           10 passed
Internal Tests:       5 passed
ROS Tests:           12 passed
Auth Tests:          15 passed
...
================ 191 passed in 105.57s ================
```

---

## File Summary

### New Files Created
| File | Lines | Purpose |
|------|-------|---------|
| `tests/suites/api/__init__.py` | 0 | Package marker |
| `tests/suites/api/conftest.py` | 15 | Suite fixtures |
| `tests/suites/api/README.md` | 50 | Documentation |
| `tests/suites/api/test_reports.py` | 120 | Report API tests |
| `tests/suites/api/test_ingress.py` | 60 | Ingress API tests |
| `tests/suites/internal/__init__.py` | 0 | Package marker |
| `tests/suites/internal/conftest.py` | 50 | Internal fixtures |
| `tests/suites/internal/README.md` | 40 | Documentation |
| `tests/suites/internal/test_koku_api.py` | 80 | Internal API tests |

### Files Modified
| File | Change |
|------|--------|
| `tests/conftest.py` | Added shared fixtures, dynamic DB config |
| `tests/e2e_helpers.py` | Added `generate_dynamic_static_report()` |
| `tests/suites/e2e/conftest.py` | Added E2E-specific fixtures |
| `tests/suites/e2e/test_complete_flow.py` | Simplified to orchestrator only |
| `tests/suites/infrastructure/test_preflight.py` | Fixed DB names |
| `tests/suites/infrastructure/test_database.py` | Fixed DB names |
| `tests/suites/ros/conftest.py` | Fixed DB names |
| `tests/pytest.ini` | Added `api` and `internal` markers |

### Files Deleted
| File | Reason |
|------|--------|
| `tests/suites/api/test_ros.py` | Redundant with `ros/test_recommendations.py` |

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        Test Execution                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │   api/       │    │  internal/   │    │    e2e/      │      │
│  │              │    │              │    │              │      │
│  │ test_reports │    │test_koku_api │    │test_complete │      │
│  │ test_ingress │    │              │    │  _flow       │      │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘      │
│         │                   │                   │               │
│         ▼                   ▼                   ▼               │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │authenticated │    │test_runner   │    │registered    │      │
│  │  _session    │    │   _pod       │    │  _source     │      │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘      │
│         │                   │                   │               │
│         ▼                   ▼                   ▼               │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    conftest.py                          │   │
│  │  jwt_token, gateway_url, database_config, cluster_config│   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     OpenShift Cluster                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐         ┌──────────────┐                     │
│  │   Gateway    │◄────────│   Keycloak   │                     │
│  │   (Envoy)    │  JWT    │              │                     │
│  └──────┬───────┘         └──────────────┘                     │
│         │                                                       │
│         ▼                                                       │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │  Koku API    │    │   Ingress    │    │   ROS API    │      │
│  │  (reads/     │    │              │    │              │      │
│  │   writes)    │    │              │    │              │      │
│  └──────┬───────┘    └──────────────┘    └──────────────┘      │
│         │                                                       │
│         ▼                                                       │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │  PostgreSQL  │    │    Kafka     │    │   Kruize     │      │
│  │   (koku,     │    │              │    │              │      │
│  │  kruize_db)  │    │              │    │              │      │
│  └──────────────┘    └──────────────┘    └──────────────┘      │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Next Steps

1. **CI Integration**: Update CI scripts to run new test markers
2. **Documentation**: Update main README with new test structure
3. **Coverage**: Add more API endpoint tests as needed
4. **Monitoring**: Add test timing metrics to identify slow tests
