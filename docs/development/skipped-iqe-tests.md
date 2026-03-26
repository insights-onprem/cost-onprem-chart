# Skipped IQE Tests for On-Prem Cost Management

> **Living Document**: This document tracks tests that are skipped when running IQE tests against on-prem Cost Management deployments. It should be updated as issues are resolved or new skip patterns are identified.
>
> **Last Updated**: 2026-03-25

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

## Pytest Markers (IQE Plugin)

The IQE plugin registers on-prem-specific markers for finer-grained test selection.
These are additive — a test may carry multiple markers.

| Marker | Functions | Purpose | Status |
|--------|-----------|---------|--------|
| `cost_ocp_on_prem` | ~242 | All on-prem eligible tests | Active — base selector |
| `cost_onprem_smoke` | 24 | Core smoke: source ingestion, cost models, basic API | Active |
| `cost_onprem_blocked` | 35+ | Blocked by external issues (GPU/MIG, backend bugs) | Active — see breakdown below |
| `cost_onprem_data_intensive` | 5 | Large/multi-month datasets (daily flow, bucketing) | Active — also `cost_onprem_blocked` currently |
| `cost_onprem_infra` | 7 | Infrastructure/config validation (bucketing, ingestion) | Active |

### `cost_onprem_blocked` Breakdown

All `cost_onprem_blocked` tests are blocked by specific external issues. The marker
comment on each test identifies the Jira ticket.

