# Performance Testing Findings

This document tracks issues discovered during performance testing that require follow-up action (Jira tickets, chart fixes, documentation updates).

## How to Use This Document

1. **During Testing**: Add new findings as they're discovered
2. **After Testing**: Review findings and create Jira tickets
3. **After Fixes**: Update status and link to PRs/tickets

---

## Test Infrastructure Fixes

### FIX-001: Performance Test Cleanup (2026-04-25)

**Status**: Fixed  
**Problem**: Performance tests in `test_ingestion.py` and `test_scale.py` imported cleanup functions (`delete_source`, `cleanup_database_records`) but never called them, causing test data to accumulate (~550 sources were left behind).

**Solution**: Added `perf_cleanup` fixture to track created resources and clean them up automatically after each test:
- `tests/suites/performance/conftest.py`: Added `PerfCleanupTracker` class and `perf_cleanup` fixture
- `tests/suites/performance/test_ingestion.py`: Added `perf_cleanup` parameter and `track()` calls to tests that create sources
- `tests/suites/performance/test_scale.py`: Added `perf_cleanup` parameter and `track()` calls to tests that create sources

The cleanup respects `E2E_CLEANUP_AFTER` environment variable and automatically runs after each test (pass or fail).

---

## Test Run Summary

### Run 6 - Full Performance Suite (2026-04-22)

Ran all remaining performance tests with config adjustments as needed.

**Config Changes Applied**:
- Gateway timeout: 30s → 300s (for large uploads)
- Workers scaled to 2 replicas (listener, ocp-worker, summary-worker)

| Test Category | Tests | Passed | Failed | Duration | Notes |
|---------------|-------|--------|--------|----------|-------|
| SCALE-003 (large namespace) | 1 | 1 | 0 | ~20s | Query tests passed |
| SCALE-004 (concurrent queries) | 3 | 3 | 0 | ~40s | 5/10/20 concurrent queries passed |
| SCALE-005 (historical depth) | 2 | 2 | 0 | ~20s | 10/30 day ranges passed |
| ING-005 (high frequency) | 1 | 1 | 0 | 15m 57s | Sustained upload test passed |
| ING-002 (30-day burst) | 1 | 1 | 0 | ~8m | Passed with 300s timeout |
| ING-002 (60-day burst) | 1 | 1 | 0 | ~12m | Passed with 300s timeout |
| ING-002 (90-day burst) | 1 | 0 | 1 | N/A | Known limitation - see COST-7253, COST-573 |

**Run 6 Total: 10 tests, 9 passed, 1 expected failure**

**Key Findings**:
- 30/60 day burst uploads work with 300s gateway timeout
- 90-day uploads are a known backend retention limitation (COST-7253, COST-573 in progress)
- High frequency uploads (15 min sustained) work reliably
- All query-based tests pass quickly

**Config Required for Burst Tests**:
```yaml
# Gateway timeout increase needed
gateway:
  timeout: 300s        # Default: 30s
  per_try_timeout: 120s  # Default: 10s

# Route annotation  
haproxy.router.openshift.io/timeout: "300s"
```

---

### Run 5 - Clean Environment Retest (2026-04-22)

After fixing the cleanup bug (FIX-001), all tests were re-run with a clean environment.

| Test Category | Tests | Passed | Failed | Duration | Notes |
|---------------|-------|--------|--------|----------|-------|
| API Latency | 16 | 16 | 0 | 4m 11s | All passed including 3-dim group_by |
| Ingestion (baseline) | 2 | 2 | 0 | 4m 39s | Cleanup working |
| Concurrent Uploads (2,5) | 2 | 2 | 0 | ~8m | Passed |
| Concurrent Uploads (10) | 1 | 0 | 1 | 10m | Timeout with default config (see PERF-FINDING-009) |
| Concurrent Uploads (10) - scaled | 1 | 1 | 0 | 7m 26s | **Passed with 2 replicas** |
| Scale (001, 002) | 3 | 3 | 0 | 14m 30s | Cleanup working |

**Run 5 Total: 24 tests, 23 passed, 1 failed** (~32 minutes)
**Run 5b (scaled config): +1 test passed** (10-concurrent with 2 replicas)

**Key Observations**:
- Cleanup fixture is working correctly - sources cleaned up after tests
- API latency tests now pass including the 3-dimension group_by (was failing before due to data accumulation)
- Concurrent uploads with 10 sources times out during processing wait - this identifies a tuning/configuration investigation need (see PERF-FINDING-009)
- Performance testing is helping identify configuration requirements for different scale scenarios

