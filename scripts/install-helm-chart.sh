#!/bin/bash

# Cost Management On Premise Helm Chart Installation Script
# This script deploys the Cost Management On Premise Helm chart to an OpenShift cluster
# By default, it downloads and uses the latest release from GitHub
# Set USE_LOCAL_CHART=true to use local chart source instead
# Requires: kubectl/oc configured with target cluster context, helm installed, curl, jq
#
# Environment Variables:
#   LOG_LEVEL       - Control output verbosity (ERROR|WARN|INFO|DEBUG, default: WARN)
#   USE_LOCAL_CHART - Use local chart instead of GitHub release (true|false, default: false)
#   NAMESPACE       - Target namespace (default: cost-onprem)
#
# Examples:
#   # Default (clean output with successes/warnings/errors only)
#   ./install-helm-chart.sh
#
#   # Detailed output with all info messages
#   LOG_LEVEL=INFO ./install-helm-chart.sh
#
#   # Quiet (errors only)
#   LOG_LEVEL=ERROR ./install-helm-chart.sh

set -e  # Exit on any error

# Trap to cleanup downloaded charts on script exit
trap 'cleanup_downloaded_chart' EXIT INT TERM

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging configuration
# LOG_LEVEL controls output verbosity:
#   ERROR - Only show errors (quietest)
#   WARN  - Show errors, warnings, and successes (default, clean output)
#   INFO  - Show errors, warnings, successes, and info messages (detailed)
#   DEBUG - Show everything (most verbose)
LOG_LEVEL=${LOG_LEVEL:-WARN}

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELM_RELEASE_NAME=${HELM_RELEASE_NAME:-cost-onprem}
NAMESPACE=${NAMESPACE:-cost-onprem}
VALUES_FILE=${VALUES_FILE:-}
REPO_OWNER="insights-onprem"
REPO_NAME="cost-onprem-chart"
USE_LOCAL_CHART=${USE_LOCAL_CHART:-false}  # Set to true to use local chart instead of GitHub release
LOCAL_CHART_PATH=${LOCAL_CHART_PATH:-../cost-onprem}  # Path to local chart directory
STRIMZI_NAMESPACE=${STRIMZI_NAMESPACE:-}  # If set, use existing Strimzi operator in this namespace
KAFKA_NAMESPACE=${KAFKA_NAMESPACE:-}  # If set, use existing Kafka cluster in this namespace

# Logging functions with level-based filtering
log_debug() {
    [[ "$LOG_LEVEL" == "DEBUG" ]] && echo -e "${BLUE}[DEBUG]${NC} $1"
    return 0
}

log_info() {
    [[ "$LOG_LEVEL" =~ ^(INFO|DEBUG)$ ]] && echo -e "${BLUE}[INFO]${NC} $1"
    return 0
}

log_success() {
    [[ "$LOG_LEVEL" =~ ^(WARN|INFO|DEBUG)$ ]] && echo -e "${GREEN}[SUCCESS]${NC} $1"
    return 0
}

log_warning() {
    [[ "$LOG_LEVEL" =~ ^(WARN|INFO|DEBUG)$ ]] && echo -e "${YELLOW}[WARNING]${NC} $1"
    return 0
}

log_error() {
    # Errors are always shown regardless of log level
    echo -e "${RED}[ERROR]${NC} $1" >&2
    return 0
}

# Backward compatibility aliases (to be replaced incrementally)
echo_info() { log_info "$1"; }
echo_success() { log_success "$1"; }
echo_warning() { log_warning "$1"; }
echo_error() { log_error "$1"; }

# Parse MinIO endpoint: strips protocol and port from FQDN
# Usage: parse_minio_host "http://minio.ns.svc.cluster.local:80" => "minio.ns.svc.cluster.local"
parse_minio_host() {
    echo "$1" | sed -E 's|^https?://||; s|:[0-9]+/?$||; s|/$||'
}

# Extract namespace from FQDN: "minio.ns.svc.cluster.local" => "ns"
parse_minio_namespace() {
    parse_minio_host "$1" | cut -d. -f2
}

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to check prerequisites for Helm installation
check_prerequisites() {
    echo_info "Checking prerequisites for Helm chart installation..."

    local missing_tools=()

    if ! command_exists kubectl; then
        missing_tools+=("kubectl")
    fi

    if ! command_exists helm; then
        missing_tools+=("helm")
    fi

    if ! command_exists jq; then
        missing_tools+=("jq")
    fi

    if [ ${#missing_tools[@]} -gt 0 ]; then
        echo_error "Missing required tools: ${missing_tools[*]}"
        echo_info "Please install the missing tools:"

        for tool in "${missing_tools[@]}"; do
            case $tool in
                "kubectl")
                    echo_info "  Install kubectl: https://kubernetes.io/docs/tasks/tools/"
                    if [[ "$OSTYPE" == "darwin"* ]]; then
                        echo_info "  macOS: brew install kubectl"
                    fi
                    ;;
                "helm")
                    echo_info "  Install Helm: https://helm.sh/docs/intro/install/"
                    if [[ "$OSTYPE" == "darwin"* ]]; then
                        echo_info "  macOS: brew install helm"
                    fi
                    ;;
                "jq")
                    echo_info "  Install jq: https://stedolan.github.io/jq/download/"
                    if [[ "$OSTYPE" == "darwin"* ]]; then
                        echo_info "  macOS: brew install jq"
                    fi
                    ;;
            esac
        done

        return 1
    fi

    # Check kubectl context
    echo_info "Checking kubectl context..."
    local current_context=$(kubectl config current-context 2>/dev/null || echo "none")
    if [ "$current_context" = "none" ]; then
        echo_error "No kubectl context is set. Please configure kubectl to connect to your OpenShift cluster."
        echo_info "For OpenShift: oc login <cluster-url>"
        return 1
    fi

    echo_info "Current kubectl context: $current_context"

    # Test kubectl connectivity
    if ! kubectl get nodes >/dev/null 2>&1; then
        echo_error "Cannot connect to cluster. Please check your kubectl configuration."
        return 1
    fi

    echo_success "All prerequisites are met"
    return 0
}

# Function to verify OpenShift platform
detect_platform() {
    echo_info "Verifying OpenShift platform..."

    if kubectl get routes.route.openshift.io >/dev/null 2>&1; then
        echo_success "Verified OpenShift platform"
        export PLATFORM="openshift"
        # Use OpenShift values if available and no custom values specified
        if [ -z "$VALUES_FILE" ] && [ -f "$SCRIPT_DIR/../../../openshift-values.yaml" ]; then
            echo_info "Using OpenShift-specific values file"
            VALUES_FILE="$SCRIPT_DIR/../../../openshift-values.yaml"
        fi
    else
        echo_error "OpenShift platform not detected. This chart requires OpenShift."
        echo_error "Please ensure you are connected to an OpenShift cluster."
        exit 1
    fi
}

# Function to create namespace
create_namespace() {
    echo_info "Creating namespace: $NAMESPACE"

    if kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
        echo_warning "Namespace '$NAMESPACE' already exists"
    else
        kubectl create namespace "$NAMESPACE"
        echo_success "Namespace '$NAMESPACE' created"
    fi

    # Apply Cost Management Metrics Operator label for resource optimization data collection
    # This label is required by the operator to collect ROS metrics from the namespace
    echo_info "Applying cost management optimization label to namespace..."
    kubectl label namespace "$NAMESPACE" cost_management_optimizations=true --overwrite
    echo_success "Cost management optimization label applied"
    echo_info "  Label: cost_management_optimizations=true"
    echo_info "  This enables the Cost Management Metrics Operator to collect resource optimization data"
}

