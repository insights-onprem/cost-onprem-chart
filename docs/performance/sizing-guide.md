# Cost On-Prem Sizing Guide

Sizing recommendations for Cost On-Prem deployments based on performance testing
results (FLPATH-4036, COST-7567). Profile definitions are derived from production
data analysis by Pau Garcia Quiles (April 2026) and validated through automated
performance runs on a 3-worker OCP 4.20 cluster (54 CPU / 183 Gi).

## Quick Reference

| Profile | Clusters | Nodes | CPU Cores | Memory | % of Customers |
|---------|----------|-------|-----------|--------|----------------|
| Small | 1 | 15 | ~200 | ~1.1 TB | 37% |
| Medium | 2 | ~49 | ~544 | ~2.8 TB | 35% |
| Large | 7 | ~133 | ~1,964 | ~9.7 TB | 21% |
| XLarge | 23 | ~346 | ~6,954 | ~48.5 TB | 6% |
| Stress (P99) | 33 | ~1,072 | ~57,424 | ~137 TB | <1% |

**Validation status**: Small, medium, large, and xlarge profiles have been validated
with clean (0-failure) automated runs. Stress profiles have not yet been executed.

---

## Component Resource Recommendations

CPU, memory, and replica values for core pipeline components are applied
dynamically by `apply_perf_profile_config()` during performance test runs.
Kruize memory and Database resources should be set in `values.yaml` at
deployment time. All values have been validated through successful end-to-end
test suites at each profile level.

### Kruize (ROS Optimization Engine)

| Profile | Replicas | CPU Request | CPU Limit | Memory Request | Memory Limit |
|---------|----------|-------------|-----------|----------------|--------------|
| Small | 1 | 500m | 2000m | chart default | chart default |
| Medium | 1 | 500m | 2000m | chart default | chart default |
| Large | 1 | 500m | 2000m | 2Gi (rec.) | 4Gi (rec.) |
| XLarge | 1 | 1000m | 2000m | 2Gi (rec.) | 4Gi (rec.) |

Kruize memory is not dynamically adjusted by `apply_perf_profile_config()`.
For large/xlarge workloads, 2Gi/4Gi is recommended based on observed heap
pressure during experiment creation.

**Key finding (PERF-FINDING-004, PERF-FINDING-006)**: Kruize throughput is
CPU-bound, not connection-pool-bound. At the chart default of 500m/1000m,
experiment creation runs at ~8/min. At 1000m/2000m (xlarge), it reaches
**31 exp/min** — a 4x improvement. The CPU limit should always be at least
2000m to prevent liveness probe failures under load.

Always run Kruize as a **single replica** — scaling replicas increases DB
contention and degrades throughput.

### Koku Listener (Ingestion)

| Profile | Replicas | CPU Request | CPU Limit | Memory Request | Memory Limit |
|---------|----------|-------------|-----------|----------------|--------------|
| Small | 2 | 150m | 300m | 300Mi | 600Mi |
| Medium | 2 | 150m | 300m | 1Gi | 2Gi |
| Large | 3 | 150m | 300m | 2Gi | 4Gi |
| XLarge | 3 | 150m | 300m | 2Gi | 4Gi |

**Key finding (PERF-FINDING-002)**: The listener is the **first-to-saturate
component** at every scale. At the 300m CPU limit, it runs at 157% throttled
during medium-profile ingestion. The `--listener-cpu max` flag in the deploy
script dynamically boosts CPU to all available node headroom during perf runs.
Production deployments handling burst ingestion should raise the CPU limit to
at least 1000m. Listener CPU is managed separately via the `--listener-cpu`
flag and is not part of `apply_perf_profile_config()`.

### Celery Workers

#### OCP Worker (Cost Processing)

| Profile | Replicas | CPU Request | CPU Limit | Memory Request | Memory Limit |
|---------|----------|-------------|-----------|----------------|--------------|
| Small | 2 | 250m | 500m | 512Mi | 1Gi |
| Medium | 2 | 250m | 1000m | 512Mi | 2Gi |
| Large | 3 | 500m | 1000m | 1Gi | 2Gi |
| XLarge | 3 | 1000m | 2000m | 2Gi | 4Gi |

#### Summary Worker

| Profile | Replicas | CPU Request | CPU Limit | Memory Request | Memory Limit |
|---------|----------|-------------|-----------|----------------|--------------|
| Small | 2 | 250m | 500m | chart default | chart default |
| Medium | 2 | 250m | 1000m | chart default | chart default |
| Large | 3 | 500m | 1000m | chart default | chart default |
| XLarge | 3 | 1000m | 2000m | chart default | chart default |

