# Test Coverage Analysis: Chart Repo vs IQE Plugin

This document analyzes the test coverage between the test suite in this repository (`cost-onprem-chart/tests/`) and the IQE cost-management plugin tests (`iqe-cost-management-plugin`).

## Summary

| Metric | Chart Repo Tests | IQE Plugin Tests (cost_ocp_on_prem) |
|--------|------------------|-------------------------------------|
| Location | `tests/suites/` | `iqe-cost-management-plugin` repo |
| Total Test Functions | 213 | ~2,400+ (242 with on-prem marker) |
| Test Focus | Infrastructure, Integration, E2E | API Validation, Data Accuracy |
| Execution Time | ~5-10 min | ~45-60 min |
| Data Dependency | Minimal | Requires NISE data generation |
| Run Command | `pytest tests/suites` | `./scripts/run-iqe-tests.sh` |

## Chart Repo Test Suites Breakdown

| Suite | Tests | Purpose |
|-------|-------|---------|
| `infrastructure/` | 35 | Database, Kafka, Storage, Pods, Preflight |
| `ui/` | 32 | Login, Navigation, Cost Explorer, Optimizations |
| `sources/` | 29 | Sources API CRUD, Filtering, Validation |
| `e2e/` | 26 | Complete flow, Smoke tests, Scenarios |
| `api/` | 23 | Reports, Cost Models, Tagging, Ingress |
| `helm/` | 20 | Chart linting, Template rendering |
| `auth/` | 17 | Keycloak, Gateway auth, OAuth, JWT |
| `ros/` | 14 | Kruize, Recommendations |
| `cost_management/` | 11 | Processing, Cost validation |
| `interpod/` | 6 | Internal service communication |

## Coverage Comparison by Area

### 1. Sources API

**Chart Repo Tests (29)**:
- CRUD operations via gateway
- Filtering by name, type, source_type_id
- Error handling (404, 400, 401, 403, 424)
- Duplicate detection
- Source type enumeration

**IQE Tests (~60 with cost_ocp_on_prem)**:
- Source CRUD with data ingestion validation
- Raw calculation verification
- Multi-cluster scenarios
- Cost model association
- S3 archiving
- Cross-org ingestion

**Overlap**: Basic CRUD operations  
**IQE Adds**: Data accuracy validation, complex multi-source scenarios  
**Chart Repo Adds**: Infrastructure validation, error edge cases, gateway integration

### 2. Cost Reports API

**Chart Repo Tests (12)**:
- OCP costs, compute, memory, volume endpoints
- Basic response structure validation
- Filtering by project, time scope
- Group by operations

**IQE Tests (~200+ OCP report tests)**:
- Comprehensive date range validation
- Delta calculations (daily/monthly)
- CSV export validation
- Exact match filtering
- Order by operations
- Pagination
- Tag filtering
- Capacity validation

**Overlap**: Basic endpoint accessibility  
**IQE Adds**: Deep data accuracy, calculation verification, edge cases  
**Chart Repo Adds**: Gateway integration, basic contract validation

### 3. Cost Models

**Chart Repo Tests (6)**:
- CRUD operations
- Rate types enumeration
- Basic validation

**IQE Tests (~55)**:
- Markup calculations (OCP, AWS, Azure, GCP)
- GPU rates
- Cost distribution
- Currency handling
- Tag-based rates
- Recalculation windows

**Overlap**: Basic CRUD  
**IQE Adds**: Calculation accuracy, complex rate scenarios  
**Chart Repo Adds**: None significant

### 4. Tagging

**Chart Repo Tests (6)**:
- Tag endpoint accessibility
- Basic filtering
- Multiple tag filters

**IQE Tests (~130)**:
- Tag key endpoints per provider
- Tag value filtering
- Tag inheritance
- Tag precedence
- Backpopulation
- Pagination

**Overlap**: Basic tag operations  
**IQE Adds**: Comprehensive tag validation across all providers  
**Chart Repo Adds**: None significant

### 5. Infrastructure (Chart Repo Only)

**Chart Repo Tests (35)**:
- Database connectivity and migrations
- Kafka cluster health
- S3/MinIO bucket access
- Pod readiness checks
- Service discovery
- Preflight validation

**IQE Tests**: None - IQE assumes infrastructure is healthy

**Chart Repo Adds**: Critical infrastructure validation before functional tests

