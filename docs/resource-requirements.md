# Cost Management On-Premise Resource Requirements

> **For ros-helm-chart team**: This document details the CPU, memory, and infrastructure requirements for deploying the complete Cost Management stack. Please add this to `docs/resource-requirements.md` and reference it from the README.md.

---

## Executive Summary

| Resource | Minimum (Requests) | Recommended | Maximum (Limits) |
|----------|-------------------|-------------|------------------|
| **CPU** | 10.3 cores | 12-14 cores | 17.5 cores |
| **Memory** | 24 Gi | 32-40 Gi | 52 Gi |
| **Worker Nodes** | 3 nodes @ 8 Gi each | 3 nodes @ 12-16 Gi each | - |
| **Total Pods** | 55 | - | - |

---

## Deployment Architecture

The Cost Management stack consists of the cost-onprem Helm chart plus infrastructure dependencies:

```
┌─────────────────────────────────────────────────────────────────┐
│                         cost-onprem                             │
│  Koku APIs, Celery Workers, ROS, Kruize, Sources, UI, Ingress   │
│  PostgreSQL, Valkey                                             │
└─────────────────────────────────────────────────────────────────┘
                              +
┌─────────────────────────────────────────────────────────────────┐
│                    Kafka (Strimzi Operator)                     │
│  3 Kafka Brokers, 3 ZooKeeper nodes, Entity Operator            │
└─────────────────────────────────────────────────────────────────┘
```

---

## Detailed Resource Requirements by Component

### 1. Koku Core Services (API + Processing)

| Component | Replicas | CPU Request | CPU Limit | Memory Request | Memory Limit |
|-----------|----------|-------------|-----------|----------------|--------------|
| koku-api-reads | 2 | 300m | 600m | 500 Mi | 1 Gi |
| koku-api-writes | 1 | 300m | 600m | 500 Mi | 1 Gi |
| koku-api-masu | 2 | 300m | 1000m | 1 Gi | 8 Gi |
| koku-api-listener | 2 | 200m | 500m | 512 Mi | 1 Gi |

**Subtotal**: 7 pods, **1.9 cores** request, **4.5 Gi** memory request

> ⚠️ **Critical**: The `koku-api-masu` pods run database migrations on startup. If these pods cannot be scheduled, all Celery workers will be stuck waiting for migrations.

---

### 2. Celery Workers (Background Processing)

The system deploys **24 Celery pods** across different queues:

| Worker Type | Replicas | CPU Request | CPU Limit | Memory Request | Memory Limit | Purpose |
|-------------|----------|-------------|-----------|----------------|--------------|---------|
| celery-beat | 1 | 100m | 200m | 512 Mi | 1 Gi | Task scheduler |
| cost-model | 1 | 100m | 200m | 256 Mi | 512 Mi | Cost model calculations |
| cost-model-penalty | 1 | 100m | 200m | 256 Mi | 512 Mi | Penalty queue overflow |
| cost-model-xl | 1 | 100m | 200m | 256 Mi | 512 Mi | Large cost model jobs |
| download | 1 | 100m | 200m | 512 Mi | 1 Gi | Report downloads |
| download-penalty | 1 | 100m | 200m | 512 Mi | 1 Gi | Penalty queue overflow |
| download-xl | 1 | 100m | 200m | 512 Mi | 1 Gi | Large downloads |
| ocp | 1 | 100m | 200m | 256 Mi | 512 Mi | OpenShift processing |
| ocp-penalty | 1 | 100m | 200m | 256 Mi | 512 Mi | Penalty queue overflow |
| ocp-xl | 1 | 100m | 200m | 256 Mi | 512 Mi | Large OCP jobs |
| summary | 1 | 100m | 200m | 200 Mi | 400 Mi | Data summarization |
| summary-penalty | 1 | 100m | 200m | 200 Mi | 400 Mi | Penalty queue overflow |
| summary-xl | 1 | 100m | 200m | 200 Mi | 400 Mi | Large summaries |
| priority | 1 | 100m | 200m | 200 Mi | 400 Mi | High-priority tasks |
| priority-penalty | 1 | 100m | 200m | 200 Mi | 400 Mi | Penalty queue overflow |
| priority-xl | 1 | 100m | 200m | 200 Mi | 400 Mi | Large priority jobs |
| refresh | 1 | 100m | 200m | 200 Mi | 400 Mi | Data refresh |
| refresh-penalty | 1 | 100m | 200m | 200 Mi | 400 Mi | Penalty queue overflow |
| refresh-xl | 1 | 100m | 200m | 200 Mi | 400 Mi | Large refresh jobs |
| default | 1 | 100m | 200m | 200 Mi | 400 Mi | Default queue |
| hcs | 1 | 100m | 200m | 200 Mi | 400 Mi | HCS processing |
| subs-extraction | 1 | 100m | 200m | 256 Mi | 512 Mi | Subscription extraction |
| subs-transmission | 1 | 100m | 200m | 256 Mi | 512 Mi | Subscription transmission |