# Function to verify Strimzi and Kafka prerequisites
verify_strimzi_and_kafka() {
    echo_info "Verifying Strimzi operator and Kafka cluster prerequisites..."

    # If user provided external Kafka bootstrap servers, skip verification
    if [ -n "$KAFKA_BOOTSTRAP_SERVERS" ]; then
        echo_info "Using provided Kafka bootstrap servers: $KAFKA_BOOTSTRAP_SERVERS"
        HELM_EXTRA_ARGS+=("--set" "kafka.bootstrapServers=$KAFKA_BOOTSTRAP_SERVERS")
        echo_success "Kafka configuration verified"
        return 0
    fi

    # Determine which namespace to check
    local check_namespace="${KAFKA_NAMESPACE:-kafka}"

    # Check if Strimzi operator exists
    local strimzi_ns=""

    # Look for Strimzi operator in any namespace
    strimzi_ns=$(kubectl get pods -A -l name=strimzi-cluster-operator -o jsonpath='{.items[0].metadata.namespace}' 2>/dev/null || echo "")

    if [ -n "$strimzi_ns" ]; then
        echo_success "Found Strimzi operator in namespace: $strimzi_ns"
        check_namespace="$strimzi_ns"
    else
        echo_error "Strimzi operator not found in cluster"
        echo_info ""
        echo_info "Strimzi operator is required to manage Kafka clusters."
        echo_info "Please deploy Strimzi before installing Cost Management On Premise:"
        echo_info ""
        echo_info "  cd $SCRIPT_DIR"
        echo_info "  ./deploy-strimzi.sh"
        echo_info ""
        echo_info "Or set KAFKA_BOOTSTRAP_SERVERS to use an existing Kafka cluster:"
        echo_info "  export KAFKA_BOOTSTRAP_SERVERS=my-kafka-bootstrap.my-namespace:9092"
        echo_info "  $0"
        echo_info ""
        return 1
    fi

    # Check if Kafka cluster exists
    if ! kubectl get kafka -n "$check_namespace" >/dev/null 2>&1; then
        echo_error "No Kafka cluster found in namespace: $check_namespace"
        echo_info ""
        echo_info "A Kafka cluster is required for Cost Management On Premise."
        echo_info "Please deploy a Kafka cluster before installing Cost Management On Premise:"
        echo_info ""
        echo_info "  cd $SCRIPT_DIR"
        echo_info "  ./deploy-strimzi.sh"
        echo_info ""
        return 1
    fi

    # Get Kafka cluster details
    local kafka_cluster=$(kubectl get kafka -n "$check_namespace" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    if [ -n "$kafka_cluster" ]; then
        echo_success "Found Kafka cluster: $kafka_cluster in namespace: $check_namespace"

        # Check Kafka status
        local kafka_ready=$(kubectl get kafka "$kafka_cluster" -n "$check_namespace" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "False")
        if [ "$kafka_ready" != "True" ]; then
            echo_warning "Kafka cluster is not ready yet. Installation may fail if Kafka is not fully operational."
        fi

        # Import Kafka bootstrap servers if available from deploy-strimzi.sh output
        if [ -f /tmp/kafka-bootstrap-servers.env ]; then
            source /tmp/kafka-bootstrap-servers.env
            if [ -n "$KAFKA_BOOTSTRAP_SERVERS" ]; then
                HELM_EXTRA_ARGS+=("--set" "kafka.bootstrapServers=$KAFKA_BOOTSTRAP_SERVERS")
                echo_info "Using Kafka bootstrap servers: $KAFKA_BOOTSTRAP_SERVERS"
            fi
        else
            # Fallback: auto-detect bootstrap servers
            local bootstrap_servers="${kafka_cluster}-kafka-bootstrap.${check_namespace}.svc.cluster.local:9092"
            HELM_EXTRA_ARGS+=("--set" "kafka.bootstrapServers=$bootstrap_servers")
            echo_info "Auto-detected Kafka bootstrap servers: $bootstrap_servers"
        fi
    fi

    echo_success "Strimzi and Kafka verification completed"
    return 0
}

# Function to detect and configure external ObjectBucketClaim (OBC)
# Used for Direct Ceph RGW deployments
detect_external_obc() {
    local obc_name="${1:-ros-data-ceph}"
    local obc_namespace="${2:-$NAMESPACE}"

    echo_info "Checking for external ObjectBucketClaim..."

    # Check if OBC exists
    if ! kubectl get obc "$obc_name" -n "$obc_namespace" >/dev/null 2>&1; then
        echo_info "No external OBC found, using standard bucket provisioning"
        return 1
    fi

    # Check if OBC is bound
    local obc_phase=$(kubectl get obc "$obc_name" -n "$obc_namespace" -o jsonpath='{.status.phase}' 2>/dev/null)
    if [ "$obc_phase" != "Bound" ]; then
        echo_warning "External OBC '$obc_name' found but not bound (phase: $obc_phase)"
        return 1
    fi

    echo_success "✓ Detected external ObjectBucketClaim: $obc_name"

    # Extract configuration from OBC
    local bucket_name=$(kubectl get configmap "$obc_name" -n "$obc_namespace" -o jsonpath='{.data.BUCKET_NAME}' 2>/dev/null)
    local bucket_host=$(kubectl get configmap "$obc_name" -n "$obc_namespace" -o jsonpath='{.data.BUCKET_HOST}' 2>/dev/null)
    local bucket_port=$(kubectl get configmap "$obc_name" -n "$obc_namespace" -o jsonpath='{.data.BUCKET_PORT}' 2>/dev/null || echo "443")

    if [ -z "$bucket_name" ] || [ -z "$bucket_host" ]; then
        echo_error "Failed to extract bucket configuration from OBC"
        return 1
    fi

    echo_info "  Bucket Name: $bucket_name"
    echo_info "  Bucket Host: $bucket_host"
    echo_info "  Bucket Port: $bucket_port"

    # Export configuration for use in Helm deployment
    export EXTERNAL_OBC_BUCKET_NAME="$bucket_name"
    export EXTERNAL_OBC_ENDPOINT="$bucket_host"
    export EXTERNAL_OBC_PORT="$bucket_port"
    export EXTERNAL_OBC_NAME="$obc_name"

    # Create or update storage credentials secret from OBC
    # Use HELM_RELEASE_NAME (set by main()) or fall back to NAMESPACE
    local secret_name="${HELM_RELEASE_NAME:-cost-onprem}-storage-credentials"

    echo_info "Extracting credentials from OBC..."
    local access_key=$(kubectl get secret "$obc_name" -n "$obc_namespace" -o jsonpath='{.data.AWS_ACCESS_KEY_ID}' 2>/dev/null | base64 -d)
    local secret_key=$(kubectl get secret "$obc_name" -n "$obc_namespace" -o jsonpath='{.data.AWS_SECRET_ACCESS_KEY}' 2>/dev/null | base64 -d)

    if [ -z "$access_key" ] || [ -z "$secret_key" ]; then
        echo_error "Failed to extract credentials from OBC secret"
        return 1
    fi

    if kubectl get secret "$secret_name" -n "$NAMESPACE" >/dev/null 2>&1; then
        echo_info "Storage credentials secret '$secret_name' already exists, replacing with OBC credentials..."
        kubectl delete secret "$secret_name" -n "$NAMESPACE" >/dev/null 2>&1
    fi

    echo_info "Creating storage credentials from OBC..."
    kubectl create secret generic "$secret_name" \
        -n "$NAMESPACE" \
        --from-literal=access-key="$access_key" \
        --from-literal=secret-key="$secret_key" \
        >/dev/null 2>&1

    if [ $? -eq 0 ]; then
        echo_success "✓ Storage credentials created from OBC"
    else
        echo_error "Failed to create storage credentials secret"
        return 1
    fi

    echo_info "External OBC configuration ready for deployment"
    return 0
}

# Function to create database credentials secret
# Creates a secret with credentials for all database users (postgres, ros, kruize, koku)
create_database_credentials_secret() {
    echo_info "Creating database credentials secret..."

    local secret_name="cost-onprem-db-credentials"

    # Check if secret already exists
    if kubectl get secret "$secret_name" -n "$NAMESPACE" >/dev/null 2>&1; then
        echo_warning "Database credentials secret '$secret_name' already exists (preserving existing credentials)"
        return 0
    fi

    echo_info "Generating secure random database passwords..."

    # Generate secure random passwords (32 characters, alphanumeric)
    local postgres_password=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-32)
    local ros_password=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-32)
    local kruize_password=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-32)
    local koku_password=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-32)

    # Create the secret
    kubectl create secret generic "$secret_name" \
        --namespace="$NAMESPACE" \
        --from-literal=postgres-user=postgres \
        --from-literal=postgres-password="$postgres_password" \
        --from-literal=ros-user=ros_user \
        --from-literal=ros-password="$ros_password" \
        --from-literal=kruize-user=kruize_user \
        --from-literal=kruize-password="$kruize_password" \
        --from-literal=koku-user=koku_user \
        --from-literal=koku-password="$koku_password"

    if [ $? -eq 0 ]; then
        echo_success "Database credentials secret created successfully"
        echo_info "  Secret: $NAMESPACE/$secret_name"
        echo_info "  📋 To retrieve credentials:"
        echo_info "    kubectl get secret $secret_name -n $NAMESPACE -o jsonpath='{.data.ros-password}' | base64 -d"
    else
        echo_error "Failed to create database credentials secret"
        return 1
    fi
}

# Function to create storage credentials secret
create_storage_credentials_secret() {
    echo_info "Creating storage credentials secret..."

    # Use the same naming convention as the Helm chart fullname template
    # The fullname template logic: if release name contains chart name, use release name as-is
    # Otherwise use: ${HELM_RELEASE_NAME}-${CHART_NAME}
    # For cost-onprem-test release: fullname = cost-onprem-test (contains "cost-onprem")
    # For other releases: fullname = ${HELM_RELEASE_NAME}-cost-onprem
    local chart_name="cost-onprem"
    local fullname
    if [[ "$HELM_RELEASE_NAME" == *"$chart_name"* ]]; then
        fullname="$HELM_RELEASE_NAME"
    else
        fullname="${HELM_RELEASE_NAME}-${chart_name}"
    fi
    local secret_name="${fullname}-storage-credentials"

    # Check if secret already exists
    if kubectl get secret "$secret_name" -n "$NAMESPACE" >/dev/null 2>&1; then
        echo_warning "Storage credentials secret '$secret_name' already exists"
        return 0
    fi

    # PRIORITY: If MINIO_ENDPOINT is set, use MinIO credentials (for testing/dev)
    if [ -n "$MINIO_ENDPOINT" ]; then
        echo_info "MINIO_ENDPOINT detected: Using MinIO credentials..."
        local minio_host minio_ns
        minio_host=$(parse_minio_host "$MINIO_ENDPOINT")
        minio_ns=$(parse_minio_namespace "$MINIO_ENDPOINT")

        # Try the MinIO namespace first, then the chart namespace
        for ns in "$minio_ns" "$NAMESPACE"; do
            if kubectl get secret minio-credentials -n "$ns" >/dev/null 2>&1; then
                echo_info "Found minio-credentials secret in namespace: $ns"
                local access_key=$(kubectl get secret minio-credentials -n "$ns" -o jsonpath='{.data.access-key}' | base64 -d)
                local secret_key=$(kubectl get secret minio-credentials -n "$ns" -o jsonpath='{.data.secret-key}' | base64 -d)
                if [ -n "$access_key" ] && [ -n "$secret_key" ]; then
                    kubectl create secret generic "$secret_name" \
                        --namespace="$NAMESPACE" \
                        --from-literal=access-key="$access_key" \
                        --from-literal=secret-key="$secret_key"
                    echo_success "Storage credentials created from MinIO credentials (namespace: $ns)"
                    return 0
                fi
            fi
        done
        echo_warning "MinIO credentials secret not found in $minio_ns or $NAMESPACE, falling back to default logic..."
    fi

    # OpenShift-only deployment: discover S3 credentials from known sources
    # Priority: existing S3 credentials secret > NooBaa admin > MinIO > fail
    local s3_creds_secret="cost-onprem-s3-credentials"

    if kubectl get secret "$s3_creds_secret" -n "$NAMESPACE" >/dev/null 2>&1; then
        # Scenario 1: S3 credentials secret exists (created manually or by prior run)
        echo_info "Found existing S3 credentials secret: $s3_creds_secret"
        echo_info "Creating storage credentials from existing secret..."
        local access_key=$(kubectl get secret "$s3_creds_secret" -n "$NAMESPACE" -o jsonpath='{.data.access-key}')
        local secret_key=$(kubectl get secret "$s3_creds_secret" -n "$NAMESPACE" -o jsonpath='{.data.secret-key}')
        kubectl create secret generic "$secret_name" \
            --namespace="$NAMESPACE" \
            --from-literal=access-key="$(echo "$access_key" | base64 -d)" \
            --from-literal=secret-key="$(echo "$secret_key" | base64 -d)"
        echo_success "Storage credentials created from $s3_creds_secret"
    elif kubectl get secret noobaa-admin -n openshift-storage >/dev/null 2>&1; then
        # Scenario 2: NooBaa admin secret exists (ODF deployment)
        echo_info "Found noobaa-admin secret in openshift-storage namespace"
        echo_info "Extracting S3 credentials from NooBaa..."

        local access_key=$(kubectl get secret noobaa-admin -n openshift-storage -o jsonpath='{.data.AWS_ACCESS_KEY_ID}' | base64 -d)
        local secret_key=$(kubectl get secret noobaa-admin -n openshift-storage -o jsonpath='{.data.AWS_SECRET_ACCESS_KEY}' | base64 -d)

        # Cache S3 credentials for future runs
        kubectl create secret generic "$s3_creds_secret" \
            --namespace="$NAMESPACE" \
            --from-literal=access-key="$access_key" \
            --from-literal=secret-key="$secret_key"
        echo_success "Cached S3 credentials from noobaa-admin"

        # Create storage credentials secret
        kubectl create secret generic "$secret_name" \
            --namespace="$NAMESPACE" \
            --from-literal=access-key="$access_key" \
            --from-literal=secret-key="$secret_key"
        echo_success "Storage credentials created from NooBaa"
        echo_info "  Storage backend: NooBaa (via ODF)"
    elif kubectl get secret minio-credentials -n minio >/dev/null 2>&1; then
        # Scenario 3: MinIO credentials exist (testing/CI environment on OpenShift)
        echo_info "Checking for MinIO deployment..."
        echo_info "Found MinIO credentials secret in minio namespace"

        local access_key=$(kubectl get secret minio-credentials -n minio -o jsonpath='{.data.access-key}')
        local secret_key=$(kubectl get secret minio-credentials -n minio -o jsonpath='{.data.secret-key}')
        kubectl create secret generic "$secret_name" \
            --namespace="$NAMESPACE" \
            --from-literal=access-key="$(echo "$access_key" | base64 -d)" \
            --from-literal=secret-key="$(echo "$secret_key" | base64 -d)"
        echo_success "Storage credentials created from MinIO"
        echo_info "  Storage backend: MinIO"
    else
        # Scenario 4: No storage backend found - FAIL
        echo_error "No S3 storage credentials detected!"
        echo_error ""
        echo_error "This chart requires S3-compatible storage credentials."
        echo_error ""
        echo_info "Available options:"
        echo_info ""
        echo_info "Option 1: Configure in values.yaml (production - recommended)"
        echo_info "  - Set objectStorage.endpoint and objectStorage.existingSecret"
        echo_info "  - Pre-create the secret with 'access-key' and 'secret-key' keys"
        echo_info ""
        echo_info "Option 2: Provide credentials manually"
        echo_info "  kubectl create secret generic $s3_creds_secret \\"
        echo_info "      --namespace=$NAMESPACE \\"
        echo_info "      --from-literal=access-key=<your-access-key> \\"
        echo_info "      --from-literal=secret-key=<your-secret-key>"
        echo_info ""
        echo_info "Option 3: Deploy with MinIO (Testing/CI only)"
        echo_info "  - First deploy MinIO: ./scripts/deploy-minio-test.sh minio"
        echo_info "  - Then re-run this installation script"
        echo_info ""
        echo_error "Deployment aborted. Please configure a storage backend and try again."
        return 1
    fi
}

