#!/bin/bash
set -euo pipefail

################################################################################
# Cost Management On-Premise OpenShift JWT Authentication Deployment Script
#
# This script orchestrates the complete JWT authentication setup for Cost Management on
# OpenShift by wrapping the authoritative scripts from cost-onprem-chart repository.
#
# Based on: https://github.com/insights-onprem/cost-onprem-chart/blob/main/scripts/README.md
# Section: JWT Authentication Setup
#
# Usage:
#   ./deploy-test-cost-onprem.sh [OPTIONS]
#
# Options:
#   --skip-rhbk               Skip Red Hat Build of Keycloak (RHBK) deployment
#   --skip-strimzi            Skip Kafka/Strimzi deployment
#   --skip-helm               Skip COST Helm chart installation
#   --skip-tls                Skip TLS certificate setup
#   --skip-test               Skip JWT authentication test
#   --skip-image-override     Skip creating custom values file for image override
#   --namespace NAME          Target namespace (default: cost-onprem)
#   --image-tag TAG           Custom image tag for cost-onprem-ocp-backend services
#   --use-local-chart         Use local Helm chart instead of GitHub release
#   --verbose                 Enable verbose output
#   --dry-run                 Show what would be executed without running
#   --tests-only              Run only JWT authentication tests (skip all deployments)
#   --include-ui              Include UI tests (requires Playwright system dependencies)
#   --save-versions [FILE]    Save deployment version info to JSON file (default: version_info.json)
#   --help                    Display this help message
#
# Environment Variables:
#   KUBECONFIG               Path to kubeconfig file (default: ~/.kube/config)
#   KUBEADMIN_PASSWORD_FILE  Path to kubeadmin password file
#   SHARED_DIR               Shared directory containing kubeadmin-password
#   OPENSHIFT_API            OpenShift API URL (auto-detected from kubeconfig)
#   OPENSHIFT_USERNAME       OpenShift username (default: kubeadmin)
#   OPENSHIFT_PASSWORD       OpenShift password (auto-detected from files)
#
# Note: This script will automatically login to OpenShift using credentials from:
#       1. KUBECONFIG file (for API URL)
#       2. KUBEADMIN_PASSWORD_FILE or SHARED_DIR/kubeadmin-password (for password)
#       If already logged in, it will skip the login step.
#
# Prerequisites:
#   - oc CLI installed and configured
#   - kubectl CLI installed and configured
#   - helm CLI installed (v3+)
#   - yq installed for YAML/JSON processing
#   - OpenShift cluster with admin access
#
# Example:
#   # Full deployment with custom image
#   ./deploy-test-cost-onprem.sh --image-tag main-abc123
#
#   # Skip RHBK if already deployed
#   ./deploy-test-cost-onprem.sh --skip-rhbk --namespace cost-onprem-production
#
#   # Dry run to preview actions
#   ./deploy-test-cost-onprem.sh --dry-run --verbose
#
################################################################################

# Script metadata
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Default configuration
NAMESPACE="${NAMESPACE:-cost-onprem}"
USE_LOCAL_CHART="${USE_LOCAL_CHART:-false}"
VERBOSE="${VERBOSE:-false}"
DRY_RUN="${DRY_RUN:-false}"
TESTS_ONLY="${TESTS_ONLY:-false}"
INCLUDE_UI="${INCLUDE_UI:-false}"
SAVE_VERSIONS="${SAVE_VERSIONS:-false}"
VERSION_INFO_FILE="${VERSION_INFO_FILE:-version_info.json}"

# OpenShift authentication
KUBECONFIG="${KUBECONFIG:-${HOME}/.kube/config}"
OPENSHIFT_USERNAME="${OPENSHIFT_USERNAME:-kubeadmin}"
OPENSHIFT_API="${OPENSHIFT_API:-}"
OPENSHIFT_PASSWORD="${OPENSHIFT_PASSWORD:-}"
KUBEADMIN_PASSWORD_FILE="${KUBEADMIN_PASSWORD_FILE:-}"
SHARED_DIR="${SHARED_DIR:-}"

