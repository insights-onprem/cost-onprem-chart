# Cost Management On-Premise Installation Guide

**Version:** 1.0
**Date:** November 2025
**Audience:** Development and DevOps Teams

---

## Table of Contents

1. [Pre-Requirements](#pre-requirements)
2. [Architecture Overview](#architecture-overview)
3. [Installation Steps](#installation-steps)
4. [Post-Installation Verification](#post-installation-verification)
5. [Running E2E Tests](#running-e2e-tests)
6. [Cost Calculation and Verification](#cost-calculation-and-verification)
7. [Troubleshooting](#troubleshooting)
8. [Maintenance](#maintenance)

---

## Pre-Requirements

### Platform Requirements

| Component | Requirement | Notes |
|-----------|-------------|-------|
| **OpenShift Container Platform (OCP)** | **4.18+** | Minimum tested version |
| **Storage** | 150GB+ available | For development/testing, 300GB+ production |
| **CPU** | 8+ cores | Minimum for all components |
| **Memory** | 16GB+ RAM | Minimum for all components |
| **Network** | Cluster networking | Inter-pod communication required |

### Resource Requirements by Component

#### Infrastructure Components

| Component | Pods | CPU Request | CPU Limit | Memory Request | Memory Limit |
|-----------|------|-------------|-----------|----------------|--------------|
| **PostgreSQL** | 1 | 100m | 500m | 256Mi | 512Mi |
| **Valkey** | 1 | 100m | 500m | 256Mi | 512Mi |
| **Subtotal** | **2** | **200m** | **1 core** | **512Mi** | **1Gi** |

#### Application Components

| Component | Pods | CPU Request | CPU Limit | Memory Request | Memory Limit |
|-----------|------|-------------|-----------|----------------|--------------|
| **Koku API** | 1 | 250m | 1 | 1Gi | 2Gi |
| **Koku Listener** | 1 | 150m | 300m | 300Mi | 600Mi |
| **MASU** | 1 | 250m | 500m | 1Gi | 2Gi |
| **Celery Beat** | 1 | 50m | 100m | 200Mi | 400Mi |
| **Celery Workers** | 5 | 100-250m | 200m-500m | 200Mi-1Gi | 400Mi-2Gi |
| **ROS (API, Processor, Housekeeper, Poller)** | 4 | 500m each | 1 each | 1Gi each | 1Gi each |
| **Kruize** | 1 | 500m | 1 | 1Gi | 2Gi |
| **Gateway (Envoy)** | 2 | 100m each | 500m each | 128Mi each | 256Mi each |
| **Ingress** | 1 | 500m | 1 | 1Gi | 1Gi |
| **UI** | 1 | 100m | 200m | 128Mi | 256Mi |
| **Subtotal** | **18** | **~5.0 cores** | **~11 cores** | **~11.8Gi** | **~18.6Gi** |

#### Total Deployment Resources

| Metric | Development | Production |
|--------|-------------|------------|
| **Total Pods** | ~20 | 27+ (with replicas) |
| **Total CPU Request** | **~5.2 cores** | **8+ cores** |
| **Total CPU Limit** | **~12 cores** | **14+ cores** |
| **Total Memory Request** | **~12.3Gi** | **19+ Gi** |
| **Total Memory Limit** | **~19.6Gi** | **30+ Gi** |
| **S3 Object Storage** | **150 GB** | **300+ GB** |

**Note:** These totals exclude Kafka (Strimzi), which adds ~7 pods and ~3.2 cores / ~7Gi memory.

### Required OpenShift Components

#### 1. S3-Compatible Object Storage

The chart requires S3-compatible object storage. ODF is **not required** — any S3 provider works (AWS S3, ODF with Direct Ceph RGW, MinIO, etc.).

See the [Storage Configuration](configuration.md#storage-configuration) section for full setup options.

**Minimum Requirements:**
- **S3-compatible endpoint** accessible from the cluster
- **Credentials** with read/write access to the required buckets
- **150GB+** for development (300GB+ for production)

#### 2. Kafka / Strimzi

**Automated Deployment (Recommended):**
```bash
# Deploy Strimzi operator and Kafka cluster
./scripts/deploy-strimzi.sh

# Script will:
# - Install Strimzi operator (version 0.45.1)
# - Deploy Kafka cluster (version 3.8.0)
# - Configure appropriate storage class
# - Wait for cluster to be ready
```

**Customization:**
```bash
# Custom namespace
KAFKA_NAMESPACE=my-kafka ./scripts/deploy-strimzi.sh

# Custom Kafka cluster name
KAFKA_CLUSTER_NAME=my-cluster ./scripts/deploy-strimzi.sh

# For OpenShift with specific storage class
STORAGE_CLASS=ocs-storagecluster-ceph-rbd ./scripts/deploy-strimzi.sh
```

**Manual Verification:**
```bash
# Check Strimzi operator
oc get csv -A | grep strimzi

# Check Kafka cluster
oc get kafka -n kafka

# Verify Kafka is ready
oc wait kafka/cost-onprem-kafka --for=condition=Ready --timeout=300s -n kafka
```

**Required Kafka Topics:**
- `platform.upload.announce` (created automatically by Koku on first message)

#### 3. Command Line Tools
```bash
# Required tools
oc version        # OpenShift CLI 4.12+
helm version      # Helm 3.8+
python3 --version # Python 3.9+
psql --version    # PostgreSQL client 13+

# Optional (for development)
git --version
jq --version
```

---

## Architecture Overview

### Component Stack

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         COST MANAGEMENT STACK                           │
└─────────────────────────────────────────────────────────────────────────┘

┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃  APPLICATION LAYER (cost-onprem chart)                               ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛

    ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
    │   Koku API      │   │ Kafka Listener  │   │  MASU Workers   │
    │   (Django)      │   │   (Celery)      │   │   (Celery)      │
    └────────┬────────┘   └────────┬────────┘   └────────┬────────┘
             │                     │                     │
             │                     │                     │
             └─────────────────────┴─────────────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │   PostgreSQL (Unified DB)    │
                    │  • Koku: Summary tables      │
                    │  • Sources: Provider data    │
                    │    (integrated in Koku DB)   │
                    └──────────────────────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │   Valkey (Cache/Broker)      │
                    │  • Celery task queue         │
                    │  • Session caching           │
                    └──────────────────────────────┘

┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃  STORAGE LAYER (S3-Compatible Object Storage)                        ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛

            ┌─────────────────────────┐
            │   S3 Object Storage     │
            │  • Raw CSV uploads      │
            │  • Processed data       │
            │  • Monthly partitions   │
            └─────────────────────────┘
                       ▲
                       │ (uploads)
                       │
            ┌──────────┴──────────┐
            │   MASU Workers      │
            └─────────────────────┘

┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃  MESSAGE QUEUE (Kafka/Strimzi - deployed separately)                 ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛

            ┌───────────────────────────────┐
            │     Kafka Cluster             │
            │  Topic: platform.upload.      │
            │         announce              │
            └──────────────┬────────────────┘
                           │
                           ▼
                ┌──────────────────┐
                │ Kafka Listener   │
                │   (Consumes)     │
                └──────────────────┘
```

### Data Flow

1. **Data Ingestion:** OCP metrics → Kafka → Koku Listener
2. **CSV Processing:** Listener → S3 (raw CSVs)
3. **Data Processing:** MASU workers parse and process CSV data
4. **Aggregation:** PostgreSQL stores and aggregates summary tables
5. **API Access:** Koku API → PostgreSQL (serve data)

---

## Installation Steps

### Step 1: Prepare Namespace

```bash
# Create namespace for Cost Management
export NAMESPACE=cost-onprem
oc new-project $NAMESPACE

# Verify namespace
oc project $NAMESPACE
```

### Step 2: Deploy Cost Management Chart

The unified `cost-onprem` chart deploys all components: PostgreSQL, Valkey, Koku API (with integrated Sources API), MASU workers, Celery workers, ROS, and Kruize.

**Option A: Using Helm directly (manual control)**
```bash
cd /path/to/cost-onprem-chart

# Deploy cost management chart
helm install cost-onprem ./cost-onprem \
  --namespace $NAMESPACE \
  --create-namespace \
  --wait \
  --timeout 10m \
  --set kafka.bootstrap_servers="cost-onprem-kafka-kafka-bootstrap.kafka.svc.cluster.local:9092"

# Verify all pods
oc get pods -n $NAMESPACE
```

**Option B: Automated Installation (Recommended)**

Use the automated installation script for the simplest deployment:

```bash
cd /path/to/cost-onprem-chart/scripts

# Run automated installation (recommended)
./install-helm-chart.sh
```

**What the script does:**
1. ✅ Verifies pre-requirements (S3 storage, Kafka)
2. ✅ Auto-discovers S3 credentials (OBC, NooBaa, MinIO)
3. ✅ Creates namespace if needed
4. ✅ Deploys unified chart (PostgreSQL, Valkey, Koku, ROS, Sources, Kruize)
5. ✅ Runs database migrations automatically via init container
6. ✅ Verifies all components are healthy

**Features:**
- 🔐 Automatic secret creation (Django, S3)
- 🔍 Auto-discovers S3 credentials from cluster (OBC, NooBaa, MinIO)
- ✅ Chart validation and linting before deployment
- 🎯 Pod readiness checks and status reporting

**Customization with Environment Variables:**
```bash
# Custom namespace
NAMESPACE=my-namespace ./install-helm-chart.sh

# Custom Kafka configuration
KAFKA_NAMESPACE=my-kafka \
KAFKA_CLUSTER=my-cluster \
./install-helm-chart.sh

# Use local chart for development
USE_LOCAL_CHART=true ./install-helm-chart.sh

# Show deployment status
./install-helm-chart.sh status

# Clean uninstall
./install-helm-chart.sh cleanup
```

**Expected Pods:**
- `cost-onprem-database-0` (StatefulSet, Ready 1/1) - PostgreSQL
- `cost-onprem-valkey-*` (Deployment, Ready 1/1) - Cache/Broker
- `cost-onprem-koku-api-*` (Deployment)
- `cost-onprem-koku-listener-*` (Deployment)
- `cost-onprem-koku-masu-*` (Deployment)
- `cost-onprem-celery-*` (Multiple Deployments)
- `cost-onprem-ros-*` (Deployment)

**Verify Deployment:**
```bash
# Check PostgreSQL
oc exec -n $NAMESPACE cost-onprem-database-0 -- psql -U koku -d costonprem_koku -c "SELECT version();"

# Check Koku API health
oc exec -n $NAMESPACE $(oc get pod -n $NAMESPACE -l app.kubernetes.io/component=cost-management-api -o name | head -1) \
  -- python manage.py showmigrations --database=default

# Check Kafka listener
oc logs -n $NAMESPACE $(oc get pod -n $NAMESPACE -l app.kubernetes.io/component=koku-listener -o name) --tail=50
```

---

## Post-Installation Verification

### 1. Check All Pods are Running

```bash
# All pods should be Ready and Running
oc get pods -n $NAMESPACE

# Expected output (no CrashLoopBackOff, no Error)
NAME                                            READY   STATUS    RESTARTS   AGE
cost-onprem-database-0                          1/1     Running   0          5m
cost-onprem-valkey-*                            1/1     Running   0          5m
cost-onprem-koku-api-*                          1/1     Running   0          3m
cost-onprem-koku-api-listener-*                 1/1     Running   0          3m
cost-onprem-koku-api-masu-*                     1/1     Running   0          3m
cost-onprem-celery-*                            1/1     Running   0          3m
cost-onprem-ros-*                               1/1     Running   0          3m
cost-onprem-kruize-*                            1/1     Running   0          3m
```

### 2. Verify Database

```bash
# Check PostgreSQL connectivity and schema
oc exec -n $NAMESPACE cost-onprem-database-0 -- psql -U koku -d costonprem_koku -c "\dt" | head -20

# Expected: Many tables (reporting_*, api_*, etc.)
```

### 3. Verify S3 Storage

The installation automatically creates the following S3 buckets:

| Bucket | Purpose |
|--------|---------|
| `koku-bucket` | Koku/Cost Management parquet data and reports |
| `ros-data` | Resource Optimization Service data |
| `insights-upload-perma` | Ingress service for operator uploads |

```bash
# Get S3 credentials from the storage credentials secret
S3_ACCESS_KEY=$(kubectl get secret cost-onprem-storage-credentials -n cost-onprem -o jsonpath='{.data.access-key}' | base64 -d)
S3_SECRET_KEY=$(kubectl get secret cost-onprem-storage-credentials -n cost-onprem -o jsonpath='{.data.secret-key}' | base64 -d)

# Get the S3 endpoint from Helm values
S3_ENDPOINT=$(helm get values cost-onprem -n cost-onprem -o json | jq -r '.objectStorage.endpoint // empty')

# Verify buckets were created
aws s3 ls --endpoint-url https://$S3_ENDPOINT

# Expected output should include:
# insights-upload-perma
# koku-bucket
# ros-data
```

### 5. Verify Kafka Integration

```bash
# Check that listener is connected to Kafka
oc logs -n $NAMESPACE $(oc get pod -n $NAMESPACE -l app=koku-api-listener -o name) | grep -i "kafka\|connected\|subscribed"

# Expected: "Subscribed to topic(s): platform.upload.announce"
```

---

## Running E2E Tests

### Overview

The E2E test validates the entire data pipeline:
1. ✅ **Preflight** - Environment checks
2. ✅ **Provider** - Creates OCP cost provider
3. ✅ **Data Upload** - Generates and uploads test data (CSV → TAR.GZ → S3)
4. ✅ **Kafka** - Publishes message to trigger processing
5. ✅ **Processing** - CSV parsing and data ingestion
6. ✅ **Database** - Validates data in PostgreSQL tables
7. ✅ **Aggregation** - Summary table generation
8. ✅ **Validation** - Verifies cost calculations

### Running the Test

```bash
cd /path/to/cost-onprem-helm-chart/scripts

# Run E2E test (smoke test mode - ~3 minutes)
./cost-onprem-ocp-dataflow.sh

# Re-run with force cleanup (recommended for repeated runs)
./cost-onprem-ocp-dataflow.sh --force

# Run with diagnostics (shows infrastructure health on failure)
./cost-onprem-ocp-dataflow.sh --diagnose
```

### Expected Output

A successful run shows actual data proof from PostgreSQL, not just "PASSED":

```
======================================================================
  ✅ SMOKE VALIDATION PASSED
======================================================================

  📊 DATA PROOF - Actual rows from PostgreSQL:
  ------------------------------------------------------------------
  Date         Namespace            CPU(h)     CPU Req    Mem(GB)
  ------------------------------------------------------------------
  2025-12-01   test-namespace           6.00     12.00     12.00
  ------------------------------------------------------------------
  TOTALS       (1 rows)                 6.00     12.00     12.00
  ------------------------------------------------------------------

  📋 EXPECTED vs ACTUAL (from nise YAML):
  --------------------------------------------------
  Metric                      Expected     Actual Match
  --------------------------------------------------
  CPU Request (hours)            12.00      12.00 ✅
  Memory Request (GB-hrs)        24.00      24.00 ✅
  --------------------------------------------------

  ✅ File Processing: 3 checks passed
     - 3 file(s) processed
     - Manifest ID: 9
  ✅ Cost: 2 checks passed
======================================================================

Phases: 8/8 passed
  ✅ preflight
  ✅ migrations
  ✅ kafka_validation
  ✅ provider
  ✅ data_upload
  ✅ processing
  ✅ database
  ✅ validation

✅ E2E SMOKE TEST PASSED

╔═══════════════════════════════════════════════════════════════╗
║  ✓ OCP E2E Validation PASSED                                  ║
║  Total time: 3m 19s                                          ║
╚═══════════════════════════════════════════════════════════════╝
```

**Key validation points:**
- **DATA PROOF**: Actual rows from `reporting_ocpusagelineitem_daily_summary`
- **EXPECTED vs ACTUAL**: Side-by-side comparison of nise YAML values vs PostgreSQL
- **Match icons**: ✅ indicates values match within 5% tolerance

### Test Data

The E2E test uses a minimal static report defined in:
```
scripts/e2e_validator/static_reports/minimal_ocp_pod_only.yml
```

**Test Data Specifications:**
- **Date Range:** 2 days (2025-11-01 to 2025-11-02)
- **Cluster ID:** `test-cluster-123`
- **Node:** `test-node-1` (2 cores, 8GB RAM)
- **Namespace:** `test-namespace`
- **Pod:** `test-pod-1`
  - CPU request: 0.5 cores
  - Memory request: 1 GB
  - CPU usage: 0.25 cores (50% of request)
  - Memory usage: 0.5 GB (50% of request)

---

## Cost Calculation and Verification

### Understanding the Nise YAML
s
The nise YAML defines the test infrastructure and usage:

```yaml
generators:
  - OCPGenerator:
      start_date: 2025-11-01
      end_date: 2025-11-02      # 2 days = 48 hours
      nodes:
        - node:
          node_name: test-node-1
          cpu_cores: 2
          memory_gig: 8
          namespaces:
            test-namespace:
              pods:
                - pod:
                  pod_name: test-pod-1
                  cpu_request: 0.5      # cores
                  mem_request_gig: 1    # GB
                  cpu_limit: 1
                  mem_limit_gig: 2
                  pod_seconds: 3600     # 1 hour
                  cpu_usage:
                    full_period: 0.25   # cores (50% of request)
                  mem_usage_gig:
                    full_period: 0.5    # GB (50% of request)
```

### Calculating Expected Values

#### CPU Request Hours
```
CPU Request Hours = cpu_request × hours
                  = 0.5 cores × 24 hours (per day) × 2 days
                  = 0.5 × 48
                  = 24 core-hours
```

**Note:** The test currently generates hourly data, so actual calculation depends on nise behavior.
For the minimal test:
```
CPU Request Hours = 0.5 cores × 24 hours
                  = 12 core-hours (per day)
```

#### Memory Request GB-Hours
```
Memory Request GB-Hours = mem_request_gig × hours
                        = 1 GB × 24 hours
                        = 24 GB-hours (per day)
```

### Verifying in PostgreSQL

Once the E2E test completes, verify the aggregated data:

```bash
# Port-forward to PostgreSQL
oc port-forward -n cost-onprem pod/cost-onprem-database-0 5432:5432 &

# Connect and query
psql -h localhost -U koku -d costonprem_koku << 'SQL'
-- View summary data for test cluster
SELECT
    usage_start,
    cluster_id,
    namespace,
    node,
    resource_id as pod,
    pod_request_cpu_core_hours,
    pod_request_memory_gigabyte_hours,
    pod_usage_cpu_core_hours,
    pod_usage_memory_gigabyte_hours,
    CAST(infrastructure_usage_cost->>'value' AS NUMERIC) as infra_cost
FROM org1234567.reporting_ocpusagelineitem_daily_summary
WHERE cluster_id = 'test-cluster-123'
ORDER BY usage_start;
SQL
```

**Expected Output:**
```
 usage_start |  cluster_id     |   namespace    |     node      |        pod
-------------+-----------------+----------------+---------------+-------------------
 2025-11-01  | test-cluster-123| test-namespace | test-node-1   | i-test-resource-1

 pod_request_cpu_core_hours | pod_request_memory_gigabyte_hours
----------------------------+-----------------------------------
                      12.00 |                             24.00
```

### Cost Validation Query

The E2E test uses this query to validate costs:

```sql
-- Aggregate cost validation
SELECT
    cluster_id,
    COUNT(*) as daily_rows,
    SUM(pod_usage_cpu_core_hours) as total_cpu_usage,
    SUM(pod_request_cpu_core_hours) as total_cpu_request,
    SUM(pod_usage_memory_gigabyte_hours) as total_mem_usage,
    SUM(pod_request_memory_gigabyte_hours) as total_mem_request,
    SUM(CAST(infrastructure_usage_cost->>'value' AS NUMERIC)) as total_infra_cost
FROM org1234567.reporting_ocpusagelineitem_daily_summary
WHERE cluster_id = 'test-cluster-123'
  AND infrastructure_usage_cost IS NOT NULL
GROUP BY cluster_id;
```

**Validation Criteria:**
- CPU request hours: Within ±5% of expected (12.00 core-hours per day)
- Memory request GB-hours: Within ±5% of expected (24.00 GB-hours per day)
- All resource names match exactly

### Understanding the Data Pipeline

#### 1. Raw CSV Data (from nise)
Nise generates hourly usage data in CSVs with columns:
- `pod`, `namespace`, `node`, `resource_id`
- `pod_request_cpu_core_seconds` (converted to hours)
- `pod_request_memory_byte_seconds` (converted to GB-hours)
- `interval_start`, `interval_end` (hourly intervals)

#### 2. PostgreSQL Summary Tables
Processed data is aggregated into PostgreSQL summary tables:
```sql
-- Final summary table (used by Koku API)
SELECT * FROM org1234567.reporting_ocpusagelineitem_daily_summary;
```

---

## Troubleshooting

### Common Issues

#### 1. Kafka Listener Not Receiving Messages

**Symptom:** E2E test hangs at "Processing" phase

**Cause:** Kafka connection issues or missing topic

**Solution:**
```bash
# Check Kafka cluster is healthy
oc get kafka -n kafka

# Verify topic exists
oc exec -n kafka cost-onprem-kafka-kafka-0 -- bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 \
  --list | grep platform.upload.announce

# Check listener logs
oc logs -n $NAMESPACE $(oc get pod -n $NAMESPACE -l app=koku-api-listener -o name) --tail=100
```

#### 2. E2E Test Validation Failures

**Symptom:** Test passes all phases but validation shows incorrect data

**Cause:** Old data from previous runs

**Solution:**
```bash
# Run test with force cleanup
./cost-onprem-ocp-dataflow.sh --force

# Or manually clear summary table
oc exec -n $NAMESPACE cost-onprem-database-0 -- psql -U koku -d costonprem_koku -c \
  "DELETE FROM org1234567.reporting_ocpusagelineitem_daily_summary WHERE cluster_id = 'test-cluster-123';"
```

#### 3. Nise Generates Random Data

**Symptom:** Pod/node names in database don't match nise YAML

**Cause:** Incorrect YAML format (nested instead of flat)

**Solution:**
Ensure nise YAML uses **flat format** (IQE style):
```yaml
# CORRECT:
nodes:
  - node:
    node_name: test-node-1    # Same indentation as "- node:"

# WRONG:
nodes:
  - node:
      node_name: test-node-1  # Extra indentation
```

See `COMPLETE_RESOLUTION_JOURNEY.md` for details.

---

## Maintenance

### Upgrading Charts

```bash
# Upgrade the unified chart
helm upgrade cost-onprem ./cost-onprem \
  --namespace $NAMESPACE \
  --reuse-values
```

### Scaling Workers

```bash
# Scale Celery workers for higher throughput
oc scale deployment cost-onprem-celery-worker-ocp -n $NAMESPACE --replicas=3
oc scale deployment cost-onprem-celery-worker-summary -n $NAMESPACE --replicas=3
```

### Database Backups

```bash
# Backup PostgreSQL (Koku DB)
oc exec -n $NAMESPACE cost-onprem-database-0 -- pg_dump -U koku costonprem_koku > koku-backup-$(date +%Y%m%d).sql
```

### Monitoring

```bash
# Watch pod resource usage
oc adm top pods -n $NAMESPACE

# Monitor Celery queue (MASU workers)
oc exec -n $NAMESPACE $(oc get pod -n $NAMESPACE -l app=koku-worker -o name | head -1) \
  -- celery -A koku inspect active
```

---

## Additional Resources

- **Project Repository:** https://github.com/project-koku
- **Koku Documentation:** https://koku.readthedocs.io/
- **Strimzi (Kafka):** https://strimzi.io/

### Project Documentation

- `COMPLETE_RESOLUTION_JOURNEY.md` - Troubleshooting guide and lessons learned
- `E2E_TEST_SUCCESS.md` - E2E test results and validation details
- `README.md` - Project overview

---

## Support

For issues or questions:
1. Check `COMPLETE_RESOLUTION_JOURNEY.md` for common issues
2. Review logs: `oc logs -n $NAMESPACE <pod-name>`
3. Run E2E test to validate environment
4. Contact the development team

---

**Status:** Production Ready ✅
**Last Updated:** November 2025
**Maintained By:** Cost Management Team

