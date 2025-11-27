#!/bin/bash
#
# Complete Cost Management Installation Script
# =============================================
# Automated end-to-end installation of Cost Management on OpenShift.
#
# This script orchestrates the complete installation:
#   Phase 1: Infrastructure Setup (Kafka, PostgreSQL, Trino, Redis)
#   Phase 2: Application Deployment (Koku API, Celery, Sources)
#   Phase 3: Verification (Health checks)
#   Phase 4: E2E Testing (Optional)
#
# Usage:
#   ./install-cost-management-complete.sh --namespace <namespace> [options]
#
# Options:
#   --namespace <name>          Kubernetes namespace (default: cost-mgmt)
#   --s3-access-key <key>       S3 access key (will prompt if not provided)
#   --s3-secret-key <key>       S3 secret key (will prompt if not provided)
#   --skip-kafka                Skip Kafka deployment
#   --skip-verification         Skip verification checks
#   --skip-e2e                  Skip E2E tests
#   --chart-path <path>         Path to chart directory
#   --help                      Show this help message
#

set -e
set -o pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color

# Default values
NAMESPACE="cost-mgmt"
S3_ACCESS_KEY=""
S3_SECRET_KEY=""
SKIP_KAFKA=false
SKIP_VERIFICATION=false
SKIP_E2E=false
CHART_PATH=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[⚠]${NC} $1"
}

log_error() {
    echo -e "${RED}[✗]${NC} $1"
}

log_header() {
    echo ""
    echo -e "${MAGENTA}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${MAGENTA}║ $(printf '%-60s' "$1")║${NC}"
    echo -e "${MAGENTA}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

log_phase() {
    echo ""
    echo -e "${CYAN}════════════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}  $1${NC}"
    echo -e "${CYAN}════════════════════════════════════════════════════════════════${NC}"
    echo ""
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --namespace)
            NAMESPACE="$2"
            shift 2
            ;;
        --s3-access-key)
            S3_ACCESS_KEY="$2"
            shift 2
            ;;
        --s3-secret-key)
            S3_SECRET_KEY="$2"
            shift 2
            ;;
        --skip-kafka)
            SKIP_KAFKA=true
            shift
            ;;
        --skip-verification)
            SKIP_VERIFICATION=true
            shift
            ;;
        --skip-e2e)
            SKIP_E2E=true
            shift
            ;;
        --chart-path)
            CHART_PATH="$2"
            shift 2
            ;;
        --help)
            head -n 25 "$0" | tail -n +3 | sed 's/^# //;s/^#//'
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Set chart path if not specified
if [ -z "$CHART_PATH" ]; then
    CHART_PATH="$ROOT_DIR/cost-management-onprem"
fi

# =============================================================================
# Welcome Banner
# =============================================================================
# clear  # Commented out for CI/CD and logging
log_header "Cost Management Complete Installation"

echo -e "${CYAN}This script will install:${NC}"
echo "  • Kafka Infrastructure (Strimzi Operator)"
echo "  • Cost Management Infrastructure (PostgreSQL, Trino, Redis)"
echo "  • Cost Management Application (Koku API, Celery, Sources)"
echo ""
echo -e "${CYAN}Configuration:${NC}"
echo "  • Namespace: $NAMESPACE"
echo "  • Chart Path: $CHART_PATH"
echo "  • Skip Kafka: $SKIP_KAFKA"
echo "  • Skip Verification: $SKIP_VERIFICATION"
echo "  • Skip E2E: $SKIP_E2E"
echo ""
log_info "Starting installation..."

# =============================================================================
# Prerequisites Check
# =============================================================================
log_phase "Prerequisites Check"

log_info "Checking required tools..."

MISSING_TOOLS=()

if ! command -v oc &> /dev/null; then
    MISSING_TOOLS+=("oc")
fi

if ! command -v kubectl &> /dev/null; then
    MISSING_TOOLS+=("kubectl")
fi

if ! command -v helm &> /dev/null; then
    MISSING_TOOLS+=("helm")
fi