# Function to create S3 buckets (required for data storage)
# This function MUST succeed for the installation to continue.
# - If buckets already exist: prints notification and continues
# - If buckets don't exist and creation fails: exits with error
#
# Environment Variables:
#   SKIP_S3_SETUP=true       - Skip bucket creation entirely (for CI/testing)
#   S3_ENDPOINT              - Manual S3 endpoint override (e.g., "s3.test.example.com")
#   S3_ACCESS_KEY            - Manual S3 access key override
#   S3_SECRET_KEY            - Manual S3 secret key override
create_s3_buckets() {
    echo_info "Creating S3 buckets..."

    # Check if S3 setup should be skipped entirely
    if [ "${SKIP_S3_SETUP:-false}" = "true" ]; then
        echo_info "Skipping S3 bucket creation (SKIP_S3_SETUP=true)"
        echo_info "This is typically used in CI environments or when S3 is managed externally"
        return 0
    fi

    local secret_name="${HELM_RELEASE_NAME}-storage-credentials"

    # Get S3 credentials from environment variables or secret
    local access_key="${S3_ACCESS_KEY:-}"
    local secret_key="${S3_SECRET_KEY:-}"

    # If credentials not provided via environment variables, get from secret
    if [ -z "$access_key" ] || [ -z "$secret_key" ]; then
        echo_info "Getting S3 credentials from secret: $secret_name"
        access_key=$(kubectl get secret "$secret_name" -n "$NAMESPACE" -o jsonpath='{.data.access-key}' 2>/dev/null | base64 -d)
        secret_key=$(kubectl get secret "$secret_name" -n "$NAMESPACE" -o jsonpath='{.data.secret-key}' 2>/dev/null | base64 -d)
    else
        echo_info "Using S3 credentials from environment variables"
    fi

    if [ -z "$access_key" ] || [ -z "$secret_key" ]; then
        echo_error "S3 credentials not found in secret $secret_name or environment variables"
        echo_error "Cannot proceed without S3 storage. Aborting installation."
        echo_error ""
        echo_error "To bypass this requirement for CI environments, set:"
        echo_error "  export SKIP_S3_SETUP=true"
        exit 1
    fi

    # Determine S3 endpoint and configuration
    local s3_url mc_insecure

    # PRIORITY: If MINIO_ENDPOINT is set, use MinIO (for testing/dev)
    if [ -n "$MINIO_ENDPOINT" ]; then
        local minio_host
        minio_host=$(parse_minio_host "$MINIO_ENDPOINT")
        s3_url="http://${minio_host}:80"
        mc_insecure=""
        echo_info "  ✓ Using MinIO: $s3_url"
    elif kubectl get crd noobaas.noobaa.io >/dev/null 2>&1 && \
       kubectl get noobaa -n openshift-storage >/dev/null 2>&1; then
        # NooBaa detected (ODF S3 backend)
        s3_url="https://s3.openshift-storage.svc:443"
        mc_insecure="--insecure"
        echo_info "  ✓ Detected: NooBaa S3 (via ODF)"
    else
        echo_error "Could not detect S3 storage backend"
        echo_error "Checked for: MINIO_ENDPOINT env var, NooBaa CRD in openshift-storage"
        echo_error ""
        echo_error "Solutions:"
        echo_error "  1. Configure objectStorage.endpoint in values.yaml (recommended)"
        echo_error "  2. Set MINIO_ENDPOINT environment variable"
        echo_error "  3. Set SKIP_S3_SETUP=true to skip bucket creation"
        exit 1
    fi

    # Read bucket names from values.yaml (single source of truth)
    local chart_dir="${CHART_DIR:-${SCRIPT_DIR}/../cost-onprem}"
    local values_file="${chart_dir}/values.yaml"
    local ingress_bucket koku_bucket ros_bucket bucket_list
    if [ -f "$values_file" ] && command_exists yq; then
        ingress_bucket=$(yq '.ingress.storage.bucket' "$values_file")
        koku_bucket=$(yq '.costManagement.storage.bucketName' "$values_file")
        ros_bucket=$(yq '.costManagement.storage.rosBucketName' "$values_file")
        echo_info "Bucket names from values.yaml: $ingress_bucket $koku_bucket $ros_bucket"
    else
        # Fallback to defaults if values.yaml or yq not available (e.g., GitHub release install)
        ingress_bucket="insights-upload-perma"
        koku_bucket="koku-bucket"
        ros_bucket="ros-data"
        echo_info "Using default bucket names (values.yaml or yq not available)"
    fi
    bucket_list="$ingress_bucket $koku_bucket $ros_bucket"

    echo_info "Creating buckets at ${s3_url}..."

    # Use kubectl run --rm for one-shot bucket creation (auto-cleanup)
    # Using UBI9 minimal image (available on OpenShift without Docker Hub rate limits)
    # Real failures (connectivity, permissions) will cause non-zero exit
    # Security context overrides required for OpenShift Pod Security Standards
    local bucket_image="registry.access.redhat.com/ubi9/ubi-minimal:latest"
    local output
    if output=$(kubectl run bucket-setup --rm -i --restart=Never \
        --image="$bucket_image" \
        -n "$NAMESPACE" \
        --overrides='{
            "spec": {
                "securityContext": {
                    "runAsNonRoot": true,
                    "runAsUser": 1001,
                    "seccompProfile": {
                        "type": "RuntimeDefault"
                    }
                },
                "containers": [{
                    "name": "bucket-setup",
                    "image": "'"$bucket_image"'",
                    "securityContext": {
                        "allowPrivilegeEscalation": false,
                        "capabilities": {
                            "drop": ["ALL"]
                        },
                        "runAsNonRoot": true,
                        "runAsUser": 1001
                    },
                    "volumeMounts": [{
                        "name": "mc-config",
                        "mountPath": "/.mc"
                    }]
                }],
                "volumes": [{
                    "name": "mc-config",
                    "emptyDir": {}
                }]
            }
        }' \
        -- sh -c "
            set -e
            curl -sSL https://dl.min.io/client/mc/release/linux-amd64/mc -o /tmp/mc && chmod +x /tmp/mc
            export PATH=/tmp:\$PATH
            mc alias set s3 ${s3_url} '${access_key}' '${secret_key}' ${mc_insecure}
            for bucket in ${bucket_list}; do
                if mc ls s3/\${bucket} ${mc_insecure} >/dev/null 2>&1; then
                    echo \"ℹ️  Bucket \${bucket} already exists\"
                else
                    mc mb s3/\${bucket} ${mc_insecure}
                    echo \"✅ Created bucket: \${bucket}\"
                fi
            done
            echo ''
            echo 'Available buckets:'
            mc ls s3 ${mc_insecure}
        " 2>&1); then
        echo "$output"
        echo_success "S3 buckets ready"
        return 0
    else
        echo "$output"
        echo_error "Failed to create S3 buckets. Cannot proceed without storage."
        echo_error "Check S3 connectivity and credentials."
        exit 1
    fi
}

# Function to download latest chart from GitHub
download_latest_chart() {
    echo_info "Downloading latest Helm chart from GitHub..."

    # Create temporary directory for chart download
    local temp_dir=$(mktemp -d)
    local chart_path=""

    # Get the latest release info from GitHub API
    echo_info "Fetching latest release information from GitHub..."
    local latest_release
    if ! latest_release=$(curl -s "https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/releases/latest"); then
        echo_error "Failed to fetch release information from GitHub API"
        rm -rf "$temp_dir"
        return 1
    fi

    # Extract the tag name and download URL for the .tgz file
    local tag_name=$(echo "$latest_release" | jq -r '.tag_name')
    local download_url=$(echo "$latest_release" | jq -r '.assets[] | select(.name | contains("latest")) | .browser_download_url')
    local filename=$(echo "$latest_release" | jq -r '.assets[] | select(.name | contains("latest")) | .name')

    if [ -z "$download_url" ] || [ "$download_url" = "null" ]; then
        echo_error "No .tgz file found in the latest release ($tag_name)"
        echo_info "Available assets:"
        echo "$latest_release" | jq -r '.assets[].name' | sed 's/^/  - /'
        rm -rf "$temp_dir"
        return 1
    fi

    echo_info "Latest release: $tag_name"
    echo_info "Downloading: $filename"
    echo_info "From: $download_url"

    # Download the chart
    if ! curl -L -o "$temp_dir/$filename" "$download_url"; then
        echo_error "Failed to download chart from GitHub"
        rm -rf "$temp_dir"
        return 1
    fi

    # Verify the download
    if [ ! -f "$temp_dir/$filename" ]; then
        echo_error "Downloaded chart file not found: $temp_dir/$filename"
        rm -rf "$temp_dir"
        return 1
    fi

    local file_size=$(stat -c%s "$temp_dir/$filename" 2>/dev/null || stat -f%z "$temp_dir/$filename" 2>/dev/null)
    echo_success "Downloaded chart: $filename (${file_size} bytes)"

    # Export the chart path for use by deploy_helm_chart function
    export DOWNLOADED_CHART_PATH="$temp_dir/$filename"
    export CHART_TEMP_DIR="$temp_dir"

    return 0
}

