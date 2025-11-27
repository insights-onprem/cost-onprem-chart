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

#### Infrastructure Chart (cost-management-infrastructure)

| Component | Pods | CPU Request | CPU Limit | Memory Request | Memory Limit |
|-----------|------|-------------|-----------|----------------|--------------|
| **PostgreSQL** | 1 | 500m | 1000m | 1Gi | 2Gi |
| **Trino Coordinator** | 1 | 1000m | 2000m | 2Gi | 4Gi |
| **Trino Worker** | 1 | 1000m | 2000m | 2Gi | 4Gi |
| **Hive Metastore** | 1 | 500m | 1000m | 1Gi | 2Gi |
| **Hive Metastore DB** | 1 | 250m | 500m | 512Mi | 1Gi |
| **Redis** | 1 | 200m | 400m | 256Mi | 512Mi |
| **Subtotal** | **6** | **3.45 cores** | **6.9 cores** | **6.75 GB** | **13.5 GB** |

#### Application Chart (cost-management-onprem)

| Component | Pods | CPU Request | CPU Limit | Memory Request | Memory Limit |
|-----------|------|-------------|-----------|----------------|--------------|
| **Koku API Reads** | 2 | 300m each | 600m each | 500Mi each | 1Gi each |
| **Koku API Writes** | 1 | 300m | 600m | 500Mi | 1Gi |
| **Koku API Listener** | 1 | 200m | 400m | 256Mi | 512Mi |
| **MASU** | 1 | 300m | 600m | 500Mi | 1Gi |
| **Celery Beat** | 1 | 100m | 200m | 256Mi | 512Mi |
| **Celery Workers** | 17 | 100-500m | 200-1000m | 256Mi-1Gi | 512Mi-2Gi |
| **Sources API** | 1 | 200m | 400m | 256Mi | 512Mi |
| **Sources DB** | 1 | 250m | 500m | 512Mi | 1Gi |
| **Subtotal** | **26** | **~6.5 cores** | **~13 cores** | **~12 GB** | **~24 GB** |

#### Total Deployment Resources

| Metric | Development | Production |
|--------|-------------|------------|
| **Total Pods** | 37 | 37+ (with replicas) |
| **Total CPU Request** | **~10 cores** | **15+ cores** |
| **Total CPU Limit** | **~20 cores** | **30+ cores** |
| **Total Memory Request** | **~19 GB** | **32+ GB** |
| **Total Memory Limit** | **~38 GB** | **64+ GB** |
| **Storage (ODF)** | **150 GB** | **300+ GB** |

**Note:** Production deployments should scale Koku API reads, Celery workers, and add Trino workers based on data volume.

### Required OpenShift Components

#### 1. OpenShift Data Foundation (ODF)
```bash
# Verify ODF is installed
oc get csv -n openshift-storage | grep odf-operator

# Verify NooBaa (S3-compatible storage) is running
oc get noobaa -n openshift-storage

# Check available storage
oc get pvc -n openshift-storage
```

**Minimum ODF Requirements:**
- **Storage Class:** `ocs-storagecluster-ceph-rbd` or equivalent
- **Disk Space:** 150GB+ for development (300GB+ for production)
- **S3 Endpoint:** NooBaa S3 service accessible
- **Credentials:** Auto-discovered from `noobaa-admin` secret

#### 2. Kafka / Strimzi

