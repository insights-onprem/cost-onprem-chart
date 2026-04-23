# Cost On-Prem Sizing Guide

This document provides sizing recommendations for Cost On-Prem deployments based on performance testing results (FLPATH-4036).

## Quick Reference

| Profile | Clusters | Nodes | CPU Cores | Memory | % of Customers |
|---------|----------|-------|-----------|--------|----------------|
| Small | 1 | 15 | ~200 | ~1.1 TB | 37% |
| Medium | 2 | ~49 | ~544 | ~2.8 TB | 35% |
| Large | 7 | ~133 | ~1,964 | ~9.7 TB | 21% |
| XLarge | 23 | ~346 | ~6,954 | ~48.5 TB | 6% |
| Stress (P99) | 33 | ~1,072 | ~57,424 | ~137 TB | <1% |

---

## Component Resource Recommendations

### Database (PostgreSQL)

| Profile | CPU Request | CPU Limit | Memory Request | Memory Limit | Storage |
|---------|-------------|-----------|----------------|--------------|---------|
| Small | 500m | 2000m | 1Gi | 4Gi | 10Gi |
| Medium | 1000m | 4000m | 2Gi | 8Gi | 50Gi |
| Large | 2000m | 4000m | 4Gi | 16Gi | 100Gi |
| XLarge | 4000m | 8000m | 8Gi | 32Gi | 200Gi |

**PostgreSQL Configuration** (validated settings):
- `work_mem`: 4MB (default, consider increasing for complex queries)
- `shared_buffers`: 128MB (increase to 256MB+ for Large/XLarge)
- `effective_cache_size`: Set to 75% of available memory
- `log_min_duration_statement`: 1000 (log queries >1s for monitoring)

### Koku API

| Profile | Replicas | CPU Request | CPU Limit | Memory Request | Memory Limit |
|---------|----------|-------------|-----------|----------------|--------------|
| Small | 1 | 500m | 1000m | 1Gi | 2Gi |
| Medium | 2 | 500m | 1000m | 1Gi | 2Gi |
| Large | 2 | 1000m | 2000m | 2Gi | 4Gi |
| XLarge | 3 | 1000m | 2000m | 2Gi | 4Gi |

### Celery Workers

#### OCP Worker (Cost Processing)

| Profile | Replicas | Concurrency | CPU Request | CPU Limit | Memory Request | Memory Limit |
|---------|----------|-------------|-------------|-----------|----------------|--------------|
| Small | 1 | 5 | 250m | 500m | 512Mi | 1Gi |
| Medium | 1 | 5 | 500m | 1000m | 512Mi | 1Gi |
| Large | 2 | 5 | 500m | 1000m | 512Mi | 1Gi |
| XLarge | 3 | 10 | 500m | 1000m | 1Gi | 2Gi |

#### Summary Worker

| Profile | Replicas | Concurrency | CPU Request | CPU Limit | Memory Request | Memory Limit |
|---------|----------|-------------|-------------|-----------|----------------|--------------|
| Small | 1 | 5 | 250m | 500m | 512Mi | 1Gi |
| Medium | 1 | 5 | 500m | 1000m | 512Mi | 1Gi |
| Large | 2 | 5 | 500m | 1000m | 512Mi | 1Gi |
| XLarge | 3 | 10 | 500m | 1000m | 1Gi | 2Gi |

### Listener (Ingestion)

| Profile | Replicas | CPU Request | CPU Limit | Memory Request | Memory Limit |
|---------|----------|-------------|-----------|----------------|--------------|
| Small | 1 | 500m | 1000m | 1Gi | 1Gi |
| Medium | 1 | 500m | 1000m | 1Gi | 1Gi |
| Large | 2 | 500m | 1000m | 1Gi | 1Gi |
| XLarge | 2 | 1000m | 2000m | 2Gi | 2Gi |

### Kruize (ROS)

| Profile | Replicas | CPU Request | CPU Limit | Memory Request | Memory Limit |
|---------|----------|-------------|-----------|----------------|--------------|
| Small | 1 | 500m | 1000m | 1Gi | 2Gi |
| Medium | 1 | 500m | 1000m | 1Gi | 2Gi |
| Large | 1 | 1000m | 2000m | 2Gi | 4Gi |
| XLarge | 1 | 1000m | 2000m | 2Gi | 4Gi |

### Ingress (Upload Service)

| Profile | Replicas | CPU Request | CPU Limit | Memory Request | Memory Limit |
|---------|----------|-------------|-----------|----------------|--------------|
| Small | 1 | 100m | 500m | 256Mi | 512Mi |
| Medium | 1 | 200m | 500m | 256Mi | 512Mi |
| Large | 2 | 200m | 500m | 512Mi | 1Gi |
| XLarge | 2 | 500m | 1000m | 512Mi | 1Gi |

---

## Gateway Configuration

### Timeout Settings

The default gateway timeout (30s) is insufficient for large data uploads. Configure based on expected data volume:

| Data Volume | Recommended Timeout | Notes |
|-------------|---------------------|-------|
| < 2 weeks | 30s (default) | Sufficient for small uploads |
| 2-4 weeks | 120s | ~48MB packages |
| 30 days | 180s | ~48MB packages |
| 60 days | 300s | ~95MB packages |

**Configuration** (`values.yaml`):