Summary worker memory is not dynamically adjusted — the chart default is
sufficient for all validated profiles.

**Key finding (PERF-FINDING-025)**: Boosting worker CPU from 250m/500m to
500m/1000m reduced end-to-end processing time by 15% on the large profile
(112 min → 97.6 min). XLarge uses 1000m/2000m to handle tag-based cost model
processing.

### ROS Processor

| Profile | Replicas | Memory Request | Memory Limit |
|---------|----------|----------------|--------------|
| Small | 1 | 1Gi | 1Gi |
| Medium | 2 | 2Gi | 4Gi |
| Large | 3 | 2Gi | 4Gi |
| XLarge | 3 | 2Gi | 4Gi |

### Ingress (Upload Service)

| Profile | Max Upload Size | Max Upload Memory | App Memory Req/Lim |
|---------|-----------------|-------------------|--------------------|
| Small | 100 MB (default) | 32 MB (default) | 1Gi / 1Gi |
| Medium | 200 MB | 64 MB | 1Gi / 2Gi |
| Large | 500 MB | 128 MB | 2Gi / 4Gi |
| XLarge | 500 MB | 128 MB | 2Gi / 4Gi |

**Key finding (PERF-FINDING-022)**: The ingress pod shares the
`resources.application` memory block with other services. Large/concurrent
uploads cause OOM at the 1Gi default. Raising to 2Gi/4Gi for large profiles
resolves this, but a dedicated `resources.ingress` block would be cleaner.

**Key finding (PERF-FINDING-024)**: `insights-ingress-go` uses single-part
S3 uploads which fail for payloads >150 MB. This is an upstream code limitation,
not a resource constraint.

### Database (PostgreSQL)

| Profile | CPU Request | CPU Limit | Memory Request | Memory Limit | Storage |
|---------|-------------|-----------|----------------|--------------|---------|
| Small | 500m | 2000m | 1Gi | 4Gi | 10Gi |
| Medium | 1000m | 4000m | 2Gi | 8Gi | 50Gi |
| Large | 2000m | 4000m | 4Gi | 16Gi | 100Gi |
| XLarge | 4000m | 8000m | 8Gi | 32Gi | 200Gi |

Database resources are not dynamically adjusted by `apply_perf_profile_config()` —
they should be set via `values.yaml` at deployment time.

**Empirical validation (COST-7605)**: CPU and memory sweeps at medium and large profiles
confirm these recommendations are well-positioned. API query latency is sub-10ms at all
tested CPU levels (2000m–8000m), with diminishing returns above 4000m (PERF-FINDING-027).
Memory beyond 4Gi provides no latency benefit for current workloads since the dataset
fits entirely in shared_buffers at 1GB (PERF-FINDING-028). Larger memory allocations
are recommended for extended retention periods where the working set may exceed the
buffer pool.

---

## Gateway Configuration

### Timeout Settings

The default gateway timeout (30s) is insufficient for medium and larger profiles.
These are applied by `apply_perf_profile_config()` during perf runs and should
be set in `values.yaml` for production deployments.

| Profile | HAProxy Timeout | Envoy Route Timeout | Envoy Per-Try Timeout |
|---------|-----------------|---------------------|----------------------|
| Small | 30s (default) | 30s (default) | 30s (default) |
| Medium | 180s | 180s | 180s |
| Large | 600s | 600s | 300s |
| XLarge | 600s | 600s | 300s |

**Configuration** (`values.yaml`):

```yaml
gateway:
  envoy:
    routes:
      ingress:
        timeout: 600s
        per_try_timeout: 300s

gatewayRoute:
  annotations:
    haproxy.router.openshift.io/timeout: "600s"
```

**Key finding (PERF-FINDING-001, PERF-FINDING-020)**: After `helm upgrade`,
the Envoy gateway pod must be restarted for timeout changes to take effect.
Envoy reads config at startup only. A `checksum/envoy-config` pod annotation
is recommended (see FINDING-020).

---

## Performance Baselines (Validated)

### Upload Throughput