**Automated Deployment (Recommended):**
```bash
# Deploy Strimzi operator and Kafka cluster
./scripts/deploy-strimzi.sh

# Script will:
# - Install Strimzi operator (version 0.45.1)
# - Deploy Kafka cluster (version 3.8.0)
# - Auto-detect platform (Kubernetes/OpenShift)
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
oc wait kafka/cost-mgmt-kafka --for=condition=Ready --timeout=300s -n kafka
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
┃  APPLICATION LAYER (cost-management-onprem chart)                      ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛

    ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
    │   Koku API      │   │ Kafka Listener  │   │  MASU Workers   │
    │   (Django)      │   │   (Celery)      │   │   (Celery)      │
    └────────┬────────┘   └────────┬────────┘   └────────┬────────┘
             │                     │                      │
             │                     │                      │
             └─────────────────────┴──────────────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │   PostgreSQL (Koku DB)       │
                    │  • Summary tables            │
                    │  • Metadata                  │
                    │  • Application state         │
                    └──────────────────────────────┘

┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃  DATA PROCESSING LAYER (cost-management-infrastructure chart)          ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛

    ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
    │     Trino       │   │      Hive       │   │   PostgreSQL    │
    │  Coordinator    │──▶│   Metastore     │──▶│ (Metastore DB)  │
    │ (Query Engine)  │   │ (Table Metadata)│   │   (Metadata)    │
    └────────┬────────┘   └────────┬────────┘   └─────────────────┘
             │                     │
             │                     │
             └─────────┬───────────┘
                       │
                       ▼
            ┌─────────────────────────┐
            │   S3 Storage (NooBaa)   │
            │  • Parquet files        │
            │  • Raw CSVs             │
            │  • Monthly partitions   │
            └─────────────────────────┘
                       ▲
                       │
                       │ (uploads)
                       │
            ┌──────────┴──────────┐
            │   MASU Workers      │
            └─────────────────────┘

┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃  MESSAGE QUEUE (Kafka/Strimzi - deployed separately)                   ┃
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
3. **Parquet Conversion:** MASU → S3 (Parquet files)
4. **Trino Tables:** Hive Metastore → Trino (query layer)
5. **Aggregation:** Trino SQL → PostgreSQL (summary tables)
6. **API Access:** Koku API → PostgreSQL (serve data)

---

## Installation Steps

### Step 1: Prepare Namespace

```bash
# Create namespace for Cost Management
export NAMESPACE=cost-mgmt
oc new-project $NAMESPACE

# Verify namespace
oc project $NAMESPACE
```

### Step 2: Deploy Infrastructure Chart

The infrastructure chart deploys the data processing layer: PostgreSQL, Hive Metastore, Trino, and Redis.

**Option A: Using Helm directly (manual control)**
```bash
cd /path/to/ros-helm-chart

# Deploy infrastructure chart
helm install cost-management-infrastructure ./cost-management-infrastructure \
  --namespace $NAMESPACE \
  --create-namespace \
  --wait \
  --timeout 10m

# Verify infrastructure pods
oc get pods -n $NAMESPACE | grep -E "postgres|hive|trino"
```

**Option B: Using provided script (Infrastructure Only)**

This script deploys **only the infrastructure chart** with automated setup and migration handling.

**Features:**
- 🎯 Automated infrastructure deployment
- 📦 PostgreSQL, Trino, Hive Metastore, Redis setup
- 🔄 Automatic database migration execution
- ✅ Component health verification

**Note:** This script is also used internally by `install-cost-management-complete.sh` (Option C) for infrastructure deployment.

```bash
# Deploy only infrastructure
./scripts/bootstrap-infrastructure.sh --namespace cost-mgmt

# With custom release name
./scripts/bootstrap-infrastructure.sh --namespace cost-mgmt --release-name my-infra

# Skip migrations (run manually later)
./scripts/bootstrap-infrastructure.sh --namespace cost-mgmt --skip-migrations
```

**Option C: Use automated script (recommended - see Step 4)**
Skip to Step 4 for automated deployment of **both** infrastructure and application charts using `install-cost-management-complete.sh`.

**Expected Pods:**
- `postgres-0` (StatefulSet, Ready 1/1)
- `hive-metastore-0` (StatefulSet, Ready 1/1)
- `hive-metastore-db-0` (StatefulSet, Ready 1/1)
- `trino-coordinator-0` (StatefulSet, Ready 1/1)
- `trino-worker-*` (StatefulSet, Ready 1/1 each)

**Verify Infrastructure:**
```bash
# Check PostgreSQL
oc exec -n $NAMESPACE postgres-0 -- psql -U koku -d koku -c "SELECT version();"

# Check Hive Metastore
oc logs -n $NAMESPACE hive-metastore-0 --tail=20

