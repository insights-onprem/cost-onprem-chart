# Skipped IQE Tests for On-Prem Cost Management

> **Living Document**: This document tracks tests that are skipped when running IQE tests against on-prem Cost Management deployments. It should be updated as issues are resolved or new skip patterns are identified.
>
> **Last Updated**: 2026-03-17

## Overview

IQE tests are organized into profiles that progressively include more tests:

| Profile | Tests | Duration | Use Case |
|---------|-------|----------|----------|
| `smoke` | ~43 | ~17 min | PR checks, quick validation |
| `extended` | ~2100 | ~33 min | Daily CI, broader coverage |
| `stable` | ~2350 | ~40 min | Weekly CI, comprehensive |
| `full` | ~3324 | ~60 min | Release validation |

> **Note**: Durations measured with `--listener-cpu max` on test cluster (3 workers, 12 CPU / 32GB each)

### Quick Start

```bash
# Quick smoke tests for PR validation
./scripts/run-iqe-tests.sh --profile smoke

# Extended daily CI run
./scripts/run-iqe-tests.sh --profile extended

# Stable weekly run
./scripts/run-iqe-tests.sh --profile stable

# Full release validation (expect some failures)
./scripts/run-iqe-tests.sh --profile full
```

---

## Test Profiles

### `smoke` (Default for PRs)

**~43 tests, ~17 minutes**

A curated set of source and cost model tests that pass reliably. Good for quick validation.

```bash
# Filter: positive selection + skip filters
(test_api_ocp_source and not test_api_ocp_source_crud) or test_api_cost_model_ocp
```

Includes:
- Source CRUD operations (except update)
- Cost model creation and application
- Basic API validation

---

### `extended` (Default for Daily CI)

**~2100 tests, ~33 minutes**

All tests except infrastructure and blocked groups. Broad coverage for daily CI.

---

### `stable` (Weekly CI)

**~2350 tests, ~40 minutes**

Comprehensive coverage including infrastructure tests.

Includes everything in `extended` plus:
- **Infrastructure tests** (~250 tests) - Bucketing, ingestion, cost filtering

---

### `full` (Release Validation)

**~3353 tests, ~42 minutes** (with fail-fast + listener CPU max)

All `cost_ocp_on_prem` marked tests. No filters applied. Expect ~2,900 errors
from cascading GPU/MIG fixture failures and ~31 test failures from known issues.
With fail-fast enabled, stalled sources are detected in seconds instead of 30+ min each.

---

## Blocked Test Groups

These tests are skipped across all profiles due to known issues.

### GPU/MIG Tests (`SKIP_GPU_TESTS`)

