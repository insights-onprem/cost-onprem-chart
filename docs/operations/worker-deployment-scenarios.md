# Cost Management Deployment Scenarios

This document details the worker requirements and resource consumption for different deployment scenarios.

---

## Deployment Scenarios Overview

| Scenario | Description | Use Case |
|----------|-------------|----------|
| **OCP-Only** | Standalone OpenShift cost data | On-premise OpenShift without cloud integration |
| **OCP on Cloud** | OpenShift running on AWS, Azure, or GCP | Track OCP costs with underlying cloud infrastructure |

> **Important**: From a worker perspective, there are only **two scenarios**:
> - **OCP-Only**: No cloud provider data
> - **OCP on Cloud**: Any combination of AWS, Azure, GCP
>
> The Celery workers are **provider-agnostic** - the same `download`, `refresh`, and `hcs` workers handle all cloud providers. There is no difference in resource requirements between "OCP on AWS only" vs "OCP on AWS + Azure + GCP".

---

## Worker Requirements by Scenario

### Legend

| Symbol | Meaning |
|--------|---------|
| ✅ | Required |
| ⚠️ | Recommended (production) |
| ➖ | Optional |
| ❌ | Not needed |

### Celery Workers Matrix

> **Note**: OCP on AWS, Azure, GCP, and Multi-Cloud have **identical worker requirements**. Workers are provider-agnostic and process data from all configured cloud sources.

| Worker | OCP-Only | OCP on Cloud / Multi-Cloud |
|--------|----------|---------------------------|
| **celery-beat** | ✅ | ✅ |
| **default** | ✅ | ✅ |
| | | |
| **ocp** | ✅ | ✅ |
| **ocp-penalty** | ✅ | ✅ |
| **ocp-xl** | ✅ | ✅ |
| | | |
| **summary** | ✅ | ✅ |
| **summary-penalty** | ✅ | ✅ |
| **summary-xl** | ✅ | ✅ |
| | | |
| **cost-model** | ✅ | ✅ |
| **cost-model-penalty** | ✅ | ✅ |
| **cost-model-xl** | ✅ | ✅ |
| | | |
| **priority** | ⚠️ | ✅ |
| **priority-penalty** | ➖ | ⚠️ |
| **priority-xl** | ➖ | ⚠️ |
| | | |
| **refresh** | ❌ | ✅ |
| **refresh-penalty** | ❌ | ⚠️ |
| **refresh-xl** | ❌ | ⚠️ |
| | | |
| **download** | ❌ | ✅ |
| **download-penalty** | ❌ | ⚠️ |
| **download-xl** | ❌ | ⚠️ |
| | | |
| **hcs** | ❌ | ✅ |
| | | |
| **subs-extraction** | ❌ | ➖ |
| **subs-transmission** | ❌ | ➖ |

### Worker Count by Scenario

| Scenario | Required | Recommended | Optional | Total Active |
|----------|----------|-------------|----------|--------------|
| **OCP-Only** | 11 | 1 | 2 | 12-14 |
| **OCP on Cloud / Multi-Cloud** | 15 | 6 | 2 | 21-23 |

---

## Worker Purpose Reference

### Why Workers Are Needed

| Worker Group | Purpose | OCP-Only Impact |
|--------------|---------|-----------------|
| **ocp-*** | Process OpenShift cost data from Kafka | ✅ Core functionality |
| **summary-*** | Aggregate and summarize cost data | ✅ Core functionality |
| **cost-model-*** | Apply cost models and markup | ✅ Core functionality |
| **priority-*** | High-priority task processing | ⚠️ Recommended for production |
| **download-*** | Pull reports from cloud provider S3/Blob | ❌ OCP uses PUSH model via Kafka |
| **refresh-*** | Correlate OCP-on-cloud data, delete stale data | ❌ No cloud data to correlate |
| **hcs** | Hybrid Committed Spend calculations | ❌ Only supports AWS/Azure/GCP |
| **subs-*** | RHEL subscription data on cloud instances | ❌ Cloud-specific feature |