# Check Trino
oc exec -n $NAMESPACE trino-coordinator-0 -- trino --execute "SHOW CATALOGS;"
# Expected: hive, postgres, system
```

### Step 3: Deploy Cost Management Chart

The application chart deploys Koku (API, listeners, workers).

**Option A: Using Helm directly (manual control)**
```bash
# Deploy cost management application chart
helm install cost-management-onprem ./cost-management-onprem \
  --namespace $NAMESPACE \
  --wait \
  --timeout 10m \
  --set kafka.bootstrap_servers="cost-mgmt-kafka-kafka-bootstrap.kafka.svc.cluster.local:9092"

# Verify application pods
oc get pods -n $NAMESPACE | grep koku
```

**Option B: Using provided script (Application Only)**

This script deploys **only the Cost Management application chart** (Koku API, MASU, Celery workers).

**⚠️ Prerequisites:** Infrastructure chart must be deployed first (see Step 2 or use `bootstrap-infrastructure.sh`)

**Features:**
- 🔐 Automatic secret creation (Django, Sources API, Hive Metastore, S3)
- 🔍 Auto-discovers S3 credentials from ODF
- ✅ Chart validation and linting before deployment
- 🎯 Pod readiness checks and status reporting

**Note:** This script is also used internally by `install-cost-management-complete.sh` (Option C) for application deployment.

```bash
# Deploy only the application chart
./scripts/install-cost-helm-chart.sh

# With custom namespace
NAMESPACE=my-namespace ./scripts/install-cost-helm-chart.sh

# Show deployment status
./scripts/install-cost-helm-chart.sh status

# Clean uninstall
./scripts/install-cost-helm-chart.sh cleanup
```

**Option C: Use automated script (recommended - see Step 4)**
Skip to Step 4 for automated deployment of **both** infrastructure and application charts using `install-cost-management-complete.sh`.

**Expected Pods:**
- `koku-koku-api-*` (Deployment, multiple replicas)
- `koku-koku-api-listener-*` (Deployment)
- `koku-koku-worker-*` (Deployment, multiple replicas)

**Verify Application:**
```bash
# Check Koku API
oc exec -n $NAMESPACE $(oc get pod -n $NAMESPACE -l app=koku-api -o name | head -1) \
  -- python manage.py showmigrations --database=default

# Check Kafka listener
oc logs -n $NAMESPACE $(oc get pod -n $NAMESPACE -l app=koku-api-listener -o name) --tail=50
```

### Step 4: Automated Installation (Recommended)

**The easiest way:** Use the automated installation script that deploys both infrastructure and application charts:

```bash
cd /path/to/ros-helm-chart/scripts

# Run automated installation (recommended)
./install-cost-management-complete.sh
```

**What the script does:**
1. ✅ Verifies pre-requirements (ODF, Kafka)
2. ✅ Auto-discovers ODF S3 credentials
3. ✅ Creates namespace if needed
4. ✅ Deploys infrastructure chart via `bootstrap-infrastructure.sh` (PostgreSQL, Hive, Trino, Redis)
5. ✅ Deploys cost management chart via `install-cost-helm-chart.sh` (Koku API, listeners, workers)
6. ✅ Runs database migrations automatically
7. ✅ Verifies all components are healthy

**Architecture:**
This script orchestrates the complete deployment by calling:
- **Phase 1:** `bootstrap-infrastructure.sh` (infrastructure chart)
- **Phase 2:** `install-cost-helm-chart.sh` (application chart)

This modular approach allows you to run each phase independently if needed (see Steps 2 and 3).

**Script Features:**
- **Auto-discovery:** Finds ODF S3 credentials automatically
- **Non-interactive:** CI/CD friendly (no prompts)
- **Validated:** Each step is checked before proceeding
- **Error reporting:** Clear messages if anything fails
- **Idempotent:** Safe to run multiple times

**Customization with Environment Variables:**
```bash
# Custom namespace
NAMESPACE=my-namespace ./install-cost-management-complete.sh

# Custom Kafka configuration
KAFKA_NAMESPACE=my-kafka \
KAFKA_CLUSTER=my-cluster \
./install-cost-management-complete.sh

