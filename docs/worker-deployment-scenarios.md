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
  Cost Management Operator → Kafka → ocp workers → summary → cost-model → Database

OCP-on-Cloud:
  Cost Management Operator → Kafka → ocp workers ─┐
                                                  ├→ refresh (correlate) → summary → cost-model → Database
  Cloud Provider S3/Blob → download workers ──────┘
```

---

## Koku Resource Requirements by Scenario

### Koku Core Services (All Scenarios)

| Component | Replicas | CPU Request | Memory Request | CPU Limit | Memory Limit |
|-----------|----------|-------------|----------------|-----------|--------------|
| koku-api-reads | 1-2 | 250m | 512Mi | 500m | 1Gi |
| koku-api-writes | 1 | 250m | 512Mi | 500m | 1Gi |
| koku-api-masu | 1 | 50m | 500Mi | 100m | 700Mi |
| listener | 1 | 150m | 300Mi | 300m | 600Mi |

**Subtotal (Core)**: 4-5 pods, **700m CPU**, **1.8 Gi memory**

### Celery Resources by Scenario

#### OCP-Only (Minimal)

| Component | Count | CPU Request | Memory Request |
|-----------|-------|-------------|----------------|
| celery-beat | 1 | 50m | 200Mi |
| ocp workers (3) | 3 | 300m | 768Mi |
| summary workers (3) | 3 | 300m | 1.5Gi |
| cost-model workers (3) | 3 | 300m | 768Mi |
| default | 1 | 100m | 256Mi |
| priority | 1 | 100m | 400Mi |
| **Subtotal** | **12** | **1.15 cores** | **~3.9 Gi** |

#### OCP on Cloud (Any combination of AWS/Azure/GCP)

| Component | Count | CPU Request | Memory Request |
|-----------|-------|-------------|----------------|
| *OCP-Only workers* | 12 | 1.15 cores | 3.9 Gi |
| download workers (3) | 3 | 600m | 1.5Gi |
| refresh workers (3) | 3 | 300m | 768Mi |
| hcs | 1 | 100m | 300Mi |
| priority-penalty | 1 | 150m | 400Mi |
| priority-xl | 1 | 150m | 400Mi |
| **Subtotal** | **21** | **~2.45 cores** | **~7.3 Gi** |

> **Note**: Workers are provider-agnostic. "OCP on AWS only", "OCP on Azure only", "OCP on GCP only", and "OCP on all three" all have the **same resource requirements**.

---

## Total Requirements Summary (Koku Only)

| Scenario | Celery Workers | Koku Core | Total Pods | CPU Request | Memory Request |
|----------|----------------|-----------|------------|-------------|----------------|
| **OCP-Only** | 12 | 4-5 | 16-17 | **~1.85 cores** | **~5.7 Gi** |
| **OCP on Cloud** | 21 | 4-5 | 25-26 | **~3.15 cores** | **~9.1 Gi** |

> **Note**: "OCP on Cloud" covers AWS, Azure, GCP, or any combination. Workers are provider-agnostic.

---

## ROS Resources (Fixed Across All Scenarios)

ROS components remain constant regardless of deployment scenario:

| Component | Replicas | CPU Request | Memory Request | CPU Limit | Memory Limit |
|-----------|----------|-------------|----------------|-----------|--------------|
| ros-api | 1 | 300m | 640Mi | 1000m | 1.25Gi |
| ros-processor | 1 | 200m | 512Mi | 500m | 1Gi |
| ros-housekeeper | 1 | 200m | 512Mi | 500m | 1Gi |
| ros-rec-poller | 1 | 200m | 512Mi | 500m | 1Gi |
| kruize | 1-2 | 200m | 1Gi | 1000m | 2Gi |

**ROS Subtotal**: 5-6 pods, **~1.1 cores**, **~3.2 Gi**

---

## Grand Total by Scenario (Koku + ROS)

| Scenario | Koku Pods | ROS Pods | Total Pods | CPU Request | Memory Request |
|----------|-----------|----------|------------|-------------|----------------|
| **OCP-Only** | 16-17 | 5-6 | **21-23** | **~3.0 cores** | **~9 Gi** |
| **OCP on Cloud** | 25-26 | 5-6 | **30-32** | **~4.3 cores** | **~12.3 Gi** |

> **Note**: These totals exclude infrastructure (PostgreSQL, Kafka, Valkey) which adds ~5 Gi memory and ~2 cores.

### With Infrastructure

| Scenario | Application | Infrastructure | **Grand Total** |
|----------|-------------|----------------|-----------------|
| **OCP-Only** | ~3.0 cores / ~9 Gi | ~2 cores / ~5 Gi | **~5 cores / ~14 Gi** |
| **OCP on Cloud** | ~4.3 cores / ~12 Gi | ~2 cores / ~5 Gi | **~6.3 cores / ~17 Gi** |

---

## Helm Values for Each Scenario

### OCP-Only Values (Default in cost-onprem chart)

```yaml
celery:
  workers:
    # ===== CORE OCP PROCESSING (Required) =====
    ocp: { replicas: 1 }
    ocpPenalty: { replicas: 1 }
    ocpXl: { replicas: 1 }
    summary: { replicas: 1 }
    summaryPenalty: { replicas: 1 }
    summaryXl: { replicas: 1 }
    costModel: { replicas: 1 }
    costModelPenalty: { replicas: 1 }
    costModelXl: { replicas: 1 }
    default: { replicas: 1 }
    priority: { replicas: 1 }

    # ===== CLOUD-SPECIFIC (Disabled for OCP-Only) =====
    download: { replicas: 0 }
    downloadPenalty: { replicas: 0 }
    downloadXl: { replicas: 0 }
    refresh: { replicas: 0 }
    refreshPenalty: { replicas: 0 }
    refreshXl: { replicas: 0 }
    hcs: { replicas: 0 }
    priorityPenalty: { replicas: 0 }
    priorityXl: { replicas: 0 }
    subsExtraction: { replicas: 0 }
    subsTransmission: { replicas: 0 }
