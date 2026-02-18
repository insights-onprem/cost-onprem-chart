# Cost Management On-Premise Installation Guide

Complete installation methods, prerequisites, and upgrade procedures for the Cost Management On-Premise Helm chart.

## Table of Contents
- [Prerequisites](#prerequisites)
- [Installation Methods](#installation-methods)
  - [Method 1: Script-Based Installation (Recommended)](#method-1-script-based-installation-recommended)
  - [Method 2: Direct Helm Installation](#method-2-direct-helm-installation)
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
helm    # For installing Helm charts (v3+)
kubectl # For Kubernetes cluster access
jq      # For JSON processing

# Required for E2E Testing
python3      # Python 3 interpreter (for NISE data generation)
python3-venv # Virtual environment module (for NISE isolation)
```

### Installation by Platform

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install jq python3 python3-venv

# RHEL/CentOS/Fedora
sudo dnf install jq python3 python3-venv

# macOS
brew install jq

# Install Helm (all platforms)
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

### Cluster Access

Ensure you have:
- Valid kubeconfig with cluster admin or appropriate namespace permissions
- Ability to create namespaces (or existing target namespace)
- Sufficient cluster resources (see [Configuration Guide](../operations/configuration.md))

---

## Installation Methods

### Method 1: Script-Based Installation (Recommended)

The easiest way to install using the automation script. Best for most users, CI/CD pipelines, and quick deployments.

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
- Koku API (unified, masu, listener)
- Celery Workers (background processing)
- ROS components (API, processor, housekeeper)
- Sources API
- UI and Ingress

**Features:**
- ✅ Two-phase deployment (infrastructure first, then application)
- ✅ Automatic secret creation (Django, Sources, S3 credentials)
- ✅ Installs from the Helm chart repository (GitHub Pages)
- ✅ Auto-discovers S3 credentials (OBC, NooBaa, MinIO)
- ✅ OpenShift platform verification
- ✅ Automatic upgrade detection
- ✅ Perfect for CI/CD pipelines
- ✅ Version pinning support via `CHART_VERSION`

**Environment Variables:**
- `HELM_RELEASE_NAME`: Helm release name (default: `cost-onprem`)
- `NAMESPACE`: Target namespace (default: `cost-onprem`)
- `VALUES_FILE`: Path to custom values file
- `CHART_VERSION`: Pin a specific chart version (default: latest)
- `USE_LOCAL_CHART`: Use local chart instead of Helm repository (default: `false`)
- `LOCAL_CHART_PATH`: Path to local chart directory (default: `../cost-onprem`)

**Note**: JWT authentication is automatically enabled on OpenShift.

> **BYOI (Bring Your Own Infrastructure):** When `database.deploy: false` is set in your values file, the script skips PostgreSQL credential creation and expects you to have pre-created the database credentials secret. See [External Infrastructure (BYOI)](configuration.md#external-infrastructure-byoi) for details.
---

### Method 2: Direct Helm Installation

For administrators who prefer full control over the deployment or cannot use the `install-helm-chart.sh` script (e.g., GitOps/ArgoCD workflows, air-gapped environments, custom CI pipelines), you can install the chart directly with `helm install`. You must supply the cluster-specific values that the install script would normally auto-detect.

#### Chart Source Options

| Source | Use Case | Installation |
|--------|----------|--------------|
| Helm Repository | Production (recommended) | `helm repo add cost-onprem https://insights-onprem.github.io/cost-onprem-chart` |
| OCI Registry | Air-gapped, GitOps, oc-mirror | `helm pull oci://ghcr.io/insights-onprem/cost-onprem-chart/cost-onprem` |
| Local Source | Development, testing, modifications | Clone repo and use `./cost-onprem` directory |

**Helm Repository (recommended):**
```bash
# Add Helm repository
helm repo add cost-onprem https://insights-onprem.github.io/cost-onprem-chart
helm repo update

# Install latest version
helm install cost-onprem cost-onprem/cost-onprem \
  --namespace cost-onprem \
  --create-namespace

# Install a specific version
helm install cost-onprem cost-onprem/cost-onprem \
  --namespace cost-onprem \
  --create-namespace \
  --version 0.2.9
```

**Verify available versions:**
```bash
helm search repo cost-onprem
```

**OCI Registry (air-gapped/GitOps):**

The chart is also published as an OCI artifact to GitHub Container Registry. This is useful for:
- Air-gapped environments using `oc-mirror`
- GitOps workflows (ArgoCD, Flux) that prefer OCI references
- Environments where traditional Helm repositories are blocked

```bash
# Install latest version from OCI registry
helm install cost-onprem oci://ghcr.io/insights-onprem/cost-onprem-chart/cost-onprem \
  --namespace cost-onprem \
  --create-namespace

# Install a specific version
helm install cost-onprem oci://ghcr.io/insights-onprem/cost-onprem-chart/cost-onprem \
  --namespace cost-onprem \
  --create-namespace \
  --version 0.2.9

# Pull chart locally (for inspection or mirroring)
helm pull oci://ghcr.io/insights-onprem/cost-onprem-chart/cost-onprem --version 0.2.9

# Show available versions
helm show all oci://ghcr.io/insights-onprem/cost-onprem-chart/cost-onprem
```

> **Note:** OCI-based installation does not require `helm repo add`. The chart is fetched directly from the container registry.

**Local Source (for development):**
```bash
# Clone the repository
git clone https://github.com/insights-onprem/cost-onprem-chart.git
cd cost-onprem-chart

# Use ./cost-onprem in the helm install commands below
```

#### Step 1: Gather Cluster-Specific Values

The chart ships with safe defaults for offline templating (used by `oc-mirror`), but real deployments require actual cluster values. Gather these from your cluster:

```bash
# Cluster domain (for Route hostnames)
CLUSTER_DOMAIN=$(oc get ingress.config.openshift.io cluster -o jsonpath='{.spec.domain}')

# Default storage class
STORAGE_CLASS=$(kubectl get sc -o jsonpath='{.items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class=="true")].metadata.name}' | awk '{print $1}')

# Valkey fsGroup (from namespace supplemental-groups)
# First, create the namespace if it doesn't exist
oc create namespace cost-onprem --dry-run=client -o yaml | oc apply -f -
SUPP_GROUPS=$(oc get ns cost-onprem -o jsonpath='{.metadata.annotations.openshift\.io/sa\.scc\.supplemental-groups}')
FS_GROUP=$(echo "$SUPP_GROUPS" | cut -d'/' -f1)

# Keycloak URL (if using RHBK)
KEYCLOAK_NAMESPACE=$(oc get keycloaks.k8s.keycloak.org -A -o jsonpath='{.items[0].metadata.namespace}' 2>/dev/null)
KEYCLOAK_HOST=$(oc get keycloaks.k8s.keycloak.org -A -o jsonpath='{.items[0].status.hostname}' 2>/dev/null)
KEYCLOAK_URL="https://${KEYCLOAK_HOST}"
```

#### Step 2: Prepare S3 Storage

Create a credentials secret and note your S3 endpoint:

```bash
kubectl create secret generic my-s3-credentials \
  --namespace=cost-onprem \
  --from-literal=access-key="<YOUR_ACCESS_KEY>" \
  --from-literal=secret-key="<YOUR_SECRET_KEY>"
```

#### Step 3: Install with `--set` Flags

```bash
helm install cost-onprem ./cost-onprem \
  --namespace cost-onprem \
  --create-namespace \
  -f openshift-values.yaml \
  --set global.clusterDomain="$CLUSTER_DOMAIN" \
  --set global.storageClass="$STORAGE_CLASS" \
  --set valkey.securityContext.fsGroup="$FS_GROUP" \
  --set objectStorage.endpoint="<YOUR_S3_ENDPOINT>" \
  --set objectStorage.port=443 \
  --set objectStorage.useSSL=true \
  --set objectStorage.existingSecret="my-s3-credentials" \
  --set jwtAuth.keycloak.installed=true \
  --set jwtAuth.keycloak.namespace="$KEYCLOAK_NAMESPACE" \
  --set jwtAuth.keycloak.url="$KEYCLOAK_URL" \
  --wait
```

#### Complete Values Reference (Direct Install)

The table below lists every cluster-specific value, its chart default, and how to determine the correct value for your environment.

| Value | Chart Default | Description | How to Determine |
|-------|---------------|-------------|------------------|
| `global.clusterDomain` | `apps.cluster.local` | OpenShift wildcard domain for Routes | `oc get ingress.config.openshift.io cluster -o jsonpath='{.spec.domain}'` |
| `global.storageClass` | `ocs-storagecluster-ceph-rbd` | Default StorageClass for PVCs | `kubectl get sc` (look for the `(default)` annotation) |
| `global.volumeMode` | `Filesystem` | PVC volume mode | Usually `Filesystem`; change only for raw block storage |
| `objectStorage.endpoint` | `s3.openshift-storage.svc.cluster.local` | S3-compatible endpoint hostname | Your S3 provider's endpoint (e.g., `s3.amazonaws.com`, MinIO hostname) |
| `objectStorage.port` | `443` | S3 endpoint port | `443` for HTTPS, `80` for HTTP |
| `objectStorage.useSSL` | `true` | Use TLS for S3 connections | `true` for production, `false` for MinIO/dev |
| `objectStorage.existingSecret` | `""` | Pre-created credentials secret name | Name of the `Secret` you created in Step 2 |
| `valkey.securityContext.fsGroup` | *(unset)* | GID for Valkey PVC access on OpenShift | `oc get ns <NS> -o jsonpath='{.metadata.annotations.openshift\.io/sa\.scc\.supplemental-groups}'` (first number) |
| `jwtAuth.keycloak.installed` | `true` | Whether Keycloak is deployed | `true` if RHBK is installed, `false` otherwise |
| `jwtAuth.keycloak.url` | `""` | Keycloak external URL | `oc get route keycloak -n keycloak -o jsonpath='https://{.spec.host}'` |
| `jwtAuth.keycloak.namespace` | `""` | Namespace where Keycloak runs | Usually `keycloak` |
| `database.deploy` | `true` | Deploy bundled PostgreSQL StatefulSet | Set `false` to use an external database (see [BYOI](configuration.md#external-infrastructure-byoi)) |
| `valkey.deploy` | `true` | Deploy bundled Valkey Deployment | Set `false` to use an external Redis/Valkey (see [BYOI](configuration.md#external-infrastructure-byoi)) |

> **Important:** The chart defaults are designed for `oc-mirror` image discovery (offline templating). They produce syntactically valid manifests but point to placeholder hostnames. For a working deployment, you **must** override the values marked above with real cluster values.

#### Step 4: Create Required Secrets

The install script normally creates several secrets automatically. When installing directly, you must create them yourself:

```bash
# 1. Django secret key (required by Koku)
kubectl create secret generic cost-onprem-django \
  --namespace=cost-onprem \
  --from-literal=django-secret-key="$(openssl rand -base64 50 | tr -dc 'a-zA-Z0-9' | head -c 50)"

# 2. S3 credentials (if not already created in Step 2)
# See Step 2 above

# 3. Keycloak CA certificate (for TLS trust between oauth2-proxy and Keycloak)
# Extract the Keycloak CA certificate and create the secret:
oc get secret -n keycloak keycloak-tls -o jsonpath='{.data.ca\.crt}' | base64 -d > /tmp/keycloak-ca.crt
kubectl create secret generic keycloak-ca-cert \
  --namespace=cost-onprem \
  --from-file=ca.crt=/tmp/keycloak-ca.crt

# 4. Database credentials (required — created by install script normally, or pre-created for BYOI)
# When using database.deploy: false (external database), you must create this secret manually:
kubectl create secret generic cost-onprem-db-credentials \
  --namespace=cost-onprem \
  --from-literal=postgres-user="admin" \
  --from-literal=postgres-password="<admin_password>" \
  --from-literal=ros-user="ros_user" \
  --from-literal=ros-password="<ros_password>" \
  --from-literal=kruize-user="kruize_user" \
  --from-literal=kruize-password="<kruize_password>" \
  --from-literal=koku-user="koku_user" \
  --from-literal=koku-password="<koku_password>"
```

#### Step 5: Verify

```bash
# Check all pods are running
kubectl get pods -n cost-onprem -l app.kubernetes.io/instance=cost-onprem

# Check PVCs are bound
kubectl get pvc -n cost-onprem

# Check routes are created with correct hostnames
oc get routes -n cost-onprem
```

#### Example: Minimal `my-values.yaml` for Direct Install

Instead of passing many `--set` flags, you can create a values file:

```yaml
# my-values.yaml — cluster-specific overrides for direct helm install
global:
  clusterDomain: "apps.mycluster.example.com"
  storageClass: "gp3-csi"

objectStorage:
  endpoint: "s3.us-east-1.amazonaws.com"
  port: 443
  useSSL: true
  existingSecret: "my-s3-credentials"
  s3:
    region: "us-east-1"

valkey:
  securityContext:
    fsGroup: 1000740000  # From namespace supplemental-groups annotation

jwtAuth:
  keycloak:
    installed: true
    url: "https://keycloak-keycloak.apps.mycluster.example.com"
    namespace: "keycloak"
```

#### Example: BYOI `my-values.yaml` (External PostgreSQL + Valkey)

For deployments using external infrastructure (see also [docs/examples/byoi-values.yaml](../examples/byoi-values.yaml) for a minimal overlay):

```yaml
# my-byoi-values.yaml — external database and cache
global:
  clusterDomain: "apps.mycluster.example.com"
  storageClass: "gp3-csi"

database:
  deploy: false
  server:
    host: "my-postgres.example.com"
    port: 5432
    sslMode: require
  existingSecret: "cost-onprem-db-credentials"

valkey:
  deploy: false
  host: "my-redis.example.com"
  port: 6379

kafka:
  bootstrapServers: "my-kafka:9092"
  securityProtocol: "PLAINTEXT"

objectStorage:
  endpoint: "s3.us-east-1.amazonaws.com"
  port: 443
  useSSL: true
  existingSecret: "my-s3-credentials"
  s3:
    region: "us-east-1"

jwtAuth:
  keycloak:
    installed: true
    url: "https://keycloak-keycloak.apps.mycluster.example.com"
    namespace: "keycloak"
```

Then install:

```bash
helm install cost-onprem ./cost-onprem \
  --namespace cost-onprem \
  --create-namespace \
  -f openshift-values.yaml \
  -f my-values.yaml \
  --wait
```

---

## OpenShift Prerequisites

### 1. S3-Compatible Object Storage

The chart requires S3-compatible object storage. ODF is **not required** — any S3 provider works. For full configuration details, see the [Storage Configuration](configuration.md#storage-configuration) section.

**Supported backends:**

| Backend | Use Case | Auto-Detected |
|---------|----------|---------------|
| AWS S3 | Production (disconnected AWS) | No — configure in `values.yaml` |
| Direct Ceph RGW (ODF) | Production (OpenShift with ODF) | Yes — via OBC |
| MinIO | Development/Testing | Yes — via `MINIO_ENDPOINT` |
| NooBaa (ODF) | Fallback only | Yes — not recommended |

Choose your path:

#### Path A: Manual S3 Configuration (AWS S3 or any provider)

Pre-create buckets, create a credentials secret, and configure `values.yaml`:

```bash
# 1. Create namespace
kubectl create namespace cost-onprem

# 2. Create credentials secret
kubectl create secret generic my-s3-credentials \
  --namespace=cost-onprem \
  --from-literal=access-key=<YOUR_ACCESS_KEY> \
  --from-literal=secret-key=<YOUR_SECRET_KEY>
```

```yaml
# 3. In your values.yaml:
objectStorage:
  endpoint: "s3.us-east-1.amazonaws.com"  # Your S3 endpoint
  port: 443
  useSSL: true
  existingSecret: "my-s3-credentials"
  s3:
    region: "us-east-1"
```

The install script detects the pre-configured endpoint and skips all S3 auto-detection.

#### Path B: ODF with Direct Ceph RGW (OBC auto-detection)

Create an ObjectBucketClaim and let the install script handle the rest:

```bash
# Create OBC for Direct Ceph RGW
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

oc wait --for=condition=Ready obc/ros-data-ceph -n cost-onprem --timeout=5m
```

The install script automatically detects the OBC, extracts configuration (endpoint, credentials, bucket name), and passes it to Helm. No `values.yaml` changes needed.

> **Note**: Use Direct Ceph RGW (`ocs-storagecluster-ceph-rgw`) over NooBaa (`ocs-storagecluster-ceph-rbd`). NooBaa's eventual consistency causes 403 errors when reading freshly uploaded files.

#### Path C: MinIO (development/testing only)

```bash
# Deploy MinIO
./scripts/deploy-minio-test.sh cost-onprem

# Install with MinIO
MINIO_ENDPOINT=http://minio.cost-onprem.svc.cluster.local:80 \
  ./scripts/install-helm-chart.sh --namespace cost-onprem
```

The script creates credentials, buckets, and passes `objectStorage.*` values to Helm.

See [MinIO Development Setup Guide](../development/ocp-dev-setup-minio.md) for details.

### 2. Credentials and Secret Management

**Security Best Practices:**
- Use dedicated service accounts (not admin credentials)
- Rotate credentials regularly
- Use external secret management (Vault, Sealed Secrets) where possible
- Use least-privilege access (specific buckets only)
- Never commit credentials to version control

**External Secret Management Example:**

```bash
# Sealed Secrets
kubectl create secret generic my-s3-credentials \
  --namespace=cost-onprem \
  --from-literal=access-key=<key> \
  --from-literal=secret-key=<secret> \
  --dry-run=client -o yaml | \
  kubeseal -o yaml > sealed-secret.yaml
```

### 3. Namespace Permissions

Ensure you have permissions to:
- Create secrets in target namespace
- Deploy Helm charts
- Access S3 storage resources
- Create routes (OpenShift)

```bash
# Verify permissions
oc auth can-i create secrets -n cost-onprem
oc auth can-i create deployments -n cost-onprem
oc auth can-i create routes -n cost-onprem
```

### 5. Resource Requirements

**Single Node OpenShift (SNO):**
- SNO cluster with S3-compatible storage (ODF, MinIO, or external S3)
- 30GB+ block devices for persistent volumes
- Additional 6GB RAM for Cost Management On-Premise workloads
- Additional 2 CPU cores

**See [Configuration Guide](../operations/configuration.md) for detailed requirements**

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

> **Using an existing Kafka cluster:** If you already have a Kafka cluster (e.g., AMQ Streams, Confluent, MSK), you can skip the Strimzi deployment and configure `kafka.bootstrapServers` in your values file. Set `KAFKA_BOOTSTRAP_SERVERS` when running the install script to skip Strimzi verification. Only PLAINTEXT connections are currently supported. See [External Kafka](configuration.md#external-kafka) for details.

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
# Installs from the Helm chart repository by default
```

### Manual Helm Upgrade

#### From Helm Repository

```bash
# Update repo index and upgrade to latest
helm repo update cost-onprem
helm upgrade cost-onprem cost-onprem/cost-onprem -n cost-onprem

# Upgrade to a specific version
helm upgrade cost-onprem cost-onprem/cost-onprem -n cost-onprem --version 0.2.9

# With custom values
helm upgrade cost-onprem cost-onprem/cost-onprem -n cost-onprem --values my-values.yaml
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

# Test S3 access (endpoint depends on your storage backend)
oc rsh -n cost-onprem deployment/cost-onprem-ingress -- \
  aws s3 ls --endpoint-url https://<your-s3-endpoint>
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

**Note:** See [Worker Deployment Scenarios](../operations/worker-deployment-scenarios.md) for detailed worker requirements by scenario.

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
# Option 1: Run pytest test suite (~3 minutes)
NAMESPACE=cost-onprem ./scripts/run-pytest.sh

# Option 2: Run specific test suites
./scripts/run-pytest.sh --e2e        # E2E tests only
./scripts/run-pytest.sh --auth       # Authentication tests
./scripts/run-pytest.sh --ros        # ROS-specific tests

# Option 3: Full Cost Management E2E test (~3 minutes)
NAMESPACE=cost-onprem ./scripts/run-pytest.sh --e2e
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
psql -h localhost -U koku -d costonprem_koku -c "
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
which jq helm kubectl

# Install missing tools
sudo apt-get install jq        # Ubuntu/Debian
brew install jq                # macOS
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
# Test Helm repository connectivity
helm repo add cost-onprem https://insights-onprem.github.io/cost-onprem-chart
helm repo update cost-onprem
helm search repo cost-onprem

# If the repo add fails, verify the URL is reachable
curl -sI https://insights-onprem.github.io/cost-onprem-chart/index.yaml
```

### Resource Issues

```bash
# Check node resources
kubectl describe nodes | grep -A 5 "Allocated resources"

# Check available resources
kubectl top nodes  # requires metrics-server
```

**See [Troubleshooting Guide](../operations/troubleshooting.md) for comprehensive solutions**

---

## Next Steps

After successful installation:

1. **Configure Access**: See [Configuration Guide](../operations/configuration.md)
2. **Set Up JWT Auth**: See [JWT Authentication Guide](../api/native-jwt-authentication.md)
3. **Configure TLS**: See [TLS Setup Guide](../operations/cost-management-operator-tls-config-setup.md)
4. **Run Tests**: See [Scripts Reference](../scripts/README.md)

---

**Related Documentation:**
- [Configuration Guide](../operations/configuration.md)
- [Platform Guide](../architecture/platform-guide.md)
- [Quick Start Guide](../operations/quickstart.md)
- [Troubleshooting Guide](../operations/troubleshooting.md)