| Blocker | Tests | Files |
|---------|-------|-------|
| COST-7179: GPU/MIG schema mismatch | ~27 functions | bucketing, cost reports, volume, VM, forecasting, currency, cost model, resource types, data ingest, source |
| COST-7179: `completed_datetime` timeout | ~3 functions | cost model (tag rates), order-by, cost distribution |
| COST-7179: GPU/MIG data in `last-90-days` param | 2 param-level | `api_params.py` |
| FLPATH-3423: Source CRUD update | 1 function | `test__i_source.py` |
| Kafka consumer: external TLS access unsupported by `BrokerConfig` | 1 function | `test_ros.py` — [details](#kafka-consumer-test-limitation) |

### Markers Not Added

During marker assessment (2026-03-25), the following proposed markers were evaluated
and ultimately not created:

- **`cost_onprem_date_limited`** — Tests that fail due to NISE data coverage. Evaluation
  revealed these fail because of the GPU/MIG cascade (COST-7179), not insufficient
  data months. Reclassified as `cost_onprem_blocked`.
- **`cost_onprem_unstable`** — Tests with intermittent failures. Three consecutive runs
  showed deterministic behavior: consistently passing or consistently erroring due
  to GPU/MIG. Passing tests left unmarked; erroring tests marked `cost_onprem_blocked`.
- **`cost_onprem_slow`** — Renamed to `cost_onprem_data_intensive` to reflect that these
  tests are slow because they use large datasets, not because of inefficiency.

---

## Blocked Test Groups (Script-Level Filters)

These tests are skipped via `-k` filter expressions in `scripts/lib/iqe-filters.sh`.
Each group can be toggled independently with `SKIP_*` environment variables.

### GPU/MIG Tests (`SKIP_GPU_TESTS`)

**Jira**: [COST-7179](https://issues.redhat.com/browse/COST-7179), [FLPATH-3429](https://redhat.atlassian.net/browse/FLPATH-3429)
**Status**: Blocked — waiting for backend fix or FLPATH feature flag
**Impact**: ~90 tests

**Problem**: Backend cannot process GPU/MIG data. NISE 5.3.6+ generates
`mig_instance_uuid` column that the schema lacks, causing `ParquetReportProcessorError`
in the listener. The Kafka offset is committed (no retry), making this a permanent failure.

**Resolution path**: A FLPATH feature flag to gate GPU data generation/processing in
on-prem environments may be more practical than a schema fix, since GPU/MIG is not
a typical on-prem use case.

**Mitigation (IQE plugin)**: `check_manifest_stalled()` and `check_processing_failed()`
in `helpers.py` detect this within ~10s and call `pytest.fail()` with an actionable
message. Without fail-fast, each stalled source blocks for 30+ min. With fail-fast
enabled (branch `flpath-3369-updates-for-cost-onprem`), 17 stalled sources are
handled in seconds rather than 8+ hours.

**Filter**: `ai_workloads or mig_workloads or distro or test_api_ocp_gpu or test_api_gpu or test_api_cost_model_ocp_gpu or test_api_cost_model_ocp_cost_gpu or test_api_ocp_resource_types_gpu`

> **Gap**: `test_api_ocp_mig_*` tests (MIG report endpoints) are not caught by
> this filter. These return 404 since MIG reporting is not implemented. Consider
> adding `test_api_ocp_mig` to the GPU filter.

---

### Order By Tests (`SKIP_ORDER_BY_TESTS`)

**Jira**: Related to [COST-7179](https://issues.redhat.com/browse/COST-7179)
**Status**: Blocked — same `completed_datetime` issue
**Impact**: ~66 tests

**Filter**: `test_api_ocp_all_limit_order_by_cost or test_api_ocp_tagging_limit_order_by_cost or test_api_ocp_volume_order_by`

---

### Cost Distribution Tests (`SKIP_COST_DISTRIBUTION_TESTS`)

**Jira**: Related to [COST-7179](https://issues.redhat.com/browse/COST-7179)
**Status**: Blocked — same `completed_datetime` issue
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

### Source CRUD Update (`SKIP_SOURCE_CRUD_TESTS`)

**Jira**: [FLPATH-3423](https://redhat.atlassian.net/browse/FLPATH-3423)
**Status**: Blocked — requires IQE plugin and backend fixes
**Impact**: 1 test

**Problem**: Wrong API client in plugin + backend PATCH endpoint returns 500.

**Filter**: `test_api_ocp_source_crud`

---

### Tag-Based Rates Update (`SKIP_TAG_RATES_TESTS`)

**Jira**: [COST-7179](https://issues.redhat.com/browse/COST-7179)
**Status**: Blocked — `completed_datetime` timeout
**Impact**: 1 test

**Problem**: Fixtures timeout waiting for `completed_datetime` after tag-based rate update.

**Filter**: `test_api_cost_model_rates_update_to_tag_based`

---

### Unstable Tests (`SKIP_UNSTABLE_TESTS`)

**Jira**: [FLPATH-2689](https://redhat.atlassian.net/browse/FLPATH-2689)
**Status**: Reclassified — deterministic failures, not intermittent
**Impact**: ~20 tests

**Assessment (2026-03-25)**: Three consecutive runs showed these tests fail
deterministically due to the GPU/MIG cascade (COST-7179), not timing. The tests
that consistently pass were left unmarked; the consistently failing tests were
marked `cost_onprem_blocked` in the IQE plugin. The `cost_onprem_unstable` marker
was not added.

**Failures** (all traceable to COST-7179):
- Currency tests (8): IndexError — empty response (GPU data missing)
- Forecast tests (5): Date offset (incomplete processing)
- Volume deltas monthly (3): Delta calculation mismatch
- Virtual machines report (1): Calculation mismatch
- Date range negative tests (3): These actually pass consistently

**Filter**: `test_api_ocp_network_endpoint_date_range_end_negative or test_api_ocp_volume_endpoint_date_range_end_negative or test_api_ocp_tagging_endpoint_date_range_end_negative or test_api_ocp_virtual_machines_report_content or test_api_ocp_volume_deltas_monthly or test_api_ocp_currency_report_param or test_api_ocp_currency_compute or test_api_ocp_currency_memory or test_api_ocp_currency_volume or test_api_ocp_forecast_values or test_api_ocp_forecast_data_other_params or test_api_ocp_forecast_prediction_days`

---

### ROS Tests (`SKIP_ROS_TESTS`)

**Status**: ✅ Resolved (2026-03-25)
**Impact**: 3 tests (2 runnable, 1 skips by design)

**Previous problem**: Tests were blocked with "Missing MinIO bucket and Vault credentials
in on-prem". Investigation revealed the infrastructure was fully deployed — the real
issues were:

1. **Env name mismatch**: IQE fixtures checked for `cost_ocp_on_prem` but the actual
   `ENV_FOR_DYNACONF` is `cost_onprem`. Fixed in `general_fixtures.py`.
2. **Double-protocol URL**: `cost_minio_settings` fixture constructed
   `https://https://s3.openshift-storage.svc:443/` because `S3_ENDPOINT` already
   included the protocol. Fixed URL construction logic.
3. **In-cluster DNS**: `run-iqe-tests-local.sh` didn't export S3 env vars, and the
   in-cluster S3 endpoint (`s3.openshift-storage.svc`) isn't reachable from local
   machines. Fixed by adding S3 extraction with automatic resolution to external routes.

**Changes made**:

| File | Change |
|------|--------|
| `scripts/run-iqe-tests-local.sh` | Added S3 env var extraction + in-cluster-to-external route resolution |
| `scripts/lib/iqe-filters.sh` | `SKIP_ROS_TESTS=false`, updated comment |
| `fixtures/general_fixtures.py` | Added `"cost_onprem"` to env checks; fixed URL construction |
| `test_ros.py` | Removed `cost_onprem_blocked` from 2 tests; added `"cost_onprem"` to env checks |

**Test results (2026-03-25)**:

| Test | Result | Notes |
|------|--------|-------|
| `test_api_ocp_ros_report_upload` | ✅ PASSED | S3 upload verified — 2 ROS files in `ros-data` bucket |
| `test_api_ocp_ros_recommendations` | ❌ FAILED | Missing 1 of 5 expected recommendations (`pod-ros-A12`). Kruize had not finished processing with only 2 days of data. Not an infra issue — likely needs longer data window or explicit wait. |
| `test_api_ocp_ros_kafka_content` | ⏭️ BLOCKED | Kafka consumer requires external access — see [Kafka limitation](#kafka-consumer-test-limitation) below. Kept as `cost_onprem_blocked`. |

#### Kafka Consumer Test Limitation

`test_api_ocp_ros_kafka_content` verifies that Kafka messages are sent to the
`hccm.ros.events` topic after ROS file processing. It requires consuming from
Kafka, which works in-cluster (clowder_smoke) but presents challenges for
local/external test execution.

**Investigation (2026-03-25)**: Multiple approaches were evaluated:

| Approach | Result | Why |
|----------|--------|-----|
| `kubectl port-forward` to bootstrap | ❌ | Kafka advertises in-cluster broker DNS (`*.kafka.svc.cluster.local`) in metadata; librdkafka tries to connect to those addresses directly, which are unresolvable from outside the cluster |
| Strimzi `NodePort` listener | ❌ | Node IPs were behind a load balancer, not directly routable from the test machine |
| Strimzi `route` listener (TLS) | ⚠️ Partial | Routes created successfully, bootstrap connection works, topics listed. But librdkafka cannot verify the Strimzi self-signed CA — `BrokerConfig` in `iqe-core` doesn't expose `ssl.ca.location`, so the CA cert can't be passed to the consumer |

**Root cause**: The `BrokerConfig` dataclass in `iqe-core`'s
`_kafka/_dependency_inject.py` does not support configuring `ssl.ca.location`
for librdkafka. Strimzi route listeners require TLS with the cluster's
self-signed CA, and there's no way to inject this certificate through the
existing IQE MQ plugin configuration.

**Additional complications**:
- Dynaconf env vars uppercase all keys (e.g., `DYNACONF_BROKER__HOSTNAME` →
  `broker.HOSTNAME`), but `BrokerConfig` expects lowercase camelCase
  (`hostname`, `securityProtocol`). Workaround: generate a `mq.local.yaml`
  and use `IQE_ADDITIONAL_CONF_PATH`.
- Route hostnames require `/etc/hosts` entries on the test machine (or real
  DNS) mapping to the cluster's ingress IP.

**Possible future resolutions**:
1. Upstream `iqe-core` change to add `ssl.ca.location` support in `BrokerConfig`
2. Install the Strimzi cluster CA into the macOS trusted keychain
   (`sudo security add-trusted-cert`) so librdkafka can verify it system-wide
3. Run this test only in containerized mode where the pod has in-cluster
   Kafka access

---

## Validated Test Groups

These groups were validated and are now included in profiles above `smoke`.

| Group | Tests | Pass Rate | Duration | Included In |
|-------|-------|-----------|----------|-------------|
| ROS (upload) | 1 | 100% | ~30s | `extended`+ |
| Flaky | 54 | 100% | 11 min | `extended`+ |
| Delta | 12 | 100% | 10 min | `extended`+ |
| Slow | 13 | 100% | 20 min | `extended`+ |
| Infrastructure | 258 | 99% | 52 min | `stable`+ |

---

## Jira Ticket Summary

| Ticket | Title | Impact | Status |
|--------|-------|--------|--------|
| [COST-7179](https://issues.redhat.com/browse/COST-7179) | GPU/MIG schema mismatch (`mig_instance_uuid`) | ~90 direct + ~100 cascade (order-by, cost-dist, unstable, data-intensive, 90-day) | Open — backend fix needed |
| [FLPATH-3429](https://redhat.atlassian.net/browse/FLPATH-3429) | NISE GPU data feature flag for on-prem | Would unblock COST-7179 tests by gating GPU data generation | Open — proposed resolution |
| [FLPATH-3423](https://redhat.atlassian.net/browse/FLPATH-3423) | Source CRUD update: wrong API client + backend 500 | 1 test | Open |
| [FLPATH-3369](https://redhat.atlassian.net/browse/FLPATH-3369) | IQE plugin updates for on-prem (fail-fast, fixtures, markers) | All on-prem tests benefit | In progress — branch active |
| [FLPATH-2689](https://redhat.atlassian.net/browse/FLPATH-2689) | IQE unstable test investigation | ~20 tests — now reclassified as deterministic (COST-7179) | Resolved (2026-03-25) |

### Key Insight: COST-7179 Is the Root Cause

The overwhelming majority of blocked/failing tests trace back to a single issue:
**COST-7179** (GPU/MIG `mig_instance_uuid` schema mismatch). This cascades through:

1. **Direct GPU/MIG tests** (~90): Tests that exercise GPU endpoints
2. **Fixture cascade** (~2900 in full profile): Any source with GPU data stalls,
   causing `completed_datetime` to never be set, which times out all dependent tests
3. **Date-limited tests** (2 params): Extended data months expose the GPU failure
   in additional manifests
4. **Unstable tests** (~17): Were initially thought intermittent but are actually
   deterministic failures from the GPU cascade
5. **Data-intensive tests** (5): Large dataset tests include GPU data

**Recommended resolution**: FLPATH feature flag to disable GPU/MIG data generation
in NISE for on-prem environments, rather than requiring a backend schema migration.

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

# Run ROS tests specifically
./scripts/run-iqe-tests-local.sh --filter "test_api_ocp_ros"
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
| `SKIP_DATE_RANGE_TESTS` | ~228 | ❌ Data limit | — |
| `SKIP_COST_DISTRIBUTION_TESTS` | 5 | ❌ Blocked | COST-7179 |
| `SKIP_TAG_TESTS` | ~6 | ❌ NISE config | — |
| `SKIP_ROS_TESTS` | 3 | ✅ Resolved | — |
| `SKIP_SOURCE_CRUD_TESTS` | 1 | ❌ Blocked | FLPATH-3423 |
| `SKIP_TAG_RATES_TESTS` | 1 | ❌ Blocked | COST-7179 |
| `SKIP_UNSTABLE_TESTS` | ~20 | ⚠️ Reclassified | COST-7179 |

---

## Maintenance

1. **Issue Resolved**: Update status and move to validated groups
2. **New Skip Pattern**: Add section with Jira link and root cause
3. **Profile Change**: Update test counts and durations after validation runs
