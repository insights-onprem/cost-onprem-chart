# Performance Test Matrix

This document provides a high-level view of all performance test permutations.

## Quick Reference

| Suite | Tests | Parametrizations | Total Permutations |
|-------|-------|------------------|-------------------|
| **Ingestion** | 6 | profiles, concurrency, burst days, file sizes | 14 |
| **API Latency** | 6 | iterations, users, pages, groups, tags | 16 |
| **Scale** | 5 | sources, queries, date ranges | 9 |
| **ROS** | 4 | - | 4 |
| **Soak** | 4 | - | 4 |
| **Total** | **25** | | **47** |

---

## Performance Profiles

All tests can be run with different data profiles via `PERF_PROFILE` environment variable.

| Profile | Customers | Clusters | Nodes | CPU Cores | Use Case |
|---------|-----------|----------|-------|-----------|----------|
| `baseline` | - | 1 | 3 | 6 | Smoke/quick validation |
| `small` | 37% | 1 | 15 | 200 | Standard testing |
| `medium` | 35% | 2 | 49 | 544 | Scale testing |
| `large` | 21% | 7 | 133 | 1,964 | Stress testing |
| `xlarge` | 6% | 23 | 346 | 6,954 | Extreme scale |
| `stress_p99` | P99 | 33 | 1,072 | 57,424 | P99 workload |
| `stress_max` | Max | 67 | 4,311 | 793,424 | Maximum observed |

---

## Ingestion Tests (PERF-ING-*)

Tests data upload and processing capacity.

| Test ID | Description | Parameters | Values |
|---------|-------------|------------|--------|
| **ING-001** | Single source baseline | `profile_name` | `baseline`, `small` |
| **ING-002** | Single source burst | `burst_days` | `30`, `60`, `90` |
| **ING-003** | Concurrent uploads | `concurrent_sources` | `2`, `5`, `10` |
| **ING-004** | Large file upload (50MB+) | `target_size_mb` | `50`, `100` |
| **ING-005** | High frequency uploads | - | (configurable via env) |
| **ING-006** | Processing window validation | `profile_name` | `small`, `medium`, `large` |

### Ingestion Test Matrix

| Test | baseline | small | medium | large | xlarge | Notes |
|------|----------|-------|--------|-------|--------|-------|
| ING-001 | ✓ | ✓ | - | - | - | Quick validation |
| ING-002 | - | 30d/60d/90d | - | - | - | Burst scenarios |
| ING-003 | - | 2/5/10 concurrent | - | - | - | Concurrency limits |
| ING-004 | - | - | - | 50MB | 100MB | Large file uploads |
| ING-005 | - | ✓ | - | - | - | Sustained load |
| ING-006 | - | ✓ | ✓ | ✓ | - | 6-hour window |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PERF_ING_005_DURATION_MINUTES` | 15 | High-frequency test duration |
| `PERF_ING_005_INTERVAL_SECONDS` | 300 | Upload interval |
| `PERF_INGESTION_BURST_DAYS` | 30 | Default burst duration |

---

## API Latency Tests (PERF-API-*)

Tests API response times under various conditions.

| Test ID | Description | Parameters | Values |
|---------|-------------|------------|--------|
| **API-001** | Report baseline | `iterations` | `10`, `50` |
| **API-002** | Report under load | `concurrent_users` | `5`, `10`, `20` |
| **API-003** | Cost model CRUD | - | (configurable iterations) |
| **API-004** | Source pagination | `page_size` | `10`, `50`, `100` |
| **API-005** | Complex group-by | `group_by_dims` | `[project]`, `[project,node]`, `[project,node,namespace]` |
| **API-006** | Tag filtering | `tag_count` | `1`, `5`, `10` |

### API Test Matrix

| Test | 10 | 50 | 100 | Notes |
|------|-----|-----|------|-------|
| API-001 iterations | ✓ | ✓ | - | Request count |
| API-004 page_size | ✓ | ✓ | ✓ | Pagination |

| Test | 5 | 10 | 20 | Notes |
|------|---|-----|-----|-------|
| API-002 users | ✓ | ✓ | ✓ | Concurrent load |

| Test | 1 dim | 2 dims | 3 dims | Notes |
|------|-------|--------|--------|-------|
| API-005 group-by | ✓ | ✓ | ✓ | Query complexity |

| Test | 1 tag | 5 tags | 10 tags | Notes |
|------|-------|--------|---------|-------|
| API-006 filters | ✓ | ✓ | ✓ | Filter complexity |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PERF_API_003_ITERATIONS` | 10 | CRUD test iterations |

---

## Scale Tests (PERF-SCALE-*)

Tests multi-cluster and source management at scale.

| Test ID | Description | Parameters | Values |
|---------|-------------|------------|--------|
| **SCALE-001** | Source count baseline | `source_count` | `5`, `10` |
| **SCALE-002** | Source ramp | - | (configurable max/batch) |
| **SCALE-003** | Large namespace count | - | - |
| **SCALE-004** | Concurrent queries | `concurrent_queries` | `5`, `10`, `20` |
| **SCALE-005** | Historical depth | `date_range_days` | `10`, `30` |