**Subtotal**: 24 pods, **2.4 cores** request, **~6.5 Gi** memory request

---

### 3. Resource Optimization Service (ROS)

| Component | Replicas | CPU Request | CPU Limit | Memory Request | Memory Limit |
|-----------|----------|-------------|-----------|----------------|--------------|
| ros-api | 1 | 300m | 1000m | 640 Mi | 1.25 Gi |
| ros-processor | 1 | 200m | 500m | 512 Mi | 1 Gi |
| ros-housekeeper | 1 | 200m | 500m | 512 Mi | 1 Gi |
| ros-rec-poller | 1 | 200m | 500m | 512 Mi | 1 Gi |
| kruize | 2 | 200m | 1000m | 1 Gi | 2 Gi |

**Subtotal**: 6 pods, **1.3 cores** request, **~4.2 Gi** memory request

---

### 4. Infrastructure Services

| Component | Replicas | CPU Request | CPU Limit | Memory Request | Memory Limit |
|-----------|----------|-------------|-----------|----------------|--------------|
| **PostgreSQL (main)** | 1 | 500m | 2000m | 1 Gi | 4 Gi |
| cost-onprem-database | 1 | 100m | 500m | 256 Mi | 512 Mi |
| Valkey | 1 | 200m | 500m | 512 Mi | 1 Gi |

**Subtotal**: 3 pods, **800m** request, **~1.8 Gi** memory request

---

### 5. Supporting Services

| Component | Replicas | CPU Request | CPU Limit | Memory Request | Memory Limit |
|-----------|----------|-------------|-----------|----------------|--------------|
| sources-api | 1 | 200m | 500m | 512 Mi | 1 Gi |
| ingress | 1 | 300m | 1000m | 640 Mi | 1.25 Gi |
| ui | 1 | 100m | 200m | 128 Mi | 256 Mi |

**Subtotal**: 3 pods, **600m** request, **~1.3 Gi** memory request

---

### 6. Kafka Cluster (Strimzi)

Kafka pods typically don't have explicit resource requests set by default. Based on observed production usage:

| Component | Replicas | Observed CPU | Observed Memory | Recommended Request |
|-----------|----------|--------------|-----------------|---------------------|
| Kafka Broker | 3 | ~25m each | ~900 Mi each | 500m / 1 Gi each |
| ZooKeeper | 3 | ~12m each | ~1 Gi each | 500m / 1.5 Gi each |
| Entity Operator | 1 | ~5m | ~950 Mi | 200m / 1 Gi |

**Subtotal**: 7 pods, **~3.2 cores** recommended request, **~7 Gi** memory

> 💡 **Note**: Configure Strimzi Kafka CR with explicit resource requests for production deployments.

---

## Total Resource Summary

### By Category

| Category | Pods | CPU Request | Memory Request |
|----------|------|-------------|----------------|
| Koku Core Services | 7 | 1.9 cores | 4.5 Gi |
| Celery Workers | 24 | 2.4 cores | 6.5 Gi |
| ROS Services | 6 | 1.3 cores | 4.2 Gi |
| Infrastructure | 3 | 0.8 cores | 1.8 Gi |
| Supporting Services | 3 | 0.6 cores | 1.3 Gi |
| Kafka (recommended) | 7 | 3.2 cores | 7.0 Gi |
| **TOTAL** | **50** | **~10.2 cores** | **~25 Gi** |

### Grand Total

| Metric | Value |
|--------|-------|
| **Total Pods** | 49-50 |
| **CPU Requests** | ~9-11 cores |
| **CPU Limits** | ~15-16 cores |
| **Memory Requests** | ~22-26 Gi |
| **Memory Limits** | ~45-50 Gi |

---

## Node Sizing Recommendations

### Minimum Viable (Development/Testing)

| Nodes | Type | CPU | Memory | Notes |
|-------|------|-----|--------|-------|
| 3 | Worker | 4 cores | 8 Gi | Tight fit, may have scheduling issues |