if [ ${#MISSING_TOOLS[@]} -gt 0 ]; then
    log_error "Missing required tools: ${MISSING_TOOLS[*]}"
    echo "Please install missing tools and try again"
    exit 1
fi

log_success "All required tools are installed"

log_info "Checking cluster access..."
if ! oc whoami &>/dev/null; then
    log_error "Not logged into OpenShift cluster"
    echo "Please run: oc login --token=<token> --server=<server>"
    exit 1
fi

CURRENT_USER=$(oc whoami)
CURRENT_SERVER=$(oc whoami --show-server)
log_success "Logged in as: $CURRENT_USER"
log_success "Server: $CURRENT_SERVER"

log_info "Checking ODF installation..."
if oc get noobaa -n openshift-storage &>/dev/null; then
    log_success "OpenShift Data Foundation is installed"
else
    log_warning "ODF not detected - S3 storage may not be available"
fi

# =============================================================================
# Phase 0: Namespace Creation
# =============================================================================
log_phase "Phase 0: Namespace Setup"

log_info "Checking if namespace '$NAMESPACE' exists..."
if oc get namespace "$NAMESPACE" &>/dev/null; then
    log_error "Namespace '$NAMESPACE' already exists"
    log_error "This script requires a clean installation"
    log_error "Please delete the namespace first: oc delete namespace $NAMESPACE"
    exit 1
else
    log_info "Creating namespace '$NAMESPACE'..."
    oc create namespace "$NAMESPACE"
    log_success "Namespace created"
fi

# =============================================================================
# Phase 1: Infrastructure Setup
# =============================================================================
log_phase "Phase 1: Infrastructure Setup"

# Step 1.1: Kafka Deployment
if [ "$SKIP_KAFKA" = false ]; then
    log_header "Step 1.1: Kafka Deployment"

    log_info "Deploying Strimzi Operator and Kafka cluster..."
    if [ -f "$SCRIPT_DIR/deploy-strimzi.sh" ]; then
        if "$SCRIPT_DIR/deploy-strimzi.sh"; then
            log_success "Kafka deployment completed"
        else
            log_error "Kafka deployment failed"
            exit 1
        fi
    else
        log_error "deploy-strimzi.sh not found at $SCRIPT_DIR"
        exit 1
    fi
else
    log_warning "Skipping Kafka deployment (--skip-kafka specified)"
fi

# Step 1.2: S3 Credentials
log_header "Step 1.2: S3 Credentials Setup"

log_info "Checking for existing S3 credentials secret..."
if oc get secret odf-s3-credentials -n "$NAMESPACE" &>/dev/null; then
    log_warning "S3 credentials secret already exists"
    read -p "$(echo -e ${YELLOW}Use existing secret? [y/N]:${NC} )" -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "Please provide new credentials"
        S3_ACCESS_KEY=""
        S3_SECRET_KEY=""
    else
        log_success "Using existing S3 credentials"
    fi
fi

if [ -z "$S3_ACCESS_KEY" ] && ! oc get secret odf-s3-credentials -n "$NAMESPACE" &>/dev/null; then
    # Try to auto-discover ODF credentials
    log_info "Attempting to auto-discover ODF S3 credentials..."

    if oc get secret noobaa-admin -n openshift-storage &>/dev/null; then
        S3_ACCESS_KEY=$(oc get secret noobaa-admin -n openshift-storage -o jsonpath='{.data.AWS_ACCESS_KEY_ID}' 2>/dev/null | base64 -d)
        S3_SECRET_KEY=$(oc get secret noobaa-admin -n openshift-storage -o jsonpath='{.data.AWS_SECRET_ACCESS_KEY}' 2>/dev/null | base64 -d)

        if [ -n "$S3_ACCESS_KEY" ] && [ -n "$S3_SECRET_KEY" ]; then
            log_success "Auto-discovered S3 credentials from ODF (noobaa-admin)"
        else
            log_warning "Found noobaa-admin secret but couldn't extract credentials"
            S3_ACCESS_KEY=""
            S3_SECRET_KEY=""
        fi
    else
        log_warning "Could not find noobaa-admin secret in openshift-storage namespace"
    fi

    # If auto-discovery failed, fail with error
    if [ -z "$S3_ACCESS_KEY" ]; then
        log_error "Failed to auto-discover S3 credentials from ODF"
        log_error "Please provide credentials using --s3-access-key and --s3-secret-key flags"
        log_error "Or ensure noobaa-admin secret exists in openshift-storage namespace"
        exit 1
    fi
fi

if [ -n "$S3_ACCESS_KEY" ] && [ -n "$S3_SECRET_KEY" ]; then
    log_info "Creating S3 credentials secret..."

    # Delete existing secret if we're replacing it
    oc delete secret odf-s3-credentials -n "$NAMESPACE" &>/dev/null || true

    oc create secret generic odf-s3-credentials \
        --from-literal=AWS_ACCESS_KEY_ID="$S3_ACCESS_KEY" \
        --from-literal=AWS_SECRET_ACCESS_KEY="$S3_SECRET_KEY" \
        --namespace="$NAMESPACE"

    log_success "S3 credentials secret created"
fi

# Step 1.3: Infrastructure Chart Deployment
log_header "Step 1.3: Infrastructure Chart Deployment"

log_info "Deploying Cost Management infrastructure..."
if [ -f "$SCRIPT_DIR/bootstrap-infrastructure.sh" ]; then
    if "$SCRIPT_DIR/bootstrap-infrastructure.sh" --namespace "$NAMESPACE"; then
        log_success "Infrastructure deployment completed"
    else
        log_error "Infrastructure deployment failed"
        exit 1
    fi
else
    log_error "bootstrap-infrastructure.sh not found at $SCRIPT_DIR"
    exit 1
fi

# =============================================================================
# Phase 2: Application Deployment
# =============================================================================
log_phase "Phase 2: Application Deployment"

log_header "Step 2.1: Application Chart Deployment"

log_info "Deploying Cost Management application using install-cost-helm-chart.sh..."

if [ -f "$SCRIPT_DIR/install-cost-helm-chart.sh" ]; then
    # Set environment variables for the application installer
    export NAMESPACE="$NAMESPACE"
    export HELM_RELEASE_NAME="cost-mgmt"
    export USE_LOCAL_CHART=true
    export LOCAL_CHART_PATH="$CHART_PATH"

    # Call the application installer script
    # This script handles:
    # - Secret creation (Django, Sources API, Hive Metastore DB, S3 storage)
    # - S3 endpoint auto-discovery
    # - Chart validation and linting
    # - Helm deployment with proper configuration
    # - Pod readiness checks
    if "$SCRIPT_DIR/install-cost-helm-chart.sh"; then
        log_success "Application deployment completed"
    else
        log_error "Application deployment failed"
        exit 1
    fi
else
    log_error "install-cost-helm-chart.sh not found at $SCRIPT_DIR"
    log_error "This script is required for application deployment"
    exit 1
fi

# =============================================================================
# Phase 3: Verification
# =============================================================================
if [ "$SKIP_VERIFICATION" = false ]; then
    log_phase "Phase 3: Verification"

    log_info "Running comprehensive verification checks..."

    if [ -f "$SCRIPT_DIR/verify-cost-management.sh" ]; then
        VERIFY_ARGS="--namespace $NAMESPACE"
        if [ "$SKIP_E2E" = true ]; then
            VERIFY_ARGS="$VERIFY_ARGS --skip-e2e"
        fi

        if "$SCRIPT_DIR/verify-cost-management.sh" $VERIFY_ARGS; then
            log_success "Verification completed successfully"
        else
            log_error "Verification found issues"
            echo ""
            echo -e "${YELLOW}Note: Some checks may fail initially while pods are starting.${NC}"
            echo "Wait a few minutes and run verification again:"
            echo "  $SCRIPT_DIR/verify-cost-management.sh --namespace $NAMESPACE"
        fi
    else
        log_warning "verify-cost-management.sh not found, skipping verification"
        log_info "Manual verification: oc get pods -n $NAMESPACE"
    fi
else
    log_warning "Skipping verification (--skip-verification specified)"
fi

# =============================================================================
# Installation Complete
# =============================================================================
echo ""
echo ""
log_header "Installation Complete!"

echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                                                            ║${NC}"
echo -e "${GREEN}║  ✓ Cost Management Successfully Installed!                 ║${NC}"
echo -e "${GREEN}║                                                            ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""

echo -e "${CYAN}Deployment Summary:${NC}"
echo "  • Namespace: $NAMESPACE"
echo "  • Kafka: $([ "$SKIP_KAFKA" = false ] && echo "Deployed" || echo "Skipped")"
echo "  • Infrastructure: Deployed"
echo "  • Application: Deployed"
echo ""

echo -e "${CYAN}Quick Access Commands:${NC}"
echo ""
echo "  # Check pods"
echo "  oc get pods -n $NAMESPACE"
echo ""
echo "  # View logs"
echo "  oc logs -n $NAMESPACE -l app.kubernetes.io/component=api --tail=50"
echo ""
echo "  # Port-forward to API"
echo "  oc port-forward -n $NAMESPACE svc/cost-mgmt-cost-management-onprem-koku-api 8080:8000"
echo "  curl http://localhost:8080/api/cost-management/v1/status/"
echo ""
echo "  # Run verification again"
echo "  $SCRIPT_DIR/verify-cost-management.sh --namespace $NAMESPACE"
echo ""

echo -e "${CYAN}Next Steps:${NC}"
echo "  1. Verify all pods are running"
echo "  2. Test API endpoints"
echo "  3. Configure providers (OCP, AWS, Azure, GCP)"
echo "  4. Upload cost data"
echo ""
echo "  See documentation: docs/cost-management-installation.md"
echo ""

log_success "Installation completed successfully!"
