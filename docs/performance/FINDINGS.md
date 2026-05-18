# Performance Testing Findings

This document tracks issues discovered during performance testing that require follow-up action.

---

## Open Issues

### PERF-FINDING-001: Gateway Timeout Too Low for Large File Uploads

**Status**: Open - Jira created  
**Severity**: Critical  
**Jira**: [FLPATH-4091](https://redhat.atlassian.net/browse/FLPATH-4091)

**Problem**:
The Envoy gateway has a 30s timeout for the `/api/ingress/` route. Large file uploads (30+ days of data, ~48MB+) take 25-50 seconds to process, causing HTTP 504 Gateway Timeout errors even though ingress successfully processes the upload.

**Impact**:
- Customers with large clusters cannot upload more than ~2 weeks of data at once
- HTTP 504 errors returned to clients despite successful server-side processing

**Evidence**:
```
# Gateway logs show successful processing (202)
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

**Recommended Fix**:
1. Increase Envoy route timeout to 300s (5 minutes)
2. Increase per_try_timeout to 120s (2 minutes)
3. Increase HAProxy route timeout to 300s
4. Make timeouts configurable via values.yaml

**Workaround**:
Manually apply timeout configuration:
```yaml
gateway:
  timeout: 300s
  per_try_timeout: 120s

gatewayRoute:
  annotations:
    haproxy.router.openshift.io/timeout: "300s"
```

---

## Performance Baselines

### Large File Processing Times

| File Size | Upload Time | Upload Throughput | Processing Time | Total Time |
|-----------|-------------|-------------------|-----------------|------------|
| ~13 KB | 330ms | - | <1s | <1s |
| ~48 MB | 25-50s | 1-2 MB/s | 5-10 min | 6-11 min |
| ~67 MB | 36-62s | 1.1-1.9 MB/s | 7-20 min | 8-22 min |
| ~197 MB | 62s | 3.2 MB/s | >20 min | >21 min |

**Key Observations**:
- Upload throughput is acceptable (1-3 MB/s)
- Processing time is the bottleneck, not upload
- Processing time scales roughly linearly with data volume
- Files >100MB require extended processing timeouts (>20 min)

### Concurrent Upload Scaling

| Concurrent Sources | Recommended Config | Expected Processing Time |
|-------------------|-------------------|-------------------------|
| 1-5 | Default (1 replica) | 4-8 minutes |
| 6-10 | 2 replicas | 7-10 minutes |
| 11-20 | 3+ replicas | TBD |

For high-concurrency workloads, scale workers:
```yaml
listener:
  replicas: 2

celeryWorker:
  workers:
    ocp:
      replicas: 2
      concurrency: 5
    summary:
      replicas: 2
      concurrency: 5
```

---

## Test Run Summary

### Latest Run (2026-04-23)

| Test Category | Tests | Passed | Failed | Notes |
|---------------|-------|--------|--------|-------|
| API Latency | 16 | 16 | 0 | All passed |
| Scale | 9 | 9 | 0 | All passed |
| Soak | 3 | 3 | 0 | Thread-safe implementation validated |
| Ingestion | 5 | 5 | 0 | Including large file upload |
| ROS | 1 | 0 | 1 | Kruize experiment creation issue (environment-specific) |

**Total: 34 tests, 33 passed, 1 environment-specific failure**

---

## Action Items

| Finding | Action | Jira | Status |
|---------|--------|------|--------|
| PERF-FINDING-001 | Deploy chart fix for gateway timeout | [FLPATH-4091](https://redhat.atlassian.net/browse/FLPATH-4091) | **Open** |

---

## Related Jira Stories

- [FLPATH-4036](https://redhat.atlassian.net/browse/FLPATH-4036): Performance Testing Framework
- [FLPATH-4091](https://redhat.atlassian.net/browse/FLPATH-4091): Gateway Timeout Fix

---

_Last Updated: 2026-04-23_