# Function to cleanup downloaded chart
cleanup_downloaded_chart() {
    if [ -n "$CHART_TEMP_DIR" ] && [ -d "$CHART_TEMP_DIR" ]; then
        echo_info "Cleaning up downloaded chart..."
        rm -rf "$CHART_TEMP_DIR"
        unset DOWNLOADED_CHART_PATH
        unset CHART_TEMP_DIR
    fi
}


# Function to deploy Helm chart
deploy_helm_chart() {
    echo_info "Deploying Cost Management On Premise Helm chart..."

    local chart_source=""

    # Determine chart source
    if [ "$USE_LOCAL_CHART" = "true" ]; then
        echo_info "Using local chart source (USE_LOCAL_CHART=true)"
        cd "$SCRIPT_DIR"

        # Check if Helm chart directory exists
        if [ ! -d "$LOCAL_CHART_PATH" ]; then
            echo_error "Local Helm chart directory not found: $LOCAL_CHART_PATH"
            echo_info "Set USE_LOCAL_CHART=false to use GitHub releases, or set LOCAL_CHART_PATH to the correct chart location (default: ./cost-onprem)"
            return 1
        fi

        chart_source="$LOCAL_CHART_PATH"
        echo_info "Using local chart: $chart_source"
    else
        echo_info "Using GitHub release (USE_LOCAL_CHART=false)"

        # Download latest chart if not already downloaded
        if [ -z "$DOWNLOADED_CHART_PATH" ]; then
            if ! download_latest_chart; then
                echo_error "Failed to download latest chart from GitHub"
                echo_info "Fallback: Set USE_LOCAL_CHART=true to use local chart"
                return 1
            fi
        fi

        chart_source="$DOWNLOADED_CHART_PATH"
        echo_info "Using downloaded chart: $chart_source"
    fi

    # Build Helm command
    local helm_cmd="helm upgrade --install \"$HELM_RELEASE_NAME\" \"$chart_source\""
    helm_cmd="$helm_cmd --namespace \"$NAMESPACE\""
    helm_cmd="$helm_cmd --create-namespace"
    helm_cmd="$helm_cmd --timeout=${HELM_TIMEOUT:-600s}"
    helm_cmd="$helm_cmd --wait"

    # Add values file if specified
    if [ -n "$VALUES_FILE" ]; then
        if [ -f "$VALUES_FILE" ]; then
            echo_info "Using values file: $VALUES_FILE"
            helm_cmd="$helm_cmd -f \"$VALUES_FILE\""
        else
            echo_error "Values file not found: $VALUES_FILE"
            return 1
        fi
    fi

    # JWT authentication is auto-enabled on OpenShift via platform detection in Helm templates
    # Keycloak URL is auto-detected by Helm chart at render time
    if [ "$PLATFORM" = "openshift" ]; then
        echo_info "JWT authentication will be auto-enabled on OpenShift"
        echo_info "  Keycloak URL will be auto-detected by Helm chart"
        if [ -n "$KEYCLOAK_URL" ]; then
            echo_info "  Detected Keycloak: $KEYCLOAK_URL"
        fi
    else
        echo_info "JWT authentication disabled (non-OpenShift platform)"
    fi

    # S3 endpoint configuration for Helm:
    # If user pre-configured S3 in values.yaml, skip all --set overrides
    # (the values file already has the right config).
    # Otherwise, auto-inject from OBC detection or MINIO_ENDPOINT.
    if [ "$USER_S3_CONFIGURED" = "true" ]; then
        echo_info "S3 configuration provided in values file — skipping Helm --set overrides"
    elif [ "$USING_EXTERNAL_OBC" = "true" ]; then
        echo_info "Configuring Helm deployment for external OBC (Direct Ceph RGW)"
        helm_cmd="$helm_cmd --set objectStorage.endpoint=\"$EXTERNAL_OBC_ENDPOINT\""
        helm_cmd="$helm_cmd --set objectStorage.port=\"$EXTERNAL_OBC_PORT\""
        helm_cmd="$helm_cmd --set objectStorage.useSSL=true"
        helm_cmd="$helm_cmd --set ingress.storage.bucket=\"$EXTERNAL_OBC_BUCKET_NAME\""

        # Set bucket names for Koku and ROS (via standardized helpers in _helpers.tpl)
        helm_cmd="$helm_cmd --set costManagement.storage.bucketName=\"$EXTERNAL_OBC_BUCKET_NAME\""
        helm_cmd="$helm_cmd --set costManagement.storage.rosBucketName=\"$EXTERNAL_OBC_BUCKET_NAME\""

        # Also set ROS bucket name for ROS components
        helm_cmd="$helm_cmd --set ros.storage.bucketName=\"$EXTERNAL_OBC_BUCKET_NAME\""

        echo_success "✓ External OBC configuration added to Helm deployment"
        echo_info "  Endpoint: https://$EXTERNAL_OBC_ENDPOINT:$EXTERNAL_OBC_PORT"
        echo_info "  Bucket: $EXTERNAL_OBC_BUCKET_NAME"
        echo_info "  Bucket configured for ingress, Koku, and ROS components"
    elif [ -n "$MINIO_ENDPOINT" ]; then
        # MinIO S3 configuration (for testing/dev with MinIO in OCP)
        local minio_host
        minio_host=$(parse_minio_host "$MINIO_ENDPOINT")

        echo_info "Configuring S3 endpoint for MinIO (dev/test)"
        helm_cmd="$helm_cmd --set objectStorage.endpoint=\"${minio_host}\""
        helm_cmd="$helm_cmd --set objectStorage.port=80"
        helm_cmd="$helm_cmd --set objectStorage.useSSL=false"
        echo_success "✓ S3 endpoint configured: ${minio_host} (port 80, no SSL)"
    elif kubectl get crd noobaas.noobaa.io >/dev/null 2>&1 && \
         kubectl get noobaa -n openshift-storage >/dev/null 2>&1; then
        # NooBaa fallback (ODF S3 backend)
        echo_info "Configuring S3 endpoint for NooBaa (ODF)"
        helm_cmd="$helm_cmd --set objectStorage.endpoint=\"s3.openshift-storage.svc\""
        helm_cmd="$helm_cmd --set objectStorage.port=443"
        helm_cmd="$helm_cmd --set objectStorage.useSSL=true"
        echo_success "✓ S3 endpoint configured: s3.openshift-storage.svc (port 443, SSL)"
    fi

    # Tell Helm about the script-managed storage credentials secret so it
    # skips rendering the placeholder secret template (avoids ownership conflict).
    if [ -n "$STORAGE_CREDENTIALS_SECRET" ]; then
        helm_cmd="$helm_cmd --set objectStorage.existingSecret=\"$STORAGE_CREDENTIALS_SECRET\""
        echo_info "Storage credentials secret: $STORAGE_CREDENTIALS_SECRET (script-managed)"
    fi

    # Add additional Helm arguments passed to the script
    if [ ${#HELM_EXTRA_ARGS[@]} -gt 0 ]; then
        echo_info "Adding additional Helm arguments: ${HELM_EXTRA_ARGS[*]}"
        helm_cmd="$helm_cmd ${HELM_EXTRA_ARGS[*]}"
    fi

    echo_info "Executing: $helm_cmd"

    # Execute Helm command
    eval $helm_cmd

    local helm_exit_code=$?

    if [ $helm_exit_code -eq 0 ]; then
        echo_success "Helm chart deployed successfully"
    else
        echo_error "Failed to deploy Helm chart"
        return 1
    fi
}

# Function to wait for pods to be ready
wait_for_pods() {
    echo_info "Waiting for pods to be ready..."

    # Wait for all pods to be ready (excluding jobs) with extended timeout for full deployment
    kubectl wait --for=condition=ready pod -l "app.kubernetes.io/instance=$HELM_RELEASE_NAME" \
        --namespace "$NAMESPACE" \
        --timeout=900s \
        --field-selector=status.phase!=Succeeded

    echo_success "All pods are ready"
}

# Function to show deployment status
show_status() {
    echo_info "Deployment Status"
    echo_info "=================="

    echo_info "Platform: $PLATFORM"
    echo_info "Namespace: $NAMESPACE"
    echo_info "Helm Release: $HELM_RELEASE_NAME"
    if [ -n "$VALUES_FILE" ]; then
        echo_info "Values File: $VALUES_FILE"
    fi
    echo ""

    echo_info "Pods:"
    kubectl get pods -n "$NAMESPACE" -o wide
    echo ""

    echo_info "Services:"
    kubectl get services -n "$NAMESPACE"
    echo ""

    echo_info "Storage:"
    kubectl get pvc -n "$NAMESPACE"
    echo ""

    # Show access points via OpenShift Routes
    echo_info "OpenShift Routes:"
    kubectl get routes -n "$NAMESPACE" 2>/dev/null || echo "  No routes found"
    echo ""

    # Get route hosts for access (Centralized Gateway Architecture)
    # API route (/api) points to the gateway, UI route (/) points to UI service
    local api_route=$(kubectl get route -n "$NAMESPACE" -o jsonpath='{.items[?(@.spec.path=="/api")].spec.host}' 2>/dev/null)
    local ui_route=$(kubectl get route -n "$NAMESPACE" -l app.kubernetes.io/component=ui -o jsonpath='{.items[0].spec.host}' 2>/dev/null)

    if [ -n "$api_route" ] || [ -n "$ui_route" ]; then
        echo_info "Access Points (via OpenShift Routes):"
        if [ -n "$api_route" ]; then
            echo_info "  Gateway API Route: https://$api_route/api"
            echo_info "    All API traffic goes through the centralized Envoy gateway (JWT auth required)"
            echo_info "    - Cost Management API: https://$api_route/api/cost-management/v1/status"
            echo_info "    - Ingress API:         https://$api_route/api/ingress/ready"
            echo_info "    - ROS Recommendations: https://$api_route/api/cost-management/v1/recommendations/openshift"
        fi
        if [ -n "$ui_route" ]; then
            echo_info "  UI Route: https://$ui_route/"
        fi
        echo_info ""
        echo_info "  Note: Kruize is internal-only. Use port-forward if needed:"
        echo_info "    kubectl port-forward -n $NAMESPACE svc/${HELM_RELEASE_NAME}-kruize 8085:8085"
    else
        echo_warning "Routes not found. Use port-forwarding or check route configuration."
    fi
    echo ""

    echo_info "Useful Commands:"
    echo_info "  - View logs: kubectl logs -n $NAMESPACE -l app.kubernetes.io/instance=$HELM_RELEASE_NAME"
    echo_info "  - Delete deployment: kubectl delete namespace $NAMESPACE"
    echo_info "  - Run tests: NAMESPACE=$NAMESPACE ./run-pytest.sh"
}

# Function to check ingress controller readiness (OpenShift uses Routes, not Ingress)
check_ingress_readiness() {
    # OpenShift uses Routes managed by the router, not nginx-ingress controller
    echo_info "OpenShift Routes are managed by the built-in router - no additional ingress check needed"
    return 0
}

# Function to run health checks
run_health_checks() {
    echo_info "Running health checks..."

    local failed_checks=0

    # Test internal service connectivity first (this should always work)
    echo_info "Testing internal service connectivity..."

    # Test ROS API internally
    local api_pod=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/component=ros-api -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    if [ -n "$api_pod" ]; then
        if kubectl exec -n "$NAMESPACE" "$api_pod" -- curl -f -s http://localhost:8000/status >/dev/null 2>&1; then
            echo_success "✓ ROS API service is healthy (internal)"
        else
            echo_error "✗ ROS API service is not responding (internal)"
            failed_checks=$((failed_checks + 1))
        fi
    else
        echo_error "✗ ROS API pod not found"
        failed_checks=$((failed_checks + 1))
    fi

    # Test services via port-forwarding
    echo_info "Testing services via port-forwarding..."

    # Test Ingress API via port-forward
    # Note: The ingress container listens on port 8081. When JWT auth is enabled, Envoy sidecar
    # listens on port 8080 and requires authentication. For health checks, connect directly to
    # the ingress container on port 8081 to bypass JWT authentication.
    echo_info "Testing Ingress API via port-forward..."
    local ingress_pod=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/component=ingress -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    if [ -n "$ingress_pod" ]; then
        local ingress_pf_pid=""
        kubectl port-forward -n "$NAMESPACE" pod/"$ingress_pod" 18081:8081 --request-timeout=90s >/dev/null 2>&1 &
        ingress_pf_pid=$!
        sleep 3
        if kill -0 "$ingress_pf_pid" 2>/dev/null && curl -f -s --connect-timeout 60 --max-time 90 http://localhost:18081/ >/dev/null 2>&1; then
            echo_success "✓ Ingress API service is healthy (port-forward)"
        else
            echo_error "✗ Ingress API service is not responding (port-forward)"
            failed_checks=$((failed_checks + 1))
        fi
        # Cleanup ingress port-forward
        if [ -n "$ingress_pf_pid" ] && kill -0 "$ingress_pf_pid" 2>/dev/null; then
            kill "$ingress_pf_pid" 2>/dev/null || true
            # Wait a moment for process to terminate
            sleep 1
        fi
    else
        echo_error "✗ Ingress pod not found"
        failed_checks=$((failed_checks + 1))
    fi

    # Test Kruize API via port-forward
    echo_info "Testing Kruize API via port-forward..."
    local kruize_pf_pid=""
    kubectl port-forward -n "$NAMESPACE" svc/cost-onprem-kruize 18081:8080 --request-timeout=90s >/dev/null 2>&1 &
    kruize_pf_pid=$!
    sleep 3
    if kill -0 "$kruize_pf_pid" 2>/dev/null && curl -f -s --connect-timeout 60 --max-time 90 http://localhost:18081/listPerformanceProfiles >/dev/null 2>&1; then
        echo_success "✓ Kruize API service is healthy (port-forward)"
    else
        echo_error "✗ Kruize API service is not responding (port-forward)"
        failed_checks=$((failed_checks + 1))
    fi
    # Cleanup kruize port-forward
    if [ -n "$kruize_pf_pid" ] && kill -0 "$kruize_pf_pid" 2>/dev/null; then
        kill "$kruize_pf_pid" 2>/dev/null || true
        # Wait a moment for process to terminate
        sleep 1
    fi

    # Test external route accessibility (informational only - not counted as failure)
    echo_info "Testing external route accessibility (informational)..."
    local main_route=$(kubectl get route -n "$NAMESPACE" -o jsonpath='{.items[?(@.spec.path=="/")].spec.host}' 2>/dev/null)
    local ingress_route=$(kubectl get route -n "$NAMESPACE" -o jsonpath='{.items[?(@.spec.path=="/api/ingress")].spec.host}' 2>/dev/null)
    local kruize_route=$(kubectl get route -n "$NAMESPACE" -o jsonpath='{.items[?(@.spec.path=="/api/kruize")].spec.host}' 2>/dev/null)

    local external_accessible=0

    if [ -n "$main_route" ] && curl -f -s "http://$main_route/status" >/dev/null 2>&1; then
        echo_success "  → ROS API externally accessible: http://$main_route/status"
        external_accessible=$((external_accessible + 1))
    fi

    if [ -n "$ingress_route" ] && curl -f -s "http://$ingress_route/ready" >/dev/null 2>&1; then
        echo_success "  → Ingress API externally accessible: http://$ingress_route/ready"
        external_accessible=$((external_accessible + 1))
    fi

    if [ -n "$kruize_route" ] && curl -f -s "http://$kruize_route/api/kruize/listPerformanceProfiles" >/dev/null 2>&1; then
        echo_success "  → Kruize API externally accessible: http://$kruize_route/api/kruize/listPerformanceProfiles"
        external_accessible=$((external_accessible + 1))
    fi

    if [ $external_accessible -eq 0 ]; then
        echo_info "  → External routes not accessible (common in internal/corporate clusters)"
        echo_info "  → Use port-forwarding: kubectl port-forward svc/cost-onprem-ros-api -n $NAMESPACE 8001:8000"
    else
        echo_success "  → $external_accessible route(s) externally accessible"
    fi

    if [ $failed_checks -eq 0 ]; then
        echo_success "All core services are healthy and operational!"
    else
        echo_error "$failed_checks core service check(s) failed"
        echo_info "Check pod logs: kubectl logs -n $NAMESPACE -l app.kubernetes.io/instance=$HELM_RELEASE_NAME"
    fi

    return $failed_checks
}

# Function to cleanup
cleanup() {
    local complete_cleanup=false

    # Parse arguments
    while [ $# -gt 0 ]; do
        case "$1" in
            --complete)
                complete_cleanup=true
                ;;
            *)
                echo_warning "Unknown cleanup option: $1"
                ;;
        esac
        shift
    done

    echo_info "Cleaning up Cost Management On Premise deployment..."
    echo_info "Note: This will NOT remove Strimzi/Kafka. To clean them up separately:"
    echo_info "  ./deploy-strimzi.sh cleanup"
    echo ""

    # Check if namespace exists
    if ! kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
        echo_info "Namespace '$NAMESPACE' does not exist"
        return 0
    fi

    # Delete Helm release first
    echo_info "Deleting Helm release..."
    if helm list -n "$NAMESPACE" | grep -q "$HELM_RELEASE_NAME"; then
        helm uninstall "$HELM_RELEASE_NAME" -n "$NAMESPACE" || true
        echo_info "Waiting for Helm release deletion to complete..."
        sleep 5
    else
        echo_info "Helm release '$HELM_RELEASE_NAME' not found"
    fi

    # Delete PVCs explicitly (they often persist after namespace deletion)
    echo_info "Deleting Persistent Volume Claims..."
    local pvcs=$(kubectl get pvc -n "$NAMESPACE" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null || true)
    if [ -n "$pvcs" ]; then
        for pvc in $pvcs; do
            echo_info "Deleting PVC: $pvc"
            kubectl delete pvc "$pvc" -n "$NAMESPACE" --timeout=60s || true
        done

        # Wait for PVCs to be fully deleted
        echo_info "Waiting for PVCs to be deleted..."
        local timeout=60
        local count=0
        while [ $count -lt $timeout ]; do
            local remaining_pvcs=$(kubectl get pvc -n "$NAMESPACE" --no-headers 2>/dev/null | wc -l || echo "0")
            if [ "$remaining_pvcs" -eq 0 ]; then
                echo_success "All PVCs deleted"
                break
            fi
            echo_info "Waiting for $remaining_pvcs PVCs to be deleted... ($count/$timeout seconds)"
            sleep 2
            count=$((count + 2))
        done

        if [ $count -ge $timeout ]; then
            echo_warning "Timeout waiting for PVCs to be deleted. Some may still exist."
        fi
    else
        echo_info "No PVCs found in namespace"
    fi

    # Complete cleanup includes orphaned PVs
    if [ "$complete_cleanup" = true ]; then
        echo_info "Performing complete cleanup including orphaned Persistent Volumes..."
        local orphaned_pvs=$(kubectl get pv -o jsonpath='{.items[?(@.spec.claimRef.namespace=="'$NAMESPACE'")].metadata.name}' 2>/dev/null || true)
        if [ -n "$orphaned_pvs" ]; then
            for pv in $orphaned_pvs; do
                echo_info "Deleting orphaned PV: $pv"
                kubectl delete pv "$pv" --timeout=30s || true
            done
        else
            echo_info "No orphaned PVs found"
        fi
    fi

    # Delete namespace
    echo_info "Deleting namespace..."
    kubectl delete namespace "$NAMESPACE" --timeout=120s --ignore-not-found || true

    # Wait for namespace deletion
    echo_info "Waiting for namespace deletion to complete..."
    local timeout=120
    local count=0
    while [ $count -lt $timeout ]; do
        if ! kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
            echo_success "Namespace deleted successfully"
            break
        fi
        echo_info "Waiting for namespace deletion... ($count/$timeout seconds)"
        sleep 2
        count=$((count + 2))
    done

    if [ $count -ge $timeout ]; then
        echo_warning "Timeout waiting for namespace deletion. It may still be terminating."
    fi

    echo_success "Cleanup completed"

    # Cleanup any downloaded charts
    cleanup_downloaded_chart
}

# Function to detect RHBK (Red Hat Build of Keycloak) - OpenShift only
detect_keycloak() {
    echo_info "Detecting RHBK (Red Hat Build of Keycloak)..."

    # RHBK is only available on OpenShift clusters
    if [ "$PLATFORM" != "openshift" ]; then
        echo_info "Skipping RHBK detection - not an OpenShift cluster"
        echo_info "RHBK is only supported on OpenShift platforms"
        export KEYCLOAK_FOUND="false"
        export KEYCLOAK_NAMESPACE=""
        export KEYCLOAK_URL=""
        return 1
    fi

    local keycloak_found=false
    local keycloak_namespace=""
    local keycloak_url=""

    # Method 1: Look for RHBK Keycloak Custom Resources (k8s.keycloak.org/v2alpha1)
    echo_info "Checking for RHBK Keycloak CRs (k8s.keycloak.org/v2alpha1)..."
    if kubectl get keycloaks.k8s.keycloak.org -A >/dev/null 2>&1; then
        local keycloak_cr=$(kubectl get keycloaks.k8s.keycloak.org -A -o jsonpath='{.items[0]}' 2>/dev/null)
        if [ -n "$keycloak_cr" ]; then
            keycloak_namespace=$(echo "$keycloak_cr" | jq -r '.metadata.namespace' 2>/dev/null)
            keycloak_url=$(echo "$keycloak_cr" | jq -r '.status.hostname // empty' 2>/dev/null)
            keycloak_found=true
            echo_success "Found RHBK Keycloak CR in namespace: $keycloak_namespace"
            if [ -n "$keycloak_url" ]; then
                keycloak_url="https://$keycloak_url"
                echo_info "Keycloak URL: $keycloak_url"
            fi
        fi
    fi

    # Method 2: Look for common RHBK namespaces
    if [ "$keycloak_found" = false ]; then
        echo_info "Checking for RHBK namespaces..."
        for ns in keycloak keycloak-system; do
            if kubectl get namespace "$ns" >/dev/null 2>&1; then
                echo_info "Found potential RHBK namespace: $ns"
                # Check for Keycloak services in this namespace
                local keycloak_service=$(kubectl get service -n "$ns" -l "app=keycloak" -o name 2>/dev/null | head -1)
                if [ -n "$keycloak_service" ]; then
                    keycloak_namespace="$ns"
                    keycloak_found=true
                    echo_success "Confirmed RHBK service in namespace: $ns"
                    break
                fi
            fi
        done
    fi

    # Method 3: OpenShift Route detection
    if [ "$keycloak_found" = true ] && [ -z "$keycloak_url" ]; then
        echo_info "Detecting Keycloak route in OpenShift..."
        # Check for route named 'keycloak' (RHBK standard)
        keycloak_url=$(kubectl get route keycloak -n "$keycloak_namespace" -o jsonpath='{.spec.host}' 2>/dev/null)
        if [ -z "$keycloak_url" ]; then
            # Fallback to searching for any keycloak-related route
            keycloak_url=$(kubectl get route -n "$keycloak_namespace" -o jsonpath='{.items[?(@.metadata.name~="keycloak")].spec.host}' 2>/dev/null | head -1)
        fi
        if [ -n "$keycloak_url" ]; then
            keycloak_url="https://$keycloak_url"
            echo_info "Detected Keycloak route: $keycloak_url"
        fi
    fi

    # Export results for other functions
    export KEYCLOAK_FOUND="$keycloak_found"
    export KEYCLOAK_NAMESPACE="$keycloak_namespace"
    export KEYCLOAK_URL="$keycloak_url"
    export KEYCLOAK_API_VERSION="k8s.keycloak.org/v2alpha1"

    if [ "$keycloak_found" = true ]; then
        echo_success "RHBK detected successfully"
        echo_info "  API Version: k8s.keycloak.org/v2alpha1"
        echo_info "  Namespace: $keycloak_namespace"
        echo_info "  URL: ${keycloak_url:-"(auto-detect during deployment)"}"
        return 0
    else
        echo_warning "RHBK not detected in OpenShift cluster"
        echo_info "JWT authentication will be disabled"
        echo_info "To enable JWT auth, deploy RHBK using:"
        echo_info "  ./deploy-rhbk.sh"
        return 1
    fi
}

# Function to verify Keycloak client secret exists
# NOTE: Simplified - deploy-rhbk.sh now handles secret creation automatically
# This function only verifies the secret exists
verify_keycloak_client_secret() {
    local client_id="${1:-cost-management-operator}"

    if [ -z "$KEYCLOAK_NAMESPACE" ]; then
        echo_warning "Keycloak namespace not set, skipping client secret verification"
        return 1
    fi

    # Check if secret exists
    local secret_name="keycloak-client-secret-$client_id"
    if kubectl get secret "$secret_name" -n "$KEYCLOAK_NAMESPACE" >/dev/null 2>&1; then
        echo_success "✓ Client secret exists: $secret_name"
        return 0
    else
        echo_warning "Client secret not found: $secret_name"
        echo_info "  Run deploy-rhbk.sh to automatically create the client secret"
        echo_info "  The bulletproof deployment script handles secret extraction automatically"
        return 1
    fi
}

# Function to verify Keycloak UI client secret exists
verify_keycloak_ui_client_secret() {
    local client_id="${1:-cost-management-ui}"

    if [ -z "$KEYCLOAK_NAMESPACE" ]; then
        echo_warning "Keycloak namespace not set, skipping UI client secret verification"
        return 1
    fi

    # Check if secret exists
    local secret_name="keycloak-client-secret-$client_id"
    if kubectl get secret "$secret_name" -n "$KEYCLOAK_NAMESPACE" >/dev/null 2>&1; then
        echo_success "✓ UI client secret exists: $secret_name"
        return 0
    else
        echo_warning "UI client secret not found: $secret_name"
        echo_info "  Run deploy-rhbk.sh to automatically create the UI client secret"
        return 1
    fi
}

# Function to create UI secrets required by oauth2-proxy
# These are created BEFORE helm install (secrets should NEVER be in Helm charts)
create_ui_secrets() {
    echo_info "Creating UI secrets for oauth2-proxy..."

    local release_name="${RELEASE_NAME:-cost-onprem}"

    # 1. Create cookie secret (random session encryption key)
    local cookie_secret_name="${release_name}-ui-cookie-secret"
    if kubectl get secret "$cookie_secret_name" -n "$NAMESPACE" >/dev/null 2>&1; then
        echo_info "Cookie secret '$cookie_secret_name' already exists"
    else
        echo_info "Creating cookie secret '$cookie_secret_name'..."
        local random_secret=$(openssl rand -base64 32 | tr -d '\n' | head -c 32)
        kubectl create secret generic "$cookie_secret_name" \
            -n "$NAMESPACE" \
            --from-literal=session-secret="$random_secret" \
            >/dev/null 2>&1
        if [ $? -eq 0 ]; then
            echo_success "✓ Created cookie secret '$cookie_secret_name'"
        else
            echo_error "Failed to create cookie secret"
            return 1
        fi
    fi

    # 2. Create OAuth client secret (from Keycloak)
    if [ -z "$KEYCLOAK_NAMESPACE" ]; then
        echo_warning "Keycloak namespace not set, skipping OAuth client secret creation"
        return 0
    fi

    local oauth_secret_name="${release_name}-ui-oauth-client"
    local keycloak_ui_secret_name="keycloak-client-secret-cost-management-ui"

    if kubectl get secret "$oauth_secret_name" -n "$NAMESPACE" >/dev/null 2>&1; then
        echo_info "OAuth client secret '$oauth_secret_name' already exists"
        return 0
    fi

    # Check if Keycloak UI client secret exists
    if ! kubectl get secret "$keycloak_ui_secret_name" -n "$KEYCLOAK_NAMESPACE" >/dev/null 2>&1; then
        echo_warning "Keycloak UI client secret '$keycloak_ui_secret_name' not found in namespace '$KEYCLOAK_NAMESPACE'"
        echo_info "  Run deploy-rhbk.sh to create the Keycloak UI client secret first"
        return 1
    fi

    # Extract client ID and client secret from Keycloak secret
    echo_info "Extracting OAuth credentials from Keycloak..."
    local client_id=$(kubectl get secret "$keycloak_ui_secret_name" -n "$KEYCLOAK_NAMESPACE" -o jsonpath='{.data.CLIENT_ID}' 2>/dev/null | base64 -d)
    local client_secret=$(kubectl get secret "$keycloak_ui_secret_name" -n "$KEYCLOAK_NAMESPACE" -o jsonpath='{.data.CLIENT_SECRET}' 2>/dev/null | base64 -d)

    if [ -z "$client_id" ] || [ -z "$client_secret" ]; then
        echo_error "Failed to extract credentials from Keycloak secret"
        return 1
    fi

    # Create the OAuth client secret
    echo_info "Creating OAuth client secret '$oauth_secret_name'..."
    kubectl create secret generic "$oauth_secret_name" \
        -n "$NAMESPACE" \
        --from-literal=client-id="$client_id" \
        --from-literal=client-secret="$client_secret" \
        >/dev/null 2>&1

    if [ $? -eq 0 ]; then
        echo_success "✓ Created OAuth client secret '$oauth_secret_name'"
    else
        echo_error "Failed to create OAuth client secret"
        return 1
    fi
}

# Function to create Django secret for Koku
# This secret is used by Koku for Django's SECRET_KEY
create_django_secret() {
    echo_info "Creating Django secret for Koku..."

    local secret_name="cost-onprem-django-secret"

    # Check if secret already exists
    if kubectl get secret "$secret_name" -n "$NAMESPACE" >/dev/null 2>&1; then
        echo_info "Django secret '$secret_name' already exists in namespace '$NAMESPACE'"
        return 0
    fi

    # Generate a random 50-character secret key
    local secret_key=$(openssl rand -base64 48 | tr -dc 'a-zA-Z0-9' | head -c 50)

    if [ -z "$secret_key" ]; then
        echo_error "Failed to generate Django secret key"
        return 1
    fi

    # Create the secret
    echo_info "Creating secret '$secret_name' in namespace '$NAMESPACE'..."
    kubectl create secret generic "$secret_name" \
        --from-literal=secret-key="$secret_key" \
        --namespace="$NAMESPACE"

    if [ $? -eq 0 ]; then
        echo_success "✓ Created Django secret '$secret_name'"
        return 0
    else
        echo_error "Failed to create Django secret"
        return 1
    fi
}

# Function to create Keycloak CA certificate secret for oauth2-proxy TLS trust
create_keycloak_ca_secret() {
    echo_info "Creating Keycloak CA certificate secret for TLS trust..."

    local secret_name="keycloak-ca-cert"

    if [ -z "$KEYCLOAK_NAMESPACE" ]; then
        echo_warning "Keycloak namespace not set, skipping CA certificate extraction"
        return 0
    fi

    # Check if secret already exists
    if kubectl get secret "$secret_name" -n "$NAMESPACE" >/dev/null 2>&1; then
        echo_info "Keycloak CA secret '$secret_name' already exists in namespace '$NAMESPACE'"
        return 0
    fi

    # Get Keycloak route host
    local keycloak_host=$(kubectl get route keycloak -n "$KEYCLOAK_NAMESPACE" -o jsonpath='{.spec.host}' 2>/dev/null)
    if [ -z "$keycloak_host" ]; then
        echo_warning "Could not find Keycloak route in namespace '$KEYCLOAK_NAMESPACE'"
        echo_info "  Trying alternative methods to extract CA certificate..."
    fi

    local ca_cert=""
    local temp_cert=$(mktemp)

    # Method 1: Extract from default-ingress-cert ConfigMap (most compatible)
    # This contains the full CA bundle used by OpenShift ingress
    if kubectl get configmap default-ingress-cert -n openshift-config-managed >/dev/null 2>&1; then
        echo_info "Extracting CA from default-ingress-cert ConfigMap..."
        ca_cert=$(kubectl get configmap default-ingress-cert -n openshift-config-managed -o jsonpath='{.data.ca-bundle\.crt}' 2>/dev/null)
        if [ -n "$ca_cert" ]; then
            echo_success "✓ Extracted CA from default-ingress-cert ConfigMap"
        fi
    fi

    # Method 2: Extract from OpenShift router CA secret
    if [ -z "$ca_cert" ]; then
        if kubectl get secret router-ca -n openshift-ingress-operator >/dev/null 2>&1; then
            echo_info "Extracting CA from OpenShift router-ca secret..."
            ca_cert=$(kubectl get secret router-ca -n openshift-ingress-operator -o jsonpath='{.data.tls\.crt}' | base64 -d 2>/dev/null)
            if [ -n "$ca_cert" ]; then
                echo "$ca_cert" > "$temp_cert"
                if openssl x509 -noout -text -in "$temp_cert" >/dev/null 2>&1; then
                    echo_success "✓ Extracted CA from router-ca secret"
                else
                    ca_cert=""
                fi
            fi
        fi
    fi

    # Method 3: Extract from Keycloak route directly using openssl
    if [ -z "$ca_cert" ] && [ -n "$keycloak_host" ]; then
        echo_info "Extracting CA from Keycloak route: $keycloak_host..."
        ca_cert=$(echo | openssl s_client -connect "$keycloak_host:443" -servername "$keycloak_host" 2>/dev/null | openssl x509 2>/dev/null)
        if [ -n "$ca_cert" ]; then
            echo "$ca_cert" > "$temp_cert"
            if openssl x509 -noout -text -in "$temp_cert" >/dev/null 2>&1; then
                echo_success "✓ Extracted CA from Keycloak route"
            else
                ca_cert=""
            fi
        fi
    fi

    # Cleanup temp file
    rm -f "$temp_cert"

    if [ -z "$ca_cert" ]; then
        echo_error "Failed to extract Keycloak CA certificate using any method"
        echo_info "  The oauth2-proxy may fail to connect to Keycloak with TLS errors"
        echo_info "  You can manually create the secret with:"
        echo_info "    kubectl create secret generic $secret_name --from-file=ca.crt=<your-ca-cert> -n $NAMESPACE"
        return 1
    fi

    # Create the secret
    echo_info "Creating secret '$secret_name' in namespace '$NAMESPACE'..."
    kubectl create secret generic "$secret_name" \
        --from-literal=ca.crt="$ca_cert" \
        --namespace="$NAMESPACE"

    if [ $? -eq 0 ]; then
        echo_success "✓ Created Keycloak CA secret '$secret_name'"
        return 0
    else
        echo_error "Failed to create Keycloak CA secret"
        return 1
    fi
}

# Function to setup JWT authentication
setup_jwt_authentication() {
    echo_info "Configuring JWT authentication..."

    # JWT authentication is enabled on OpenShift (requires Keycloak)
    export JWT_AUTH_ENABLED="true"
    echo_info "JWT authentication: Enabled"
    echo_info "  JWT Method: Envoy native JWT filter"
    echo_info "  Requires: RHBK (Red Hat Build of Keycloak) deployed"

    # Detect RHBK for configuration
    if detect_keycloak; then
        echo_info "  RHBK Namespace: $KEYCLOAK_NAMESPACE"
        echo_info "  RHBK API: $KEYCLOAK_API_VERSION"

        # Verify operator client secret exists (created by deploy-rhbk.sh)
        echo_info "Verifying Keycloak operator client secret exists..."
        verify_keycloak_client_secret "cost-management-operator" || \
            echo_warning "Operator client secret not found. Run ./deploy-rhbk.sh to create it."

        # Verify UI client secret exists (created by deploy-rhbk.sh)
        echo_info "Verifying Keycloak UI client secret exists..."
        verify_keycloak_ui_client_secret "cost-management-ui" || \
            echo_warning "UI client secret not found. Run ./deploy-rhbk.sh to create it."
    else
        echo_warning "RHBK not detected - ensure it's deployed before using JWT authentication"
    fi

    return 0
}

# Function to set platform-specific configurations
set_platform_config() {
    echo_info "Using OpenShift configuration"

    # Use openshift-values.yaml if no custom values file is specified
    if [ -z "$VALUES_FILE" ]; then
        local openshift_values="$SCRIPT_DIR/../openshift-values.yaml"
        if [ -f "$openshift_values" ]; then
            VALUES_FILE="$openshift_values"
            echo_info "Using OpenShift values file: $openshift_values"
        else
            echo_warning "OpenShift values file not found: $openshift_values"
            echo_info "Using base values with minimal OpenShift overrides"
            # Fallback to minimal inline configuration if openshift-values.yaml is missing
            HELM_EXTRA_ARGS+=(
                "--set" "global.storageClass=odf-storagecluster-ceph-rbd"
            )
        fi
    else
        echo_info "Using custom values file: $VALUES_FILE"
    fi

    export KAFKA_ENVIRONMENT="ocp"
}

# Main execution
main() {
    # Process additional arguments
    HELM_EXTRA_ARGS=()

    while [ $# -gt 0 ]; do
        case "$1" in
            --namespace|-n)
                # Script argument - set namespace
                NAMESPACE="$2"
                shift 2
                ;;
            --values|-f)
                # Script argument - set values file
                VALUES_FILE="$2"
                shift 2
                ;;
            --set|--set-string|--set-file|--set-json)
                # These are Helm arguments, collect them
                HELM_EXTRA_ARGS+=("$1" "$2")
                shift 2
                ;;
            --help|-h)
                echo "Usage: $0 [OPTIONS]"
                echo ""
                echo "Options:"
                echo "  --namespace, -n NAME    Set the namespace (default: cost-onprem)"
                echo "  --values, -f FILE       Specify values file"
                echo "  --set KEY=VALUE         Set Helm values"
                echo "  --help, -h              Show this help"
                exit 0
                ;;
            *)
                # Unknown argument, skip it
                echo_warning "Unknown argument: $1 (ignoring)"
                shift
                ;;
        esac
    done

    echo_info "Cost Management On Premise Helm Chart Installation"
    echo_info "=================================================="

    # Check prerequisites
    if ! check_prerequisites; then
        exit 1
    fi

    # Detect platform (OpenShift vs Kubernetes)
    detect_platform

    # Setup JWT authentication prerequisites (if applicable)
    setup_jwt_authentication

    # Set platform-specific configuration based on auto-detection
    if ! set_platform_config; then
        exit 1
    fi

    echo_info "Configuration:"
    echo_info "  Platform: $PLATFORM"
    echo_info "  Helm Release: $HELM_RELEASE_NAME"
    echo_info "  Namespace: $NAMESPACE"
    if [ -n "$VALUES_FILE" ]; then
        echo_info "  Values File: $VALUES_FILE"
    fi
    if [ "$JWT_AUTH_ENABLED" = "true" ]; then
        echo_info "  JWT Authentication: Enabled"
        echo_info "  Keycloak Namespace: $KEYCLOAK_NAMESPACE"
    else
        echo_info "  JWT Authentication: Disabled"
    fi
    echo ""

    # Create namespace
    if ! create_namespace; then
        exit 1
    fi

    # Check if the user has pre-configured S3 storage in their values file.
    # When objectStorage.endpoint is set, the user manages their own S3
    # infrastructure and the script skips all S3 auto-detection, credential
    # creation, and bucket creation.
    export USER_S3_CONFIGURED="false"
    export USER_S3_EXISTING_SECRET=""
    if [ -n "$VALUES_FILE" ] && [ -f "$VALUES_FILE" ] && command_exists yq; then
        local user_endpoint
        user_endpoint=$(yq '.objectStorage.endpoint // ""' "$VALUES_FILE" 2>/dev/null)
        if [ -n "$user_endpoint" ]; then
            USER_S3_CONFIGURED="true"
            USER_S3_EXISTING_SECRET=$(yq '.objectStorage.existingSecret // ""' "$VALUES_FILE" 2>/dev/null)
            echo_info "S3 storage pre-configured in values file:"
            echo_info "  Endpoint: $user_endpoint"
            echo_info "  Port: $(yq '.objectStorage.port // 443' "$VALUES_FILE" 2>/dev/null)"
            echo_info "  SSL: $(yq '.objectStorage.useSSL // true' "$VALUES_FILE" 2>/dev/null)"
            if [ -n "$USER_S3_EXISTING_SECRET" ]; then
                echo_info "  Credentials Secret: $USER_S3_EXISTING_SECRET (user-managed)"
            else
                echo_info "  Credentials Secret: will be created by install script"
            fi
            echo_info "Skipping S3 auto-detection"
        fi
    fi

    # Detect external ObjectBucketClaim (OBC) for Direct Ceph RGW deployments
    # This must happen BEFORE creating storage credentials
    # Skip if user has already configured S3 in values.yaml
    export USING_EXTERNAL_OBC="false"
    if [ "$USER_S3_CONFIGURED" = "false" ] && [ "$PLATFORM" = "openshift" ]; then
        if detect_external_obc "ros-data-ceph" "$NAMESPACE"; then
            USING_EXTERNAL_OBC="true"
            echo_info "Direct Ceph RGW deployment detected via external OBC"
            echo_info "  Storage credentials and bucket creation will be skipped"
        fi
    fi

    # Determine whether to skip storage credential and bucket creation:
    #   - USER_S3_CONFIGURED=true + existingSecret set → skip credentials + buckets
    #   - USER_S3_CONFIGURED=true + no existingSecret  → create credentials, skip buckets
    #   - USING_EXTERNAL_OBC=true                      → skip credentials + buckets (OBC provides both)
    #   - Otherwise                                    → auto-detect and create both
    local skip_storage_credentials="false"
    local skip_bucket_creation="false"

    if [ "$USER_S3_CONFIGURED" = "true" ]; then
        # User manages their S3 — always skip bucket creation
        skip_bucket_creation="true"
        if [ -n "$USER_S3_EXISTING_SECRET" ]; then
            # User also manages their own credentials secret
            skip_storage_credentials="true"
        fi
    elif [ "$USING_EXTERNAL_OBC" = "true" ]; then
        skip_storage_credentials="true"
        skip_bucket_creation="true"
    fi

    echo ""
    echo_info "════════════════════════════════════════════════════════════"
    echo_info "  Creating Secrets (Before Helm Install)"
    echo_info "════════════════════════════════════════════════════════════"
    echo ""

    # Create database credentials secret (always required)
    if ! create_database_credentials_secret; then
        echo_error "Failed to create database credentials. Cannot proceed with installation."
        exit 1
    fi

    # Create storage credentials secret
    # Track the secret name so we can tell Helm about it via --set objectStorage.existingSecret
    # This prevents Helm from trying to create a conflicting placeholder secret.
    export STORAGE_CREDENTIALS_SECRET=""
    if [ "$skip_storage_credentials" = "false" ]; then
        if ! create_storage_credentials_secret; then
            echo_error "Failed to create storage credentials. Cannot proceed with installation."
            exit 1
        fi
        # Compute the same secret name used by create_storage_credentials_secret
        local chart_name="cost-onprem"
        local fullname
        if [[ "$HELM_RELEASE_NAME" == *"$chart_name"* ]]; then
            fullname="$HELM_RELEASE_NAME"
        else
            fullname="${HELM_RELEASE_NAME}-${chart_name}"
        fi
        STORAGE_CREDENTIALS_SECRET="${fullname}-storage-credentials"
    else
        if [ -n "$USER_S3_EXISTING_SECRET" ]; then
            echo_info "Skipping storage credentials creation (using existing secret: $USER_S3_EXISTING_SECRET)"
        else
            echo_info "Skipping storage credentials creation (using OBC credentials)"
        fi
    fi

    echo ""
    echo_success "✓ All required secrets created successfully"
    echo ""

    # Create S3 buckets
    if [ "$skip_bucket_creation" = "false" ]; then
        if ! create_s3_buckets; then
            echo_warning "Failed to create S3 buckets. Data storage may not work correctly."
        fi
    else
        if [ "$USER_S3_CONFIGURED" = "true" ]; then
            echo_info "Skipping bucket creation (user-managed S3 storage)"
        else
            echo_info "Skipping bucket creation (bucket provided by external OBC)"
        fi
    fi

    # Create UI secrets (cookie + OAuth client) - required before helm install
    if [ "$JWT_AUTH_ENABLED" = "true" ] && [ -n "$KEYCLOAK_NAMESPACE" ]; then
        if ! create_ui_secrets; then
            echo_warning "Failed to create UI secrets. UI OAuth may not work correctly."
            echo_info "  Ensure deploy-rhbk.sh has been run to create the Keycloak UI client secret"
        fi
    fi

    # Create Keycloak CA certificate secret for oauth2-proxy TLS trust (if Keycloak is available)
    if [ "$JWT_AUTH_ENABLED" = "true" ] && [ -n "$KEYCLOAK_NAMESPACE" ]; then
        if ! create_keycloak_ca_secret; then
            echo_warning "Failed to create Keycloak CA secret. oauth2-proxy may have TLS issues."
            echo_info "  You may need to manually create the keycloak-ca-cert secret"
        fi
    fi

    # Create Django secret for Koku (if costManagement is enabled)
    if ! create_django_secret; then
        echo_warning "Failed to create Django secret. Koku may not start correctly."
    fi

    # Verify Strimzi operator and Kafka cluster are available
    if ! verify_strimzi_and_kafka; then
        echo_error "Strimzi/Kafka prerequisites not met"
        exit 1
    fi

    # Deploy Helm chart
    if ! deploy_helm_chart; then
        exit 1
    fi

    # Wait for pods to be ready
    if ! wait_for_pods; then
        echo_warning "Some pods may not be ready. Continuing..."
    fi

    # Show deployment status
    show_status

    # Check ingress readiness before health checks
    check_ingress_readiness

    # Run health checks
    echo_info "Waiting 30 seconds for services to stabilize before running health checks..."

    # Show pod status before health checks
    echo_info "Pod status before health checks:"
    kubectl get pods -n "$NAMESPACE" -o wide

    if ! run_health_checks; then
        echo_warning "Some health checks failed, but deployment completed successfully"
        echo_info "Services may need more time to be fully ready"
        echo_info "You can run health checks manually later or check pod logs for issues"
        echo_info "Pod logs: kubectl logs -n $NAMESPACE -l app.kubernetes.io/instance=$HELM_RELEASE_NAME"
    fi

    echo ""
    echo_success "Cost Management On Prem Helm chart installation completed!"
    echo_info "The services are now running in namespace '$NAMESPACE'"
    echo_info "Next: Run NAMESPACE=$NAMESPACE ./run-pytest.sh to test the deployment"

    # Cleanup downloaded chart if we used GitHub release
    if [ "$USE_LOCAL_CHART" != "true" ]; then
        cleanup_downloaded_chart
    fi
}