### Data Flow Differences

```
OCP-Only:
  Cost Management Metrics Operator → Kafka → ocp workers → summary → cost-model → Database

OCP-on-Cloud:
  Cost Management Metrics Operator → Kafka → ocp workers ─┐
                                                          ├→ refresh (correlate) → summary → cost-model → Database
  Cloud Provider S3/Blob → download workers ──────────────┘
```

---

## Koku Resource Requirements by Scenario

### Koku Core Services (All Scenarios)

| Component | Replicas | CPU Request | Memory Request | CPU Limit | Memory Limit |
|-----------|----------|-------------|----------------|-----------|--------------|
| koku-api | 1 | 250m | 1Gi | 1 | 2Gi |
| koku-masu | 1 | 250m | 1Gi | 500m | 2Gi |
| listener | 1 | 150m | 300Mi | 300m | 600Mi |

**Subtotal (Core)**: 3 pods, **650m CPU**, **2.3 Gi memory**

### Celery Resources by Scenario

#### OCP-Only (Default)

Penalty and XL worker variants have been removed for on-premise deployments (FLPATH-3209).

| Component | Count | CPU Request | Memory Request |
|-----------|-------|-------------|----------------|
| celery-beat | 1 | 50m | 200Mi |
| ocp | 1 | 250m | 512Mi |
| summary | 1 | 250m | 1Gi |
| cost-model | 1 | 100m | 256Mi |
| default | 1 | 100m | 200Mi |
| priority | 1 | 250m | 1Gi |
| **Subtotal** | **6** | **1.0 cores** | **~3.2 Gi** |

#### OCP on Cloud (Any combination of AWS/Azure/GCP)

| Component | Count | CPU Request | Memory Request |
|-----------|-------|-------------|----------------|
| *OCP-Only workers* | 6 | 1.0 cores | 3.2 Gi |
| download | 1 | 200m | 512Mi |
| refresh | 1 | 100m | 256Mi |
| hcs | 1 | 100m | 300Mi |
| **Subtotal** | **9** | **~1.4 cores** | **~4.3 Gi** |

> **Note**: Workers are provider-agnostic. "OCP on AWS only", "OCP on Azure only", "OCP on GCP only", and "OCP on all three" all have the **same resource requirements**.

---

## Total Requirements Summary (Koku Only)

| Scenario | Celery Workers | Koku Core | Total Pods | CPU Request | Memory Request |
|----------|----------------|-----------|------------|-------------|----------------|
| **OCP-Only** | 6 | 3 | 9 | **~1.65 cores** | **~5.5 Gi** |
| **OCP on Cloud** | 9 | 3 | 12 | **~2.05 cores** | **~6.6 Gi** |

> **Note**: "OCP on Cloud" covers AWS, Azure, GCP, or any combination. Workers are provider-agnostic.

---

## ROS Resources (Fixed Across All Scenarios)

ROS components remain constant regardless of deployment scenario:

| Component | Replicas | CPU Request | Memory Request | CPU Limit | Memory Limit |
|-----------|----------|-------------|----------------|-----------|--------------|
| ros-api | 1 | 500m | 1Gi | 1 | 1Gi |
| ros-processor | 1 | 500m | 1Gi | 1 | 1Gi |
| ros-housekeeper | 1 | 500m | 1Gi | 1 | 1Gi |
| ros-rec-poller | 1 | 500m | 1Gi | 1 | 1Gi |
| kruize | 1 | 500m | 1Gi | 1 | 2Gi |

**ROS Subtotal**: 5 pods, **2.5 cores**, **5 Gi**

---

## Grand Total by Scenario (Koku + ROS)

| Scenario | Koku Pods | ROS Pods | Total Pods | CPU Request | Memory Request |
|----------|-----------|----------|------------|-------------|----------------|
| **OCP-Only** | 9 | 5 | **14** | **~4.15 cores** | **~10.5 Gi** |
| **OCP on Cloud** | 12 | 5 | **17** | **~4.55 cores** | **~11.6 Gi** |

