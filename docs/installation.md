# Cost Management On-Premise Installation Guide

Complete installation methods, prerequisites, and upgrade procedures for the Cost Management On-Premise Helm chart.

## Table of Contents
- [Prerequisites](#prerequisites)
- [Installation Methods](#installation-methods)
- [OpenShift Prerequisites](#openshift-prerequisites)
- [Upgrade Procedures](#upgrade-procedures)
- [Verification](#verification)
- [Resource Requirements by Component](#resource-requirements-by-component)
- [E2E Validation (OCP Dataflow)](#e2e-validation-ocp-dataflow)
- [Troubleshooting Installation](#troubleshooting-installation)

## Prerequisites

### Required Tools

The installation scripts require the following tools:

```bash
# Required
curl    # For downloading releases from GitHub
jq      # For parsing JSON responses
helm    # For installing Helm charts (v3+)
kubectl # For Kubernetes cluster access

# Required for E2E Testing
python3      # Python 3 interpreter (for NISE data generation)
python3-venv # Virtual environment module (for NISE isolation)
```

### Installation by Platform

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install curl jq python3 python3-venv

# RHEL/CentOS/Fedora
sudo dnf install curl jq python3 python3-venv

# macOS
brew install curl jq

# Install Helm (all platforms)
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

### Cluster Access

Ensure you have:
- Valid kubeconfig with cluster admin or appropriate namespace permissions
- Ability to create namespaces (or existing target namespace)
- Sufficient cluster resources (see [Configuration Guide](configuration.md))

---

## Installation Methods

### Method 1: Automated Installation (Recommended)

The easiest way to install using the automation script:

```bash
# Install latest release with default settings
./scripts/install-helm-chart.sh

# Custom namespace
export NAMESPACE=cost-onprem
./scripts/install-helm-chart.sh

# Custom release name
export HELM_RELEASE_NAME=cost-onprem
./scripts/install-helm-chart.sh

# Use local chart for development
export USE_LOCAL_CHART=true
./scripts/install-helm-chart.sh
```

**What the script does (Two-Phase Deployment):**

The script deploys a unified chart containing all components:

**Infrastructure:**
- PostgreSQL (unified database for Koku, Sources, ROS, Kruize)
- Valkey (caching and Celery broker)

**Applications:**
- Koku API (reads, writes, masu, listener) - includes Sources API endpoints
- Celery Workers (background processing)
- ROS components (API, processor, housekeeper)
- UI and Ingress

**Features:**
- ✅ Two-phase deployment (infrastructure first, then application)
- ✅ Automatic secret creation (Django, S3 credentials)
- ✅ Auto-discovers ODF S3 credentials
- ✅ OpenShift platform verification
- ✅ Automatic upgrade detection
- ✅ Perfect for CI/CD pipelines
- ✅ Automatic fallback to local chart if GitHub unavailable

**Environment Variables:**
- `HELM_RELEASE_NAME`: Helm release name (default: `cost-onprem`)
- `NAMESPACE`: Target namespace (default: `cost-onprem`)
- `VALUES_FILE`: Path to custom values file
- `USE_LOCAL_CHART`: Use local chart instead of GitHub release (default: `false`)
- `LOCAL_CHART_PATH`: Path to local chart directory (default: `../cost-onprem`)

**Note**: JWT authentication is automatically enabled on OpenShift.

---

### Method 2: GitHub Release Installation

For CI/CD systems that prefer direct control:

```bash
# Get latest release URL dynamically
LATEST_URL=$(curl -s https://api.github.com/repos/insights-onprem/cost-onprem-chart/releases/latest | \
  jq -r '.assets[] | select(.name | endswith(".tgz")) | .browser_download_url')

# Download and install
curl -L -o cost-onprem-latest.tgz "$LATEST_URL"
helm install cost-onprem cost-onprem-latest.tgz \
  --namespace cost-onprem \
  --create-namespace

# Verify installation
helm status cost-onprem -n cost-onprem
```

**With custom values:**
```bash
helm install cost-onprem cost-onprem-latest.tgz \
  --namespace cost-onprem \
  --create-namespace \
  --values my-values.yaml
```

---

### Method 3: Helm Repository (Future)

```bash
# Add Helm repository (once published)
helm repo add cost-onprem https://insights-onprem.github.io/cost-onprem-chart
helm repo update

# Install from repository
helm install cost-onprem cost-onprem/cost-onprem \
  --namespace cost-onprem \
  --create-namespace
```

---

### Method 4: Local Source Installation

For development, testing, or custom modifications:

```bash
# Clone the repository
git clone https://github.com/insights-onprem/cost-onprem-chart.git
cd cost-onprem-chart

# Method A: Using installation script
export USE_LOCAL_CHART=true
./scripts/install-helm-chart.sh

# Method B: Direct Helm installation
helm install cost-onprem ./cost-onprem \
  --namespace cost-onprem \
  --create-namespace

# With custom values
helm install cost-onprem ./cost-onprem \
  --namespace cost-onprem \
  --create-namespace \
  --values custom-values.yaml
```

---

## OpenShift Prerequisites

### 1. OpenShift Data Foundation (ODF)

ODF must be installed and operational:

```bash
# Verify ODF installation
oc get noobaa -n openshift-storage
oc get storagecluster -n openshift-storage

# Check S3 service availability
oc get route s3 -n openshift-storage
```

**ODF endpoints:**
- Internal: `s3.openshift-storage.svc.cluster.local:443`
- External: Check routes in `openshift-storage` namespace

**Storage Class Requirement:**

⚠️ **Important**: Use **Direct Ceph RGW** (`ocs-storagecluster-ceph-rgw`) instead of NooBaa (`ocs-storagecluster-ceph-rbd`) for strong consistency. NooBaa has eventual consistency issues that can cause ROS processing failures with 403 errors.

```bash
# Verify Ceph RGW StorageClass is available
oc get storageclass ocs-storagecluster-ceph-rgw

# Create ObjectBucketClaim (OBC) for Direct Ceph RGW (Recommended)
cat <<EOF | oc apply -f -
apiVersion: objectbucket.io/v1alpha1
kind: ObjectBucketClaim
metadata:
  name: ros-data-ceph
  namespace: cost-onprem
spec:
  generateBucketName: ros-data-ceph
  storageClassName: ocs-storagecluster-ceph-rgw
EOF

# Wait for OBC to provision
oc wait --for=condition=Ready obc/ros-data-ceph -n cost-onprem --timeout=5m
```

**Storage Class Selection:**
- ✅ **Direct Ceph RGW**: `ocs-storagecluster-ceph-rgw` (recommended for ROS)
- ⚠️ **NooBaa**: `ocs-storagecluster-ceph-rbd` (eventual consistency issues)

**OBC Auto-Detection**: The installation script automatically detects ObjectBucketClaims, extracts configuration (bucket name, endpoint, credentials), and configures the Helm deployment. No manual credential management needed when using OBC.

### 2. ODF S3 Credentials Secret (Alternative to OBC)

Create credentials secret in deployment namespace:

```bash
# Create secret with ODF S3 credentials
kubectl create secret generic cost-onprem-odf-credentials \
  --namespace=cost-onprem \
  --from-literal=access-key=<your-access-key> \
  --from-literal=secret-key=<your-secret-key>

# Verify secret
kubectl get secret cost-onprem-odf-credentials -n cost-onprem
```

### Getting ODF Credentials

#### Method 1: OpenShift Console (Recommended)

1. Navigate to **Storage** → **Object Storage**
2. Create or select bucket (e.g., `ros-data`)
3. Go to **Access Keys** tab
4. Click **Create Access Key**
5. **Important**: Copy both keys immediately (secret key shown only once)

#### Method 2: NooBaa CLI

```bash
# Install NooBaa CLI
curl -LO https://github.com/noobaa/noobaa-operator/releases/download/v5.13.0/noobaa-linux
chmod +x noobaa-linux
sudo mv noobaa-linux /usr/local/bin/noobaa

# Create account and bucket
noobaa account create ros-account -n openshift-storage
noobaa bucket create ros-data -n openshift-storage
noobaa account attach ros-account --bucket ros-data -n openshift-storage

# Get credentials
noobaa account show ros-account -n openshift-storage
```

#### Method 3: Using Admin Credentials (Not Recommended)

```bash
# Get admin credentials from noobaa-admin secret
kubectl get secret noobaa-admin -n openshift-storage \
  -o jsonpath='{.data.AWS_ACCESS_KEY_ID}' | base64 -d

kubectl get secret noobaa-admin -n openshift-storage \
  -o jsonpath='{.data.AWS_SECRET_ACCESS_KEY}' | base64 -d

# ⚠️ Warning: These are admin credentials with full access
```

#### Method 4: External Secret Management

```bash
# Example with Vault
vault kv get -field=access_key secret/odf/ros-credentials
vault kv get -field=secret_key secret/odf/ros-credentials

# Example with Sealed Secrets
kubectl create secret generic cost-onprem-odf-credentials \
  --from-literal=access-key=<key> \
  --from-literal=secret-key=<secret> \
  --dry-run=client -o yaml | \
  kubeseal -o yaml > sealed-secret.yaml
```

**Security Best Practices:**
- ✅ Use dedicated service accounts (not admin credentials)
- ✅ Rotate credentials regularly
- ✅ Store in external secret management (Vault, Sealed Secrets)
- ✅ Use least-privilege access (specific buckets only)
- ❌ Never commit credentials to version control

### 3. Namespace Permissions

Ensure you have permissions to:
- Create secrets in target namespace
- Deploy Helm charts
- Access ODF resources
- Create routes (OpenShift)

```bash
# Verify permissions
oc auth can-i create secrets -n cost-onprem
oc auth can-i create deployments -n cost-onprem
oc auth can-i create routes -n cost-onprem
```

### 4. Resource Requirements

**Single Node OpenShift (SNO):**
- SNO cluster with ODF installed
- 30GB+ block devices for ODF
- Additional 6GB RAM for Cost Management On-Premise workloads
- Additional 2 CPU cores

**See [Configuration Guide](configuration.md) for detailed requirements**

### 5. Kafka (Strimzi)

Kafka is required for the Cost Management data pipeline (OCP metrics ingestion).

**Automated Deployment (Recommended):**
```bash
# Deploy Strimzi operator and Kafka cluster
./scripts/deploy-strimzi.sh

# Script will:
# - Install Strimzi operator (version 0.45.1)
# - Deploy Kafka cluster (version 3.8.0)
# - Verify OpenShift platform
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

### 6. User Workload Monitoring (Required for ROS Metrics)

User Workload Monitoring must be enabled for Prometheus to scrape ServiceMonitors deployed by this chart. Without it, the ROS data pipeline will not function - ServiceMonitors will be created but no metrics will be collected.

**Check if User Workload Monitoring is enabled:**

```bash
# Check for prometheus-user-workload pods
oc get pods -n openshift-user-workload-monitoring

# If no pods are found, user workload monitoring is not enabled
```

**Enable User Workload Monitoring:**

```bash
cat <<EOF | oc apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: cluster-monitoring-config
  namespace: openshift-monitoring
data:
  config.yaml: |
    enableUserWorkload: true
EOF
```

**Verify:**

```bash
# Wait for prometheus-user-workload pods to start
oc get pods -n openshift-user-workload-monitoring -w

# Expected output: prometheus-user-workload-0, prometheus-user-workload-1, thanos-ruler-user-workload-*
```

**Warning:** Without User Workload Monitoring enabled, the deployment will appear successful (all pods running, ServiceMonitors created), but the ROS data pipeline will produce no metrics or recommendations. This is a **silent failure** - always verify prometheus-user-workload pods are running before testing the data pipeline.

---

## Upgrade Procedures

### Upgrade Using Scripts (Recommended)

```bash
# Upgrade to latest release automatically
./scripts/install-helm-chart.sh

# The script detects existing installations and performs upgrades
# Uses GitHub releases by default
```

### Manual Helm Upgrade

#### From GitHub Release

```bash
# Get latest release
LATEST_URL=$(curl -s https://api.github.com/repos/insights-onprem/cost-onprem-chart/releases/latest | \
  jq -r '.assets[] | select(.name | endswith(".tgz")) | .browser_download_url')

# Download and upgrade
curl -L -o cost-onprem-latest.tgz "$LATEST_URL"
helm upgrade cost-onprem cost-onprem-latest.tgz -n cost-onprem

# With custom values
helm upgrade cost-onprem cost-onprem-latest.tgz -n cost-onprem --values my-values.yaml
```

#### From Local Source

```bash
# Using script
export USE_LOCAL_CHART=true
./scripts/install-helm-chart.sh

# Direct Helm command
helm upgrade cost-onprem ./cost-onprem -n cost-onprem
```

### Upgrade Considerations

**Before upgrading:**
1. Check release notes for breaking changes
2. Backup persistent data if needed
3. Verify cluster resources are sufficient
4. Test in non-production environment first

**During upgrade:**
- Helm performs rolling updates by default
- Some downtime may occur during database upgrades
- Monitor pod status: `kubectl get pods -n cost-onprem -w`

**After upgrade:**
```bash
# Verify upgrade
./scripts/install-helm-chart.sh status

# Run health checks
./scripts/install-helm-chart.sh health

# Check version
helm list -n cost-onprem
```

---

## Verification

### Deployment Status

```bash
# Check Helm release
helm status cost-onprem -n cost-onprem

# Check all pods
kubectl get pods -n cost-onprem

# Wait for all pods to be ready
kubectl wait --for=condition=ready pod -l app.kubernetes.io/instance=cost-onprem -n cost-onprem --timeout=300s
```

### Service Health

```bash
# Run automated health checks
./scripts/install-helm-chart.sh health

# Test ingress endpoint
curl -k https://<route-host>/ready

# Check API endpoints
curl http://localhost:32061/api/ros/status
```

### Storage Verification

```bash
# Check persistent volume claims
kubectl get pvc -n cost-onprem

# Verify all PVCs are bound
kubectl get pvc -n cost-onprem | grep -v Bound && echo "ISSUE: Unbound PVCs found" || echo "OK: All PVCs bound"

# Check storage class
kubectl get pvc -n cost-onprem -o jsonpath='{.items[*].spec.storageClassName}' | tr ' ' '\n' | sort -u
```

### Service Connectivity

```bash
# Test database connections
kubectl exec -it deployment/cost-onprem-ros-api -n cost-onprem -- \
  env | grep DATABASE_URL

# Test Kafka connectivity
kubectl exec -it statefulset/cost-onprem-kafka -n cost-onprem -- \
  kafka-topics.sh --list --bootstrap-server localhost:29092

# Test ODF access
oc rsh -n cost-onprem deployment/cost-onprem-ingress -- \
  aws s3 ls --endpoint-url https://s3.openshift-storage.svc.cluster.local
```

---

## Resource Requirements by Component

> **Note:** Resource allocations are aligned with the SaaS Clowder configuration from:
> - **Koku:** `deploy/clowdapp.yaml` in [insights-onprem/koku](https://github.com/insights-onprem/koku)
> - **ROS:** `clowdapp.yaml` in [insights-onprem/ros-ocp-backend](https://github.com/insights-onprem/ros-ocp-backend)

### Infrastructure Components

| Component | Pods | CPU Request | CPU Limit | Memory Request | Memory Limit |
|-----------|------|-------------|-----------|----------------|--------------|
| **PostgreSQL** | 1 | 500m | 1000m | 1Gi | 2Gi |
| **Valkey** | 1 | 100m | 500m | 256Mi | 512Mi |
| **Subtotal** | **2** | **600m** | **1.5 cores** | **1.25 GB** | **2.5 GB** |

### Application Components

| Component | Pods | CPU Request | CPU Limit | Memory Request | Memory Limit |
|-----------|------|-------------|-----------|----------------|--------------|
| **Koku API Reads** | 1-2 | 250m each | 500m each | 512Mi each | 1Gi each |
| **Koku API Writes** | 1 | 250m | 500m | 512Mi | 1Gi |
| **Koku API MASU** | 1 | 50m | 100m | 500Mi | 700Mi |
| **Koku Listener** | 1 | 150m | 300m | 300Mi | 600Mi |
| **Celery Beat** | 1 | 50m | 100m | 200Mi | 400Mi |
| **Celery Workers** | 11-21 | 100m each | 200m each | 256Mi-512Mi | 400Mi-1Gi |
| **ROS API** | 1 | 500m | 1000m | 1Gi | 1Gi |
| **ROS Processor** | 1 | 500m | 1000m | 1Gi | 1Gi |
| **ROS Poller** | 1 | 500m | 1000m | 1Gi | 1Gi |
| **ROS Housekeeper** | 1 | 500m | 1000m | 1Gi | 1Gi |
| **Kruize** | 1-2 | 200m | 1000m | 1Gi | 2Gi |
| **Subtotal** | **18-28** | **~4-6 cores** | **~8-12 cores** | **~9-14 Gi** | **~14-22 Gi** |

### Total Deployment Summary

| Scenario | Pods | CPU Request | CPU Limit | Memory Request | Memory Limit |
|----------|------|-------------|-----------|----------------|--------------|
| **OCP-Only (minimal)** | ~24 | ~7.5 cores | ~15 cores | ~16 Gi | ~28 Gi |
| **OCP on Cloud** | ~34 | ~9 cores | ~18 cores | ~21 Gi | ~36 Gi |

**Note:** See [Worker Deployment Scenarios](worker-deployment-scenarios.md) for detailed worker requirements by scenario.

---

## E2E Validation (OCP Dataflow)

After installation, validate the complete data pipeline using the OCP dataflow test.

### Prerequisites for E2E Testing

```bash
# Install Python dependencies (required for NISE data generation)
# Ubuntu/Debian
sudo apt-get install python3 python3-venv

# RHEL/CentOS/Fedora
sudo yum install python3 python3-venv

# macOS
brew install python3
```

**Note**: NISE (test data generator) is automatically installed in a Python virtual environment during test execution. No manual NISE installation required.

### Running the Tests

```bash
cd scripts

# Option 1: ROS-only E2E test with NISE-generated data (~5 minutes)
./test-ocp-dataflow-jwt.sh

# Option 2: Full Cost Management E2E test (~3 minutes)
./cost-mgmt-ocp-dataflow.sh --namespace cost-onprem --force
```

### What the ROS E2E Test Validates

1. ✅ **NISE Integration** - Automatic installation and production-like data generation (73 lines)
2. ✅ **Data Upload** - Generates realistic test data and uploads via JWT auth
3. ✅ **Ingress Processing** - CSV file uploaded to S3
4. ✅ **ROS Processing** - CSV downloaded from S3, parsed successfully (CRLF conversion)
5. ✅ **Kruize Integration** - Recommendations generated with actual CPU/memory values
6. ✅ **ROS-Only Mode** - Skips Koku processing for faster validation

### What the Cost Management Test Validates

1. ✅ **Preflight** - Environment checks
2. ✅ **Provider** - Creates OCP cost provider
3. ✅ **Data Upload** - Generates and uploads test data (CSV → TAR.GZ → S3)
4. ✅ **Kafka** - Publishes message to trigger processing
5. ✅ **Processing** - CSV parsing and data ingestion
6. ✅ **Database** - Validates data in PostgreSQL tables
7. ✅ **Aggregation** - Summary table generation
8. ✅ **Validation** - Verifies cost calculations

### Expected Output (ROS E2E Test)

```
[SUCCESS] ===== ROS E2E Test Summary =====

Upload Status: ✅ HTTP 202 Accepted
Koku Processing: ⏭️  Skipped (ROS-only test)
ROS Processing: ✅ CSV downloaded and parsed successfully
Kruize Status: ✅ Recommendations generated

Recommendation details (short_term cost optimization):
 experiment_name                    | interval_end_time | cpu_request | cpu_limit | memory_request | memory_limit
------------------------------------+-------------------+-------------+-----------+----------------+--------------
 org1234567;test-cluster-1769027891 | 2026-01-21 20:00  | 1.78 cores  | 1.78 cores| 3.64 GB        | 3.64 GB

[SUCCESS] ✅ ROS-ONLY TEST PASSED!
[SUCCESS] Found 1 recommendation(s) for cluster test-cluster-1769027891

Test Duration: ~5 minutes
Pipeline Validated: Ingress → ROS → Kruize → Recommendations
```

### Expected Output (Cost Management Test)

```
✅ E2E SMOKE TEST PASSED

Phases: 8/8 passed
  ✅ preflight
  ✅ migrations
  ✅ kafka_validation
  ✅ provider
  ✅ data_upload
  ✅ processing
  ✅ database
  ✅ validation

Total Time: ~2-3 minutes
```

### Verify Cost Data in PostgreSQL

```bash
# Port-forward to PostgreSQL
kubectl port-forward -n cost-onprem pod/cost-onprem-database-0 5432:5432 &

# Query aggregated cost data
psql -h localhost -U koku -d koku -c "
SELECT
    cluster_id,
    COUNT(*) as daily_rows,
    SUM(pod_usage_cpu_core_hours) as total_cpu_usage,
    SUM(pod_request_cpu_core_hours) as total_cpu_request
FROM reporting_ocpusagelineitem_daily_summary
WHERE cluster_id IS NOT NULL
GROUP BY cluster_id
LIMIT 5;
"
```

---

## Troubleshooting Installation

### Script Execution Issues

**Missing prerequisites:**
```bash
# Check required tools
which curl jq helm kubectl

# Install missing tools
sudo apt-get install curl jq  # Ubuntu/Debian
brew install curl jq           # macOS
```

**GitHub API rate limiting:**
```bash
# Check rate limit
curl -s https://api.github.com/rate_limit

# Use authentication token
export GITHUB_TOKEN="your_personal_access_token"
./scripts/install-helm-chart.sh
```

**Script permissions:**
```bash
# Make executable
chmod +x scripts/install-helm-chart.sh

# Run with explicit bash
bash scripts/install-helm-chart.sh
```

### Network Issues

```bash
# Test GitHub connectivity
curl -s https://api.github.com/repos/insights-onprem/cost-onprem-chart/releases/latest

# Verbose debugging
curl -v https://api.github.com/repos/insights-onprem/cost-onprem-chart/releases/latest

# Manual download
LATEST_URL=$(curl -s https://api.github.com/repos/insights-onprem/cost-onprem-chart/releases/latest | \
  jq -r '.assets[] | select(.name | endswith(".tgz")) | .browser_download_url')
curl -L -o cost-onprem-latest.tgz "$LATEST_URL"
```

### Resource Issues

```bash
# Check node resources
kubectl describe nodes | grep -A 5 "Allocated resources"

# Check available resources
kubectl top nodes  # requires metrics-server
```

**See [Troubleshooting Guide](troubleshooting.md) for comprehensive solutions**

---

## Next Steps

After successful installation:

1. **Configure Access**: See [Configuration Guide](configuration.md)
2. **Set Up JWT Auth**: See [JWT Authentication Guide](native-jwt-authentication.md)
3. **Configure TLS**: See [TLS Setup Guide](cost-management-operator-tls-setup.md)
4. **Run Tests**: See [Scripts Reference](../scripts/README.md)

---

**Related Documentation:**
- [Configuration Guide](configuration.md)
- [Platform Guide](platform-guide.md)
- [Quick Start Guide](quickstart.md)
- [Troubleshooting Guide](troubleshooting.md)