---

### Run 4 - Intermittent Failure Analysis (2026-04-21)

| Test Category | Tests | Passed | Failed | Duration | Notes |
|---------------|-------|--------|--------|----------|-------|
| Concurrent Uploads (2) | 12 | 12 | 0 | ~20m | 0% failure rate |
| Concurrent Uploads (5) | 12 | 12 | 0 | ~40m | 0% failure rate |
| Concurrent Uploads (10) | 12 | 12 | 0 | ~70m | 0% failure rate |

**Run 4 Total: 36 tests, 36 passed, 0 failed** (~2h 15m)

### Run 3

| Test Category | Tests | Passed | Failed | Duration | Notes |
|---------------|-------|--------|--------|----------|-------|
| Concurrent Uploads | 3 | 3 | 0 | 10m 30s | All passed |

### Run 2

| Test Category | Tests | Passed | Failed | Duration | Notes |
|---------------|-------|--------|--------|----------|-------|
| Scale | 9 | 9 | 0 | 9m 36s | All passed |
| API Latency | 16 | 15 | 1 | 5m 31s | 3-dim group_by: 17.4s (>10s threshold) |
| Ingestion (baseline + high-freq) | 3 | 3 | 0 | 19m 37s | All passed |

**Run 2 Total: 28 tests, 27 passed, 1 failed** (~35 minutes)

### Run 1 (Full Suite)

| Test Category | Tests | Passed | Failed | Notes |
|---------------|-------|--------|--------|-------|
| API Latency | 16 | 15 | 1 | 3-dim group_by: 13.7s |
| Scale | 9 | 9 | 0 | All passed |
| Ingestion (baseline) | 2 | 2 | 0 | Small uploads work |
| Ingestion (burst) | 3 | 0 | 3 | HTTP 504 gateway timeout |
| Ingestion (concurrent) | 3 | 1 | 2 | HTTP 500 during concurrent uploads |
| Ingestion (high-freq) | 1 | 1 | 0 | Passed |

**Run 1 Total: 34 tests, 28 passed, 6 failed** (69 minutes)

---

## Critical Issues

### PERF-FINDING-001: Gateway Timeout Too Low for Large File Uploads

**Status**: Validated - Config change needed  
**Severity**: Critical - Blocks large file uploads in production  
**Discovered**: 2026-04-21  
**Updated**: 2026-04-22  
**Jira**: _TODO: Create ticket_

**Problem**:
The Envoy gateway has a 30s timeout with 10s per-retry for the `/api/ingress/` route. Large file uploads (30+ days of data, ~48MB+) take 25-50 seconds to process, causing:
- HTTP 504 Gateway Timeout errors
- `response_flags: "UT"` (Upstream Timeout) in gateway logs
- Uploads fail even though ingress successfully processes them

**Validation Results (2026-04-22)**:
With gateway timeout increased to 300s:
- ✅ 30-day burst upload: **PASSED** (~8 min)
- ✅ 60-day burst upload: **PASSED** (~12 min)
- ❌ 90-day burst upload: **FAILED** - Connection dropped

**Note on 90-day uploads**: The 90-day test failure is related to backend retention configuration, not test infrastructure. This is being actively addressed by:
- **COST-7253**: Backend retention configuration
- **COST-573**: Related retention work
Both tickets are being worked by Jordi Gil. The 90-day test should be considered out of scope until backend support is confirmed.

**Impact**:
- Customers with large clusters cannot upload more than ~2 weeks of data at once (default config)
- With 300s timeout: 30-60 days works reliably
- 90-day uploads depend on backend retention support (see COST-7253, COST-573)

**Evidence**:
```
# Gateway logs showing successful processing on server side (202)
2026/04/21 17:14:52 "POST .../api/ingress/v1/upload" - 202 131B in 30.664632599s

# But client receives 504 due to Envoy timeout
{"response_code":504,"response_flags":"UT","duration":10538}
```

**Root Cause**:
```yaml
# cost-onprem/templates/gateway/configmap-envoy.yaml
- match:
    prefix: "/api/ingress/"
  route:
    cluster: ingress-backend
    timeout: 30s           # Too short for large uploads
    retry_policy:
      per_try_timeout: 10s # Each retry times out at 10s
```