```

### OCP on Cloud Values (AWS, Azure, GCP, or Multi-Cloud)

```yaml
celery:
  workers:
    # ===== CORE OCP PROCESSING (Required) =====
    ocp: { replicas: 1 }
    ocpPenalty: { replicas: 1 }
    ocpXl: { replicas: 1 }
    summary: { replicas: 1 }
    summaryPenalty: { replicas: 1 }
    summaryXl: { replicas: 1 }
    costModel: { replicas: 1 }
    costModelPenalty: { replicas: 1 }
    costModelXl: { replicas: 1 }
    default: { replicas: 1 }
    priority: { replicas: 1 }
    priorityPenalty: { replicas: 1 }
    priorityXl: { replicas: 1 }

    # ===== CLOUD-SPECIFIC (Enable for any cloud integration) =====
    download: { replicas: 1 }      # Pull reports from S3/Blob
    downloadPenalty: { replicas: 1 }
    downloadXl: { replicas: 1 }
    refresh: { replicas: 1 }       # OCP-on-cloud correlation
    refreshPenalty: { replicas: 1 }
    refreshXl: { replicas: 1 }
    hcs: { replicas: 1 }           # Hybrid Committed Spend

    # ===== ALWAYS DISABLED =====
    subsExtraction: { replicas: 0 }
    subsTransmission: { replicas: 0 }
```

---

## Quick Reference

### Scenario Decision Tree

```
Q: Do you need cloud provider cost data (AWS, Azure, GCP)?
├── NO  → OCP-Only deployment (~3 cores, ~9 Gi)
└── YES → OCP on Cloud deployment (~4.3 cores, ~12 Gi)
          (Same resources whether using 1 cloud or all 3)
```

### Key Takeaways

1. **Only two resource profiles**: OCP-Only vs OCP on Cloud
2. **Workers are provider-agnostic**: Same workers handle AWS, Azure, and GCP
3. **OCP-Only saves**: ~10 worker pods, ~1.3 cores CPU, ~3 Gi memory
4. **Download workers**: Only needed for cloud provider data (PULL model)
5. **Refresh workers**: Only needed for OCP-on-cloud correlation
6. **HCS**: Only supports AWS, Azure, GCP (not standalone OCP)
7. **SUBS workers**: Generally disabled (cloud RHEL subscription tracking)

---

## Version Information

- **Document Version**: 1.0
- **Date**: December 2024
- **Based on**: Koku SaaS configuration and code analysis

