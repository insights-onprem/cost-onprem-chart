#!/bin/bash

# Cost Management On-Prem Helm Chart Installation Script
# This script deploys the Cost Management (Koku) Helm chart to OpenShift/Kubernetes
# Requires: kubectl configured with target cluster context, helm installed
# For on-prem deployments: requires S3-compatible storage (ODF, MinIO, etc.)

set -e  # Exit on any error

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELM_RELEASE_NAME=${HELM_RELEASE_NAME:-cost-mgmt}
NAMESPACE=${NAMESPACE:-cost-mgmt}
VALUES_FILE=${VALUES_FILE:-}
USE_LOCAL_CHART=${USE_LOCAL_CHART:-true}  # Default to local chart for development
LOCAL_CHART_PATH=${LOCAL_CHART_PATH:-"${SCRIPT_DIR}/../cost-management-onprem"}

echo_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

echo_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

echo_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

echo_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to check prerequisites
check_prerequisites() {
    echo_info "Checking prerequisites..."

    local missing_tools=()

    if ! command_exists kubectl; then
        missing_tools+=("kubectl")
    fi

    if ! command_exists helm; then
        missing_tools+=("helm")
    fi

    if [ ${#missing_tools[@]} -gt 0 ]; then
        echo_error "Missing required tools: ${missing_tools[*]}"
        return 1
    fi

    # Check kubectl context
    echo_info "Checking kubectl context..."
    local current_context=$(kubectl config current-context 2>/dev/null || echo "none")
    if [ "$current_context" = "none" ]; then
        echo_error "No kubectl context is set"
        return 1
    fi

    echo_info "Current kubectl context: $current_context"

    # Test kubectl connectivity
    if ! kubectl get nodes >/dev/null 2>&1; then
        echo_error "Cannot connect to cluster"
        return 1
    fi

    echo_success "All prerequisites are met"
    return 0
}

# Function to detect platform (Kubernetes vs OpenShift)
detect_platform() {
    echo_info "Detecting platform..."

    if kubectl get routes.route.openshift.io >/dev/null 2>&1; then
        echo_success "Detected OpenShift platform"
        export PLATFORM="openshift"
    else
        echo_success "Detected Kubernetes platform"
        export PLATFORM="kubernetes"
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
}

# Function to create Koku database credentials secret
create_koku_db_secret() {
    echo_info "Creating Koku database credentials secret..."

    local secret_name="koku-db-credentials"

    # Check if secret already exists
    if kubectl get secret "$secret_name" -n "$NAMESPACE" >/dev/null 2>&1; then
        echo_warning "Secret '$secret_name' already exists"
        return 0
    fi

    # Generate random password
    local db_password=$(openssl rand -base64 32 | tr -d '/+=' | head -c 24)

    echo_info "Creating secret: $secret_name"
    kubectl create secret generic "$secret_name" \
        --namespace="$NAMESPACE" \
        --from-literal=password="$db_password" \
        --from-literal=username="koku" \
        --from-literal=database="koku"

    echo_success "Koku database credentials secret created"
    echo_info "  Secret name: $secret_name (referenced in values.yaml)"
}

# Function to create Django secret key
create_django_secret() {
    echo_info "Creating Django secret key..."

    local secret_name="koku-django-secret"

    # Check if secret already exists
    if kubectl get secret "$secret_name" -n "$NAMESPACE" >/dev/null 2>&1; then
        echo_warning "Secret '$secret_name' already exists"
        return 0
    fi

    # Generate random Django secret key (50 characters, alphanumeric + special chars)
    local django_secret=$(openssl rand -base64 64 | tr -d '\n' | head -c 50)

    echo_info "Creating secret: $secret_name"
    kubectl create secret generic "$secret_name" \
        --namespace="$NAMESPACE" \
        --from-literal=secret-key="$django_secret"

    echo_success "Django secret key created"
    echo_info "  Secret name: $secret_name (referenced in values.yaml)"
}

# Function to create Hive Metastore database credentials
create_metastore_db_secret() {
    echo_info "Creating Hive Metastore database credentials secret..."

    local secret_name="koku-metastore-db-credentials"

    # Check if secret already exists
    if kubectl get secret "$secret_name" -n "$NAMESPACE" >/dev/null 2>&1; then
        echo_warning "Secret '$secret_name' already exists"
        return 0
    fi

    # Generate random password
    local db_password=$(openssl rand -base64 32 | tr -d '/+=' | head -c 24)

    echo_info "Creating secret: $secret_name"
    kubectl create secret generic "$secret_name" \
        --namespace="$NAMESPACE" \
        --from-literal=password="$db_password" \
        --from-literal=username="hive" \
        --from-literal=database="hive_metastore"

    echo_success "Hive Metastore database credentials secret created"
    echo_info "  Secret name: $secret_name (referenced in values.yaml)"
}

# Function to restore templates from backup
restore_templates_from_backup() {
    echo_info "Checking if Koku templates need to be restored from backup..."

    local templates_dir="${LOCAL_CHART_PATH}/templates"
    local backup_dir="${LOCAL_CHART_PATH}/templates-backup"

    # Check if backup directory exists
    if [ ! -d "$backup_dir" ]; then
        echo_info "No templates-backup directory found, assuming templates are in place"
        return 0
    fi

    # Check if main Koku templates are missing
    if [ ! -d "$templates_dir/cost-management" ]; then
        echo_warning "Main Koku templates missing, restoring from backup..."

        # Copy all templates from backup
        cp -r "$backup_dir"/* "$templates_dir/"

        echo_success "Templates restored from backup"
    else
        echo_info "Koku templates already present"
    fi
}

# Function to add missing helper functions
add_missing_helpers() {
    echo_info "Checking and adding missing Helm helper functions..."

    local helpers_file="${LOCAL_CHART_PATH}/templates/_helpers.tpl"
    local koku_helpers_file="${LOCAL_CHART_PATH}/templates/_helpers-koku.tpl"

    # Add storage endpoint helper if missing
    if [ -f "$helpers_file" ] && ! grep -q "cost-mgmt.storage.endpoint" "$helpers_file" 2>/dev/null; then
        echo_info "Adding storage endpoint helper to _helpers.tpl..."
        cat >> "$helpers_file" << 'EOF'

{{/*
Storage (S3) endpoint
*/}}
{{- define "cost-mgmt.storage.endpoint" -}}
{{- .Values.s3Endpoint | default "" -}}
{{- end }}

{{/*
Redis host
*/}}
{{- define "cost-mgmt.redis.host" -}}
{{- printf "%s-%s" (include "cost-mgmt.fullname" .) (include "cost-management-onprem.cache.name" .) -}}
{{- end }}

{{/*
Redis port
*/}}
{{- define "cost-mgmt.redis.port" -}}
6379
{{- end }}
EOF
        echo_success "Added storage and Redis helpers"
    fi

    # Add security context helpers if missing
    if [ -f "$koku_helpers_file" ] && ! grep -q "cost-mgmt.securityContext.pod" "$koku_helpers_file" 2>/dev/null; then
        echo_info "Adding security context helpers to _helpers-koku.tpl..."
        cat >> "$koku_helpers_file" << 'EOF'

{{/*
=============================================================================
Security Context Helpers
=============================================================================
*/}}

{{/*
Pod-level security context
*/}}
{{- define "cost-mgmt.securityContext.pod" -}}
runAsNonRoot: true
{{- end -}}

{{/*
Container-level security context
*/}}
{{- define "cost-mgmt.securityContext.container" -}}
allowPrivilegeEscalation: false
capabilities:
  drop:
  - ALL
{{- end -}}
EOF
        echo_success "Added security context helpers"
    fi

    echo_success "All required helper functions are present"
}