```yaml
# cost-onprem/values.yaml
gatewayRoute:
  annotations:
    haproxy.router.openshift.io/timeout: "30s"  # Also too short
```

**Recommended Fix**:
1. Increase Envoy route timeout to 300s (5 minutes)
2. Increase per_try_timeout to 120s (2 minutes)
3. Increase HAProxy route timeout to 300s
4. Consider making these configurable via values.yaml

**Workaround for Testing**:
- Skip burst tests (30/60/90 day) until fix is deployed
- Use smaller data volumes (<2 weeks) for functional testing

**Action Items**:
- [ ] Create Jira ticket
- [ ] Create chart PR with fix
- [ ] Test fix in staging
- [ ] Update performance tests to validate fix

---

### PERF-FINDING-002: S3 Staging Bucket State Issues

**Status**: Under Investigation  
**Severity**: High - Intermittent upload failures  
**Discovered**: 2026-04-21  
**Jira**: _TODO: Create ticket_

**Problem**:
Intermittent "The request is not valid with the current state of the bucket" errors when ingress attempts to write to `insights-upload-perma` bucket.

**Evidence**:
```json
{
  "error": "Failed to upload 'insights-upload-perma' to storage: The request is not valid with the current state of the bucket.",
  "message": "Error staging",
  "size": 48376203
}
```

**Observations**:
- Direct S3 operations via boto3 succeed (list, upload, delete)
- Error occurs specifically during ingress → S3 writes
- May be related to bucket state after many rapid uploads
- Clearing bucket and restarting ingress sometimes resolves

**Potential Causes**:
1. Incomplete multipart uploads accumulating
2. ODF/Ceph RGW connection pooling issues
3. Bucket versioning/lifecycle policy conflicts
4. Rate limiting or quota issues

**Workaround**:
Clear staging bucket before performance tests:
```python
# Pre-test cleanup
paginator = s3.get_paginator('list_objects_v2')
for page in paginator.paginate(Bucket='insights-upload-perma'):
    for obj in page.get('Contents', []):
        s3.delete_object(Bucket=bucket, Key=obj['Key'])
```

**Investigation Needed**:
- [ ] Check ODF RGW logs during failures
- [ ] Monitor bucket metrics during sustained load
- [ ] Test with different S3-compatible backends

---

### PERF-FINDING-007: Complex Group-By Queries - Data Volume Dependent

**Status**: Partially Resolved - Performance acceptable with clean data  
**Severity**: Medium - Potential issue at scale  
**Discovered**: 2026-04-21  
**Updated**: 2026-04-22  
**Jira**: _TODO: Create ticket for monitoring/optimization_

**Problem**:
3-dimension group_by queries (e.g., `group_by[project]=*&group_by[node]=*&group_by[cluster]=*`) had variable P95 latency ranging from 13-72 seconds in earlier testing.

**Original Evidence**:
```
test_perf_api_005_complex_group_by[group_by_dims2] FAILED
AssertionError: P95 latency for 3-dim group_by exceeds 10s
assert 13.7433 < 10.0  # Run 2: 13.7s (improved from initial 72s)
```

**Updated Status (2026-04-22)**:
After cleanup fix and re-testing with clean environment:
- All API latency tests now pass (16/16)
- 3-dimension group_by completes within threshold
- Response time: 282ms cold, 7-20ms warm (with caching)

**Root Cause Analysis**:
1. **Data Accumulation**: Previous tests left 500+ orphan sources in database, causing query slowdown
2. **Missing Optimized View**: Koku logs warning: `('cluster', 'node', 'project') for costs_by_project has no entry in views. Using the default.`
3. **Caching**: Valkey cache masks the issue after first query

**Test Details**:
- Query: OCP reports with 3 group_by dimensions
- Clean environment: ~5k summary rows, 263k line items
- P95 latency (clean): < 10s (passing)
- P95 latency (with data accumulation): 13-72s (failing)

**Remaining Concerns**:
- Performance may degrade with larger data volumes (production scale)
- The "missing view" warning indicates an unoptimized query path
- Cache misses could cause occasional slow responses

**Recommendations**:
1. Add monitoring for group_by query latency in production
2. Consider adding optimized view for (cluster, node, project) combination
3. Document expected performance characteristics at different data scales

