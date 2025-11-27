#!/bin/bash

# Cost Management On-Prem Installation Script
# This script deploys the Cost Management Helm chart with proper S3 credential configuration
# Requires: kubectl, helm, Object Bucket Claims already created

set -e  # Exit on any error

# Trap to cleanup on script exit
trap 'cleanup_on_exit' EXIT INT TERM

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
CHART_PATH=${CHART_PATH:-"$(dirname "$SCRIPT_DIR")"}
VALUES_FILE=${VALUES_FILE:-""}
OBC_KOKU_NAME=${OBC_KOKU_NAME:-koku-report}
OBC_ROS_NAME=${OBC_ROS_NAME:-ros-report}
DRY_RUN=${DRY_RUN:-false}

# Logging functions (reusable from ros-helm-chart)
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

cleanup_on_exit() {
    # Placeholder for any cleanup needed
    :
}

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check prerequisites
check_prerequisites() {
    echo_info "Checking prerequisites..."

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
        return 1
    fi

    # Check kubectl connection
    if ! kubectl cluster-info &>/dev/null; then
        echo_error "Cannot connect to Kubernetes cluster. Please configure kubectl."
        return 1
    fi

    echo_success "Prerequisites check passed"
    return 0
}

# Check if namespace exists
check_namespace() {
    echo_info "Checking namespace: $NAMESPACE"

    if ! kubectl get namespace "$NAMESPACE" &>/dev/null; then
        echo_info "Namespace '$NAMESPACE' does not exist. Creating..."
        kubectl create namespace "$NAMESPACE"
        echo_success "Namespace '$NAMESPACE' created"
    else
        echo_success "Namespace '$NAMESPACE' exists"
    fi
}