# Function to validate chart structure
validate_chart_structure() {
    echo_info "Validating Helm chart structure..."

    local chart_dir="$LOCAL_CHART_PATH"

    # Check Chart.yaml exists
    if [ ! -f "$chart_dir/Chart.yaml" ]; then
        echo_error "Chart.yaml not found in $chart_dir"
        return 1
    fi

    # Check values file exists
    if [ -z "$VALUES_FILE" ]; then
        # Look for default values file
        if [ -f "$chart_dir/values.yaml" ]; then
            VALUES_FILE="$chart_dir/values.yaml"
            echo_info "Using default values file: values.yaml"
        else
            echo_error "No values file found"
            return 1
        fi
    fi

    # Check templates directory exists
    if [ ! -d "$chart_dir/templates" ]; then
        echo_error "Templates directory not found in $chart_dir"
        return 1
    fi

    echo_success "Chart structure is valid"
    return 0
}

# Function to check if values file has required sections
validate_values_file() {
    echo_info "Validating values file structure..."

    if [ ! -f "$VALUES_FILE" ]; then
        echo_error "Values file not found: $VALUES_FILE"
        return 1
    fi

    # Check for costManagement section
    if ! grep -q "^costManagement:" "$VALUES_FILE"; then
        echo_warning "costManagement section not found in values file"
        echo_info "This may cause deployment issues if Koku components are enabled"
    fi

    echo_success "Values file validation complete"
}

