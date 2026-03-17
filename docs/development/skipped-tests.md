# Skipped Tests for On-Prem Cost Management

> **Living Document**: This document tracks tests that are skipped when running IQE tests against on-prem Cost Management deployments. It should be updated as issues are resolved or new skip patterns are identified.
>
> **Last Updated**: 2026-03-16

## Overview

Tests are organized into skip groups that can be toggled independently via environment variables. Set `SKIP_*=false` to include a group in the test run.

| Variable | Default | Description | Jira/Context |
|----------|---------|-------------|--------------|
| `SKIP_GPU_TESTS` | true | GPU/MIG tests | [COST-7179](https://issues.redhat.com/browse/COST-7179) |
| `SKIP_ROS_TESTS` | true | ROS tests | Missing MinIO/Vault infrastructure |
| `SKIP_DATE_RANGE_TESTS` | true | 90-day date range tests | Fresh data only spans ~60 days |
| `SKIP_ORDER_BY_TESTS` | true | Order by tests | Same root cause as COST-7179 |
| `SKIP_TAG_TESTS` | true | Tag validation tests | NISE data generation gap |
| `SKIP_COST_DISTRIBUTION_TESTS` | true | Cost distribution tests | Same root cause as COST-7179 |
| `SKIP_INFRA_TESTS` | true | Infrastructure/config tests | On-prem environment limitations |
| `SKIP_SLOW_TESTS` | true | Long-running tests (>2min) | Performance optimization |
| `SKIP_DELTA_TESTS` | true | Delta/monthly calculation tests | Data timing issues |
| `SKIP_FLAKY_TESTS` | true | Flaky tests with intermittent failures | Various root causes |

---

## GPU/MIG Tests (`SKIP_GPU_TESTS`)

**Jira**: [COST-7179](https://issues.redhat.com/browse/COST-7179)  
**Status**: Blocked - waiting for backend fix  
**Filter**: `ai_workloads or distro or test_api_ocp_gpu or test_api_gpu or test_api_cost_model_ocp_gpu or test_api_cost_model_ocp_cost_gpu or test_api_ocp_resource_types_gpu`

**Problem**: The backend cannot process GPU/MIG data from NISE 5.3.6+. When ingestion fails, `completed_datetime` is never set on manifests, causing fixtures to timeout indefinitely.

**Impact**: ~90 tests

**Resolution Path**: 
- PR #5924 addresses the `completed_datetime` bug
- Once merged, set `SKIP_GPU_TESTS=false` to verify

---

## ROS Tests (`SKIP_ROS_TESTS`)

**Status**: Expected skip for on-prem  
**Filter**: `test_api_ocp_ros`

**Problem**: ROS (Resource Optimization Service) tests require:
- MinIO bucket for storing recommendations
- Vault credentials for secure access

These components are not part of the standard on-prem deployment.

**Impact**: 3 tests

**Resolution Path**: 
- Configure MinIO for ROS if recommendations feature is needed
- Otherwise, keep skipped as expected for on-prem

---

## Date Range Tests (`SKIP_DATE_RANGE_TESTS`)

**Status**: Expected skip for on-prem  
**Filter**: `last-90-days or random_date_range or random_daily_time_filter`

**Problem**: On-prem generates fresh NISE data each test run, spanning only ~60 days (current + previous month). Tests querying 90-day ranges or random dates often request data outside the available range.

**Error Example**: `"start_date":["Parameter start_date must be from 2026-01-01 to 2026-03-11"]`

**Impact**: ~30 tests

**Resolution Path**:
- Pre-seed database with historical data (complex)
- Keep skipped as expected limitation of fresh data approach

---

## Order By Tests (`SKIP_ORDER_BY_TESTS`)

**Jira**: Related to [COST-7179](https://issues.redhat.com/browse/COST-7179)  
**Status**: Blocked - same root cause as GPU tests  
**Filter**: `test_api_ocp_all_limit_order_by_cost or test_api_ocp_tagging_limit_order_by_cost or test_api_ocp_volume_order_by`

**Problem**: These tests use fixtures that timeout waiting for `completed_datetime` to be set.

**Impact**: ~66 tests

**Resolution Path**: Same as GPU tests - fix the `completed_datetime` bug

---

## Tag Validation Tests (`SKIP_TAG_TESTS`)

**Status**: Investigation needed  
**Filter**: `volume-tag-exact_match`

**Problem**: Tests expect specific tag values like `tag:volume=stor_node-1` that don't exist in the generated NISE data.

**Impact**: ~6 tests

**Resolution Path**:
- Investigate NISE data generation templates
- Ensure required tags are generated
- File Jira if this is a NISE bug

---

## Cost Distribution Tests (`SKIP_COST_DISTRIBUTION_TESTS`)

**Jira**: Related to [COST-7179](https://issues.redhat.com/browse/COST-7179)  
**Status**: Blocked - same root cause as GPU tests  
**Filter**: `test_api_cost_model_ocp_cost_distribution`

**Problem**: Same `completed_datetime` timeout issue.

**Impact**: 5 tests

**Resolution Path**: Same as GPU tests

---

## Infrastructure Tests (`SKIP_INFRA_TESTS`)

**Status**: ✅ VALIDATED - Now passing consistently (2026-03-16)  
**Default**: `SKIP_INFRA_TESTS=false` (included in default test runs)  
**Filter**: `test_api_cost_model_rates_update_to_tag_based or test_api_ocp_all_validate_items_date_range_monthly or test_api_ocp_ingest_source_static or test_api_ocp_ingest_source_eur or test_api_ocp_for_aws or test_api_ocp_cost_filtered_top_projects or test_api_ocp_all_bucketing or test_api_ocp_coros_distribution_negative_filtering`

**Validation Results** (Phase 8 - skip-group-validation-plan.md):
- 256/258 tests passed (99%)
- Duration: 51 minutes 37 seconds
- Only 2 failures: one timeout (intermittent), one 90-day date range (expected)

**Impact**: ~258 tests (includes parameterized variants)

**Note**: These tests were expected to fail due to on-prem limitations but now work correctly. They are included in the default test run. Set `SKIP_INFRA_TESTS=true` to exclude them if needed.

---

## Slow Tests (`SKIP_SLOW_TESTS`)

**Status**: Performance optimization  
**Filter**: `test_api_ocp_source_raw_node_cluster_capacity or test_api_source_cluster_info_sources or test_api_ocp_source_all_bucketing_platform_update or test_api_ocp_all_project_classification or test_api_ocp_daily_flow_ingest`

**Problem**: These tests take >2 minutes each, consuming significant CI time.

| Test | Duration |
|------|----------|
| `test_api_ocp_source_raw_node_cluster_capacity` | ~6m 38s |
| `test_api_source_cluster_info_sources` | ~4m 56s |
| `test_api_ocp_source_all_bucketing_platform_update` | ~3m 46s |
| `test_api_ocp_all_project_classification` | ~2m 45s |
| `test_api_ocp_daily_flow_ingest` | ~1m 40s |

**Impact**: ~10 tests, ~20 minutes saved

**To include**: `./scripts/run-iqe-tests-local.sh --include-slow`

---

## Delta Tests (`SKIP_DELTA_TESTS`)

**Status**: ✅ VALIDATED - Now passing consistently (2026-03-16)  
**Default**: `SKIP_DELTA_TESTS=false` (included in default test runs)  
**Filter**: `deltas_monthly or test_api_ocp_coros_distribution_deltas`

**Validation Results** (Phase 7 - skip-group-validation-plan.md):
- 12/12 tests passed (100%)
- Duration: 10 minutes 27 seconds
- No failures or errors

**Impact**: ~12 tests (includes parameterized variants)

**Note**: These tests were previously expected to fail due to data timing issues but now work correctly. They are included in the default test run. Set `SKIP_DELTA_TESTS=true` to exclude them if needed.

---

## Flaky Tests (`SKIP_FLAKY_TESTS`)

**Status**: ✅ VALIDATED - Now passing consistently (2026-03-16)  
**Default**: `SKIP_FLAKY_TESTS=false` (included in default test runs)  
**Filter**: `test_api_ocp_forecast_data_other_params or test_api_ocp_forecast_prediction_days or test_api_ocp_forecast_values or test_api_ocp_resource_types_nodes_search or test_api_ocp_resource_types_clusters_search or test_api_ocp_resource_types_projects_search or test_api_ocp_currency_report_param or test_api_ocp_currency_compute or test_api_ocp_currency_memory or test_api_ocp_currency_volume or test_api_ocp_tags_filtered_total_match_group_by_total`

**Validation Results** (Phase 6 - skip-group-validation-plan.md):
- 54/54 tests passed (100%)
- Duration: 11 minutes 9 seconds
- No failures or errors

**Impact**: ~54 tests (includes parameterized variants)

**Note**: These tests were previously marked as flaky but are now stable. They are included in the default test run. Set `SKIP_FLAKY_TESTS=true` to exclude them if needed for faster feedback loops.

---

## Usage

```bash
# Run with default filters (all groups skipped)
./scripts/run-iqe-tests-local.sh

# Include slow tests for comprehensive run
./scripts/run-iqe-tests-local.sh --include-slow

# Test GPU fix (after COST-7179 is resolved)
SKIP_GPU_TESTS=false ./scripts/run-iqe-tests-local.sh

# Run everything (no skips) - use with caution
SKIP_GPU_TESTS=false \
SKIP_ROS_TESTS=false \
SKIP_DATE_RANGE_TESTS=false \
SKIP_ORDER_BY_TESTS=false \
SKIP_TAG_TESTS=false \
SKIP_COST_DISTRIBUTION_TESTS=false \
SKIP_INFRA_TESTS=false \
SKIP_SLOW_TESTS=false \
SKIP_DELTA_TESTS=false \
SKIP_FLAKY_TESTS=false \
./scripts/run-iqe-tests-local.sh
```

---

## Maintenance

When updating this document:

1. **Issue Resolved**: Remove the skip group or update status to "Fixed in version X"
2. **New Skip Pattern**: Add a new section with Jira link, root cause, and resolution path
3. **Filter Change**: Update the filter string and test count
4. **Verify Counts**: Run `--collect-only` to get accurate test counts

```bash
# Count tests in a skip group
iqe tests plugin cost_management -m "cost_ocp_on_prem" -k "ai_workloads or test_api_gpu" --collect-only 2>&1 | grep "selected"
```