### Recommended (Production)

| Nodes | Type | CPU | Memory | Notes |
|-------|------|-----|--------|-------|
| 3 | Worker | 8 cores | 16 Gi | Comfortable headroom |
| 3 | Control Plane | 4 cores | 8 Gi | Standard control plane |

### High Availability (Large Scale)

| Nodes | Type | CPU | Memory | Notes |
|-------|------|-----|--------|-------|
| 5+ | Worker | 8 cores | 32 Gi | Scale workers horizontally |
| 3 | Control Plane | 8 cores | 16 Gi | HA control plane |

---

## Minimum Viable Deployment (Reduced Footprint)

For resource-constrained environments, you can reduce the deployment footprint:

| Change | CPU Saved | Memory Saved | Trade-off |
|--------|-----------|--------------|-----------|
| Remove penalty workers (8 pods) | 800m | ~2 Gi | Slower recovery from failures |
| Remove XL workers (8 pods) | 800m | ~2 Gi | Large jobs may timeout |
| Single replica API pods | 600m | 1.5 Gi | No HA for API layer |
| Skip Kruize replica | 200m | 1 Gi | No HA for recommendations |

**Minimal Deployment Total**: ~6.5 cores, ~15 Gi memory

---

## Storage Requirements

| Component | Storage Class | Size | Notes |
|-----------|---------------|------|-------|
| PostgreSQL | Block (RWO) | 50 Gi | Main application database |
| ODF/MinIO | Object Storage | 150+ Gi | Cost report storage |
| Kafka | Block (RWO) | 50 Gi × 3 | Message persistence |
| ZooKeeper | Block (RWO) | 10 Gi × 3 | Coordination state |

**Total Persistent Storage**: ~280-380 Gi

---

## Common Scheduling Issues

### "Insufficient Memory" for Pending Pods

If you see pods stuck in `Pending` with "Insufficient memory":

```bash
# Check node memory allocation
kubectl describe nodes | grep -A5 "Allocated resources"
```

**Cause**: Memory *requests* (not actual usage) exceed node capacity.

**Solutions**:
1. Add more worker nodes
2. Reduce worker replicas (disable penalty/XL workers)
3. Lower memory requests in values.yaml

### MASU Pods Pending = Workers Stuck

The `koku-api-masu` pods run database migrations. If they can't schedule:

```
MASU pending → Migrations don't run → All workers stuck in migration-wait loop
```

**Priority**: Always ensure MASU pods can be scheduled first.

---

## Monitoring Resource Usage

```bash
# Current usage vs requests
kubectl top pods -n cost-onprem

# Node-level allocation
kubectl describe nodes | grep -A20 "Allocated resources"

# Find pending pods
kubectl get pods -n cost-onprem | grep Pending

# Check why pods are pending
kubectl describe pod <pod-name> -n cost-onprem | grep -A5 Events
```

---

## Helm Values for Resource Tuning

Example `values.yaml` overrides for resource-constrained environments:

```yaml
# Reduce Celery worker memory
celery:
  workers:
    default:
      resources:
        requests:
          memory: "150Mi"
          cpu: "50m"
        limits:
          memory: "300Mi"
          cpu: "150m"

# Reduce API replicas
koku:
  api:
    reads:
      replicas: 1
    writes:
      replicas: 1
```

---

## ⚠️ SaaS vs On-Prem Resource Alignment

> **IMPORTANT**: The On-Prem Helm chart resource values MUST match the Clowder SaaS configuration to ensure consistent behavior and proper resource allocation.

### Source of Truth

The authoritative resource configuration is defined in the Koku repository:
- **Location**: `deploy/kustomize/patches/*.yaml`
- **Format**: Clowder ClowdApp kustomize patches

### Comparison: Current On-Prem vs Required SaaS Values

#### Koku Core Services

| Component | SaaS CPU Req | SaaS Mem Req | SaaS CPU Lim | SaaS Mem Lim | Replicas |
|-----------|--------------|--------------|--------------|--------------|----------|
| **koku-api-reads** | 250m | 512Mi | 500m | 1Gi | 3 |
| **koku-api-writes** | 250m | 512Mi | 500m | 1Gi | 3 |
| **koku-api-masu** | 50m | 500Mi | 100m | 700Mi | 1 |
| **listener** | 150m | 300Mi | 300m | 600Mi | 2 |
| **nginx** | 100m | 100Mi | 200m | 200Mi | 3 |
| **scheduler (celery-beat)** | 50m | 200Mi | 100m | 400Mi | 1 |