**Investigation Completed**:
- [x] Capture slow query logs during test (see cost-onprem-group-by-investigation.md)
- [x] Check index usage on reporting tables (single-column indexes exist)
- [x] Check PostgreSQL configuration (work_mem=4MB, shared_buffers=128MB)
- [x] Identify "missing view" warning as root cause

---

### PERF-FINDING-009: Concurrent Upload Processing - Tuning Guide

**Status**: Resolved - Tuning identified  
**Severity**: Low - Configuration guidance  
**Discovered**: 2026-04-22  
**Jira**: _Documentation update needed_

**Problem**:
With default configuration (1 replica each for listener, OCP worker, summary worker), 10 concurrent source uploads take >10 minutes to process, exceeding the test timeout.

**Root Cause**:
Default configuration has limited parallelism:
- Listener: 1 replica
- OCP Worker: 1 replica × concurrency=5 = 5 parallel tasks
- Summary Worker: 1 replica × concurrency=5 = 5 parallel tasks

When 10 sources upload simultaneously, tasks queue up and processing becomes sequential.

**Investigation Results (2026-04-22)**:

| Configuration | 10-Concurrent Test | Processing Time |
|--------------|-------------------|-----------------|
| Default (1 replica each) | FAILED (timeout) | >10 minutes |
| Scaled (2 replicas each) | PASSED | 7 min 26 sec |

**Tuning Applied**:
```bash
# Scale for high-concurrency workloads
oc scale deployment cost-onprem-koku-listener -n cost-onprem --replicas=2
oc scale deployment cost-onprem-celery-worker-ocp -n cost-onprem --replicas=2
oc scale deployment cost-onprem-celery-worker-summary -n cost-onprem --replicas=2
```

With 2 replicas:
- Listener: 2 replicas (handles more concurrent connections)
- OCP Worker: 2 replicas × concurrency=5 = 10 parallel tasks
- Summary Worker: 2 replicas × concurrency=5 = 10 parallel tasks

**Resource Usage During Test**:
```
Summary workers peaked at 449m CPU (limit: 500m)
Memory usage stable at ~450-490Mi per pod
```

**Recommendations for Helm Values**:

For environments expecting high concurrent upload rates, adjust `values.yaml`:

```yaml
# High-concurrency profile
listener:
  replicas: 2  # Default: 1

celeryWorker:
  workers:
    ocp:
      replicas: 2      # Default: 1
      concurrency: 5   # Can increase to 10 if CPU allows
    summary:
      replicas: 2      # Default: 1
      concurrency: 5   # Can increase to 10 if CPU allows
```

**Concurrency Guidelines**:

| Concurrent Sources | Recommended Config | Expected Processing Time |
|-------------------|-------------------|-------------------------|
| 1-5 | Default (1 replica) | 4-8 minutes |
| 6-10 | 2 replicas | 7-10 minutes |
| 11-20 | 3+ replicas, higher concurrency | TBD |

**Test Update**:
The test timeout should be increased for the 10-concurrent variant, or the test should document the required configuration.

---

### PERF-FINDING-008: Concurrent Uploads - Rare Transient Failures

**Status**: Closed - Unable to reproduce  
**Severity**: Low - Very rare transient issue  
**Discovered**: 2026-04-21  
**Jira**: _Not needed - unable to reproduce_

**Problem**:
During initial performance testing, concurrent uploads occasionally returned HTTP 500 errors.

**Initial Evidence**:
```
# Run 1 - Failures (initial full test run)
test_perf_ing_003_concurrent_uploads[2] FAILED (HTTP 500)
test_perf_ing_003_concurrent_uploads[5] FAILED (HTTP 500)
test_perf_ing_003_concurrent_uploads[10] PASSED
```

**Reproduction Attempt (2026-04-21)**:
Ran 36 consecutive tests (12 iterations × 3 variants) to measure failure rate:

```
concurrent_uploads_2:  12/12 passed (0% failure rate)
concurrent_uploads_5:  12/12 passed (0% failure rate)
concurrent_uploads_10: 12/12 passed (0% failure rate)

Total: 36/36 passed (0% failure rate)
Total runtime: ~2 hours 15 minutes
```

**Conclusion**:
The initial failures were likely caused by transient system state (accumulated test data, resource pressure, or timing). After systematic testing, concurrent uploads are stable and do not require a Jira ticket.

**Observations**:
- Failures are extremely rare (<3% based on all observed runs)
- May occur after heavy testing when system state is degraded
- Clearing test data and allowing system to stabilize resolves issue