# Function to detect S3 endpoint
detect_s3_endpoint() {
    echo_info "Detecting S3 endpoint..."

    if [ "$PLATFORM" = "openshift" ]; then
        # Try to get ODF S3 endpoint
        if kubectl get route s3 -n openshift-storage >/dev/null 2>&1; then
            local s3_host=$(kubectl get route s3 -n openshift-storage -o jsonpath='{.spec.host}')
            export S3_ENDPOINT="https://$s3_host"
            echo_info "Detected ODF S3 endpoint: $S3_ENDPOINT"
            HELM_EXTRA_ARGS+=("--set" "s3Endpoint=$S3_ENDPOINT")
            return 0
        fi

        # Fallback to service endpoint
        if kubectl get service s3 -n openshift-storage >/dev/null 2>&1; then
            export S3_ENDPOINT="http://s3.openshift-storage.svc:80"
            echo_info "Using ODF S3 service endpoint: $S3_ENDPOINT"
            HELM_EXTRA_ARGS+=("--set" "s3Endpoint=$S3_ENDPOINT")
            return 0
        fi

        echo_warning "Could not detect ODF S3 endpoint automatically"
        echo_info "You may need to set s3Endpoint in values file or via --set s3Endpoint=<url>"
    else
        echo_info "For Kubernetes, S3 endpoint should be configured in values file"
    fi
}

# Function to lint the chart before deployment
lint_chart() {
    echo_info "Linting Helm chart..."

    if helm lint "$LOCAL_CHART_PATH" -f "$VALUES_FILE" > /dev/null 2>&1; then
        echo_success "Helm chart lint passed"
        return 0
    else
        echo_warning "Helm chart has linting issues (non-fatal, continuing)"
        helm lint "$LOCAL_CHART_PATH" -f "$VALUES_FILE" 2>&1 | head -20 || true
        return 0
    fi
}

# Function to create Sources API credentials
create_sources_api_secret() {
    echo_info "Creating Sources API credentials secret..."

    local sources_creds_secret="koku-sources-credentials"
    local db_creds_secret="koku-sources-db-credentials"

    # Check if secrets already exist
    if kubectl get secret "$sources_creds_secret" -n "$NAMESPACE" >/dev/null 2>&1; then
        echo_warning "Secret '$sources_creds_secret' already exists"
    else
        # Generate random database password and encryption key
        local sources_db_password=$(openssl rand -base64 32 | tr -d '/+=' | head -c 24)
        local encryption_key=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)

        echo_info "Creating secret: $sources_creds_secret"
        kubectl create secret generic "$sources_creds_secret" \
            --namespace="$NAMESPACE" \
            --from-literal=database-password="$sources_db_password" \
            --from-literal=encryption-key="$encryption_key"

        echo_success "Sources API credentials secret created"
        echo_info "  Secret name: $sources_creds_secret (referenced in values-koku.yaml)"
    fi

    # Create db-credentials secret (used by some components)
    if kubectl get secret "$db_creds_secret" -n "$NAMESPACE" >/dev/null 2>&1; then
        echo_warning "Secret '$db_creds_secret' already exists"
    else
        # Reuse same password for consistency
        local sources_db_password=$(kubectl get secret "$sources_creds_secret" -n "$NAMESPACE" -o jsonpath='{.data.database-password}' 2>/dev/null | base64 -d || openssl rand -base64 32 | tr -d '/+=' | head -c 24)

        echo_info "Creating secret: $db_creds_secret"
        kubectl create secret generic "$db_creds_secret" \
            --namespace="$NAMESPACE" \
            --from-literal=sources-password="$sources_db_password"

        echo_success "DB credentials secret created"
        echo_info "  Secret name: $db_creds_secret (referenced in values-koku.yaml)"
    fi
}