#### Celery Workers

| Worker | SaaS CPU Req | SaaS Mem Req | SaaS CPU Lim | SaaS Mem Lim | Replicas |
|--------|--------------|--------------|--------------|--------------|----------|
| **worker-ocp** | 100m | 256Mi | 200m | 512Mi | 2 |
| **worker-ocp-penalty** | 100m | 256Mi | 200m | 512Mi | 2 |
| **worker-ocp-xl** | 100m | 256Mi | 200m | 512Mi | 2 |
| **worker-cost-model** | 100m | 256Mi | 200m | 512Mi | 2 |
| **worker-cost-model-penalty** | 100m | 256Mi | 200m | 512Mi | 2 |
| **worker-cost-model-xl** | 100m | 256Mi | 200m | 512Mi | 2 |
| **worker-download** | 200m | 512Mi | 400m | 1Gi | 1-10 (HPA) |
| **worker-download-penalty** | 200m | 512Mi | 400m | 1Gi | 1-10 (HPA) |
| **worker-download-xl** | 200m | 512Mi | 400m | 1Gi | 1-10 (HPA) |
| **worker-summary** | 100m | 500Mi | 200m | 750Mi | 1-10 (HPA) |
| **worker-summary-penalty** | 100m | 500Mi | 200m | 750Mi | 1-10 (HPA) |
| **worker-summary-xl** | 100m | 500Mi | 200m | 750Mi | 1-10 (HPA) |
| **worker-priority** | 100m | 400Mi | 200m | 750Mi | 2 |
| **worker-priority-penalty** | 150m | 400Mi | 300m | 750Mi | 2 |
| **worker-priority-xl** | 150m | 400Mi | 300m | 750Mi | 2 |
| **worker-refresh** | 100m | 256Mi | 200m | 512Mi | 2 |
| **worker-refresh-penalty** | 100m | 256Mi | 200m | 512Mi | 2 |
| **worker-refresh-xl** | 100m | 256Mi | 200m | 512Mi | 2 |
| **worker-celery (default)** | 100m | 256Mi | 200m | 512Mi | 1 |
| **worker-hcs** | 100m | 300Mi | 200m | 500Mi | 1 |
| **worker-subs-extraction** | 100m | 300Mi | 200m | 500Mi | 0 (disabled) |
| **worker-subs-transmission** | 100m | 300Mi | 200m | 500Mi | 0 (disabled) |

#### Sources Integration

| Component | SaaS CPU Req | SaaS Mem Req | SaaS CPU Lim | SaaS Mem Lim | Replicas |
|-----------|--------------|--------------|--------------|--------------|----------|
| **sources-client** | 50m | 650Mi | 100m | 768Mi | 1 |
| **sources-listener** | 100m | 250Mi | 200m | 500Mi | 1 |

### Required Helm Chart Changes

The `ros-helm-chart` team needs to update `values.yaml` to match these SaaS values:

```yaml
# cost-onprem/values.yaml - REQUIRED UPDATES

koku:
  api:
    reads:
      replicas: 3
      resources:
        requests:
          cpu: 250m
          memory: 512Mi
        limits:
          cpu: 500m
          memory: 1Gi
    writes:
      replicas: 3
      resources:
        requests:
          cpu: 250m
          memory: 512Mi
        limits:
          cpu: 500m
          memory: 1Gi
    masu:
      replicas: 1
      resources:
        requests:
          cpu: 50m      # Was 300m - REDUCE
          memory: 500Mi # Was 1Gi - REDUCE
        limits:
          cpu: 100m     # Was 1000m - REDUCE
          memory: 700Mi # Was 8Gi - REDUCE

  listener:
    replicas: 2
    resources:
      requests:
        cpu: 150m
        memory: 300Mi
      limits:
        cpu: 300m
        memory: 600Mi

celery:
  beat:
    resources:
      requests:
        cpu: 50m
        memory: 200Mi
      limits:
        cpu: 100m
        memory: 400Mi

  workers:
    ocp:
      replicas: 2
      resources:
        requests:
          cpu: 100m
          memory: 256Mi
        limits:
          cpu: 200m
          memory: 512Mi

    download:
      replicas: 3  # fallback, HPA 1-10
      resources:
        requests:
          cpu: 200m      # Was 100m - INCREASE
          memory: 512Mi
        limits:
          cpu: 400m      # Was 200m - INCREASE
          memory: 1Gi

    summary:
      replicas: 3  # fallback, HPA 1-10
      resources:
        requests:
          cpu: 100m
          memory: 500Mi  # Was 200Mi - INCREASE
        limits:
          cpu: 200m
          memory: 750Mi  # Was 400Mi - INCREASE

    priority:
      replicas: 2
      resources:
        requests:
          cpu: 100m
          memory: 400Mi  # Was 200Mi - INCREASE
        limits:
          cpu: 200m
          memory: 750Mi  # Was 400Mi - INCREASE

    priorityPenalty:
      replicas: 2
      resources:
        requests:
          cpu: 150m      # Was 100m - INCREASE
          memory: 400Mi  # Was 200Mi - INCREASE
        limits:
          cpu: 300m      # Was 200m - INCREASE
          memory: 750Mi  # Was 400Mi - INCREASE

    priorityXl:
      replicas: 2
      resources:
        requests:
          cpu: 150m      # Was 100m - INCREASE
          memory: 400Mi  # Was 200Mi - INCREASE
        limits:
          cpu: 300m      # Was 200m - INCREASE
          memory: 750Mi  # Was 400Mi - INCREASE

    hcs:
      replicas: 1
      resources:
        requests:
          cpu: 100m
          memory: 300Mi  # Was 200Mi - INCREASE
        limits:
          cpu: 200m
          memory: 500Mi  # Was 400Mi - INCREASE

    # Disabled in SaaS
    subsExtraction:
      replicas: 0
    subsTransmission:
      replicas: 0
```

### Summary of Key Differences

| Component | Issue | SaaS Value | On-Prem Value | Action |
|-----------|-------|------------|---------------|--------|
| **MASU** | Over-provisioned | 50m/500Mi | 300m/1Gi | **REDUCE** |
| **Download workers** | Under-provisioned CPU | 200m | 100m | **INCREASE** |
| **Summary workers** | Under-provisioned memory | 500Mi | 200Mi | **INCREASE** |
| **Priority workers** | Under-provisioned memory | 400Mi | 200Mi | **INCREASE** |
| **Priority-penalty/xl** | Under-provisioned | 150m/400Mi | 100m/200Mi | **INCREASE** |
| **HCS worker** | Under-provisioned memory | 300Mi | 200Mi | **INCREASE** |

### Revised Total Requirements (After Alignment)

After aligning with SaaS values:

| Resource | Previous Estimate | After SaaS Alignment |
|----------|-------------------|----------------------|
| **CPU Requests** | ~10.3 cores | **~7.5 cores** |
| **Memory Requests** | ~24 Gi | **~18 Gi** |

The MASU reduction alone saves: **250m CPU** and **500Mi memory** per pod.

---

## 🎯 OCP-Only Deployment (Reduced Footprint)

For deployments that **only process OpenShift data** (no AWS, Azure, or GCP providers), many workers can be disabled to significantly reduce resource requirements.

### Worker Classification for OCP-Only

| Status | Workers | Count | Notes |
|--------|---------|-------|-------|
| ✅ **REQUIRED** | celery-beat, ocp, ocp-penalty, ocp-xl, summary, summary-penalty, summary-xl, cost-model, cost-model-penalty, cost-model-xl, default | 11 | Core OCP processing |
| ⚠️ **OPTIONAL** | priority, priority-penalty, priority-xl, refresh, refresh-penalty, refresh-xl | 6 | Can disable for minimal |
| ❌ **NOT NEEDED** | download, download-penalty, download-xl, hcs, subs-extraction, subs-transmission | 7 | Cloud-provider specific |

### Why Workers Can Be Disabled

#### Download Workers (NOT NEEDED)
- **Reason**: OCP uses a **PUSH model** via Kafka - the Cost Management Operator sends data with presigned S3 URLs
- **Cloud providers** use a **PULL model** - Koku polls and downloads reports from S3/Blob
- **Code evidence**: OCP ingestion is in `masu/api/ingest_ocp_payload.py` (Kafka-triggered)

#### HCS Worker (NOT NEEDED)
- **Reason**: Hybrid Committed Spend only supports cloud providers
- **Code evidence** (`hcs/tasks.py`):
```python
HCS_ACCEPTED_PROVIDERS = (
    Provider.PROVIDER_AWS,
    Provider.PROVIDER_AZURE,
    Provider.PROVIDER_GCP,
    # Note: OCP is NOT in this list
)
```