```yaml
gateway:
  envoy:
    routes:
      ingress:
        timeout: 300s        # For large uploads
        per_try_timeout: 120s

gatewayRoute:
  annotations:
    haproxy.router.openshift.io/timeout: "300s"
```

---

## Concurrency Guidelines

Based on performance testing (PERF-ING-003), the following configurations are recommended for concurrent upload scenarios:

| Concurrent Sources | Listener Replicas | OCP Worker Replicas | Summary Worker Replicas | Expected Processing Time |
|-------------------|-------------------|---------------------|------------------------|-------------------------|
| 1-5 | 1 | 1 | 1 | 4-8 minutes |
| 6-10 | 2 | 2 | 2 | 7-10 minutes |
| 11-20 | 2 | 3 | 3 | 10-15 minutes |
| 20+ | 3 | 4+ | 4+ | Scale testing recommended |

**Example high-concurrency configuration**:

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
| Medium | 7 days | 25 GB |
| Large | 7 days | 50 GB |
| XLarge | 7 days | 100 GB |

---

## Performance Baselines

### Upload Throughput

| File Size | Expected Duration | Throughput |
|-----------|-------------------|------------|
| ~13 KB (minimal) | 330ms | N/A |
| ~48 MB (30 days) | 25-50 seconds | ~1-2 MB/s |
| ~95 MB (60 days) | 60-90 seconds | ~1-1.5 MB/s |

### API Response Times (P95)

| Endpoint | Small Profile | Large Profile | Notes |
|----------|---------------|---------------|-------|
| GET /sources/ | < 500ms | < 1s | List all sources |
| GET /reports/openshift/costs/ | < 2s | < 5s | Monthly aggregation |
| GET /reports/openshift/costs/ (3 group_by) | < 5s | < 10s | Complex queries |
| GET /recommendations/openshift | < 2s | < 3s | ROS recommendations |

### Processing Times

| Operation | Small Profile | Large Profile | Notes |
|-----------|---------------|---------------|-------|
| Single source ingestion | 2-4 minutes | 3-6 minutes | End-to-end |
| Summary table update | 1-2 minutes | 3-5 minutes | After ingestion |
| Kruize recommendation | 3-5 minutes | 5-10 minutes | After summary |

---

## Known Limitations

### Gateway Timeout (PERF-FINDING-001)

- Default 30s timeout causes failures for uploads > 2 weeks of data
- Workaround: Increase timeout to 300s for large environments
- Status: Configuration change recommended

### Complex Group-By Queries (PERF-FINDING-007)

- 3-dimension group_by queries may exceed 10s under high data volumes
- Caching helps subsequent queries
- Status: Monitor in production, consider index optimization

### 90-Day Data Upload

- Currently limited by backend retention configuration
- Related tickets: COST-7253, COST-573
- Status: Backend work in progress

---

## Helm Values Reference

### Small Profile

```yaml
database:
  server:
    resources:
      requests:
        memory: "1Gi"
        cpu: "500m"
      limits:
        memory: "4Gi"
        cpu: "2000m"
    storage: 10Gi

api:
  replicas: 1
  resources:
    requests:
      memory: "1Gi"
      cpu: "500m"
    limits:
      memory: "2Gi"
      cpu: "1000m"

listener:
  replicas: 1

celeryWorker:
  workers:
    ocp:
      replicas: 1
      concurrency: 5
    summary:
      replicas: 1
      concurrency: 5
```

### Large Profile

```yaml
database:
  server:
    resources:
      requests:
        memory: "4Gi"
        cpu: "2000m"
      limits:
        memory: "16Gi"
        cpu: "4000m"
    storage: 100Gi

api:
  replicas: 2
  resources:
    requests:
      memory: "2Gi"
      cpu: "1000m"
    limits:
      memory: "4Gi"
      cpu: "2000m"

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

gateway:
  envoy:
    routes:
      ingress:
        timeout: 300s
        per_try_timeout: 120s

gatewayRoute:
  annotations:
    haproxy.router.openshift.io/timeout: "300s"
```

---

## Monitoring Recommendations

### Key Metrics to Watch

1. **Database**
   - Connection count (max 100 recommended)
   - Query execution time (alert > 10s)
   - Disk usage (alert > 80%)
   - Cache hit ratio (target > 90%)

2. **Celery Workers**
   - Queue depth (alert if sustained > 100)
   - Task completion time
   - CPU usage (alert > 80% sustained)

3. **Listener**
   - Files processed per minute
   - Processing latency
   - Error rate

4. **API**
   - Request latency (P95 < 10s)
   - Error rate (< 1%)
   - Request count

5. **Kruize**
   - Heap usage (alert > 80% of limit)
   - Recommendation generation time
   - Experiment count

---

## Testing Your Deployment

Run the performance test suite to validate your configuration:

```bash
# Run all performance tests
./scripts/run-pytest.sh --performance

# Run specific test categories
./scripts/run-pytest.sh --perf-ingestion  # Upload tests
./scripts/run-pytest.sh --perf-api        # API latency tests
./scripts/run-pytest.sh --perf-scale      # Scale tests
./scripts/run-pytest.sh --perf-ros        # ROS/Kruize tests
./scripts/run-pytest.sh --perf-soak       # Stability tests
```

---

_Document generated from FLPATH-4036 performance testing results. Last updated: 2026-04-25._