> **Note**: These totals exclude infrastructure (PostgreSQL, Valkey) and support services (gateway, ingress, UI).

### With Infrastructure and Support Services

Infrastructure (PostgreSQL, Valkey) and support services (gateway, ingress, UI) add ~1.0 cores CPU and ~1.9 Gi memory.

| Scenario | Koku + ROS | Infra + Support | **Grand Total** |
|----------|------------|-----------------|-----------------|
| **OCP-Only** | ~4.15 cores / ~10.5 Gi | ~1.0 cores / ~1.9 Gi | **~5.2 cores / ~12.4 Gi** |
| **OCP on Cloud** | ~4.55 cores / ~11.6 Gi | ~1.0 cores / ~1.9 Gi | **~5.6 cores / ~13.5 Gi** |

> **Note**: These totals exclude Kafka (AMQ Streams), which adds ~3.2 cores / ~7 Gi if deployed alongside.

---

## Helm Values for Each Scenario

### OCP-Only Values (Default in cost-onprem chart)

```yaml
celery:
  workers:
    # ===== CORE OCP PROCESSING (Required) =====
    ocp: { replicas: 1 }
    summary: { replicas: 1 }
    costModel: { replicas: 1 }
    default: { replicas: 1 }
    priority: { replicas: 1 }

    # ===== CLOUD-SPECIFIC (Disabled for OCP-Only) =====
    download: { replicas: 0 }
    refresh: { replicas: 0 }
    hcs: { replicas: 0 }
    subsExtraction: { replicas: 0 }
    subsTransmission: { replicas: 0 }
```

### OCP on Cloud Values (AWS, Azure, GCP, or Multi-Cloud)

```yaml
celery:
  workers:
    # ===== CORE OCP PROCESSING (Required) =====
    ocp: { replicas: 1 }
    summary: { replicas: 1 }
    costModel: { replicas: 1 }
    default: { replicas: 1 }
    priority: { replicas: 1 }

    # ===== CLOUD-SPECIFIC (Enable for any cloud integration) =====
    download: { replicas: 1 }      # Pull reports from S3/Blob
    refresh: { replicas: 1 }       # OCP-on-cloud correlation
    hcs: { replicas: 1 }           # Hybrid Committed Spend

    # ===== ALWAYS DISABLED =====
    subsExtraction: { replicas: 0 }
    subsTransmission: { replicas: 0 }
```

> **Note**: Penalty and XL worker variants have been removed for on-premise deployments (FLPATH-3209). Each queue now uses a single worker replica.

---

## Quick Reference

### Scenario Decision Tree

```
Q: Do you need cloud provider cost data (AWS, Azure, GCP)?
├── NO  → OCP-Only deployment (~5.2 cores, ~12.4 Gi)
└── YES → OCP on Cloud deployment (~5.6 cores, ~13.5 Gi)
          (Same resources whether using 1 cloud or all 3)
```

### Key Takeaways

1. **Only two resource profiles**: OCP-Only vs OCP on Cloud
2. **Workers are provider-agnostic**: Same workers handle AWS, Azure, and GCP
3. **OCP-Only saves**: ~3 worker pods, ~0.4 cores CPU, ~1.1 Gi memory
4. **Download workers**: Only needed for cloud provider data (PULL model)
5. **Refresh workers**: Only needed for OCP-on-cloud correlation
6. **HCS**: Only supports AWS, Azure, GCP (not standalone OCP)
7. **SUBS workers**: Generally disabled (cloud RHEL subscription tracking)
8. **Penalty/XL variants removed**: On-premise uses single replicas per queue (FLPATH-3209)

---

## Version Information

- **Document Version**: 1.0
- **Date**: December 2024
- **Based on**: Koku SaaS configuration and code analysis