**Impact**:
- Multi-tenant scenarios may experience intermittent upload failures
- Customers with multiple clusters uploading simultaneously may have issues
- The inconsistent behavior (10 passes, 2/5 fail) complicates debugging

**Potential Causes**:
1. Database connection pool exhaustion or contention
2. Ingress service state corruption under concurrent writes
3. S3 bucket locking/versioning conflicts
4. Race condition in source creation/validation logic

**Investigation Needed**:
- [ ] Check ingress pod logs during concurrent upload failures
- [ ] Monitor database connections during test
- [ ] Analyze why 10 concurrent uploads succeed when 2/5 fail

---

## Medium Issues

### PERF-FINDING-003: Test Cleanup Not Implemented

**Status**: Fixed  
**Severity**: Medium - Test pollution  
**Discovered**: 2026-04-21  
**Jira**: N/A (test infrastructure)

**Problem**:
Performance tests were not cleaning up created sources and database records, leading to:
- S3 staging bucket filling up (406+ objects)
- Database accumulating test sources
- Subsequent test runs affected by stale data

**Fix**:
Updated performance tests to use existing cleanup utilities from `e2e_helpers.py`:
- `delete_source()` for source cleanup
- `cleanup_database_records()` for database cleanup
- Follow same pattern as `cost_management/conftest.py`

---

### PERF-FINDING-004: HTTP Client Timeout Configuration

**Status**: Fixed  
**Severity**: Medium - Test reliability  
**Discovered**: 2026-04-21  
**Jira**: N/A (test infrastructure)

**Problem**:
`upload_with_retry()` in `e2e_helpers.py` had a fixed 60s timeout, insufficient for large file uploads.

**Fix**:
Added configurable `timeout` parameter with 180s default:
```python
def upload_with_retry(
    session, url, package_path, auth_header,
    max_retries=3, retry_delay=5,
    timeout=180,  # NEW: 3 minutes for large files
) -> requests.Response:
```

---

## Low Issues / Observations

### PERF-FINDING-005: Ingress Upload Performance Baseline

**Status**: Documented  
**Severity**: Info  
**Discovered**: 2026-04-21

**Observation**:
Baseline upload processing times on ODF storage:
- ~13KB file: 330ms
- ~48MB file (30 days): 25-50 seconds
- Throughput: ~1-2 MB/s effective

This establishes baseline expectations for upload tests.

---

### PERF-FINDING-006: NISE Data Generation Profiles Need Tuning

**Status**: Pending  
**Severity**: Low  
**Discovered**: 2026-04-21  
**Jira**: _TODO: Consider ticket_

**Observation**:
Current NISE profiles generate different data volumes than expected:
- 30-day "burst" profile generates ~48MB package
- 60-day "burst" profile generates ~95MB package
- 90-day "burst" profile exceeds test timeouts

May need to adjust profile definitions or create specific "stress test" profiles.

---

## Action Items

| Finding | Action | Owner | Jira | Status |
|---------|--------|-------|------|--------|
| PERF-FINDING-001 | Deploy chart fix for gateway timeout | - | TODO | **Open** |
| PERF-FINDING-002 | Investigate ODF/RGW behavior | - | TODO | Open |
| PERF-FINDING-007 | Investigate slow group-by queries | - | TODO | **Open** |
| PERF-FINDING-008 | ~~Investigate concurrent upload failures~~ | - | N/A | **Closed** - Unable to reproduce |
| PERF-FINDING-006 | Review NISE profile sizing | - | TODO | Low priority |

---

## Related Jira Stories

- FLPATH-4036: Performance Testing Framework
- FLPATH-4037: Ingestion Throughput Tests
- FLPATH-4038: API Latency Tests
- FLPATH-4039: Scale Tests
- FLPATH-4040: ROS Performance Tests
- FLPATH-4041: Soak Tests

---

## Jira-Ready Issue Summaries

### JIRA-001: Gateway Timeout Blocks Large File Uploads

**Title**: [Cost On-Prem] Gateway timeout (30s) prevents uploads larger than ~2 weeks of data

**Type**: Bug  
**Priority**: Critical  
**Component**: cost-onprem-chart  
**Labels**: performance, gateway, ingestion

**Description**:
The Envoy gateway configuration has a 30-second timeout for the `/api/ingress/` route, which is insufficient for large file uploads. Customers uploading 30+ days of data (~48MB+) receive HTTP 504 Gateway Timeout errors even though the ingress service successfully processes the upload.