# Check if Object Bucket Claims exist
check_object_bucket_claims() {
    echo_info "Checking Object Bucket Claims..."

    local obcs_missing=()

    if ! kubectl get obc "$OBC_KOKU_NAME" -n "$NAMESPACE" &>/dev/null; then
        obcs_missing+=("$OBC_KOKU_NAME")
    fi

    if ! kubectl get obc "$OBC_ROS_NAME" -n "$NAMESPACE" &>/dev/null; then
        obcs_missing+=("$OBC_ROS_NAME")
    fi

    if [ ${#obcs_missing[@]} -gt 0 ]; then
        echo_error "Missing Object Bucket Claims: ${obcs_missing[*]}"
        echo_info "Please create the OBCs first using:"
        echo_info "  kubectl apply -f obc-configs/"
        return 1
    fi

    echo_success "Object Bucket Claims exist"

    # Wait for OBCs to be bound
    echo_info "Waiting for OBCs to be bound..."
    local timeout=60
    local elapsed=0

    while [ $elapsed -lt $timeout ]; do
        local koku_phase=$(kubectl get obc "$OBC_KOKU_NAME" -n "$NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
        local ros_phase=$(kubectl get obc "$OBC_ROS_NAME" -n "$NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null || echo "")

        if [[ "$koku_phase" == "Bound" && "$ros_phase" == "Bound" ]]; then
            echo_success "OBCs are bound"
            return 0
        fi

        sleep 2
        elapsed=$((elapsed + 2))
    done

    echo_error "Timeout waiting for OBCs to be bound"
    return 1
}

# Verify S3 credentials are available
verify_s3_credentials() {
    echo_info "Verifying S3 credentials from OBC secrets..."

    # Check koku-report secret
    if ! kubectl get secret "$OBC_KOKU_NAME" -n "$NAMESPACE" &>/dev/null; then
        echo_error "Secret '$OBC_KOKU_NAME' not found in namespace '$NAMESPACE'"
        echo_info "OBC may not have created the secret yet. Wait a few seconds and try again."
        return 1
    fi

    # Verify required keys exist
    local access_key=$(kubectl get secret "$OBC_KOKU_NAME" -n "$NAMESPACE" -o jsonpath='{.data.AWS_ACCESS_KEY_ID}' 2>/dev/null || echo "")
    local secret_key=$(kubectl get secret "$OBC_KOKU_NAME" -n "$NAMESPACE" -o jsonpath='{.data.AWS_SECRET_ACCESS_KEY}' 2>/dev/null || echo "")

    if [[ -z "$access_key" || -z "$secret_key" ]]; then
        echo_error "S3 credentials not found in secret '$OBC_KOKU_NAME'"
        return 1
    fi

    # Decode and display (partial) for verification
    local decoded_access_key=$(echo "$access_key" | base64 -d)
    echo_success "S3 credentials found: ${decoded_access_key:0:8}***"

    return 0
}

# Configure S3 credentials for deployments
configure_s3_credentials() {
    echo_info "S3 credentials will be configured from secrets automatically by Kubernetes"
    echo_info "Secret: $OBC_KOKU_NAME"
    echo_info "Keys: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY"

    # The Helm chart should reference the secret, and Kubernetes will inject the env vars
    # This is documented in the values file

    echo_success "S3 credential configuration ready"
}

# Install or upgrade Helm chart
install_helm_chart() {
    echo_info "Installing/Upgrading Cost Management Helm chart..."

    local helm_args=(
        "$HELM_RELEASE_NAME"
        "$CHART_PATH"
        "--namespace" "$NAMESPACE"
        "--create-namespace"
    )

    if [ -n "$VALUES_FILE" ]; then
        helm_args+=("--values" "$VALUES_FILE")
    fi

    if [ "$DRY_RUN" == "true" ]; then
        helm_args+=("--dry-run")
        echo_warning "DRY RUN MODE: No actual installation will occur"
    fi

    echo_info "Running: helm upgrade --install ${helm_args[*]}"

    if helm upgrade --install "${helm_args[@]}"; then
        echo_success "Helm chart installed/upgraded successfully"
        return 0
    else
        echo_error "Helm chart installation failed"
        return 1
    fi
}

# Post-install: Ensure MASU and workers have S3 credentials
post_install_configuration() {
    if [ "$DRY_RUN" == "true" ]; then
        echo_info "Skipping post-install configuration (dry run mode)"
        return 0
    fi

    echo_info "Applying post-install configuration..."

    # Wait for deployments to be ready
    echo_info "Waiting for deployments to be ready..."
    local timeout=180

    echo_info "Waiting for MASU deployment..."
    if ! kubectl rollout status deployment/"${HELM_RELEASE_NAME}-cost-management-onprem-koku-api-masu" \
        -n "$NAMESPACE" --timeout="${timeout}s" &>/dev/null; then
        echo_warning "MASU deployment did not become ready within ${timeout}s"
    else
        echo_success "MASU deployment ready"
    fi

    echo_info "Waiting for Celery worker deployment..."
    if ! kubectl rollout status deployment/"${HELM_RELEASE_NAME}-cost-management-onprem-celery-worker-default" \
        -n "$NAMESPACE" --timeout="${timeout}s" &>/dev/null; then
        echo_warning "Celery worker deployment did not become ready within ${timeout}s"
    else
        echo_success "Celery worker deployment ready"
    fi

    # Add S3 credentials to MASU if not already present
    echo_info "Ensuring MASU has S3 credentials..."
    if kubectl set env deployment/"${HELM_RELEASE_NAME}-cost-management-onprem-koku-api-masu" \
        -n "$NAMESPACE" \
        --from=secret/"$OBC_KOKU_NAME" \
        AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY \
        --dry-run=client &>/dev/null; then

        kubectl set env deployment/"${HELM_RELEASE_NAME}-cost-management-onprem-koku-api-masu" \
            -n "$NAMESPACE" \
            --from=secret/"$OBC_KOKU_NAME" \
            AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY

        echo_success "S3 credentials added to MASU"

        # Wait for rollout
        kubectl rollout status deployment/"${HELM_RELEASE_NAME}-cost-management-onprem-koku-api-masu" \
            -n "$NAMESPACE" --timeout="${timeout}s" || true
    fi

    echo_success "Post-install configuration complete"
}

# Verify installation
verify_installation() {
    echo_info "Verifying installation..."

    # Check pod status
    echo_info "Checking pod status..."
    local ready_pods=$(kubectl get pods -n "$NAMESPACE" --field-selector=status.phase=Running --no-headers 2>/dev/null | wc -l | tr -d ' ')
    local total_pods=$(kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null | wc -l | tr -d ' ')

    echo_info "Pods running: $ready_pods/$total_pods"

    # Check for critical pods
    local critical_components=(
        "koku-db"
        "redis"
        "koku-api-reads"
        "koku-api-writes"
        "koku-api-masu"
        "celery-beat"
        "celery-worker-default"
    )

    local missing_components=()
    for component in "${critical_components[@]}"; do
        if ! kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/component=${component}" --no-headers 2>/dev/null | grep -q "Running"; then
            missing_components+=("$component")
        fi
    done

    if [ ${#missing_components[@]} -gt 0 ]; then
        echo_warning "Some critical components are not running: ${missing_components[*]}"
        echo_info "Use 'kubectl get pods -n $NAMESPACE' to check status"
    else
        echo_success "All critical components are running"
    fi

    echo_success "Installation verification complete"
}

# Display next steps
display_next_steps() {
    echo ""
    echo_success "========================================"
    echo_success "Cost Management Installation Complete!"
    echo_success "========================================"
    echo ""
    echo_info "Next steps:"
    echo ""
    echo_info "1. Check pod status:"
    echo "   kubectl get pods -n $NAMESPACE"
    echo ""
    echo_info "2. Create a provider (data source):"
    echo "   cd ../koku-e2e-data-injection"
    echo "   ./create-provider-direct.sh"
    echo ""
    echo_info "3. Upload test data:"
    echo "   ./generate-and-upload-data.sh"
    echo ""
    echo_info "4. Monitor data processing:"
    echo "   kubectl logs -n $NAMESPACE -l app.kubernetes.io/component=koku-api-masu -f"
    echo ""
    echo_info "5. Access the API:"
    echo "   kubectl port-forward -n $NAMESPACE svc/${HELM_RELEASE_NAME}-cost-management-onprem-koku-api 8000:8000"
    echo "   curl http://localhost:8000/api/cost-management/v1/status/"
    echo ""
    echo_info "For more information, see the documentation:"
    echo "   - README.md"
    echo "   - docs/cost-management-installation.md"
    echo ""
}

# Main installation flow
main() {
    echo_info "========================================"
    echo_info "Cost Management On-Prem Installation"
    echo_info "========================================"
    echo ""
    echo_info "Configuration:"
    echo_info "  Release Name: $HELM_RELEASE_NAME"
    echo_info "  Namespace: $NAMESPACE"
    echo_info "  Chart Path: $CHART_PATH"
    echo_info "  OBC (Koku): $OBC_KOKU_NAME"
    echo_info "  OBC (ROS): $OBC_ROS_NAME"
    [ -n "$VALUES_FILE" ] && echo_info "  Values File: $VALUES_FILE"
    [ "$DRY_RUN" == "true" ] && echo_warning "  DRY RUN MODE"
    echo ""

    # Run installation steps
    check_prerequisites || exit 1
    check_namespace || exit 1
    check_object_bucket_claims || exit 1
    verify_s3_credentials || exit 1
    configure_s3_credentials || exit 1
    install_helm_chart || exit 1
    post_install_configuration || exit 1
    verify_installation || exit 1
    display_next_steps

    echo ""
    echo_success "Installation script completed successfully!"
    echo ""
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --release-name)
            HELM_RELEASE_NAME="$2"
            shift 2
            ;;
        --namespace)
            NAMESPACE="$2"
            shift 2
            ;;
        --chart-path)
            CHART_PATH="$2"
            shift 2
            ;;
        --values)
            VALUES_FILE="$2"
            shift 2
            ;;
        --obc-koku)
            OBC_KOKU_NAME="$2"
            shift 2
            ;;
        --obc-ros)
            OBC_ROS_NAME="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --release-name NAME    Helm release name (default: cost-mgmt)"
            echo "  --namespace NAME       Kubernetes namespace (default: cost-mgmt)"
            echo "  --chart-path PATH      Path to Helm chart (default: parent dir)"
            echo "  --values FILE          Additional values file"
            echo "  --obc-koku NAME        Koku Object Bucket Claim name (default: koku-report)"
            echo "  --obc-ros NAME         ROS Object Bucket Claim name (default: ros-report)"
            echo "  --dry-run              Perform dry run without actual installation"
            echo "  --help, -h             Show this help message"
            echo ""
            echo "Environment variables:"
            echo "  HELM_RELEASE_NAME      Same as --release-name"
            echo "  NAMESPACE              Same as --namespace"
            echo "  CHART_PATH             Same as --chart-path"
            echo "  VALUES_FILE            Same as --values"
            echo "  OBC_KOKU_NAME          Same as --obc-koku"
            echo "  OBC_ROS_NAME           Same as --obc-ros"
            echo "  DRY_RUN                Same as --dry-run"
            echo ""
            exit 0
            ;;
        *)
            echo_error "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Run main installation
main