# All options
NAMESPACE=my-namespace \
KAFKA_NAMESPACE=my-kafka \
KAFKA_CLUSTER=my-cluster \
./install-cost-management-complete.sh
```

**When to use manual deployment (Steps 2-3):**
- You need fine-grained control over each component
- You're deploying in a restricted environment
- You want to customize chart values files
- You're debugging deployment issues

---

## Post-Installation Verification

### 1. Check All Pods are Running

```bash
# All pods should be Ready and Running
oc get pods -n $NAMESPACE

# Expected output (no CrashLoopBackOff, no Error)
NAME                                    READY   STATUS    RESTARTS   AGE
postgres-0                              1/1     Running   0          5m
hive-metastore-0                        1/1     Running   0          5m
hive-metastore-db-0                     1/1     Running   0          5m
trino-coordinator-0                     1/1     Running   0          5m
trino-worker-0                          1/1     Running   0          5m
koku-koku-api-*                         1/1     Running   0          3m
koku-koku-api-listener-*                1/1     Running   0          3m
koku-koku-worker-*                      1/1     Running   0          3m
```

### 2. Verify Database

```bash
# Check PostgreSQL connectivity and schema
oc exec -n $NAMESPACE postgres-0 -- psql -U koku -d koku -c "\dt" | head -20

# Expected: Many tables (reporting_*, api_*, etc.)
```

### 3. Verify Trino

```bash
# Check Trino catalogs
oc exec -n $NAMESPACE trino-coordinator-0 -- trino --execute "SHOW CATALOGS;"

# Expected output:
# hive
# postgres
# system

# Check Hive schemas (should be empty initially)
oc exec -n $NAMESPACE trino-coordinator-0 -- trino --execute "SHOW SCHEMAS IN hive;"
```

### 4. Verify S3 Storage

```bash
# Get S3 credentials from ODF
S3_ENDPOINT=$(oc get route s3 -n openshift-storage -o jsonpath='{.spec.host}')
S3_ACCESS_KEY=$(oc get secret noobaa-admin -n openshift-storage -o jsonpath='{.data.AWS_ACCESS_KEY_ID}' | base64 -d)
S3_SECRET_KEY=$(oc get secret noobaa-admin -n openshift-storage -o jsonpath='{.data.AWS_SECRET_ACCESS_KEY}' | base64 -d)

echo "S3 Endpoint: https://$S3_ENDPOINT"
echo "Access Key: $S3_ACCESS_KEY"

# Verify bucket exists (using aws-cli or s3cmd)
aws s3 ls --endpoint-url https://$S3_ENDPOINT
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
5. ✅ **Processing** - CSV → Parquet conversion, Trino table creation
6. ✅ **Trino** - Validates tables and data
7. ✅ **Aggregation** - Trino SQL → PostgreSQL summary tables
8. ✅ **Validation** - Verifies cost calculations

### Running the Test

```bash
cd /path/to/ros-helm-chart/scripts

# Run E2E test
./cost-mgmt-ocp-dataflow.sh

# With force cleanup (recommended for repeated runs)
./cost-mgmt-ocp-dataflow.sh --force
```

### Expected Output

```
✅ E2E SMOKE TEST PASSED

Phases: 8/8 passed
  ✅ preflight
  ✅ migrations
  ✅ kafka_validation
  ✅ provider
  ✅ data_upload
  ✅ processing
  ✅ trino
  ✅ validation

Total Time: ~2-3 minutes
```

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
oc port-forward -n cost-mgmt pod/postgres-0 5432:5432 &

# Connect and query
psql -h localhost -U koku -d koku << 'SQL'
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

#### 2. Parquet Files (in S3)
CSVs are converted to Parquet and stored in S3:
```
s3://cost-data/
  └── org1234567/
      └── ocp/
          └── source=test-cluster-123/
              └── year=2025/
                  └── month=11/
                      └── openshift_pod_usage_line_items/
                          └── *.parquet
```

#### 3. Trino Tables
Parquet files are exposed as Trino tables:
```sql
-- Raw hourly data
SELECT * FROM hive.org1234567.openshift_pod_usage_line_items;

-- Daily aggregated data (partitioned by source, year, month)
SELECT * FROM hive.org1234567.openshift_pod_usage_line_items_daily;
```