| File Size | Profile | Upload Throughput | Processing Time | Total Time |
|-----------|---------|-------------------|-----------------|------------|
| ~13 KB | baseline | — | <1s | <1s |
| ~48 MB | medium | 1-2 MB/s | 5-10 min | 6-11 min |
| ~67 MB | large | 1.1-1.9 MB/s | 7-20 min | 8-22 min |
| ~70 MB | xlarge | 14.5 MB/s | 31s | ~3 min |
| ~102 MB | xlarge | 14.2 MB/s | 31s | ~3 min |

The xlarge throughput improvement (14+ MB/s vs 1-3 MB/s) is from the higher
worker CPU allocation (1000m/2000m).

### API Response Times (P95)

| Endpoint | Small | Medium | Large/XLarge | Notes |
|----------|-------|--------|--------------|-------|
| GET /sources/ | < 500ms | < 500ms | < 1s | List all sources |
| GET /reports/openshift/costs/ | < 2s | < 3s | < 5s | Monthly aggregation |
| GET /reports/openshift/costs/ (group_by) | < 5s | < 10s | < 25s | Complex queries |
| GET /recommendations/openshift | < 2s | < 2s | < 3s | ROS recommendations |

### Kruize Experiment Creation

| Profile | CPU Req/Lim | Workloads | Rate (exp/min) | Peak Memory | Restarts |
|---------|-------------|-----------|----------------|-------------|----------|
| Medium | 500m/1000m | 160 | 8.6 | 723 MB | 0 |
| XLarge | 1000m/2000m | 600 | 31.0 | 415 MB | 0 |

### Concurrent Upload Scaling

| Concurrent Sources | Replicas (listener/ocp/summary) | Profile | Result |
|--------------------|--------------------------------|---------|--------|
| 2 | 1/1/1 | baseline | PASS |
| 5 | 2/2/2 | medium | PASS |
| 10 | 2/2/2 | medium | PASS |
| 10 | 3/3/3 | xlarge | PASS |

---

## Storage Requirements

### PostgreSQL Storage

| Profile | Initial Size | 30-day Retention | 90-day Retention |
|---------|--------------|------------------|------------------|
| Small | 5 GB | 10-15 GB | 30-45 GB |
| Medium | 10 GB | 25-40 GB | 75-120 GB |
| Large | 25 GB | 75-125 GB | 225-375 GB |
| XLarge | 50 GB | 150-250 GB | 450-750 GB |

### S3/MinIO Storage

| Component | Small | Medium | Large | XLarge |
|-----------|-------|--------|-------|--------|
| koku-bucket | 10 GB | 50 GB | 100 GB | 250 GB |
| insights-upload-perma | 5 GB | 10 GB | 20 GB | 50 GB |
| ros-data | 2 GB | 5 GB | 10 GB | 25 GB |

### Kafka Storage

| Profile | Retention Period | Recommended Storage |
|---------|------------------|---------------------|
| Small | 7 days | 10 GB |
| Medium | 7 days | 10 GB |
| Large | 7 days | 10 GB |
| XLarge | 7 days | 10 GB |

10 Gi per broker is sufficient for all validated profiles (through xlarge).
The default 100 Gi is sized for sustained multi-day production ingestion.
For perf testing clusters, 10 Gi saves 270 Gi of ODF PV capacity.

### Kafka Broker Resources (COST-7638)

| Tier | Brokers | CPU Req/Limit | Memory Req/Limit | Disk | Platform Partitions | Announce Partitions |
|------|---------|---------------|-------------------|------|---------------------|---------------------|
| Small | 1 | 250m/1 | 1Gi/2Gi | 20Gi | 1 | 1 |
| Medium | 1 | 500m/2 | 2Gi/4Gi | 50Gi | 3 | 3 |
| Large | 3 | 1/2 | 2Gi/4Gi | 100Gi | 6 | 6 |
| XLarge | 3 | 2/4 | 4Gi/8Gi | 200Gi | 12 | 12 |

> **Note**: Single-broker tiers (Small/Medium) are validated — PERF-KAF-001
> confirmed zero consumer lag through 30 concurrent sources on a single broker
> (FINDING-029). The multi-broker recommendations for Large/XLarge provide
> availability, not throughput, and require a cluster with multiple Kafka replicas
> to validate HA behavior.

---

## Helm Values Examples

### Small Profile

```yaml
resources:
  kruize:
    requests: { cpu: "500m" }
    limits:   { cpu: "2000m" }

costManagement:
  listener:
    replicas: 2
  celeryWorker:
    workers:
      ocp:
        replicas: 2
      summary:
        replicas: 2
```

### Medium Profile