### Scale Test Matrix

| Test | 5 | 10 | 20 | 30 | Notes |
|------|---|-----|-----|-----|-------|
| SCALE-001 sources | ✓ | ✓ | - | - | Baseline |
| SCALE-004 queries | ✓ | ✓ | ✓ | - | Concurrent |
| SCALE-005 days | ✓ | - | - | ✓ | History |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PERF_SCALE_002_MAX_SOURCES` | 25 | Max sources for ramp |
| `PERF_SCALE_002_BATCH_SIZE` | 5 | Source batch size |

---

## ROS/Kruize Tests (PERF-ROS-*)

Tests Resource Optimization Service performance.

| Test ID | Description | Prerequisites |
|---------|-------------|---------------|
| **ROS-001** | Recommendation baseline | Data with ROS metrics |
| **ROS-002** | Multi-workload scale | Multiple namespaces |
| **ROS-003** | Recommendation refresh | Existing experiments |
| **ROS-004** | Kruize memory pressure | Extended run |

### ROS Test Requirements

- Kruize must be deployed and healthy
- Data must include resource metrics (CPU/memory requests/limits)
- Kafka must be configured for ROS events

---

## Soak/Stability Tests (PERF-SOAK-*)

Tests long-running stability and resource trends.

| Test ID | Description | Default Duration |
|---------|-------------|------------------|
| **SOAK-001** | Continuous operation | 1 hour |
| **SOAK-002** | Memory leak detection | 1 hour |
| **SOAK-003** | Disk usage monitoring | 1 hour |
| **SOAK-004** | Queue health monitoring | 1 hour |

### Soak Test Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SOAK_DURATION_HOURS` | 1 | Test duration |
| `SOAK_UPLOAD_INTERVAL_MINUTES` | 15 | Upload frequency |
| `SOAK_QUERY_INTERVAL_MINUTES` | 5 | Query frequency |
| `SOAK_METRICS_INTERVAL_SECONDS` | 60 | Metrics collection |

### Soak Duration Recommendations

| Scenario | Duration | Use Case |
|----------|----------|----------|
| Quick validation | 0.02h (~72s) | CI/development |
| Standard | 1h | Nightly |
| Extended | 4h | Weekly |
| Full soak | 24h | Release qualification |

---

## Test Execution Patterns

### Quick Validation (~5 min)
```bash
# Infrastructure check + health
pytest suites/performance/test_api_latency.py::TestAPIHealthCheck -v

# Baseline ingestion only
pytest -k "ing_001 and baseline" -v
```

### Standard Suite (~30 min)
```bash
# All API + Scale tests
pytest suites/performance/test_api_latency.py suites/performance/test_scale.py -v
```

### Full Performance (~2-4 hours)
```bash
PERF_PROFILE=small pytest -m performance --timeout=7200 -v
```

### Profile-Specific
```bash
# Small profile
PERF_PROFILE=small pytest -m performance -v

# Medium profile (longer timeouts)
PERF_PROFILE=medium pytest -m performance --timeout=14400 -v

# Large profile (stress testing)
PERF_PROFILE=large pytest -m performance --timeout=28800 -v
```

---

## Timeout Multipliers

Timeouts automatically scale based on profile:

| Profile | Multiplier | 300s base → |
|---------|------------|-------------|
| baseline | 1.0x | 300s |
| small | 1.0x | 300s |
| medium | 2.0x | 600s |
| large | 4.0x | 1200s |
| xlarge | 8.0x | 2400s |
| stress_p99 | 12.0x | 3600s |
| stress_max | 20.0x | 6000s |

---

## Data Setup Scenarios

For tests requiring pre-existing data, use `setup-test-data.sh`:

| Scenario | Clusters | Nodes | Days | ROS | Upload Time | Processing |
|----------|----------|-------|------|-----|-------------|------------|
| `minimal` | 1 | 1 | 1 | No | <30s | <2min |
| `baseline` | 1 | 2 | 7 | Yes | <2min | <10min |
| `perf-small` | 1 | 15 | 30 | Yes | <5min | <30min |
| `perf-medium` | 2 | 49 | 30 | Yes | <15min | <60min |
| `perf-large` | 7 | 133 | 30 | Yes | <45min | <3hr |
| `ros` | 1 | 3 | 7 | Yes | <2min | <15min |

```bash
# Setup for E2E tests
./scripts/setup-test-data.sh --scenario baseline

# Setup for performance tests
./scripts/setup-test-data.sh --scenario perf-small
```

---

## Related Documentation

- [Performance Testing Plan](./performance-testing-plan.md) - Full FLPATH-4036 plan
- [Test Data Setup Guide](../development/test-data-setup.md) - Data generation
- [Sizing Guide](./sizing-guide.md) - Resource recommendations
- [FINDINGS.md](./FINDINGS.md) - Issues discovered during testing
