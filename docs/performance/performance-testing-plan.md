# Cost On-Prem Performance Testing Plan

**Date**: 2026-04-15 (updated 2026-06-25)  
**Status**: Execution — Small through XLarge profiles validated  
**Epic**: [FLPATH-4036](https://redhat.atlassian.net/browse/FLPATH-4036) / [COST-7567](https://redhat.atlassian.net/browse/COST-7567) - CoP - Performance Tuning & Hardware Sizing Guidelines

---

## Ownership & Collaboration

| Area | Owner | Notes |
|------|-------|-------|
| **Performance testing** | Thomas Stetson | Test execution, automation, analysis |
| **Dev guidance & infrastructure** | Moti Asayag | Primary dev contact, epic author |
| **Customer size profiles** | Pau Garcia Quiles | ✅ Delivered - production data analysis |

---

## Success Criteria (from FLPATH-4036)

| ID | Criteria | Description |
|----|----------|-------------|
| SC-1 | Sizing table | Published sizing table mapping cluster profiles to resource requirements |
| SC-2 | Cluster count limits | Documented maximum supported cluster count per deployment size (S/M/L/XL) |
| SC-3 | Bottleneck analysis | Identified top-3 bottlenecks with measured impact and mitigation options |
| SC-4 | Processing window | Validated that recommended configurations sustain daily processing within 6-hour window |
| SC-5 | Soak test | 7-day stability test without OOM, disk exhaustion, or queue starvation |

---

## Executive Summary

This document outlines the performance testing strategy for Cost Management On-Premise deployments. Performance testing will validate:

1. **Ingestion throughput** - How fast can data be uploaded and processed?
2. **Scale limits** - How many clusters/sources can be managed?
3. **Resource efficiency** - Are resource allocations appropriate?
4. **Latency** - API response times under various loads

---

## Architecture Overview (Performance-Critical View)

From FLPATH-4036:

```
Cost Management On-Premise processes OpenShift cluster cost and resource 
optimization data through a multi-stage pipeline:

  Ingestion (insights-ingress-go)
      ↓
  Cost Processing (koku/MASU + Celery workers)
      ↓
  Resource Optimization (ros-ocp-backend + Kruize)
      ↓
  Storage (PostgreSQL + Valkey)
```

Current Helm chart deploys all components with **conservative defaults** (single replicas, low resource limits) without validated sizing guidance.

---

## Known Bottlenecks

### From Existing Documentation

| Component | Role | Constraint | Reference |
|-----------|------|------------|-----------|
| **Koku Listener** | Processes uploaded CSV files | Single-threaded, serial Kafka consumer | `iqe-testing-setup.md` |
| **Celery Workers** | Background summarization tasks | Queue-based, depends on worker count | `resource-requirements.md` |
| **PostgreSQL** | Cost data storage & queries | Query complexity, connection pooling | `resource-requirements.md` |
| **Kafka** | Message queue for uploads | Topic partitions, broker count | `deploy-kafka.sh` |
| **Ingress** | File upload API | Upload size limits (100MB default) | `configuration.md` |

### Measured Performance (from test runs)

```
Listener Throughput:
- Processes files serially via single Kafka consumer
- CPU-bound (parquet conversion, SQL insertion)
- Default CPU limit throttles processing
- Boosting to 4 cores = ~40-50% faster processing

Source Processing Time:
- Small static source (1 month, no GPU): 30-60s
- Dynamic daily source (3 months, 6 CSVs/month): 2-5 min
```

---

## Scenario Definitions (from FLPATH-4036)

### Workload Density

OCP uploads cost data daily (24h reporting window, typically 288 intervals at 5-min granularity per pod).

### Row Count Formula

```
daily_rows = pods × 288 intervals/day × (pod_usage + storage_usage factor ~1.0)
monthly_rows = daily_rows × 30
upload_size = ~43 bytes/CSV row compressed at ~10:1 ratio
```

### Cluster Profiles (from Pau Garcia Quiles - Production Data April 2026)

Based on production metrics snapshot covering 417 active OCP accounts.
Resource sizing per profile is maintained in [sizing-guide.md](sizing-guide.md).

| Profile | % of Customers | Clusters | Nodes | CPU Cores | Memory | PVCs | Cost Models |
|---------|---------------|----------|-------|-----------|--------|------|-------------|
| **Small** | 37% | 1 | 15 | 200 | 1.1 TB | 48 | 1 (CPU dist) |
| **Medium** | 35% | 2 | 49 | 544 | 2.8 TB | 177 | 1 (CPU dist) |
| **Large** | 21% | 7 | 133 | 1,964 | 9.7 TB | 492 | 1-2 (CPU dist) |
| **Extra-Large** | 6% | 23 | 346 | 6,954 | 48.5 TB | 1,255 | 1-3 (CPU dist + tag rates) |

**Key Production Insights**:
- 39% of customers have single cluster, 72% have ≤4 clusters
- Control plane: universally 3 nodes per cluster
- Typical node: 16 cores / 63 GB RAM (median)
- 71% of accounts use cost models; adoption increases with size (100% at XL)
- CPU distribution is dominant cost model feature (58%); tag rates rare (9.4%)

**Stress/Edge-Case Values (P99/Max)**:
| Metric | P99 | Max |
|--------|-----|-----|
| Clusters | 33 | 67 |
| Nodes | 1,072 | 4,311 |
| CPU Cores | 57,424 | 793,424 |
| PVCs | 6,099 | 32,443 |
| Cost Models | 7 | 12 |

### Multi-Cluster Scenarios

Each cluster gets a unique `cluster_id` and `source_id`. Data generation produces separate payloads per cluster.

| Scenario | Clusters | Profile per Cluster | Total Pods | Notes |
|----------|----------|---------------------|------------|-------|
| Single Small | 1 | Small | TBD | Baseline |
| Multi Small | 5 | Small | TBD | Small fleet |
| Single Large | 1 | Large | TBD | Enterprise single |
| Multi Mixed | 10 | Mixed | TBD | Realistic fleet |

---

## Data Generation Design (from FLPATH-4036)

### Tooling: NISE (koku-nise)

NISE is the existing data generation tool used by E2E tests. It generates proper OCP cost CSVs with `manifest.json`.

### QE Approach: Leverage Existing Infrastructure

> **Note**: PR #144 proposes standalone bash scripts (`generate-test-data.sh`, `upload-test-data.sh`). From a QE/test automation perspective, we should leverage the **existing pytest/NISE integration** rather than creating parallel tooling:

| Existing Infrastructure | Location | Use For Performance Tests |
|------------------------|----------|---------------------------|
| `NISEConfig` class | `tests/e2e_helpers.py` | Configure scenario parameters |
| `generate_nise_data()` | `tests/e2e_helpers.py` | Generate test data |
| `upload_with_retry()` | `tests/e2e_helpers.py` | Upload to ingress |
| `register_source()` | `tests/e2e_helpers.py` | Source registration |
| `create_upload_package_from_files()` | `tests/utils.py` | Package payloads |
| NISE templates | `tests/data/nise_templates/` | Scenario definitions |

**Recommended approach**:
1. Create scenario YAML files for S1-S11, M1-M6 profiles
2. Use pytest fixtures to generate and upload data
3. Use `@pytest.mark.performance` marker for performance tests
4. Integrate with existing CI/reporting infrastructure

This ensures:
- Consistent tooling across functional and performance tests
- JWT auth handled by existing fixtures
- JUnit XML output for CI integration
- No duplication of NISE/upload logic

### Static Report Configuration

NISE static report YAML defines workloads. Scenario profiles (S1-S11) are being generated as part of PR #144 under `scripts/perf/scenarios/`.

### Upload Frequency Simulation

| Pattern | Description | Use Case |
|---------|-------------|----------|
| Real-world | Every 6 hours (4x/day) | Baseline validation |
| Accelerated | Every 1 minute | Simulate backlog processing |
| Spike | 24 uploads simultaneously | Simulate 6 days of backlog |

---

## Test Categories

### 1. Ingestion Throughput Tests

**Goal**: Measure data ingestion capacity under various loads.

| ID | Test Case | Description | Metrics |
|----|-----------|-------------|---------|
| PERF-ING-001 | Single source baseline | 1 source, 1 month data, default config | Time to complete, listener CPU% |
| PERF-ING-002 | Single source burst | 1 source, 3 months data (90 days), max listener CPU | Time to complete, throughput MB/s |
| PERF-ING-003 | Concurrent uploads | N sources uploading simultaneously | Queue depth, time to complete all |
| PERF-ING-004 | Large file upload | Single 50MB+ payload | Upload time, processing time |
| PERF-ING-005 | High frequency uploads | Upload every 5 min for 1 hour | Message queue lag, error rate |

**Variables**:
- Listener CPU allocation: 150m (default) vs 1000m vs 4000m (max)
- Data volume: 1 month vs 3 months (90 days - current test boundary)
- Concurrent sources: 1, 5, 10, 20

### 2. Multi-Cluster Scale Tests

**Goal**: Determine limits for number of managed OCP clusters/sources.

| ID | Test Case | Description | Metrics |
|----|-----------|-------------|---------|
| PERF-SCALE-001 | Source count baseline | 5 sources, steady state | Memory usage, API latency |
| PERF-SCALE-002 | Source count ramp | Add sources until degradation | Max sources, breaking point |
| PERF-SCALE-003 | Large source dataset | 1 source, 100+ namespaces | Query time, memory pressure |
| PERF-SCALE-004 | Concurrent API queries | N parallel report requests | P50/P95/P99 latency |
| PERF-SCALE-005 | Historical data depth | 3+ months data, various query ranges | Query time vs date range |

**Variables**:
- Number of sources: 1, 5, 10, 25, 50, 100
- Namespaces per cluster: 10, 50, 100, 500
- Pods per namespace: 10, 50, 100
- Data retention period: 1, 3 months (90 days is current test boundary; retention policy TBD)

### 3. API Latency Tests

**Goal**: Measure API response times under load.

| ID | Test Case | Description | Metrics |
|----|-----------|-------------|---------|
| PERF-API-001 | Report API baseline | Single report query, no load | Response time |
| PERF-API-002 | Report API under load | 10 concurrent report queries | P50/P95/P99 |
| PERF-API-003 | Cost model CRUD | Create/read/update/delete cycle | Operations/sec |
| PERF-API-004 | Source list pagination | List 100+ sources with pagination | Time per page |
| PERF-API-005 | Complex group-by query | Multi-dimension grouping | Query time |
| PERF-API-006 | Tag filtering | Filter by N tags | Query time vs tag count |

### 4. ROS/Kruize Performance Tests

**Goal**: Validate resource optimization recommendation pipeline.

| ID | Test Case | Description | Metrics |
|----|-----------|-------------|---------|
| PERF-ROS-001 | Recommendation baseline | Single workload, 15 min data | Time to recommendation |
| PERF-ROS-002 | Multi-workload scale | 50 workloads concurrently | Memory usage, queue depth |
| PERF-ROS-003 | Recommendation refresh | Update existing recommendations | Refresh time |
| PERF-ROS-004 | Kruize memory pressure | High workload count | Kruize heap usage |

### 5. Soak Testing (SC-5)

**Goal**: Validate 7-day stability.

| ID | Test Case | Description | Success Criteria |
|----|-----------|-------------|------------------|
| PERF-SOAK-001 | 7-day continuous operation | Normal upload pattern, query load | No OOM |
| PERF-SOAK-002 | Memory leak detection | Monitor memory growth over time | < 5% growth/day |
| PERF-SOAK-003 | Disk usage | Monitor PostgreSQL, Kafka storage | No exhaustion |
| PERF-SOAK-004 | Queue health | Monitor Celery/Kafka queue depths | No starvation |

---

## Metrics Collection (from FLPATH-4036)

### Observability Stack Requirements

- **Prometheus** with 15-second scrape interval, 30-day retention
- **Grafana** with pre-built dashboards per component

### Metrics by Layer

| Layer | Metrics |
|-------|---------|
| **Ingress** | Upload count, size, latency, error rate |
| **Kafka** | Consumer lag, message throughput, partition health |
| **Listener** | Files processed/min, CPU utilization, processing time |
| **Celery** | Queue lengths, task completion times, worker utilization |
| **PostgreSQL** | Connections, query time, disk usage, cache hit ratio |
| **Kruize** | Heap usage, recommendation latency, experiment count |
| **API** | Request count, latency histogram (P50/P95/P99), error rate |

### Prometheus Queries

```promql
# Listener CPU usage
rate(container_cpu_usage_seconds_total{pod=~".*listener.*"}[5m])

# Celery queue length
celery_queue_length{queue="ocp"}

# API request latency
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket{job="koku-api"}[5m]))
```

---

## Testing Phases (from FLPATH-4036)

### Phase 1: Baseline Establishment

1. Deploy cost-onprem with default configuration
2. Run single-source ingestion test (PERF-ING-001)
3. Run API latency baseline (PERF-API-001)
4. Capture resource utilization baseline

### Phase 2: Optimization Testing

1. Test listener CPU boost impact (PERF-ING-002)
2. Establish optimal listener CPU setting
3. Document throughput improvements

### Phase 3: Scale Testing

1. Incrementally add sources (PERF-SCALE-001, 002)
2. Test with large datasets (PERF-SCALE-003)
3. Identify breaking points and limits

### Phase 4: Load Testing

1. Concurrent API load (PERF-API-002, 003)
2. Sustained ingestion load (PERF-ING-005)
3. Combined load scenarios

### Phase 5: Soak Testing (SC-5)

1. 7-day continuous operation test
2. Monitor for OOM, disk exhaustion, queue starvation
3. Document steady-state resource usage

---

## Test Infrastructure Requirements

### Cluster Sizing for Performance Tests

| Tier | Workers | CPU/Worker | Memory/Worker | Storage | Use Case |
|------|---------|------------|---------------|---------|----------|
| Small | 3 | 4 cores | 16 Gi | 200 Gi | Baseline tests |
| Medium | 5 | 8 cores | 32 Gi | 500 Gi | Scale tests |
| Large | 7+ | 16 cores | 64 Gi | 1 Ti | Limit testing |

### Storage Requirements

- **ODF recommended** for shared storage (S3, PVCs)
- **Minimum**: 500 Gi for extended scale tests
- **Network**: 10 Gbps between workers for Kafka/DB traffic

### Resource Baseline (from `resource-requirements.md`)

| Configuration | Pods | CPU Request | Memory Request |
|---------------|------|-------------|----------------|
| OCP-only (default) | 27 | ~8.4 cores | ~19.4 Gi |
| High Availability | 30+ | ~12+ cores | ~24+ Gi |

---

## Deliverables

### Documentation (SC-1, SC-2, SC-3)

- [ ] Sizing table (cluster profiles → resource requirements)
- [ ] Max cluster count per deployment size
- [ ] Top-3 bottlenecks with mitigations
- [ ] Tuning recommendations

### Automation

- [ ] Performance test suite (pytest markers: `@pytest.mark.performance`)
- [ ] NISE data generation scripts per scenario
- [ ] Metrics collection automation
- [ ] Grafana dashboards

### Validation (SC-4, SC-5)

- [ ] 6-hour processing window validation
- [ ] 7-day soak test results

---

## Dependencies

### Pending Information

| Item | Status | Notes |
|------|--------|-------|
| **Customer size profiles** | ✅ Complete | See Section 3 - profiles from Pau's production analysis |
| **Data retention policy** | TBD | Separate investigation needed |
| Latency SLOs | TBD | Acceptable response times |
| Rate counts per cost model | Unknown | Not in production data (requires DB query) |

---

## Related Documentation

### In Repository

- `docs/operations/resource-requirements.md` - Resource allocations
- `docs/development/iqe-testing-setup.md` - Test performance analysis
- `.cursor/prompts/analyze-test-run.md` - Test run analysis guide
- `tests/suites/e2e/README.md` - E2E test scenarios
- `tests/e2e_helpers.py` - NISE integration (NISEConfig, generate_nise_data, upload_with_retry)
- `tests/utils.py` - Upload package creation utilities

### External

- [FLPATH-4036](https://redhat.atlassian.net/browse/FLPATH-4036) - Epic with full details
- [PR #144](https://github.com/insights-onprem/cost-onprem-chart/pull/144) - Performance tuning plan and handover doc
- [IQE Cost Management Plugin](https://gitlab.cee.redhat.com/insights-qe/iqe-cost-management-plugin) - SaaS test suite
- [Koku Repository](https://github.com/project-koku/koku) - Backend source

---

## Open Questions

1. **What is the data retention policy for on-prem?**
   - Current test boundary: 90 days
   - Production retention requirements TBD
   - Affects storage sizing and query performance

2. **What are acceptable API latencies?**
   - Need to define P50/P95/P99 targets

3. **Should we test disconnected/air-gapped scenarios?**
   - Different performance characteristics
   - No external S3, local storage only

4. **What monitoring/observability is required in production?**
   - Prometheus/Grafana integration
   - Performance dashboards

---

## Implementation Status

### Completed

- [x] Customer size profiles received from Pau Garcia Quiles
- [x] Performance test marker registered (`@pytest.mark.performance`)
- [x] Profile definitions created (`tests/suites/performance/profiles.py`)
- [x] Test fixtures implemented (`tests/suites/performance/conftest.py`)
- [x] Ingestion throughput tests (PERF-ING-001 through PERF-ING-006)
- [x] API latency tests (PERF-API-001 through PERF-API-006)
- [x] Multi-cluster scale tests (PERF-SCALE-001 through PERF-SCALE-005)
- [x] ROS/Kruize performance tests (PERF-ROS-001 through PERF-ROS-004)
- [x] Soak test stubs (PERF-SOAK-001 through PERF-SOAK-004, opt-in via `SOAK_TESTS=true`)
- [x] Observability stack (FLPATH-4061 / COST-7625) — metrics collection, HTML reports, S3 archival
- [x] Profile-aware resource tuning (`apply_perf_profile_config()` in `perf-testing.sh`)
- [x] Jenkins CI integration (`insights_onprem.groovy` with `PERF_PROFILE` and `PERF_SUITE` params)
- [x] Self-contained HTML run reports and JSON summaries
- [x] S3 result archival to shared MinIO

### Validated Profiles

| Profile | Run ID | Tests | Result | Duration | Key Metric |
|---------|--------|-------|--------|----------|------------|
| baseline | multiple | 41 | PASS | ~5 min | Smoke test |
| xlarge | 1782335628 | 41 | **41 passed, 0 failed** | 123 min | 14+ MB/s upload, 31 exp/min Kruize |

### Test Files

| File | Tests | Description |
|------|-------|-------------|
| `test_ingestion.py` | 6 | Ingestion throughput (ING-001 through ING-006) |
| `test_api_latency.py` | 6 | API latency (API-001 through API-006) |
| `test_scale.py` | 5 | Multi-cluster scale (SCALE-001 through SCALE-005) |
| `test_ros.py` | 4 | ROS/Kruize performance (ROS-001 through ROS-004) |
| `test_soak.py` | 4 | Soak stability (SOAK-001 through SOAK-004, opt-in) |
| `profiles.py` | — | Profile definitions + NISE YAML generation |
| `conftest.py` | — | Fixtures, cleanup, data generation |
| `verify_infrastructure.py` | — | Pre-run infrastructure validation |

### Running Performance Tests

```bash
# Via the deploy script (recommended — handles profile config, metrics, S3 upload)
./scripts/deploy-test-cost-onprem.sh \
    --skip-deploy --perf-only \
    --perf-profile xlarge --perf-suite all \
    --listener-cpu max --collect-metrics --upload-metrics

# Direct pytest (requires manual profile config)
PERF_PROFILE=medium pytest -m performance tests/suites/performance/

# Specific suite
PERF_PROFILE=xlarge pytest -m "performance and ingestion" tests/suites/performance/

# Via Jenkins
# Job: flightpath-insights-onprem (RUN_PERF_TESTS=true, PERF_PROFILE=xlarge, PERF_SUITE=all)
```

---

## Success Criteria Status

| ID | Criteria | Status | Evidence |
|----|----------|--------|----------|
| SC-1 | Sizing table | **Done** | [sizing-guide.md](./sizing-guide.md) — small through xlarge validated |
| SC-2 | Cluster count limits | **Partial** | XLarge (23 clusters) validated; stress profiles (33+) not yet tested |
| SC-3 | Bottleneck analysis | **Done** | [FINDINGS.md](./FINDINGS.md) — 13 findings documented with severity and evidence |
| SC-4 | Processing window | **Partial** | XLarge completes in ~2h; need to validate against 6-hour SLA formally |
| SC-5 | Soak test | **Not started** | Tests exist but require `SOAK_TESTS=true` and dedicated 7-day run window |

## Next Steps

1. [ ] Run medium profile for a clean validated baseline (target: 0 failures)
2. [ ] Execute stress_p99 profile (33 clusters) to find the actual breaking point
3. [ ] Execute 7-day soak test (SC-5) — requires dedicated cluster time
4. [ ] Publish sizing guide to product documentation (COST-7618)
5. [ ] File tickets for untracked findings (FINDING-013, -020, -022, -024)