# Function to create S3 storage credentials secret
create_storage_credentials_secret() {
    echo_info "Creating S3 storage credentials secret..."

    local secret_name="koku-storage-credentials"

    # Check if secret already exists
    if kubectl get secret "$secret_name" -n "$NAMESPACE" >/dev/null 2>&1; then
        echo_warning "Storage credentials secret '$secret_name' already exists"
        return 0
    fi

    if [ "$PLATFORM" = "openshift" ]; then
        # For OpenShift, try to get credentials from ODF
        echo_info "Attempting to retrieve ODF S3 credentials..."

        if kubectl get secret noobaa-admin -n openshift-storage >/dev/null 2>&1; then
            echo_info "Found noobaa-admin secret, extracting S3 credentials..."

            local access_key=$(kubectl get secret noobaa-admin -n openshift-storage -o jsonpath='{.data.AWS_ACCESS_KEY_ID}' | base64 -d)
            local secret_key=$(kubectl get secret noobaa-admin -n openshift-storage -o jsonpath='{.data.AWS_SECRET_ACCESS_KEY}' | base64 -d)

            kubectl create secret generic "$secret_name" \
                --namespace="$NAMESPACE" \
                --from-literal=access-key="$access_key" \
                --from-literal=secret-key="$secret_key"

            echo_success "Storage credentials secret created from ODF noobaa-admin"
            echo_info "  Secret name: $secret_name (referenced in values.yaml)"
            return 0
        else
            echo_warning "noobaa-admin secret not found in openshift-storage namespace"
            echo_info "For OpenShift deployments with ODF, ensure OpenShift Data Foundation is installed"
            echo_info "Or manually create the secret:"
            echo_info "  kubectl create secret generic $secret_name \\"
            echo_info "    --namespace=$NAMESPACE \\"
            echo_info "    --from-literal=access-key=<your-s3-access-key> \\"
            echo_info "    --from-literal=secret-key=<your-s3-secret-key>"
            return 1
        fi
    else
        # For Kubernetes/KIND, create MinIO credentials for development
        echo_info "Creating MinIO credentials for development environment..."

        # Generate random credentials even for dev/test
        local minio_access_key=$(openssl rand -base64 32 | tr -d '/+=' | head -c 20)
        local minio_secret_key=$(openssl rand -base64 32 | tr -d '/+=' | head -c 40)

        kubectl create secret generic "$secret_name" \
            --namespace="$NAMESPACE" \
            --from-literal=access-key="$minio_access_key" \
            --from-literal=secret-key="$minio_secret_key"

        echo_success "Storage credentials secret created with random MinIO credentials"
        echo_info "  Secret name: $secret_name (referenced in values.yaml)"
        echo_warning "  MinIO must be configured with these credentials!"
        echo_info "  Access Key: $minio_access_key"
        echo_info "  Secret Key: $minio_secret_key"
    fi
}