# Local scripts directory (this script sits alongside the other scripts)
LOCAL_SCRIPTS_DIR="${SCRIPT_DIR}"
SCRIPT_DEPLOY_RHBK="deploy-rhbk.sh"  # Red Hat Build of Keycloak (RHBK)
SCRIPT_DEPLOY_STRIMZI="deploy-strimzi.sh"
SCRIPT_INSTALL_HELM="install-helm-chart.sh"
SCRIPT_SETUP_TLS="setup-cost-mgmt-tls.sh"
OPENSHIFT_VALUES_FILE="openshift-values.yaml"

# Step flags (default: run all steps)
SKIP_RHBK=false  # Red Hat Build of Keycloak
SKIP_STRIMZI=false
SKIP_HELM=false
SKIP_TLS=false
SKIP_TEST=false

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

################################################################################
# Logging functions
################################################################################

log_info() {
    echo -e "${BLUE}ℹ INFO:${NC} $*"
}

log_success() {
    echo -e "${GREEN}✅ SUCCESS:${NC} $*"
}

log_warning() {
    echo -e "${YELLOW}⚠ WARNING:${NC} $*"
}

log_error() {
    echo -e "${RED}❌ ERROR:${NC} $*" >&2
}

log_step() {
    echo -e "${CYAN}▶${NC} $*"
}

log_verbose() {
    if [[ "${VERBOSE}" == "true" ]]; then
        echo -e "${CYAN}[VERBOSE]${NC} $*"
    fi
}

################################################################################
# Utility functions
################################################################################

show_help() {
    sed -n '/^# Usage:/,/^################################################################################$/p' "$0" | sed 's/^# \?//'
    exit 0
}