# Handle script arguments
case "${1:-}" in
    "cleanup")
        shift  # Remove "cleanup" from arguments
        cleanup "$@"
        exit 0
        ;;
    "status")
        detect_platform
        show_status
        exit 0
        ;;
    "health")
        detect_platform
        run_health_checks
        exit $?
        ;;
    "help"|"-h"|"--help")
        echo "Usage: $0 [command] [options] [--set key=value ...]"
        echo ""
        echo "Platform Detection:"
        echo "  The script automatically detects whether you're running on:"
        echo "  - OpenShift (production configuration: HA, large resources, ODF storage)"
        echo "  - Kubernetes (development configuration: single node, small resources)"
        echo ""
        echo "Prerequisites:"
        echo "  Before running this installation, ensure you have:"
        echo "  1. Strimzi operator and Kafka cluster deployed (run ./deploy-strimzi.sh)"
        echo "     OR provide KAFKA_BOOTSTRAP_SERVERS for existing Kafka"
        echo "  2. For OpenShift with JWT auth: RHBK (optional, run ./deploy-rhbk.sh)"
        echo ""
        echo "Commands:"
        echo "  (none)              - Install Cost Management On Premise Helm chart"
        echo "  cleanup             - Delete Helm release and namespace (preserves PVs)"
        echo "  cleanup --complete  - Complete removal including Persistent Volumes"
        echo "                        Note: Strimzi/Kafka are NOT removed. Use ./deploy-strimzi.sh cleanup"
        echo "  status              - Show deployment status"
        echo "  health              - Run health checks"
        echo "  help                - Show this help message"
        echo ""
        echo "Helm Arguments:"
        echo "  --set key=value     - Set individual values (can be used multiple times)"
        echo "  --set-string key=value - Set string values"
        echo "  --set-file key=path - Set values from file"
        echo "  --set-json key=json - Set JSON values"
        echo ""
        echo "Uninstall/Reinstall Workflow:"
        echo "  # For clean reinstall with fresh data:"
        echo "  $0 cleanup --complete    # Remove everything including data volumes"
        echo "  ./deploy-strimzi.sh cleanup  # Optional: remove Kafka/Strimzi too"
        echo "  ./deploy-strimzi.sh      # Optional: reinstall Kafka/Strimzi"
        echo "  $0                       # Fresh installation"
        echo ""
        echo "  # For reinstall preserving data:"
        echo "  $0 cleanup               # Remove workloads but keep volumes"
        echo "  $0                       # Reinstall (reuses existing volumes and Kafka)"
        echo ""
        echo "Environment Variables:"
        echo "  HELM_RELEASE_NAME       - Name of Helm release (default: cost-onprem)"
        echo "  NAMESPACE               - Kubernetes namespace (default: cost-onprem)"
        echo "  VALUES_FILE             - Path to custom values file (optional)"
        echo "  USE_LOCAL_CHART         - Use local chart instead of GitHub release (default: false)"
        echo "  LOCAL_CHART_PATH        - Path to local chart directory (default: ../cost-onprem)"
        echo "  KAFKA_BOOTSTRAP_SERVERS - Bootstrap servers for existing Kafka (skips verification)"
        echo "                            Example: my-kafka-bootstrap.kafka:9092"
        echo ""
        echo "S3 Storage Configuration:"
        echo "  Option 1 (Recommended for production): Configure in values.yaml"
        echo "    Set objectStorage.endpoint, objectStorage.port, objectStorage.useSSL,"
        echo "    objectStorage.existingSecret in your values file."
        echo "    The script skips all S3 auto-detection when objectStorage.endpoint is set."
        echo ""
        echo "  Option 2 (Automated): Let the script auto-detect"
        echo "    MINIO_ENDPOINT        - MinIO endpoint (for dev/test with MinIO in OCP)"
        echo "                            Example: http://minio.minio-test.svc.cluster.local:80"
        echo "    (OBC auto-detection)  - Detects ObjectBucketClaim 'ros-data-ceph' automatically"
        echo "    (NooBaa fallback)     - Falls back to NooBaa if available"
        echo ""
        echo "  Option 3 (Legacy): Environment variable overrides"
        echo "    SKIP_S3_SETUP         - Skip S3 bucket creation entirely (default: false)"
        echo "    S3_ACCESS_KEY         - Manual S3 access key for credential creation"
        echo "    S3_SECRET_KEY         - Manual S3 secret key for credential creation"
        echo ""
        echo "Chart Source Options:"
        echo "  - Default: Downloads latest release from GitHub (recommended)"
        echo "  - Local: Set USE_LOCAL_CHART=true to use local chart directory"
        echo "  - Chart Path: Set LOCAL_CHART_PATH to specify custom chart location"
        echo "  - Examples:"
        echo "    USE_LOCAL_CHART=true LOCAL_CHART_PATH=../cost-onprem $0"
        echo "    USE_LOCAL_CHART=true LOCAL_CHART_PATH=../cost-onprem-chart/cost-onprem $0"
        echo ""
        echo "Examples:"
        echo "  # Complete fresh installation"
        echo "  ./deploy-strimzi.sh                           # Install Strimzi and Kafka first"
        echo "  USE_LOCAL_CHART=true LOCAL_CHART_PATH=../cost-onprem $0  # Then install Cost Management On Premise"
        echo ""
        echo "  # Install from GitHub release (with Strimzi already deployed)"
        echo "  ./deploy-strimzi.sh                           # Install prerequisites"
        echo "  $0                                            # Install Cost Management On Premise from latest release"
        echo ""
        echo "  # Custom namespace and release name"
        echo "  NAMESPACE=my-namespace HELM_RELEASE_NAME=my-release \\"
        echo "    USE_LOCAL_CHART=true LOCAL_CHART_PATH=../cost-onprem $0"
        echo ""
        echo "  # Use existing Kafka on cluster (deployed by other means)"
        echo "  KAFKA_BOOTSTRAP_SERVERS=my-kafka-bootstrap.my-namespace:9092 $0"
        echo ""
        echo "  # With custom overrides"
        echo "  USE_LOCAL_CHART=true LOCAL_CHART_PATH=../cost-onprem $0 \\"
        echo "    --set database.ros.storage.size=200Gi"
        echo ""
        echo "  # Install latest release from GitHub"
        echo "  $0"
        echo ""
        echo "  # Cleanup and reinstall"
        echo "  $0 cleanup --complete && USE_LOCAL_CHART=true LOCAL_CHART_PATH=../cost-onprem $0"
        echo ""
        echo "Platform Detection:"
        echo "  - Automatically detects Kubernetes vs OpenShift"
        echo "  - Uses openshift-values.yaml for OpenShift if available"
        echo "  - Auto-detects optimal storage class for platform"
        echo "  - Verifies Strimzi operator and Kafka cluster prerequisites"
        echo ""
        echo "Deployment Scenarios:"
        echo "  1. Fresh deployment (recommended):"
        echo "     ./deploy-strimzi.sh    # Deploy Strimzi and Kafka first"
        echo "     $0                     # Deploy Cost Management On Premise"
        echo "     - Auto-detects platform (OpenShift or Kubernetes)"
        echo "     - Verifies Strimzi/Kafka prerequisites"
        echo "     - Deploys Cost Management On Premise with platform-specific configuration"
        echo ""
        echo "  2. With existing Kafka (external):"
        echo "     KAFKA_BOOTSTRAP_SERVERS=kafka.example.com:9092 $0"
        echo "     - Uses provided Kafka bootstrap servers"
        echo "     - Skips Strimzi/Kafka verification"
        echo ""
        echo "  3. Custom configuration:"
        echo "     ./deploy-strimzi.sh"
        echo "     $0 --set key=value"
        echo "     - Override any Helm value"
        echo "     - Platform detection still applies"
        echo ""
        echo "Requirements:"
        echo "  - kubectl must be configured with target cluster"
        echo "  - helm must be installed"
        echo "  - jq must be installed for JSON processing"
        echo "  - Target cluster must have sufficient resources"
        exit 0
        ;;
esac

# Run main function
main "$@"
