# Cost Management On-Premise Resource Requirements

> Resource requirements for deploying the complete Cost Management stack via the cost-onprem Helm chart.

---

## Executive Summary

| Resource | Minimum (Requests) | Recommended | Maximum (Limits) |
|----------|-------------------|-------------|------------------|
| **CPU** | ~8.5 cores | 10-12 cores | ~15 cores |
| **Memory** | ~19 Gi | 24-32 Gi | ~27 Gi |
| **Worker Nodes** | 3 nodes @ 8 Gi each | 3 nodes @ 12-16 Gi each | - |
| **Total Pods** | ~27 | - | - |

> **Note**: Default deployment excludes 5 cloud-only Celery workers (gated on `cloudProviderSupported`). With cloud providers enabled, add ~0.6 cores and ~1.9 Gi memory (see Celery section).

---

## Deployment Architecture

The Cost Management stack consists of the cost-onprem Helm chart plus infrastructure dependencies:

```
┌─────────────────────────────────────────────────────────────────┐
│                         cost-onprem                             │
│  Koku API, Celery Workers, ROS, Kruize, Gateway, UI, Ingress   │
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
| koku-api | 1 | 250m | 1000m | 1 Gi | 2 Gi |
| koku-masu | 1 | 250m | 500m | 1 Gi | 2 Gi |
| koku-listener | 1 | 150m | 300m | 300 Mi | 600 Mi |

**Subtotal**: 3 pods, **650m** CPU request, **~2.3 Gi** memory request

> The unified `koku-api` deployment serves both read and write API traffic with 2 Gunicorn workers. MASU runs database migrations on startup; if it cannot be scheduled, Celery workers will be stuck waiting.

---

### 2. Celery Workers (Background Processing)

The chart deploys **6 Celery pods** by default (beat scheduler + 5 queue workers). Penalty and XL worker variants have been removed for on-premise deployments to reduce resource footprint (FLPATH-3209). Five cloud-only workers are **gated** on `cloudProviderSupported` (hard-coded `false`) and are not deployed unless enabled.

#### Deployed by default (6 pods)

| Worker Type | Replicas | CPU Request | CPU Limit | Memory Request | Memory Limit | Purpose |
|-------------|----------|-------------|-----------|----------------|--------------|---------|
| celery-beat | 1 | 50m | 100m | 200 Mi | 400 Mi | Task scheduler |
| cost-model | 1 | 100m | 200m | 256 Mi | 512 Mi | Cost model calculations |
| ocp | 1 | 250m | 500m | 512 Mi | 1 Gi | OpenShift data processing |
| summary | 1 | 250m | 500m | 1 Gi | 2 Gi | Data summarization |
| priority | 1 | 250m | 500m | 1 Gi | 2 Gi | High-priority tasks |
| default | 1 | 100m | 200m | 200 Mi | 400 Mi | Default queue |

**Subtotal (default)**: 6 pods, **1.0 cores** request, **~3.2 Gi** memory request

#### Cloud-only workers (gated; 5 workers, not deployed by default)

| Worker Type | Replicas | CPU Request | CPU Limit | Memory Request | Memory Limit | Purpose |
|-------------|----------|-------------|-----------|----------------|--------------|---------|
| download | 1 | 200m | 400m | 512 Mi | 1 Gi | Report downloads (cloud PULL model) |
| refresh | 1 | 100m | 200m | 256 Mi | 512 Mi | Cloud data refresh |
| hcs | 1 | 100m | 200m | 300 Mi | 500 Mi | Hybrid Committed Spend |
| subs-extraction | 0 | 100m | 200m | 300 Mi | 500 Mi | Subscription extraction (disabled in SaaS) |
| subs-transmission | 0 | 100m | 200m | 300 Mi | 500 Mi | Subscription transmission (disabled in SaaS) |

**When cloud providers enabled**: +3 active pods (download, refresh, hcs), **+0.4 cores** request, **+~1.1 Gi** memory request.

---

### 3. Resource Optimization Service (ROS)

ROS components use the shared `resources.application` defaults from values.yaml unless overridden.

| Component | Replicas | CPU Request | CPU Limit | Memory Request | Memory Limit |
|-----------|----------|-------------|-----------|----------------|--------------|
| ros-api | 1 | 500m | 1000m | 1 Gi | 1 Gi |
| ros-processor | 1 | 500m | 1000m | 1 Gi | 1 Gi |
| ros-housekeeper | 1 | 500m | 1000m | 1 Gi | 1 Gi |
| ros-rec-poller | 1 | 500m | 1000m | 1 Gi | 1 Gi |
| kruize | 1 | 500m | 1000m | 1 Gi | 2 Gi |

**Subtotal**: 5 pods, **2.5 cores** request, **5 Gi** memory request

---

### 4. Infrastructure Services

| Component | Replicas | CPU Request | CPU Limit | Memory Request | Memory Limit |
|-----------|----------|-------------|-----------|----------------|--------------|
| PostgreSQL (database) | 1 | 100m | 500m | 256 Mi | 512 Mi |
| Valkey (cache) | 1 | 100m | 500m | 256 Mi | 512 Mi |

**Subtotal**: 2 pods, **200m** request, **512 Mi** memory request

---

### 5. Supporting Services

| Component | Replicas | CPU Request | CPU Limit | Memory Request | Memory Limit |
|-----------|----------|-------------|-----------|----------------|--------------|
| gateway (Envoy) | 2 | 100m | 500m | 128 Mi | 256 Mi |
| ingress | 1 | 500m | 1000m | 1 Gi | 1 Gi |
| ui (app + OAuth proxy) | 1 | 100m | 200m | 128 Mi | 256 Mi |

**Subtotal**: 4 pods, **800m** request, **~1.4 Gi** memory request

> **Note**: Sources API functionality is embedded within the unified Koku API deployment and is not a separate service.

---

### 6. Kafka Cluster (Strimzi)

Kafka pods typically don't have explicit resource requests set by default. Based on observed production usage:

| Component | Replicas | Observed CPU | Observed Memory | Recommended Request |
|-----------|----------|--------------|-----------------|---------------------|
| Kafka Broker | 3 | ~25m each | ~900 Mi each | 500m / 1 Gi each |
| ZooKeeper | 3 | ~12m each | ~1 Gi each | 500m / 1.5 Gi each |
| Entity Operator | 1 | ~5m | ~950 Mi | 200m / 1 Gi |

**Subtotal**: 7 pods, **~3.2 cores** recommended request, **~7 Gi** memory

> Configure Strimzi Kafka CR with explicit resource requests for production deployments.

---

## Total Resource Summary

### By Category

| Category | Pods | CPU Request | Memory Request |
|----------|------|-------------|----------------|
| Koku Core Services | 3 | 0.65 cores | 2.3 Gi |
| Celery Workers (default) | 6 | 1.0 cores | 3.2 Gi |
| ROS Services | 5 | 2.5 cores | 5.0 Gi |
| Infrastructure | 2 | 0.2 cores | 0.5 Gi |
| Supporting Services | 4 | 0.8 cores | 1.4 Gi |
| Kafka (recommended) | 7 | 3.2 cores | 7.0 Gi |
| **TOTAL (default)** | **27** | **~8.4 cores** | **~19.4 Gi** |

With cloud providers enabled (3 additional Celery workers): **30** pods, **~8.8 cores**, **~20.5 Gi**.

### Grand Total

| Metric | Default (OCP-only) | With cloud workers |
|--------|--------------------|--------------------|
| **Total Pods** | 27 | 30 |
| **CPU Requests** | ~8.4 cores | ~8.8 cores |
| **CPU Limits** | ~15 cores | ~15.8 cores |
| **Memory Requests** | ~19.4 Gi | ~20.5 Gi |
| **Memory Limits** | ~26.5 Gi | ~28.5 Gi |

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
| Single gateway replica | 100m | 128 Mi | No HA for gateway layer |
| Single replica API pods | 250m | 1 Gi | No HA for API layer |
| Skip Kruize (disables ROS) | 500m | 1 Gi | No resource optimization recommendations |

**Minimal Deployment Total**: ~7 cores, ~17 Gi memory

---

## Storage Requirements

| Component | Storage Class | Size | Notes |
|-----------|---------------|------|-------|
| PostgreSQL | Block (RWO) | 30 Gi | Main application database |
| S3 Object Storage | Object Storage | 150+ Gi | Cost report storage |
| Kafka | Block (RWO) | 50 Gi x 3 | Message persistence |
| ZooKeeper | Block (RWO) | 10 Gi x 3 | Coordination state |
| Valkey | Block (RWO) | 5 Gi | Cache persistence |

**Total Persistent Storage**: ~265-365 Gi

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
2. Lower memory requests in values.yaml

### MASU Pods Pending = Workers Stuck

The `koku-masu` pod runs database migrations. If it can't schedule:

```
MASU pending -> Migrations don't run -> All workers stuck in migration-wait loop
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
costManagement:
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

  # Reduce API resources
  api:
    replicas: 1
    resources:
      requests:
        cpu: 200m
        memory: 512Mi