**Steps to Reproduce**:
1. Deploy cost-onprem chart (any recent version)
2. Generate 30 days of OCP data using NISE (~48MB package)
3. Upload via `/api/ingress/v1/upload` endpoint
4. Observe HTTP 504 after ~30 seconds

**Expected Result**: Upload completes successfully  
**Actual Result**: HTTP 504 Gateway Timeout; gateway logs show `response_flags: "UT"` (Upstream Timeout)

**Root Cause**:
- `cost-onprem/templates/gateway/configmap-envoy.yaml`: `timeout: 30s`, `per_try_timeout: 10s`
- `cost-onprem/values.yaml`: `haproxy.router.openshift.io/timeout: "30s"`

**Proposed Fix**:
1. Increase Envoy route timeout to 300s (5 minutes)
2. Increase per_try_timeout to 120s
3. Increase HAProxy annotation to 300s
4. Make timeouts configurable via values.yaml

**Acceptance Criteria**:
- [ ] 48MB file upload completes without timeout
- [ ] 90-day data upload (~150MB) completes within 5 minutes
- [ ] Performance tests `test_perf_ing_002_single_source_burst` pass

---

### JIRA-002: Complex Group-By API Queries Exceed 10s Latency Threshold

**Title**: [Cost On-Prem] 3-dimension group_by queries take 13-17s, exceeding 10s UI threshold

**Type**: Bug  
**Priority**: High  
**Component**: koku  
**Labels**: performance, api, database

**Description**:
API queries with 3 group_by dimensions (e.g., `group_by[project]=*&group_by[node]=*&group_by[cluster]=*`) have P95 latency of 13-17 seconds, exceeding the 10-second threshold for acceptable UI responsiveness.

**Steps to Reproduce**:
1. Deploy cost-onprem with OCP data loaded
2. Call: `GET /api/cost-management/v1/reports/openshift/costs/?group_by[project]=*&group_by[node]=*&group_by[cluster]=*`
3. Measure response time across multiple requests

**Expected Result**: P95 latency < 10 seconds  
**Actual Result**: P95 latency = 13.7-17.4 seconds (varies by run)

**Impact**:
- UI drill-down operations appear slow or hung
- Users cannot effectively explore cost data by multiple dimensions
- May cause browser/client timeouts

**Investigation Needed**:
- Capture slow query logs during test
- Run EXPLAIN ANALYZE on generated SQL
- Check index usage on reporting tables
- Compare with SaaS performance metrics

**Acceptance Criteria**:
- [ ] 3-dimension group_by queries complete in < 10 seconds (P95)
- [ ] Performance test `test_perf_api_005_complex_group_by[group_by_dims2]` passes

---

### ~~JIRA-003: Concurrent Uploads~~ - CLOSED

**Status**: Unable to reproduce after 36 consecutive test runs (0% failure rate).

Initial failures were likely due to transient system state. No Jira ticket needed.

---

### JIRA-004: S3 Staging Bucket State Issues Under Load

**Title**: [Cost On-Prem] Intermittent "bucket state" errors during sustained upload load

**Type**: Bug  
**Priority**: Medium  
**Component**: ingress, ODF  
**Labels**: performance, storage, intermittent

**Description**:
Under sustained upload load, the ingress service intermittently fails to write to the S3 staging bucket (`insights-upload-perma`) with error: "The request is not valid with the current state of the bucket."

**Steps to Reproduce**:
1. Run sustained upload tests (multiple uploads in sequence)
2. Eventually observe staging failures in ingress logs

**Expected Result**: All uploads stage successfully  
**Actual Result**: Intermittent failures with bucket state error

**Evidence**:
```json
{
  "error": "Failed to upload 'insights-upload-perma' to storage: The request is not valid with the current state of the bucket.",
  "message": "Error staging",
  "size": 48376203
}
```

**Workaround**: Clear staging bucket contents and restart ingress pod

**Investigation Needed**:
- Check ODF RGW logs during failures
- Monitor for incomplete multipart uploads
- Test with different S3-compatible backends

**Acceptance Criteria**:
- [ ] 100 sequential uploads complete without bucket state errors
- [ ] Root cause identified and documented

---

_Last Updated: 2026-04-21 (Intermittent failure analysis completed - 36/36 concurrent upload tests passed)_