check_prerequisites() {
    log_step "Checking prerequisites"

    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "DRY RUN: Skipping prerequisite checks"
        return 0
    fi

    local missing_tools=()

    # Check required tools
    for tool in oc kubectl helm yq; do
        if ! command -v "$tool" &> /dev/null; then
            missing_tools+=("$tool")
        else
            log_verbose "Found: $tool ($(command -v "$tool"))"
        fi
    done

    if [[ ${#missing_tools[@]} -gt 0 ]]; then
        log_error "Missing required tools: ${missing_tools[*]}"
        log_error "Please install missing tools and try again"
        log_error ""
        log_error "Installation instructions:"
        log_error "  macOS:  brew install kubectl yq"
        log_error "  Linux:  kubectl: https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/  |  yq: https://github.com/mikefarah/yq#install"
        exit 1
    fi

    log_success "All required tools are installed"
}

detect_openshift_credentials() {
    log_info "Detecting OpenShift credentials from environment..."

    # Detect API URL from kubeconfig if not set
    if [[ -z "${OPENSHIFT_API}" ]] && [[ -f "${KUBECONFIG}" ]]; then
        OPENSHIFT_API=$(yq e '.clusters[0].cluster.server' "${KUBECONFIG}" 2>/dev/null || echo "")
        if [[ -n "${OPENSHIFT_API}" ]]; then
            log_verbose "Detected API URL from kubeconfig: ${OPENSHIFT_API}"
        fi
    fi

    # Detect password from files if not set
    if [[ -z "${OPENSHIFT_PASSWORD}" ]]; then
        if [[ -n "${KUBEADMIN_PASSWORD_FILE}" ]] && [[ -s "${KUBEADMIN_PASSWORD_FILE}" ]]; then
            OPENSHIFT_PASSWORD="$(cat "${KUBEADMIN_PASSWORD_FILE}")"
            log_verbose "Loaded password from KUBEADMIN_PASSWORD_FILE"
        elif [[ -n "${SHARED_DIR}" ]] && [[ -s "${SHARED_DIR}/kubeadmin-password" ]]; then
            OPENSHIFT_PASSWORD="$(cat "${SHARED_DIR}/kubeadmin-password")"
            log_verbose "Loaded password from SHARED_DIR/kubeadmin-password"
        fi
    fi
}

login_to_openshift() {
    log_step "Logging into OpenShift"

    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "DRY RUN: Would login to OpenShift"
        return 0
    fi

    # Detect credentials from environment
    detect_openshift_credentials

    # Check if credentials are available
    if [[ -z "${OPENSHIFT_API}" ]]; then
        log_error "OPENSHIFT_API not set and could not be detected from kubeconfig"
        log_error "Please set OPENSHIFT_API environment variable or ensure KUBECONFIG is valid"
        return 1
    fi

    if [[ -z "${OPENSHIFT_PASSWORD}" ]]; then
        log_error "OPENSHIFT_PASSWORD not set and could not be detected from files"
        log_error "Please set one of:"
        log_error "  - OPENSHIFT_PASSWORD environment variable"
        log_error "  - KUBEADMIN_PASSWORD_FILE pointing to password file"
        log_error "  - SHARED_DIR containing kubeadmin-password file"
        return 1
    fi

    # Configure kubeconfig to skip TLS verification
    if [[ -f "${KUBECONFIG}" ]]; then
        log_verbose "Configuring kubeconfig to skip TLS verification..."
        yq -i 'del(.clusters[].cluster.certificate-authority-data) | .clusters[].cluster.insecure-skip-tls-verify=true' "${KUBECONFIG}" 2>/dev/null || true
    fi

    # Attempt login
    log_info "Logging in as ${OPENSHIFT_USERNAME} to ${OPENSHIFT_API}..."
    if oc login "${OPENSHIFT_API}" \
        --username="${OPENSHIFT_USERNAME}" \
        --password="${OPENSHIFT_PASSWORD}" \
        --insecure-skip-tls-verify=true &> /dev/null; then
        log_success "Successfully logged into OpenShift"
    else
        log_error "Failed to login to OpenShift"
        log_error "Please verify credentials and API URL"
        return 1
    fi
}

check_oc_connection() {
    log_step "Verifying OpenShift connection"

    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "DRY RUN: Would verify OpenShift connection"
        return 0
    fi

    # Check if already logged in
    if ! oc whoami &> /dev/null; then
        log_info "Not currently logged into OpenShift, attempting automatic login..."

        # Try to login automatically
        if ! login_to_openshift; then
            log_error "Automatic login failed"
            log_error ""
            log_error "Manual login options:"
            log_error "  1. Set environment variables:"
            log_error "     export OPENSHIFT_API='https://api.example.com:6443'"
            log_error "     export OPENSHIFT_PASSWORD='your-password'"
            log_error ""
            log_error "  2. Or login manually:"
            log_error "     oc login https://api.example.com:6443"
            log_error ""
            exit 1
        fi
    else
        log_success "Already logged into OpenShift"
    fi

    local current_user
    current_user=$(oc whoami)
    local current_server
    current_server=$(oc whoami --show-server)

    log_success "Connected to OpenShift as: ${current_user}"
    log_info "Server: ${current_server}"

    # Check if user has admin privileges
    if oc auth can-i create clusterrole &> /dev/null; then
        log_success "User has cluster-admin privileges"
    else
        log_warning "User may not have sufficient privileges for cluster-scoped resources"
        log_warning "Some deployment steps may fail without admin access"
    fi
}

execute_script() {
    local script_name="$1"
    shift
    local script_path="${LOCAL_SCRIPTS_DIR}/${script_name}"

    if [[ ! -f "${script_path}" ]]; then
        log_error "Script not found: ${script_path}"
        return 1
    fi
    if [[ ! -x "${script_path}" ]]; then
        chmod +x "${script_path}" 2>/dev/null || true
    fi

    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "DRY RUN: Would execute: ${script_path} $*"
        return 0
    fi

    log_info "Executing: ${script_path} $*"

    local exit_code=0
    if [[ "${VERBOSE}" == "true" ]]; then
        bash -x "${script_path}" "$@" || exit_code=$?
    else
        "${script_path}" "$@" || exit_code=$?
    fi

    return ${exit_code}
}

create_namespace() {
    log_step "Creating namespace: ${NAMESPACE}"

    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "DRY RUN: Would create namespace ${NAMESPACE}"
        return 0
    fi

    if oc get namespace "${NAMESPACE}" &> /dev/null; then
        log_info "Namespace ${NAMESPACE} already exists"
    else
        oc create namespace "${NAMESPACE}"
        log_success "Created namespace: ${NAMESPACE}"
    fi

    # Label namespace for Cost Management Operator
    log_info "Labeling namespace for Cost Management Operator..."
    oc label namespace "${NAMESPACE}" cost_management_optimizations=true --overwrite
    log_success "Namespace labeled successfully"
}

################################################################################
# Deployment steps
################################################################################

deploy_rhbk() {
    if [[ "${SKIP_RHBK}" == "true" ]]; then
        log_warning "Skipping Red Hat Build of Keycloak (RHBK) deployment (--skip-rhbk)"
        return 0
    fi

    log_step "Deploying Red Hat Build of Keycloak (RHBK) (1/5)"

    # Export environment variables for RHBK script
    # export NAMESPACE="${NAMESPACE}"

    if [[ "${VERBOSE}" == "true" ]]; then
        export VERBOSE="true"
    fi

    if ! execute_script "${SCRIPT_DEPLOY_RHBK}"; then
        log_error "Red Hat Build of Keycloak (RHBK) deployment failed"
        exit 1
    fi

    log_success "Red Hat Build of Keycloak (RHBK) deployment completed"
}

deploy_strimzi() {
    if [[ "${SKIP_STRIMZI}" == "true" ]]; then
        log_warning "Skipping Kafka/Strimzi deployment (--skip-strimzi)"
        return 0
    fi

    log_step "Deploying Kafka/Strimzi (2/5)"

    # Export environment variables for Strimzi script
    # export KAFKA_NAMESPACE="${NAMESPACE}"
    export KAFKA_ENVIRONMENT="ocp"
    export STORAGE_CLASS="${STORAGE_CLASS:-}"

    log_verbose "Using storage class: ${STORAGE_CLASS}"

    if [[ "${VERBOSE}" == "true" ]]; then
        export VERBOSE="true"
    fi

    if ! execute_script "${SCRIPT_DEPLOY_STRIMZI}"; then
        log_error "Kafka/Strimzi deployment failed"
        exit 1
    fi

    log_success "Kafka/Strimzi deployment completed"
}

deploy_helm_chart() {
    if [[ "${SKIP_HELM}" == "true" ]]; then
        log_warning "Skipping Cost On-Prem Helm chart installation (--skip-helm)"
        return 0
    fi

    log_step "Deploying Cost On-Prem Helm chart (3/5)"

    # Use the official openshift-values.yaml from repo root
    local values_file="${PROJECT_ROOT}/${OPENSHIFT_VALUES_FILE}"
    download_openshift_values "${values_file}"
    export VALUES_FILE="${values_file}"

    # Export environment variables for Helm script
    export NAMESPACE="${NAMESPACE}"
    export JWT_AUTH_ENABLED="true"
    export USE_LOCAL_CHART="${USE_LOCAL_CHART}"
    export SKIP_S3_SETUP="true"  # Skip S3 auto-detection in CI environments

    if [[ "${VERBOSE}" == "true" ]]; then
        export VERBOSE="true"
    fi

    if ! execute_script "${SCRIPT_INSTALL_HELM}"; then
        log_error "Helm chart deployment failed"
        log_info ""
        log_info "To troubleshoot:"
        log_info "  1. Check Helm release status: helm list -n ${NAMESPACE}"
        log_info "  2. Check pod status: oc get pods -n ${NAMESPACE}"
        log_info "  3. View pod logs: oc logs -n ${NAMESPACE} <pod-name>"
        log_info "  4. Check events: oc get events -n ${NAMESPACE} --sort-by='.lastTimestamp'"
        exit 1
    fi

    log_success "Cost On-Prem Helm chart deployment completed"
}

download_openshift_values() {
    local values_file="$1"

    log_info "Using local OpenShift values file from repository"
    log_verbose "Path: ${values_file}"
    if [[ ! -f "${values_file}" ]]; then
        log_error "OpenShift values file not found at: ${values_file}"
        log_error "Ensure ${OPENSHIFT_VALUES_FILE} exists at the repository root"
        return 1
    fi

    if [[ "${VERBOSE}" == "true" ]]; then
        log_verbose "Values file contents (first 30 lines):"
        head -30 "${values_file}" | while IFS= read -r line; do
            log_verbose "  ${line}"
        done
    fi
}

setup_tls() {
    if [[ "${SKIP_TLS}" == "true" ]]; then
        log_warning "Skipping TLS certificate setup (--skip-tls)"
        return 0
    fi

    log_step "Configuring TLS certificates (4/5)"

    # Export environment variables for TLS script
    export NAMESPACE="${NAMESPACE}"

    if [[ "${VERBOSE}" == "true" ]]; then
        export VERBOSE="true"
    fi

    execute_script "${SCRIPT_SETUP_TLS}"

    log_success "TLS certificate setup completed"
}

run_tests() {
    if [[ "${SKIP_TEST}" == "true" ]]; then
        log_warning "Skipping JWT authentication test (--skip-test)"
        return 0
    fi

    log_step "Testing JWT authentication (5/5)"

    # Ensure we're logged in to OpenShift for JWT test
    if [[ "${DRY_RUN}" != "true" ]]; then
        if ! oc whoami -t &> /dev/null; then
            log_info "Not logged in to OpenShift with a user that has an available token, attempting login for JWT test..."
            if ! login_to_openshift; then
                log_warning "Failed to login to OpenShift, skipping JWT test"
                return 0
            fi
        fi
    fi
    
    # Export environment variables for pytest
    export NAMESPACE="${NAMESPACE}"
    export HELM_RELEASE_NAME="${HELM_RELEASE_NAME:-cost-onprem}"
    export KEYCLOAK_NAMESPACE="${KEYCLOAK_NAMESPACE:-keycloak}"

    if [[ "${VERBOSE}" == "true" ]]; then
        export VERBOSE="true"
    fi
    
    # Run pytest test suite
    local pytest_script="${LOCAL_SCRIPTS_DIR}/run-pytest.sh"
    if [[ ! -x "${pytest_script}" ]]; then
        log_error "Pytest runner not found at: ${pytest_script}"
        exit 1
    fi
    
    log_info "Running pytest test suite..."
    
    # Build pytest arguments
    local pytest_args=()
    if [[ "${VERBOSE}" == "true" ]]; then
        pytest_args+=("-v")
    fi
    if [[ "${INCLUDE_UI}" == "true" ]]; then
        # Override default "not ui" marker to include UI tests
        pytest_args+=("-m" "")
        log_info "Including UI tests (Playwright)"
    fi
    
    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "DRY RUN: Would execute: ${pytest_script} ${pytest_args[*]}"
        return 0
    fi
    
    # Run tests
    # Note: cost_validation tests have their own E2E setup with 300s provider timeout
    if ! "${pytest_script}" "${pytest_args[@]}"; then
        log_error "Pytest test suite failed"
        log_info "JUnit report available at: tests/reports/junit.xml"
        exit 1
    fi
    
    log_success "Pytest test suite completed"
}

################################################################################
# Version tracking
################################################################################

save_version_info() {
    log_step "Saving deployment version information"
    
    local check_components_script="${LOCAL_SCRIPTS_DIR}/qe/check-components.sh"
    
    if [[ ! -x "${check_components_script}" ]]; then
        log_warning "check-components.sh not found at: ${check_components_script}"
        log_warning "Skipping version info generation"
        return 0
    fi
    
    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "DRY RUN: Would execute: MODE=deployment-info NAMESPACE=${NAMESPACE} ${check_components_script}"
        log_info "DRY RUN: Would save version info to: ${VERSION_INFO_FILE}"
        return 0
    fi
    
    # Export environment variables for check-components.sh
    export NAMESPACE="${NAMESPACE}"
    export HELM_RELEASE_NAME="${HELM_RELEASE_NAME:-cost-onprem}"
    export VERSION_INFO_FILE="${VERSION_INFO_FILE}"
    export MODE="deployment-info"
    
    if "${check_components_script}"; then
        log_success "Version info saved to: ${VERSION_INFO_FILE}"
        
        # Display summary if verbose
        if [[ "${VERBOSE}" == "true" ]] && [[ -f "${VERSION_INFO_FILE}" ]]; then
            log_verbose "Version info contents:"
            cat "${VERSION_INFO_FILE}"
        fi
    else
        log_warning "Failed to generate version info"
    fi
}

################################################################################
# Main deployment workflow
################################################################################

print_summary() {
    echo ""
    log_info "Deployment Configuration:"
    echo "  Namespace:           ${NAMESPACE}"
    echo "  Use Local Chart:     ${USE_LOCAL_CHART}"
    echo ""
    log_info "Steps to execute:"
    [[ "${SKIP_RHBK}" == "false" ]] && echo "  ✓ Deploy Red Hat Build of Keycloak (RHBK)" || echo "  ✗ Deploy RHBK (SKIPPED)"
    [[ "${SKIP_STRIMZI}" == "false" ]] && echo "  ✓ Deploy Kafka/Strimzi" || echo "  ✗ Deploy Kafka/Strimzi (SKIPPED)"
    [[ "${SKIP_HELM}" == "false" ]] && echo "  ✓ Deploy Cost On-Prem Helm Chart" || echo "  ✗ Deploy Cost On-Prem Helm Chart (SKIPPED)"
    [[ "${SKIP_TLS}" == "false" ]] && echo "  ✓ Setup TLS Certificates" || echo "  ✗ Setup TLS Certificates (SKIPPED)"
    [[ "${SKIP_TEST}" == "false" ]] && echo "  ✓ Test JWT Flow" || echo "  ✗ Test JWT Flow (SKIPPED)"
    echo ""
}

print_completion() {
    echo ""
    log_success "Deployment completed successfully"
    echo ""
    log_info "Cost On-Prem with JWT authentication deployed to namespace: ${NAMESPACE}"
    echo ""
    log_info "Next steps:"
    echo "  1. Verify: oc get pods -n ${NAMESPACE}"
    echo "  2. Check route: oc get route -n ${NAMESPACE}"
    echo "  3. View logs: oc logs -n ${NAMESPACE} -l app.kubernetes.io/component=ingress -f"
    echo ""
}

main() {
    echo ""
    echo -e "${CYAN}Cost On-Prem OpenShift JWT Authentication Deployment${NC}"
    echo ""

    # Parse command line arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --skip-rhbk)
                SKIP_RHBK=true
                shift
                ;;
            --skip-strimzi)
                SKIP_STRIMZI=true
                shift
                ;;
            --skip-helm)
                SKIP_HELM=true
                shift
                ;;
            --skip-tls)
                SKIP_TLS=true
                shift
                ;;
            --skip-test)
                SKIP_TEST=true
                shift
                ;;
            --namespace)
                NAMESPACE="$2"
                shift 2
                ;;
            --image-tag)
                IMAGE_TAG="$2"
                shift 2
                ;;
            --use-local-chart)
                USE_LOCAL_CHART=true
                shift
                ;;
            --verbose)
                VERBOSE=true
                shift
                ;;
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            --tests-only)
                TESTS_ONLY=true
                shift
                ;;
            --include-ui)
                INCLUDE_UI=true
                shift
                ;;
            --save-versions)
                SAVE_VERSIONS=true
                # Check if next argument is a file path (not another flag)
                if [[ -n "${2:-}" ]] && [[ ! "$2" =~ ^-- ]]; then
                    VERSION_INFO_FILE="$2"
                    shift
                fi
                shift
                ;;
            --help|-h)
                show_help
                ;;
            *)
                log_error "Unknown option: $1"
                echo "Use --help for usage information"
                exit 1
                ;;
        esac
    done

    # In tests-only mode, skip all deployment steps and run tests
    if [[ "${TESTS_ONLY}" == "true" ]]; then
        SKIP_RHBK=true
        SKIP_STRIMZI=true
        SKIP_HELM=true
        SKIP_TLS=true
        SKIP_TEST=false
    fi

    # Show deployment summary
    print_summary

    if [[ "${DRY_RUN}" == "true" ]]; then
        log_warning "DRY RUN MODE: No changes will be made"
        echo ""
    fi

    # Execute deployment steps
    check_prerequisites
    check_oc_connection
    if [[ "${TESTS_ONLY}" != "true" ]]; then
        create_namespace
    fi

    deploy_rhbk
    deploy_strimzi

    # Run Helm sanity test before deploying complex chart
    log_info "Running Helm sanity test to verify basic functionality..."
    if ! bash "${SCRIPT_DIR}/helm-sanity-test.sh"; then
        log_error "Helm sanity test failed - aborting deployment"
        exit 1
    fi

    deploy_helm_chart
    setup_tls
    run_tests

    # Save version information if requested
    if [[ "${SAVE_VERSIONS}" == "true" ]]; then
        save_version_info
    fi

    # Print completion message
    if [[ "${DRY_RUN}" == "false" ]]; then
        print_completion
    else
        echo ""
        log_info "DRY RUN completed. No changes were made."
        echo ""
    fi

    exit 0
}

# Run main function
main "$@"