```yaml
resources:
  kruize:
    requests: { cpu: "500m" }
    limits:   { cpu: "2000m" }
  rosProcessor:
    requests: { memory: "2Gi" }
    limits:   { memory: "4Gi" }
  application:
    requests: { memory: "1Gi" }
    limits:   { memory: "2Gi" }

costManagement:
  listener:
    replicas: 2
    resources:
      requests: { memory: "1Gi" }
      limits:   { memory: "2Gi" }
  celeryWorker:
    workers:
      ocp:
        replicas: 2
        resources:
          requests: { cpu: "250m" }
          limits:   { cpu: "1000m" }
      summary:
        replicas: 2
        resources:
          requests: { cpu: "250m" }
          limits:   { cpu: "1000m" }

ros:
  processor:
    replicas: 2

ingress:
  upload:
    maxSize: "200MB"
    maxMemory: "64MB"

gateway:
  envoy:
    routes:
      ingress:
        timeout: 180s
        per_try_timeout: 180s
gatewayRoute:
  annotations:
    haproxy.router.openshift.io/timeout: "180s"
```

### Large Profile

```yaml
resources:
  kruize:
    requests: { cpu: "500m" }
    limits:   { cpu: "2000m" }
  rosProcessor:
    requests: { memory: "2Gi" }
    limits:   { memory: "4Gi" }
  application:
    requests: { memory: "2Gi" }
    limits:   { memory: "4Gi" }

costManagement:
  listener:
    replicas: 3
    resources:
      requests: { memory: "2Gi" }
      limits:   { memory: "4Gi" }
  celeryWorker:
    workers:
      ocp:
        replicas: 3
        resources:
          requests: { cpu: "500m" }
          limits:   { cpu: "1000m", memory: "2Gi" }
      summary:
        replicas: 3
        resources:
          requests: { cpu: "500m" }
          limits:   { cpu: "1000m" }

ros:
  processor:
    replicas: 3

ingress:
  upload:
    maxSize: "500MB"
    maxMemory: "128MB"

gateway:
  envoy:
    routes:
      ingress:
        timeout: 600s
        per_try_timeout: 300s
gatewayRoute:
  annotations:
    haproxy.router.openshift.io/timeout: "600s"
```

### XLarge Profile

```yaml
resources:
  kruize:
    requests: { cpu: "1000m" }
    limits:   { cpu: "2000m" }
  rosProcessor:
    requests: { memory: "2Gi" }
    limits:   { memory: "4Gi" }
  application:
    requests: { memory: "2Gi" }
    limits:   { memory: "4Gi" }

costManagement:
  listener:
    replicas: 3
    resources:
      requests: { memory: "2Gi" }
      limits:   { memory: "4Gi" }
  celeryWorker:
    workers:
      ocp:
        replicas: 3
        resources:
          requests: { cpu: "1000m" }
          limits:   { cpu: "2000m", memory: "4Gi" }
      summary:
        replicas: 3
        resources:
          requests: { cpu: "1000m" }
          limits:   { cpu: "2000m" }

ros:
  processor:
    replicas: 3

ingress:
  upload:
    maxSize: "500MB"
    maxMemory: "128MB"

gateway:
  envoy:
    routes:
      ingress:
        timeout: 600s
        per_try_timeout: 300s
gatewayRoute:
  annotations:
    haproxy.router.openshift.io/timeout: "600s"
```

---

## Known Limitations

- **Single-part S3 upload ceiling** (PERF-FINDING-024): Uploads >150 MB fail
  against NooBaa/Ceph. Upstream `insights-ingress-go` enhancement needed.
- **Gateway ConfigMap not hot-reloaded** (PERF-FINDING-020): `helm upgrade`
  timeout changes require gateway pod restart.
- **Ingress shares `resources.application`** (PERF-FINDING-022): Cannot size
  ingress memory independently from other services.
- **Stress profiles (P99, max) not yet validated**: Profiles beyond xlarge
  have not been tested.

---

## Related

- [Performance Testing Plan](./performance-testing-plan.md) — test methodology and profiles
- [FINDINGS.md](./FINDINGS.md) — detailed product findings and evidence
- [TEST-MATRIX.md](./TEST-MATRIX.md) — test coverage matrix
- [OBSERVABILITY.md](./OBSERVABILITY.md) — metrics collection infrastructure

---

_Based on FLPATH-4036 / COST-7567 performance testing. Last updated: 2026-06-25._