#### SUBS Workers (NOT NEEDED)
- **Reason**: Subscription data extraction is for RHEL instances on cloud providers
- **Note**: Already disabled in SaaS (`replicas: 0`)

#### Refresh Workers (OPTIONAL)
- **Reason**: Primary use is `delete_openshift_on_cloud_data` for OCP-on-cloud scenarios
- **OCP-only impact**: None - no cloud data to correlate

### OCP-Only Helm Values

```yaml
# values.yaml for OCP-only deployment

celery:
  beat:
    replicas: 1
    enabled: true

  workers:
    # ===== REQUIRED FOR OCP =====
    ocp:
      replicas: 2
      enabled: true
    ocpPenalty:
      replicas: 1
      enabled: true
    ocpXl:
      replicas: 1
      enabled: true

    summary:
      replicas: 2
      enabled: true
    summaryPenalty:
      replicas: 1
      enabled: true
    summaryXl:
      replicas: 1
      enabled: true

    costModel:
      replicas: 2
      enabled: true
    costModelPenalty:
      replicas: 1
      enabled: true
    costModelXl:
      replicas: 1
      enabled: true

    default:
      replicas: 1
      enabled: true

    # ===== OPTIONAL =====
    priority:
      replicas: 1  # Keep for production
      enabled: true
    priorityPenalty:
      replicas: 0
      enabled: false
    priorityXl:
      replicas: 0
      enabled: false

    refresh:
      replicas: 0
      enabled: false
    refreshPenalty:
      replicas: 0
      enabled: false
    refreshXl:
      replicas: 0
      enabled: false

    # ===== NOT NEEDED FOR OCP-ONLY =====
    download:
      replicas: 0
      enabled: false
    downloadPenalty:
      replicas: 0
      enabled: false
    downloadXl:
      replicas: 0
      enabled: false

    hcs:
      replicas: 0
      enabled: false

    subsExtraction:
      replicas: 0
      enabled: false
    subsTransmission:
      replicas: 0
      enabled: false
```

### OCP-Only Resource Savings

| Deployment | Workers | CPU Request | Memory Request |
|------------|---------|-------------|----------------|
| **Full (all providers)** | 24 | ~2.4 cores | ~6.5 Gi |
| **OCP-Only** | 11 | ~1.1 cores | ~3.0 Gi |
| **OCP-Only (minimal)** | 8 | ~0.8 cores | ~2.2 Gi |

**Savings**: 13 fewer pods, ~1.3 cores CPU, ~3.5 Gi memory

### Queue to Worker Reference

| Queue | Worker | OCP-Only |
|-------|--------|----------|
| `celery` | worker-celery | ✅ Required |
| `ocp`, `ocp_xl`, `ocp_penalty` | worker-ocp-* | ✅ Required |
| `summary`, `summary_xl`, `summary_penalty` | worker-summary-* | ✅ Required |
| `cost_model`, `cost_model_xl`, `cost_model_penalty` | worker-cost-model-* | ✅ Required |
| `priority`, `priority_xl`, `priority_penalty` | worker-priority-* | ⚠️ Optional |
| `refresh`, `refresh_xl`, `refresh_penalty` | worker-refresh-* | ⚠️ Optional |
| `download`, `download_xl`, `download_penalty` | worker-download-* | ❌ Disable |
| `hcs` | worker-hcs | ❌ Disable |
| `subs_extraction`, `subs_transmission` | worker-subs-* | ❌ Disable |

---

## Version Information

- **Document Version**: 1.2
- **Based on**: Production deployment observations + Clowder SaaS configuration + OCP-only code analysis (December 2024)
- **Helm Chart Version**: cost-onprem v0.2.x
- **Koku Image**: `quay.io/insights-onprem/koku:latest`
- **SaaS Config Source**: `deploy/kustomize/patches/*.yaml` in koku repository

---

## README.md Addition

Add the following to the ros-helm-chart README.md under the "Configuration" section:

```markdown
### Resource Requirements

Complete Cost Management deployment requires significant cluster resources:

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| **CPU** | 10 cores | 12-14 cores |
| **Memory** | 24 Gi | 32-40 Gi |
| **Worker Nodes** | 3 × 8 Gi | 3 × 16 Gi |
| **Storage** | 300 Gi | 400+ Gi |
| **Pods** | ~55 | - |

**📖 See [Resource Requirements Guide](docs/resource-requirements.md) for detailed breakdown by component.**
```