### 6. Authentication (Chart Repo Only)

**Chart Repo Tests (17)**:
- Keycloak accessibility
- JWT token acquisition
- Gateway authentication
- OAuth proxy validation
- Identity header validation

**IQE Tests**: Handled by fixtures, not explicit tests

**Chart Repo Adds**: Auth infrastructure validation

### 7. UI Tests

**Chart Repo Tests (32)**:
- Login flow
- Navigation
- Cost Explorer components
- Optimizations page

**IQE Tests (~100+ UI tests)**: Comprehensive UI validation but NOT marked for on-prem

**Note**: IQE UI tests exist but aren't included in `cost_ocp_on_prem` marker

### 8. E2E Flow (Chart Repo Only)

**Chart Repo Tests (26)**:
- Complete ingestion pipeline
- Source registration → Data upload → Processing → API access
- Scenario-based testing

**IQE Tests**: Data setup tests exist but focus on validation, not flow

**Chart Repo Adds**: Pipeline integration testing

## Duplicate/Overlapping Tests

These tests exist in both suites with similar functionality:

| Area | Chart Repo Test | IQE Equivalent |
|------|------------|----------------|
| Source CRUD | `test_create_and_delete_source_via_gateway` | `test_api_ocp_source_crud` |
| Source filtering | `test_filter_sources_by_name` | `test_api_source_filter_by_name` |
| Cost report access | `test_ocp_costs_report` | `test_api_ocp_cost_endpoint` |
| Compute report | `test_ocp_compute_report` | `test_api_ocp_compute_endpoint` |
| Memory report | `test_ocp_memory_report` | `test_api_ocp_memory_endpoint` |
| Volume report | `test_ocp_volume_report` | `test_api_ocp_volume_endpoint` |
| Cost model CRUD | `test_cost_model_create` | `test_api_cost_model_ocp_crud` |
| Tag endpoint | `test_ocp_tags_endpoint_accessible` | `test_api_ocp_tagging_endpoint` |

**Recommendation**: Chart repo tests provide quick smoke tests; IQE provides deep validation. Both are valuable.

## Unique Coverage

### Chart Repo Tests Provide (IQE doesn't cover):

1. **Infrastructure Health**
   - Database connectivity and migrations
   - Kafka cluster status
   - S3/MinIO accessibility
   - Pod readiness

2. **Authentication Flow**
   - Keycloak integration
   - JWT token mechanics
   - Gateway auth validation

3. **Helm Chart Validation**
   - Chart linting
   - Template rendering
   - Values validation

4. **E2E Pipeline**
   - Complete data flow testing
   - Integration between components

5. **Error Edge Cases**
   - Malformed headers
   - Invalid JSON
   - Missing entitlements

### IQE Tests Provide (Chart repo doesn't cover):

1. **Data Accuracy**
   - Calculation verification
   - Delta computations
   - Capacity validation

2. **Complex Scenarios**
   - Multi-cluster setups
   - Cross-org ingestion
   - Cost distribution

3. **Comprehensive API Validation**
   - All date range combinations
   - All filter combinations
   - Pagination edge cases

4. **Provider Coverage**
   - AWS, Azure, GCP tests (not applicable to on-prem OCP-only)

## Recommendations

### Keep Both Test Suites

1. **Chart repo tests** (`tests/suites/`) for:
   - Quick feedback (~5 min)
   - Infrastructure validation
   - Deployment verification
   - CI/CD gates

2. **IQE plugin tests** for:
   - Deep API validation
   - Data accuracy verification
   - Comprehensive coverage
   - Release qualification

### Potential Consolidation

Consider removing from chart repo if IQE provides better coverage:
- Basic report endpoint tests (IQE has deeper validation)
- Basic cost model tests (IQE has calculation verification)

Consider keeping in chart repo even with IQE overlap:
- Source CRUD (quick smoke test value)
- Tag endpoint access (quick validation)

### Test Execution Strategy

```bash
# Quick validation (chart repo only) - ~5 min
pytest tests/suites -m "not slow"

# Infrastructure + smoke (chart repo) - ~10 min
pytest tests/suites

# Full API validation (IQE plugin) - ~45 min
./scripts/run-iqe-tests.sh

# Complete validation (both) - ~60 min
pytest tests/suites && ./scripts/run-iqe-tests.sh
```