**Jira**: [COST-7179](https://issues.redhat.com/browse/COST-7179), [FLPATH-3429](https://redhat.atlassian.net/browse/FLPATH-3429)  
**Status**: Blocked - waiting for backend fix  
**Impact**: ~90 tests

**Problem**: Backend cannot process GPU/MIG data. NISE 5.3.6+ generates
`mig_instance_uuid` column that the schema lacks, causing `ParquetReportProcessorError`
in the listener. The Kafka offset is committed (no retry), making this a permanent failure.

**Mitigation (IQE plugin)**: `check_manifest_stalled()` and `check_processing_failed()`
in `helpers.py` detect this within ~10s and call `pytest.fail()` with an actionable
message. Without fail-fast, each stalled source blocks for 30+ min. With fail-fast
enabled (branch `flpath-3369-updates-for-cost-onprem`), 17 stalled sources are
handled in seconds rather than 8+ hours.

**Filter**: `ai_workloads or distro or test_api_ocp_gpu or test_api_gpu or test_api_cost_model_ocp_gpu or test_api_cost_model_ocp_cost_gpu or test_api_ocp_resource_types_gpu`

> **Gap**: `test_api_ocp_mig_*` tests (MIG report endpoints) are not caught by
> this filter. These return 404 since MIG reporting is not implemented. Consider
> adding `test_api_ocp_mig` to the GPU filter.

---

### Order By Tests (`SKIP_ORDER_BY_TESTS`)

**Jira**: Related to [COST-7179](https://issues.redhat.com/browse/COST-7179)  
**Status**: Blocked - same `completed_datetime` issue  
**Impact**: ~66 tests

**Filter**: `test_api_ocp_all_limit_order_by_cost or test_api_ocp_tagging_limit_order_by_cost or test_api_ocp_volume_order_by`

---

### Cost Distribution Tests (`SKIP_COST_DISTRIBUTION_TESTS`)

**Jira**: Related to [COST-7179](https://issues.redhat.com/browse/COST-7179)  
**Status**: Blocked - same `completed_datetime` issue  
**Impact**: 5 tests

**Filter**: `test_api_cost_model_ocp_cost_distribution`

---

### Date Range Tests (`SKIP_DATE_RANGE_TESTS`)

**Status**: Expected limitation  
**Impact**: ~228 tests

**Problem**: On-prem generates ~60 days of NISE data. Tests querying 90-day ranges fail.

**Filter**: `(last and 90 and days) or random_date_range or random_daily_time_filter`

---

### Tag Validation Tests (`SKIP_TAG_TESTS`)

**Status**: NISE configuration needed  
**Impact**: ~6 tests

**Problem**: Tests expect `tag:volume=stor_node-1` which NISE doesn't generate.

**Filter**: `(volume and tag and exact_match)`

---

### ROS Tests (`SKIP_ROS_TESTS`)

**Status**: Blocked - requires IQE plugin update  
**Impact**: 3 tests

**Problem**: Infrastructure is ready, but tests skip for `cost_ocp_on_prem` environment.

**Filter**: `test_api_ocp_ros`

---

### Source CRUD Update (`test_api_ocp_source_crud`)

**Status**: Blocked - requires IQE plugin and backend fixes  
**Impact**: 1 test

**Problem**: Wrong API client in plugin + backend PATCH endpoint returns 500.

---

### Tag-Based Rates Update (`SKIP_TAG_RATES_TESTS`)

**Jira**: [COST-7179](https://issues.redhat.com/browse/COST-7179)  
**Status**: Blocked - `completed_datetime` timeout  
**Impact**: 1 test

**Problem**: Fixtures timeout waiting for `completed_datetime` after tag-based rate update.

**Filter**: `test_api_cost_model_rates_update_to_tag_based`

---

### Unstable Tests (`SKIP_UNSTABLE_TESTS`)

**Jira**: [FLPATH-2689](https://redhat.atlassian.net/browse/FLPATH-2689)  
**Status**: Under investigation  
**Impact**: ~20 tests

**Problem**: Tests that fail intermittently due to timing, date calculations, or data dependencies. Observed in stable profile run 2026-03-17.

**Failures**:
- Date range negative tests (3): Expect API exception not raised
- Virtual machines report (1): Calculation mismatch
- Volume deltas monthly (3): Delta calculation mismatch  
- Currency tests (8): IndexError - empty response
- Forecast tests (5): Date offset off-by-one

**Filter**: `test_api_ocp_network_endpoint_date_range_end_negative or test_api_ocp_volume_endpoint_date_range_end_negative or test_api_ocp_tagging_endpoint_date_range_end_negative or test_api_ocp_virtual_machines_report_content or test_api_ocp_volume_deltas_monthly or test_api_ocp_currency_report_param or test_api_ocp_currency_compute or test_api_ocp_currency_memory or test_api_ocp_currency_volume or test_api_ocp_forecast_values or test_api_ocp_forecast_data_other_params or test_api_ocp_forecast_prediction_days`

---

## Validated Test Groups

These groups were validated and are now included in profiles above `smoke`.

| Group | Tests | Pass Rate | Duration | Included In |
|-------|-------|-----------|----------|-------------|
| Flaky | 54 | 100% | 11 min | `extended`+ |
| Delta | 12 | 100% | 10 min | `extended`+ |
| Slow | 13 | 100% | 20 min | `extended`+ |
| Infrastructure | 258 | 99% | 52 min | `stable`+ |

---

## Usage

### Profiles

```bash
# PR validation
./scripts/run-iqe-tests.sh --profile smoke

# With boosted listener CPU for faster processing
./scripts/deploy-test-cost-onprem.sh --tests-only --run-iqe --iqe-profile extended --listener-cpu 1000m
```

### Custom Filters

```bash
# Run specific tests
./scripts/run-iqe-tests.sh --filter "test_api_ocp_cost_endpoint"

# Test a specific skip group
SKIP_GPU_TESTS=false ./scripts/run-iqe-tests.sh --filter "test_api_ocp_gpu"
```

### Local Development

```bash
./scripts/run-iqe-tests-local.sh --setup
./scripts/run-iqe-tests-local.sh --clean-sources --filter "test_api_ocp_source"
```

---

## Skip Group Summary

| Skip Group | Tests | Status | Blocked By |
|------------|-------|--------|------------|
| `SKIP_GPU_TESTS` | ~90 | ❌ Blocked | COST-7179 |
| `SKIP_ORDER_BY_TESTS` | ~66 | ❌ Blocked | COST-7179 |
| `SKIP_DATE_RANGE_TESTS` | ~228 | ❌ Data limit | - |
| `SKIP_COST_DISTRIBUTION_TESTS` | 5 | ❌ Blocked | COST-7179 |
| `SKIP_TAG_TESTS` | ~6 | ❌ NISE config | - |
| `SKIP_ROS_TESTS` | 3 | ❌ Plugin update | - |
| `SKIP_SOURCE_CRUD_TESTS` | 1 | ❌ Blocked | Backend bug |
| `SKIP_TAG_RATES_TESTS` | 1 | ❌ Blocked | COST-7179 |
| `SKIP_UNSTABLE_TESTS` | ~20 | ⚠️ Investigating | - |

---

## Maintenance

1. **Issue Resolved**: Update status and move to validated groups
2. **New Skip Pattern**: Add section with Jira link and root cause
3. **Profile Change**: Update test counts and durations after validation runs