#### 4. PostgreSQL Summary Tables
Trino aggregates data into PostgreSQL:
```sql
-- Final summary table (used by Koku API)
SELECT * FROM org1234567.reporting_ocpusagelineitem_daily_summary;
```

---

## Troubleshooting

### Common Issues

#### 1. Hive Metastore Crash Loop

**Symptom:** `hive-metastore-0` pod in CrashLoopBackOff

**Cause:** Database password mismatch or connection issues

**Solution:**
```bash
# Delete pod to force clean restart
oc delete pod -n $NAMESPACE hive-metastore-0

# Verify metastore DB is ready
oc exec -n $NAMESPACE hive-metastore-db-0 -- psql -U metastore -d metastore -c "SELECT 1;"
```

#### 2. Trino Cannot Access PostgreSQL

**Symptom:** Trino queries fail with "connection refused" or "authentication failed"

**Cause:** NetworkPolicy blocking traffic or stale credentials

**Solution:**
```bash
# Verify NetworkPolicy allows Trino → PostgreSQL
oc get networkpolicy -n $NAMESPACE postgresql-access -o yaml

# Restart Trino pods to pick up new credentials
oc delete pod -n $NAMESPACE trino-coordinator-0
oc delete pod -n $NAMESPACE trino-worker-0
```

#### 3. Kafka Listener Not Receiving Messages

**Symptom:** E2E test hangs at "Processing" phase

**Cause:** Kafka connection issues or missing topic

**Solution:**
```bash
# Check Kafka cluster is healthy
oc get kafka -n kafka

# Verify topic exists
oc exec -n kafka cost-mgmt-kafka-kafka-0 -- bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 \
  --list | grep platform.upload.announce

# Check listener logs
oc logs -n $NAMESPACE $(oc get pod -n $NAMESPACE -l app=koku-api-listener -o name) --tail=100
```

#### 4. E2E Test Validation Failures

**Symptom:** Test passes all phases but validation shows incorrect data

**Cause:** Old data from previous runs

**Solution:**
```bash
# Run test with force cleanup
./cost-mgmt-ocp-dataflow.sh --force

# Or manually clear summary table
oc exec -n $NAMESPACE postgres-0 -- psql -U koku -d koku -c \
  "DELETE FROM org1234567.reporting_ocpusagelineitem_daily_summary WHERE cluster_id = 'test-cluster-123';"
```

#### 5. Nise Generates Random Data

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
# Upgrade infrastructure
helm upgrade cost-management-infrastructure ./cost-management-infrastructure \
  --namespace $NAMESPACE \
  --reuse-values

# Upgrade application
helm upgrade cost-management-onprem ./cost-management-onprem \
  --namespace $NAMESPACE \
  --reuse-values
```

### Scaling Workers

```bash
# Scale MASU workers for higher throughput
oc scale deployment koku-koku-worker -n $NAMESPACE --replicas=5

# Scale Trino workers for better query performance
oc scale statefulset trino-worker -n $NAMESPACE --replicas=3
```

### Database Backups

```bash
# Backup PostgreSQL (Koku DB)
oc exec -n $NAMESPACE postgres-0 -- pg_dump -U koku koku > koku-backup-$(date +%Y%m%d).sql

# Backup Hive Metastore DB
oc exec -n $NAMESPACE hive-metastore-db-0 -- pg_dump -U metastore metastore > metastore-backup-$(date +%Y%m%d).sql
```

### Monitoring

```bash
# Watch pod resource usage
oc adm top pods -n $NAMESPACE

# Monitor Celery queue (MASU workers)
oc exec -n $NAMESPACE $(oc get pod -n $NAMESPACE -l app=koku-worker -o name | head -1) \
  -- celery -A koku inspect active

# Check Trino query history
oc exec -n $NAMESPACE trino-coordinator-0 -- trino --execute \
  "SELECT query_id, state, query FROM system.runtime.queries ORDER BY created DESC LIMIT 10;"
```

---

## Additional Resources

- **Project Repository:** https://github.com/project-koku
- **Koku Documentation:** https://koku.readthedocs.io/
- **Trino Documentation:** https://trino.io/docs/current/
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