# Function to deploy Helm chart
deploy_helm_chart() {
    echo_info "Deploying Cost Management Helm chart..."

    local chart_source=""

    # Determine chart source
    if [ "$USE_LOCAL_CHART" = "true" ]; then
        echo_info "Using local chart source"

        # Check if Helm chart directory exists
        if [ ! -d "$LOCAL_CHART_PATH" ]; then
            echo_error "Local Helm chart directory not found: $LOCAL_CHART_PATH"
            return 1
        fi

        chart_source="$LOCAL_CHART_PATH"
        echo_info "Using local chart: $chart_source"
    else
        echo_error "GitHub release download not yet implemented for cost-management-onprem"
        echo_info "Set USE_LOCAL_CHART=true to use local chart"
        return 1
    fi

    # Build Helm command
    local helm_cmd="helm upgrade --install \"$HELM_RELEASE_NAME\" \"$chart_source\""
    helm_cmd="$helm_cmd --namespace \"$NAMESPACE\""
    helm_cmd="$helm_cmd --create-namespace"
    helm_cmd="$helm_cmd --timeout=${HELM_TIMEOUT:-1200s}"
    # Note: Removed --wait to allow post-install hooks (like DB migrations) to run
    # The post-install hook will initialize the database schema

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

    # Add additional Helm arguments passed to the script
    if [ ${#HELM_EXTRA_ARGS[@]} -gt 0 ]; then
        echo_info "Adding additional Helm arguments: ${HELM_EXTRA_ARGS[*]}"
        helm_cmd="$helm_cmd ${HELM_EXTRA_ARGS[*]}"
    fi

    echo_info "Executing: $helm_cmd"

    # Execute Helm command
    eval $helm_cmd

    if [ $? -eq 0 ]; then
        echo_success "Helm chart deployed successfully"
    else
        echo_error "Failed to deploy Helm chart"
        return 1
    fi
}

# Function to wait for pods to be ready
wait_for_pods() {
    echo_info "Waiting for pods to be ready..."

    # Wait for all pods to be ready (excluding jobs and completed pods)
    if kubectl wait --for=condition=ready pod -l "app.kubernetes.io/instance=$HELM_RELEASE_NAME" \
        --namespace "$NAMESPACE" \
        --timeout=900s \
        --field-selector=status.phase!=Succeeded 2>/dev/null; then
        echo_success "All pods are ready"
    else
        echo_warning "Some pods may not be ready yet. Checking status..."
        kubectl get pods -n "$NAMESPACE"
    fi
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

    echo_info "Useful Commands:"
    echo_info "  - View logs: kubectl logs -n $NAMESPACE -l app.kubernetes.io/instance=$HELM_RELEASE_NAME"
    echo_info "  - Delete deployment: kubectl delete namespace $NAMESPACE"
    echo_info "  - Port forward API: kubectl port-forward -n $NAMESPACE svc/koku-koku-api 8000:8000"
}

# Function to cleanup
cleanup() {
    echo_info "Cleaning up Cost Management Helm deployment..."

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

    # Delete PVCs
    echo_info "Deleting Persistent Volume Claims..."
    local pvcs=$(kubectl get pvc -n "$NAMESPACE" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null || true)
    if [ -n "$pvcs" ]; then
        for pvc in $pvcs; do
            echo_info "Deleting PVC: $pvc"
            kubectl delete pvc "$pvc" -n "$NAMESPACE" --timeout=60s || true
        done
    fi

    # Delete namespace
    echo_info "Deleting namespace..."
    kubectl delete namespace "$NAMESPACE" --timeout=120s --ignore-not-found || true

    echo_success "Cleanup completed"
}

# Main execution
main() {
    # Extract chart name from release name for secret naming
    export CHART_NAME="cost-management-onprem"

    # Process additional arguments
    HELM_EXTRA_ARGS=()

    while [ $# -gt 0 ]; do
        case "$1" in
            --set|--set-string|--set-file|--set-json)
                HELM_EXTRA_ARGS+=("$1" "$2")
                shift 2
                ;;
            --*)
                HELM_EXTRA_ARGS+=("$1")
                shift
                ;;
            *)
                echo_warning "Unknown argument: $1 (ignoring)"
                shift
                ;;
        esac
    done

    echo_info "Cost Management On-Prem Helm Chart Installation"
    echo_info "================================================"

    # Check prerequisites
    if ! check_prerequisites; then
        exit 1
    fi

    # Detect platform
    detect_platform

    echo_info "Configuration:"
    echo_info "  Platform: $PLATFORM"
    echo_info "  Helm Release: $HELM_RELEASE_NAME"
    echo_info "  Namespace: $NAMESPACE"
    echo_info "  Chart: $CHART_NAME"
    if [ -n "$VALUES_FILE" ]; then
        echo_info "  Values File: $VALUES_FILE"
    fi
    echo ""

    # Validate chart structure
    if ! validate_chart_structure; then
        exit 1
    fi

    # Validate values file
    validate_values_file

    # Bootstrap: Restore templates from backup if needed
    restore_templates_from_backup

    # Bootstrap: Add missing helper functions
    add_missing_helpers

    # Bootstrap: Lint chart before deployment
    lint_chart

    # Create namespace
    if ! create_namespace; then
        exit 1
    fi

    # Detect S3 endpoint and add to Helm args if found
    detect_s3_endpoint

    # Create all required secrets (external to Helm)
    echo_info "Creating required secrets (managed externally, referenced in values)..."

    # NOTE: Database credentials are now created by cost-management-infrastructure chart
    # Run scripts/bootstrap-infrastructure.sh before deploying the application

    create_django_secret
    create_metastore_db_secret
    create_sources_api_secret

    # Storage credentials are critical - fail if they can't be created
    if ! create_storage_credentials_secret; then
        echo_error "Failed to create storage credentials secret"
        echo_info "Cost Management requires S3-compatible storage"
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

    echo ""
    echo_success "Cost Management Helm chart installation completed!"
    echo_info "The services are now running in namespace '$NAMESPACE'"
    echo ""
    echo_info "Next Steps:"
    echo_info "  1. Wait for all pods to be ready: kubectl get pods -n $NAMESPACE"
    echo_info "  2. Check database migration: kubectl logs -n $NAMESPACE -l app.kubernetes.io/component=db-migration"
    echo_info "  3. Create a provider via Sources API or direct database insertion"
    echo_info "  4. Upload cost data to S3 bucket"
    echo_info "  5. Monitor MASU processing: kubectl logs -n $NAMESPACE -l app.kubernetes.io/component=masu"
}