```

---

## SaaS vs On-Prem Resource Alignment

> **IMPORTANT**: The On-Prem Helm chart resource values are aligned with the Clowder SaaS configuration to ensure consistent behavior.

### Source of Truth

The authoritative resource configuration is defined in the Koku repository:
- **Location**: `deploy/kustomize/patches/*.yaml`
- **Format**: Clowder ClowdApp kustomize patches

### Comparison: Current On-Prem vs SaaS Values

#### Koku Core Services

| Component | SaaS CPU Req | SaaS Mem Req | On-Prem CPU Req | On-Prem Mem Req | On-Prem Replicas |
|-----------|--------------|--------------|-----------------|-----------------|------------------|
| **koku-api** | 250m | 512Mi | 250m | 1Gi | 1 |
| **koku-masu** | 50m | 500Mi | 250m | 1Gi | 1 |
| **listener** | 150m | 300Mi | 150m | 300Mi | 1 |
| **scheduler (celery-beat)** | 50m | 200Mi | 50m | 200Mi | 1 |

#### Celery Workers (deployed by default)

| Worker | SaaS CPU Req | SaaS Mem Req | On-Prem CPU Req | On-Prem Mem Req | Notes |
|--------|--------------|--------------|-----------------|-----------------|-------|
| **worker-ocp** | 100m | 256Mi | 250m | 512Mi | |
| **worker-cost-model** | 100m | 256Mi | 100m | 256Mi | Aligned |
| **worker-summary** | 100m | 500Mi | 250m | 1Gi | |
| **worker-priority** | 100m | 400Mi | 250m | 1Gi | |
| **worker-celery (default)** | 100m | 256Mi | 100m | 200Mi | |

> **Note**: On-prem no longer deploys penalty or XL worker variants (FLPATH-3209). In SaaS, these handle overflow and large jobs but are unnecessary for on-premise workloads where data volume is predictable. Celery will route overflow tasks to the base queue worker.

#### Cloud-only Workers (gated, not deployed by default)

| Worker | SaaS CPU Req | SaaS Mem Req | On-Prem Status |
|--------|--------------|--------------|----------------|
| **worker-download** | 200m | 512Mi | Gated (replicas: 0) |
| **worker-refresh** | 100m | 256Mi | Gated (replicas: 0) |
| **worker-hcs** | 100m | 300Mi | Gated (replicas: 0) |
| **worker-subs-extraction** | 100m | 300Mi | Disabled (replicas: 0) |
| **worker-subs-transmission** | 100m | 300Mi | Disabled (replicas: 0) |

---

## OCP-Only Deployment (Default)

The chart default is OCP-only: cloud-only Celery workers (download, refresh, hcs, subs*) are gated on `cloudProviderSupported` (false) and are not deployed. Penalty and XL worker variants have been removed entirely. No extra configuration is needed for OCP-only deployments.

### Queue to Worker Reference

| Queue | Worker | Status |
|-------|--------|--------|
| `celery` | worker-default | Deployed |
| `ocp` | worker-ocp | Deployed |
| `summary` | worker-summary | Deployed |
| `cost_model` | worker-cost-model | Deployed |
| `priority` | worker-priority | Deployed |
| `download` | worker-download | Gated (cloud-only) |
| `refresh` | worker-refresh | Gated (cloud-only) |
| `hcs` | worker-hcs | Gated (cloud-only) |
| `subs_extraction` | worker-subs-extraction | Disabled |
| `subs_transmission` | worker-subs-transmission | Disabled |

### Why Cloud Workers Are Disabled

#### Download Workers
- **Reason**: OCP uses a **PUSH model** via Kafka - the Cost Management Operator sends data with presigned S3 URLs
- **Cloud providers** use a **PULL model** - Koku polls and downloads reports from S3/Blob

#### HCS Worker
- **Reason**: Hybrid Committed Spend only supports cloud providers (AWS, Azure, GCP)

#### SUBS Workers
- **Reason**: Subscription data extraction is for RHEL instances on cloud providers
- Already disabled in SaaS (`replicas: 0`)

#### Refresh Workers
- **Reason**: Primary use is `delete_openshift_on_cloud_data` for OCP-on-cloud scenarios
- No impact for OCP-only deployments

---

## Version Information

- **Based on**: Production deployment observations + Clowder SaaS configuration + OCP-only code analysis
- **Helm Chart Version**: cost-onprem v0.3.0
- **Koku Image**: `quay.io/insights-onprem/koku:latest`
- **SaaS Config Source**: `deploy/kustomize/patches/*.yaml` in koku repository
- **Last updated**: FLPATH-3209 (remove XL/penalty workers), FLPATH-3210 (unified API)