# Handle script arguments
case "${1:-}" in
    "cleanup")
        shift
        cleanup "$@"
        exit 0
        ;;
    "status")
        detect_platform
        show_status
        exit 0
        ;;
    "help"|"-h"|"--help")
        echo "Usage: $0 [command] [options] [--set key=value ...]"
        echo ""
        echo "Commands:"
        echo "  (none)    - Install Cost Management Helm chart"
        echo "  cleanup   - Delete Helm release and namespace"
        echo "  status    - Show deployment status"
        echo "  help      - Show this help message"
        echo ""
        echo "Environment Variables:"
        echo "  HELM_RELEASE_NAME  - Name of Helm release (default: cost-mgmt)"
        echo "  NAMESPACE          - Kubernetes namespace (default: cost-mgmt)"
        echo "  VALUES_FILE        - Path to custom values file (optional)"
        echo "  USE_LOCAL_CHART    - Use local chart (default: true)"
        echo "  LOCAL_CHART_PATH   - Path to local chart (default: ../cost-management-onprem)"
        echo ""
        echo "Examples:"
        echo "  # Install with default configuration"
        echo "  $0"
        echo ""
        echo "  # Install with custom values file"
        echo "  VALUES_FILE=my-values.yaml $0"
        echo ""
        echo "  # Install with custom namespace"
        echo "  NAMESPACE=my-cost-mgmt $0"
        echo ""
        echo "  # Cleanup"
        echo "  $0 cleanup"
        echo ""
        echo "Requirements:"
        echo "  - kubectl configured with target cluster"
        echo "  - helm installed"
        echo "  - S3-compatible storage (ODF for OpenShift, MinIO for Kubernetes)"
        echo "  - Sufficient cluster resources (see values.yaml for requirements)"
        exit 0
        ;;
esac

# Run main function
main "$@"

