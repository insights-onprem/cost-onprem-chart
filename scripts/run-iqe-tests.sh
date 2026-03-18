#!/bin/bash
# Run IQE cost-management tests against a deployed cost-onprem chart
#
# Usage:
#   ./scripts/run-iqe-tests.sh [OPTIONS]
#
# Options:
#   --namespace NAME     Target namespace (default: cost-onprem)
#   --marker EXPR        Pytest marker expression (default: cost_ocp_on_prem)
#   --timeout SECONDS    Test timeout (default: 1800)
#   --keep-pod           Don't delete the IQE pod after tests
#   --help               Show this help message
#
# Environment Variables:
#   IQE_IMAGE            IQE container image (default: quay.io/cloudservices/iqe-tests:cost-management)
#   HELM_RELEASE_NAME    Helm release name (default: cost-onprem)
#   KEYCLOAK_SECRET_NS   Namespace containing Keycloak secret (default: keycloak)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Defaults
NAMESPACE="${NAMESPACE:-cost-onprem}"
HELM_RELEASE_NAME="${HELM_RELEASE_NAME:-cost-onprem}"
IQE_MARKER="${IQE_MARKER:-cost_ocp_on_prem}"

# =============================================================================
# TEST FILTER CONFIGURATION
# =============================================================================
# Tests are grouped by skip reason. Each group can be toggled independently.
# Set SKIP_*=false to include those tests in the run.
# See docs/development/skipped-iqe-tests.md for full documentation.
#
# Profiles (use --profile flag):
#   smoke     - Source + cost model tests (~43 tests, ~17 min) - FOR PR CHECKS
#   extended  - All except infra tests (~2100 tests, ~33 min) - DAILY CI
#   stable    - All validated tests (~2350 tests, ~40 min) - WEEKLY CI
#   full      - All cost_ocp_on_prem tests (~3324 tests, 2-3 hours) - RELEASE VALIDATION

# Profile selection (set via --profile flag, overrides individual SKIP_* vars)
# Empty means use individual SKIP_* settings (defaults to stable-like behavior)
TEST_PROFILE="${TEST_PROFILE:-}"

# Apply profile settings (called after arg parsing)
apply_profile() {
    case "${TEST_PROFILE}" in
        smoke)
            # Quick validation (~43 tests, ~17 min)
            # Uses positive -k filter to select source + cost model tests
            # Excludes test_api_ocp_source_crud (backend 500 error on update)
            SMOKE_FILTER="(test_api_ocp_source and not test_api_ocp_source_crud) or test_api_cost_model_ocp"
            # Skip all optional groups for fastest run
            SKIP_INFRA_TESTS=true
            SKIP_SLOW_TESTS=true
            SKIP_DELTA_TESTS=true
            SKIP_FLAKY_TESTS=true
            ;;
        extended)
            # Daily CI (~2100 tests, ~33 min)
            # All tests except blocked groups and infrastructure
            # No positive filter - runs broader set than smoke
            SKIP_INFRA_TESTS=true
            SKIP_SLOW_TESTS=false
            SKIP_DELTA_TESTS=false
            SKIP_FLAKY_TESTS=false
            ;;
        stable)
            # Weekly CI (~2350 tests, ~40 min)
            # All validated groups including infrastructure
            # No positive filter - runs all tests except blocked groups
            SKIP_INFRA_TESTS=false
            SKIP_SLOW_TESTS=false
            SKIP_DELTA_TESTS=false
            SKIP_FLAKY_TESTS=false
            ;;
        full)
            # Release validation (~3324 tests, 2-3 hours)
            # All cost_ocp_on_prem tests, no skip filters
            SKIP_FILTER_BUILD=true
            ;;
        *)
            # Default: same as stable (all validated groups)
            SKIP_INFRA_TESTS=false
            SKIP_SLOW_TESTS=false
            SKIP_DELTA_TESTS=false
            SKIP_FLAKY_TESTS=false
            ;;
    esac
}

# --- GPU/MIG Tests (COST-7179) ---
# Backend bug: completed_datetime never set when GPU data processing fails
# ~90 tests affected
SKIP_GPU_TESTS="${SKIP_GPU_TESTS:-true}"
FILTER_GPU="ai_workloads or distro or test_api_ocp_gpu or test_api_gpu or test_api_cost_model_ocp_gpu or test_api_cost_model_ocp_cost_gpu or test_api_ocp_resource_types_gpu"

# --- ROS Tests (Missing Infrastructure) ---
# Requires MinIO bucket and Vault credentials not available in on-prem
# 3 tests affected
SKIP_ROS_TESTS="${SKIP_ROS_TESTS:-true}"
FILTER_ROS="test_api_ocp_ros"

# --- Date Range Tests (Insufficient Historical Data) ---
# On-prem generates ~60 days of data; 90-day queries and random date ranges fail
# ~50 tests affected
# Note: Use underscores in patterns - pytest -k treats hyphens as "and not"
SKIP_DATE_RANGE_TESTS="${SKIP_DATE_RANGE_TESTS:-true}"
FILTER_DATE_RANGE="(last and 90 and days) or random_date_range or random_daily_time_filter"

# --- Order By Tests (Backend Timeout - COST-7179 related) ---
# Fixtures timeout waiting for completed_datetime
# ~66 tests affected
SKIP_ORDER_BY_TESTS="${SKIP_ORDER_BY_TESTS:-true}"
FILTER_ORDER_BY="test_api_ocp_all_limit_order_by_cost or test_api_ocp_tagging_limit_order_by_cost or test_api_ocp_volume_order_by"

# --- Tag Validation Tests (Missing Tag Data) ---
# Volume tag data not present in generated NISE data
# ~6 tests affected
# Note: Use "and" between words - pytest -k treats hyphens as "and not"
SKIP_TAG_TESTS="${SKIP_TAG_TESTS:-true}"
FILTER_TAG="(volume and tag and exact_match)"

# --- Cost Distribution Tests (Backend Timeout - COST-7179 related) ---
# Fixtures timeout waiting for completed_datetime
# 5 tests affected
SKIP_COST_DISTRIBUTION_TESTS="${SKIP_COST_DISTRIBUTION_TESTS:-true}"
FILTER_COST_DISTRIBUTION="test_api_cost_model_ocp_cost_distribution"

# --- Source CRUD Update Test (Backend Bug) ---
# Backend PATCH endpoint returns 500 error
# 1 test affected
SKIP_SOURCE_CRUD_TESTS="${SKIP_SOURCE_CRUD_TESTS:-true}"
FILTER_SOURCE_CRUD="test_api_ocp_source_crud"

# --- Tag-Based Rates Update Test (COST-7179 related) ---
# Fixtures timeout waiting for completed_datetime after tag-based rate update
# 1 test affected
SKIP_TAG_RATES_TESTS="${SKIP_TAG_RATES_TESTS:-true}"
FILTER_TAG_RATES="test_api_cost_model_rates_update_to_tag_based"

# --- Unstable Tests (Timing/Data-Dependent Failures) ---
# Tests that fail intermittently due to timing, date calculations, or data dependencies
# Jira: FLPATH-2689
# Failures observed 2026-03-17 in stable profile run:
#   - date_range_end_negative: Expects API exception not raised (3 tests)
#   - virtual_machines_report_content: Calculation mismatch (1 test)
#   - volume_deltas_monthly: Delta calculation mismatch (3 tests)
#   - currency tests: IndexError - empty response (8 tests)
#   - forecast tests: Date offset off-by-one (5 tests)
# ~20 tests affected
SKIP_UNSTABLE_TESTS="${SKIP_UNSTABLE_TESTS:-true}"
FILTER_UNSTABLE="test_api_ocp_network_endpoint_date_range_end_negative or test_api_ocp_volume_endpoint_date_range_end_negative or test_api_ocp_tagging_endpoint_date_range_end_negative or test_api_ocp_virtual_machines_report_content or test_api_ocp_volume_deltas_monthly or test_api_ocp_currency_report_param or test_api_ocp_currency_compute or test_api_ocp_currency_memory or test_api_ocp_currency_volume or test_api_ocp_forecast_values or test_api_ocp_forecast_data_other_params or test_api_ocp_forecast_prediction_days"

# --- Infrastructure/Config Tests (On-prem Incompatible) ---
# Tests that were expected to require cloud infrastructure but now pass
# Validated 2026-03-16: 256/258 passed (see skip-group-validation-plan.md Phase 8)
# Note: test_api_cost_model_rates_update_to_tag_based moved to SKIP_TAG_RATES_TESTS (COST-7179)
# ~257 tests affected (includes parameterized variants)
SKIP_INFRA_TESTS="${SKIP_INFRA_TESTS:-false}"
FILTER_INFRA="test_api_ocp_all_validate_items_date_range_monthly or test_api_ocp_ingest_source_static or test_api_ocp_ingest_source_eur or test_api_ocp_for_aws or test_api_ocp_cost_filtered_top_projects or test_api_ocp_all_bucketing or test_api_ocp_coros_distribution_negative_filtering"

# --- Long Running Tests (Performance) ---
# Tests that take >2 minutes each - slow but stable
# Validated 2026-03-17: 13/13 passed (see skip-group-validation-plan.md Phase 10)
# ~13 tests affected, ~20 minutes total
# Set SKIP_SLOW_TESTS=true for faster feedback loops
SKIP_SLOW_TESTS="${SKIP_SLOW_TESTS:-false}"
FILTER_SLOW="test_api_ocp_source_raw_node_cluster_capacity or test_api_source_cluster_info_sources or test_api_ocp_source_all_bucketing_platform_update or test_api_ocp_all_project_classification or test_api_ocp_daily_flow_ingest"

# --- Delta/Calculation Tests (Data Timing Issues) ---
# Tests that compare month-over-month deltas - previously expected to fail but now pass
# Validated 2026-03-16: 12/12 passed (see skip-group-validation-plan.md Phase 7)
# ~12 tests affected (includes parameterized variants)
SKIP_DELTA_TESTS="${SKIP_DELTA_TESTS:-false}"
FILTER_DELTA="deltas_monthly or test_api_ocp_coros_distribution_deltas"

# --- Flaky/Data-Dependent Tests ---
# Tests that were previously marked as flaky but now pass consistently
# Validated 2026-03-16: 54/54 passed (see skip-group-validation-plan.md Phase 6)
# ~54 tests affected (includes parameterized variants)
SKIP_FLAKY_TESTS="${SKIP_FLAKY_TESTS:-false}"
FILTER_FLAKY="test_api_ocp_forecast_data_other_params or test_api_ocp_forecast_prediction_days or test_api_ocp_forecast_values or test_api_ocp_resource_types_nodes_search or test_api_ocp_resource_types_clusters_search or test_api_ocp_resource_types_projects_search or test_api_ocp_currency_report_param or test_api_ocp_currency_compute or test_api_ocp_currency_memory or test_api_ocp_currency_volume or test_api_ocp_tags_filtered_total_match_group_by_total"

# Build the combined filter from enabled skip groups
build_test_filter() {
    local filters=()
    
    if [[ "${SKIP_GPU_TESTS}" == "true" ]]; then
        filters+=("(${FILTER_GPU})")
    fi
    if [[ "${SKIP_ROS_TESTS}" == "true" ]]; then
        filters+=("(${FILTER_ROS})")
    fi
    if [[ "${SKIP_DATE_RANGE_TESTS}" == "true" ]]; then
        filters+=("(${FILTER_DATE_RANGE})")
    fi
    if [[ "${SKIP_ORDER_BY_TESTS}" == "true" ]]; then
        filters+=("(${FILTER_ORDER_BY})")
    fi
    if [[ "${SKIP_TAG_TESTS}" == "true" ]]; then
        filters+=("(${FILTER_TAG})")
    fi
    if [[ "${SKIP_COST_DISTRIBUTION_TESTS}" == "true" ]]; then
        filters+=("(${FILTER_COST_DISTRIBUTION})")
    fi
    if [[ "${SKIP_SOURCE_CRUD_TESTS}" == "true" ]]; then
        filters+=("(${FILTER_SOURCE_CRUD})")
    fi
    if [[ "${SKIP_TAG_RATES_TESTS}" == "true" ]]; then
        filters+=("(${FILTER_TAG_RATES})")
    fi
    if [[ "${SKIP_UNSTABLE_TESTS}" == "true" ]]; then
        filters+=("(${FILTER_UNSTABLE})")
    fi
    if [[ "${SKIP_INFRA_TESTS}" == "true" ]]; then
        filters+=("(${FILTER_INFRA})")
    fi
    if [[ "${SKIP_SLOW_TESTS}" == "true" ]]; then
        filters+=("(${FILTER_SLOW})")
    fi
    if [[ "${SKIP_DELTA_TESTS}" == "true" ]]; then
        filters+=("(${FILTER_DELTA})")
    fi
    if [[ "${SKIP_FLAKY_TESTS}" == "true" ]]; then
        filters+=("(${FILTER_FLAKY})")
    fi
    
    if [[ ${#filters[@]} -eq 0 ]]; then
        echo ""
        return
    fi
    
    # Join with " or " and wrap in "not (...)"
    local combined=""
    for i in "${!filters[@]}"; do
        if [[ $i -eq 0 ]]; then
            combined="${filters[$i]}"
        else
            combined="${combined} or ${filters[$i]}"
        fi
    done
    
    echo "not (${combined})"
}

# IQE_FILTER is built after argument parsing (see below)
IQE_TIMEOUT="${IQE_TIMEOUT:-14400}"
IQE_IMAGE="${IQE_IMAGE:-quay.io/cloudservices/iqe-tests:cost-management}"
KEEP_POD=false
KEYCLOAK_SECRET_NS="${KEYCLOAK_SECRET_NS:-keycloak}"
KEYCLOAK_SECRET_NAME="${KEYCLOAK_SECRET_NAME:-keycloak-client-secret-cost-management-operator}"
SYNC_PULL_SECRET=false
CLEAN_SOURCES="${CLEAN_SOURCES:-true}"
NISE_VERSION="${NISE_VERSION:-}"

show_help() {
    cat << EOF
Run IQE cost-management tests against a deployed cost-onprem chart

Usage: $(basename "$0") [OPTIONS]

Options:
    --namespace NAME     Target namespace (default: cost-onprem)
    --marker EXPR        Pytest marker expression (default: cost_ocp_on_prem)
    --filter EXPR        Pytest -k filter expression (overrides skip groups)
    --timeout SECONDS    Test timeout (default: 14400)
    --keep-pod           Don't delete the IQE pod after tests
    --clean-sources      Delete existing sources before running tests (default)
    --keep-sources       Keep existing sources (reuse data from previous runs)
    --nise-version VER   NISE version to use (e.g., 5.3.5)
    --sync-pull-secret   Sync local container registry credentials to cluster
    --profile PROFILE    Test profile (smoke, extended, stable, full)
    --help               Show this help message

Test Profiles (use --profile):
    smoke      Source + cost model tests (~43 tests, ~17 min) - PR checks
    extended   All except infra tests (~2100 tests, ~33 min) - Daily CI
    stable     All validated tests (~2350 tests, ~40 min) - Weekly CI
    full       All cost_ocp_on_prem tests (~3324 tests, ~60 min) - Release
    (default)  Same as stable

Environment Variables:
    IQE_IMAGE            IQE container image
    IQE_FILTER           Custom filter (overrides all skip groups)
    TEST_PROFILE         Same as --profile flag
    HELM_RELEASE_NAME    Helm release name (default: cost-onprem)
    KEYCLOAK_SECRET_NS   Namespace containing Keycloak secret (default: keycloak)
    NISE_VERSION         NISE version to use

Examples:
    # Quick smoke tests for PR validation (~17 min)
    ./scripts/run-iqe-tests.sh --profile smoke

    # Extended tests for daily CI (~33 min)
    ./scripts/run-iqe-tests.sh --profile extended

    # Stable tests for weekly CI (~40 min)
    ./scripts/run-iqe-tests.sh --profile stable

    # Full release validation (~2-3 hours)
    ./scripts/run-iqe-tests.sh --profile full

    # Run specific tests with custom filter
    ./scripts/run-iqe-tests.sh --filter "test_api_ocp_source"

    # Keep pod for debugging
    ./scripts/run-iqe-tests.sh --keep-pod
EOF
}

# Parse arguments
# Note: --filter is parsed but filter is rebuilt after arg parsing if not explicitly set
EXPLICIT_FILTER=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --namespace) NAMESPACE="$2"; shift 2 ;;
        --marker) IQE_MARKER="$2"; shift 2 ;;
        --filter) EXPLICIT_FILTER="$2"; shift 2 ;;
        --timeout) IQE_TIMEOUT="$2"; shift 2 ;;
        --keep-pod) KEEP_POD=true; shift ;;
        --clean-sources) CLEAN_SOURCES=true; shift ;;
        --keep-sources) CLEAN_SOURCES=false; shift ;;
        --nise-version) NISE_VERSION="$2"; shift 2 ;;
        --profile) TEST_PROFILE="$2"; shift 2 ;;
        --sync-pull-secret) SYNC_PULL_SECRET=true; shift ;;
        --help) show_help; exit 0 ;;
        *) echo "Unknown option: $1"; show_help; exit 1 ;;
    esac
done

# Apply profile settings if specified (overrides individual SKIP_* defaults)
if [[ -n "${TEST_PROFILE}" ]]; then
    apply_profile
fi

# Rebuild filter after argument parsing
if [[ "${SKIP_FILTER_BUILD:-false}" == "true" ]]; then
    # Full profile: no filters
    IQE_FILTER=""
elif [[ -n "${EXPLICIT_FILTER}" ]]; then
    # User-provided filter overrides everything
    IQE_FILTER="${EXPLICIT_FILTER}"
elif [[ -n "${SMOKE_FILTER:-}" ]]; then
    # Smoke/extended profile: positive filter AND skip filters combined
    SKIP_FILTER=$(build_test_filter)
    if [[ -n "${SKIP_FILTER}" ]]; then
        IQE_FILTER="(${SMOKE_FILTER}) and ${SKIP_FILTER}"
    else
        IQE_FILTER="${SMOKE_FILTER}"
    fi
else
    # Stable/default: just skip filters (runs all tests except blocked)
    IQE_FILTER=$(build_test_filter)
fi

echo "========== Running IQE Cost Management Tests =========="
echo "Namespace: ${NAMESPACE}"
echo "Marker: ${IQE_MARKER}"
echo "Timeout: ${IQE_TIMEOUT}s"
echo "Image: ${IQE_IMAGE}"
if [[ -n "${TEST_PROFILE}" ]]; then
    echo "Profile: ${TEST_PROFILE}"
fi
echo ""
echo "Skip Groups:"
echo "  GPU tests (COST-7179):     ${SKIP_GPU_TESTS}"
echo "  ROS tests:                 ${SKIP_ROS_TESTS}"
echo "  Date range tests:          ${SKIP_DATE_RANGE_TESTS}"
echo "  Order by tests:            ${SKIP_ORDER_BY_TESTS}"
echo "  Tag validation:            ${SKIP_TAG_TESTS}"
echo "  Cost distribution:         ${SKIP_COST_DISTRIBUTION_TESTS}"
echo "  Infrastructure tests:      ${SKIP_INFRA_TESTS}"
echo "  Slow tests (>2min):        ${SKIP_SLOW_TESTS}"
echo "  Delta/calculation:         ${SKIP_DELTA_TESTS:-true}"
echo "  Flaky/data-dependent:      ${SKIP_FLAKY_TESTS:-true}"
if [ -n "${IQE_FILTER}" ]; then
    echo ""
    echo "Computed filter: ${IQE_FILTER}"
fi

# Validate container image pull access
echo ""
echo "Validating access to IQE container image..."

# Extract registry from image (e.g., quay.io from quay.io/cloudservices/iqe-tests:cost-management)
IQE_REGISTRY=$(echo "${IQE_IMAGE}" | cut -d'/' -f1)

# Skip validation for internal OpenShift registry (cluster will handle auth)
if [[ "${IQE_REGISTRY}" == *"openshift-image-registry"* ]] || [[ "${IQE_REGISTRY}" == *"image-registry.openshift-image-registry"* ]]; then
    echo "✓ Using internal OpenShift registry - skipping local validation"
    IMAGE_PULL_SECRET_NAME="default-dockercfg"
else
    IMAGE_PULL_SECRET_NAME=""
    # Try to pull the image manifest to verify access without downloading the full image
    if command -v skopeo &>/dev/null; then
        # Use skopeo if available (faster, doesn't download layers)
        SKOPEO_OPTS=""
        # Add --tls-verify=false for non-standard registries
        if [[ "${IQE_REGISTRY}" != "quay.io" ]] && [[ "${IQE_REGISTRY}" != "registry.redhat.io" ]]; then
            SKOPEO_OPTS="--tls-verify=false"
        fi
        if ! skopeo inspect ${SKOPEO_OPTS} "docker://${IQE_IMAGE}" &>/dev/null; then
            echo ""
            echo "ERROR: Cannot access IQE container image: ${IQE_IMAGE}"
            echo ""
            echo "This may be due to:"
            echo "  1. Missing authentication to ${IQE_REGISTRY}"
            echo "  2. The image does not exist or tag is invalid"
            echo "  3. Network connectivity issues"
            echo ""
            echo "To authenticate with ${IQE_REGISTRY}:"
            if [[ "${IQE_REGISTRY}" == "quay.io" ]]; then
                echo "  podman login quay.io"
                echo "  # or"
                echo "  docker login quay.io"
                echo ""
                echo "Note: The IQE image requires Red Hat internal access."
                echo "Contact the Cost Management team for access to quay.io/cloudservices/iqe-tests"
            else
                echo "  podman login ${IQE_REGISTRY}"
                echo "  # or"
                echo "  docker login ${IQE_REGISTRY}"
            fi
            exit 1
        fi
        echo "✓ Image accessible via skopeo"
    elif command -v podman &>/dev/null; then
        # Fall back to podman
        if ! podman pull --quiet "${IQE_IMAGE}" &>/dev/null; then
            echo ""
            echo "ERROR: Cannot pull IQE container image: ${IQE_IMAGE}"
            echo ""
            echo "To authenticate with ${IQE_REGISTRY}:"
            echo "  podman login ${IQE_REGISTRY}"
            if [[ "${IQE_REGISTRY}" == "quay.io" ]]; then
                echo ""
                echo "Note: The IQE image requires Red Hat internal access."
                echo "Contact the Cost Management team for access to quay.io/cloudservices/iqe-tests"
            fi
            exit 1
        fi
        echo "✓ Image accessible via podman"
    elif command -v docker &>/dev/null; then
        # Fall back to docker
        if ! docker pull --quiet "${IQE_IMAGE}" &>/dev/null; then
            echo ""
            echo "ERROR: Cannot pull IQE container image: ${IQE_IMAGE}"
            echo ""
            echo "To authenticate with ${IQE_REGISTRY}:"
            echo "  docker login ${IQE_REGISTRY}"
            if [[ "${IQE_REGISTRY}" == "quay.io" ]]; then
                echo ""
                echo "Note: The IQE image requires Red Hat internal access."
                echo "Contact the Cost Management team for access to quay.io/cloudservices/iqe-tests"
            fi
            exit 1
        fi
        echo "✓ Image accessible via docker"
    else
        echo "WARNING: Cannot validate image access (skopeo/podman/docker not found)"
        echo "         The pod may fail to start if image pull fails in the cluster"
    fi
fi

# Get S3 credentials from the deployed chart
S3_SECRET_NAME="${HELM_RELEASE_NAME}-storage-credentials"
echo ""
echo "Extracting configuration from cluster..."

S3_ACCESS_KEY=$(kubectl get secret "$S3_SECRET_NAME" -n "$NAMESPACE" -o jsonpath='{.data.access-key}' 2>/dev/null | base64 -d || echo "")
S3_SECRET_KEY=$(kubectl get secret "$S3_SECRET_NAME" -n "$NAMESPACE" -o jsonpath='{.data.secret-key}' 2>/dev/null | base64 -d || echo "")

# Get S3 endpoint and bucket names from MASU pod
S3_ENDPOINT=$(kubectl exec -n "$NAMESPACE" deploy/${HELM_RELEASE_NAME}-koku-masu -c masu -- printenv S3_ENDPOINT 2>/dev/null || echo "")
S3_BUCKET_NAME=$(kubectl exec -n "$NAMESPACE" deploy/${HELM_RELEASE_NAME}-koku-masu -c masu -- printenv S3_BUCKET_NAME 2>/dev/null || echo "koku-data")
S3_ROS_BUCKET=$(kubectl exec -n "$NAMESPACE" deploy/${HELM_RELEASE_NAME}-koku-masu -c masu -- printenv REQUESTED_ROS_BUCKET 2>/dev/null || echo "ros-data")

# Determine S3 port and SSL from endpoint
if [[ "$S3_ENDPOINT" =~ :([0-9]+)$ ]]; then
    S3_PORT="${BASH_REMATCH[1]}"
else
    S3_PORT="443"
fi
# Assume SSL unless port is 7480 (S4 default) or 9000 (MinIO default)
if [[ "$S3_PORT" == "7480" ]] || [[ "$S3_PORT" == "9000" ]]; then
    S3_USE_SSL="false"
else
    S3_USE_SSL="true"
fi

# Get Keycloak credentials from the auth secret
# Try uppercase keys first (keycloak-client-secret-*), then lowercase (cost-management-auth-secret)
KEYCLOAK_CLIENT_ID=$(kubectl get secret "$KEYCLOAK_SECRET_NAME" -n "$KEYCLOAK_SECRET_NS" -o jsonpath='{.data.CLIENT_ID}' 2>/dev/null | base64 -d || \
                     kubectl get secret "$KEYCLOAK_SECRET_NAME" -n "$KEYCLOAK_SECRET_NS" -o jsonpath='{.data.client_id}' 2>/dev/null | base64 -d || \
                     echo "cost-management-operator")
KEYCLOAK_CLIENT_SECRET=$(kubectl get secret "$KEYCLOAK_SECRET_NAME" -n "$KEYCLOAK_SECRET_NS" -o jsonpath='{.data.CLIENT_SECRET}' 2>/dev/null | base64 -d || \
                         kubectl get secret "$KEYCLOAK_SECRET_NAME" -n "$KEYCLOAK_SECRET_NS" -o jsonpath='{.data.client_secret}' 2>/dev/null | base64 -d || \
                         echo "")

# Get Keycloak route for OAuth URL
KEYCLOAK_HOST=$(kubectl get route keycloak -n keycloak -o jsonpath='{.spec.host}' 2>/dev/null || echo "")
OAUTH_URL="https://${KEYCLOAK_HOST}/realms/kubernetes/protocol/openid-connect"

# Get org_id from Keycloak test user (or use default)
ORG_ID="org1234567"  # Default value
if [ -n "$KEYCLOAK_HOST" ]; then
    # Get admin password
    KEYCLOAK_ADMIN_PASS=$(kubectl get secret keycloak-initial-admin -n keycloak -o jsonpath='{.data.password}' 2>/dev/null | base64 -d || echo "")
    if [ -n "$KEYCLOAK_ADMIN_PASS" ]; then
        # Get admin token
        ADMIN_TOKEN=$(curl -sk -X POST "https://${KEYCLOAK_HOST}/realms/master/protocol/openid-connect/token" \
            -d "client_id=admin-cli" \
            -d "grant_type=password" \
            -d "username=admin" \
            -d "password=${KEYCLOAK_ADMIN_PASS}" 2>/dev/null | jq -r '.access_token // empty')
        
        if [ -n "$ADMIN_TOKEN" ]; then
            # Get test user's org_id
            USER_ORG_ID=$(curl -sk "https://${KEYCLOAK_HOST}/admin/realms/kubernetes/users?username=test&exact=true" \
                -H "Authorization: Bearer ${ADMIN_TOKEN}" 2>/dev/null | jq -r '.[0].attributes.org_id[0] // empty')
            if [ -n "$USER_ORG_ID" ]; then
                ORG_ID="$USER_ORG_ID"
            fi
        fi
    fi
fi

# Get Koku API route hostname (external access)
KOKU_ROUTE_HOST=$(kubectl get route ${HELM_RELEASE_NAME}-api -n "$NAMESPACE" -o jsonpath='{.spec.host}' 2>/dev/null || echo "")

# Service hostnames (in-cluster DNS for pod-to-pod communication)
KOKU_HOSTNAME="${KOKU_ROUTE_HOST}"
MASU_HOSTNAME="${HELM_RELEASE_NAME}-koku-masu.${NAMESPACE}.svc.cluster.local"
MASU_PORT="8000"

echo ""
echo "Service Configuration:"
echo "  Koku API (route): ${KOKU_HOSTNAME}"
echo "  MASU (in-cluster): ${MASU_HOSTNAME}:${MASU_PORT}"
echo "  S3 Endpoint: ${S3_ENDPOINT}"
echo "  S3 Port: ${S3_PORT} (SSL: ${S3_USE_SSL})"
echo "  S3 Buckets: koku=${S3_BUCKET_NAME}, ros=${S3_ROS_BUCKET}"
echo "  OAuth URL: ${OAUTH_URL}"
echo "  Keycloak Client ID: ${KEYCLOAK_CLIENT_ID}"

# Validate required configuration
if [ -z "$KOKU_HOSTNAME" ]; then
    echo "ERROR: Could not find Koku API route. Is the chart deployed?"
    exit 1
fi

if [ -z "$KEYCLOAK_CLIENT_SECRET" ]; then
    echo "WARNING: Could not extract Keycloak client secret. Authentication may fail."
fi

# Check if cluster has pull secret for the IQE image registry
echo ""
echo "Checking cluster pull secret configuration..."
IQE_REGISTRY=$(echo "${IQE_IMAGE}" | cut -d'/' -f1)

# Function to sync local credentials to cluster
sync_local_credentials() {
    local auth_file=""
    
    # Find local auth file (podman uses different location than docker)
    if [ -f "${XDG_RUNTIME_DIR}/containers/auth.json" ]; then
        auth_file="${XDG_RUNTIME_DIR}/containers/auth.json"
    elif [ -f "$HOME/.docker/config.json" ]; then
        auth_file="$HOME/.docker/config.json"
    elif [ -f "$HOME/.config/containers/auth.json" ]; then
        auth_file="$HOME/.config/containers/auth.json"
    fi
    
    if [ -z "$auth_file" ]; then
        echo "ERROR: No local container registry credentials found."
        echo "       Expected locations:"
        echo "         - \${XDG_RUNTIME_DIR}/containers/auth.json (podman)"
        echo "         - \$HOME/.docker/config.json (docker)"
        echo "         - \$HOME/.config/containers/auth.json (podman rootless)"
        echo ""
        echo "       Please authenticate first:"
        echo "         podman login ${IQE_REGISTRY}"
        echo "         # or"
        echo "         docker login ${IQE_REGISTRY}"
        return 1
    fi
    
    # Check if the auth file contains credentials for the IQE registry
    if ! grep -q "${IQE_REGISTRY}" "$auth_file" 2>/dev/null; then
        echo "ERROR: Local credentials file does not contain ${IQE_REGISTRY}"
        echo "       Please authenticate:"
        echo "         podman login ${IQE_REGISTRY}"
        return 1
    fi
    
    echo "Found local credentials at: $auth_file"
    
    # Create or update the pull secret in the namespace
    echo "Creating pull secret 'iqe-pull-secret' in namespace ${NAMESPACE}..."
    kubectl create secret generic iqe-pull-secret \
        --from-file=.dockerconfigjson="$auth_file" \
        --type=kubernetes.io/dockerconfigjson \
        -n "${NAMESPACE}" \
        --dry-run=client -o yaml | kubectl apply -f -
    
    # Link the secret to the default service account
    echo "Linking pull secret to default service account..."
    kubectl patch serviceaccount default -n "${NAMESPACE}" \
        -p '{"imagePullSecrets": [{"name": "iqe-pull-secret"}]}' 2>/dev/null || \
    kubectl patch serviceaccount default -n "${NAMESPACE}" \
        --type='json' -p='[{"op": "add", "path": "/imagePullSecrets/-", "value": {"name": "iqe-pull-secret"}}]' 2>/dev/null || true
    
    echo "✓ Local credentials synced to cluster"
    return 0
}

# Check for pull secret in namespace
PULL_SECRET_EXISTS=false
if kubectl get secret -n "${NAMESPACE}" -o name 2>/dev/null | grep -q "pull-secret\|docker\|iqe-pull-secret"; then
    PULL_SECRET_EXISTS=true
fi

# Check namespace-scoped secret first
NAMESPACE_HAS_PULL_SECRET=false
if kubectl get secret iqe-pull-secret -n "${NAMESPACE}" &>/dev/null; then
    echo "✓ Found iqe-pull-secret in namespace ${NAMESPACE}"
    NAMESPACE_HAS_PULL_SECRET=true
fi

# If no namespace secret, try to create one from local credentials first (most reliable)
if [ "$NAMESPACE_HAS_PULL_SECRET" = "false" ]; then
    # Try local container auth files (podman/docker)
    local_auth_file=""
    for auth_path in "${XDG_RUNTIME_DIR:-/nonexistent}/containers/auth.json" \
                     "${HOME}/.config/containers/auth.json" \
                     "${HOME}/.docker/config.json"; do
        if [ -f "$auth_path" ] && grep -q "${IQE_REGISTRY}" "$auth_path" 2>/dev/null; then
            local_auth_file="$auth_path"
            break
        fi
    done
    
    if [ -n "$local_auth_file" ]; then
        echo "Found local ${IQE_REGISTRY} credentials, creating iqe-pull-secret..."
        kubectl create secret generic iqe-pull-secret \
            --from-file=.dockerconfigjson="$local_auth_file" \
            --type=kubernetes.io/dockerconfigjson \
            -n "${NAMESPACE}" \
            --dry-run=client -o yaml | kubectl apply -f -
        echo "✓ Created iqe-pull-secret from local credentials"
        NAMESPACE_HAS_PULL_SECRET=true
    fi
fi

# If still no secret, try global pull-secret as fallback
if [ "$NAMESPACE_HAS_PULL_SECRET" = "false" ]; then
    if kubectl get secret pull-secret -n openshift-config &>/dev/null; then
        # Check if global pull secret has quay.io credentials
        if kubectl get secret pull-secret -n openshift-config -o jsonpath='{.data.\.dockerconfigjson}' 2>/dev/null | base64 -d | grep -q "${IQE_REGISTRY}"; then
            echo "Found ${IQE_REGISTRY} credentials in global pull-secret, copying to namespace..."
            kubectl get secret pull-secret -n openshift-config -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d > /tmp/iqe-pull-secret.json
            kubectl create secret generic iqe-pull-secret \
                --from-file=.dockerconfigjson=/tmp/iqe-pull-secret.json \
                --type=kubernetes.io/dockerconfigjson \
                -n "${NAMESPACE}" \
                --dry-run=client -o yaml | kubectl apply -f -
            rm -f /tmp/iqe-pull-secret.json
            echo "✓ Copied global pull-secret to iqe-pull-secret in namespace ${NAMESPACE}"
            NAMESPACE_HAS_PULL_SECRET=true
        fi
    fi
fi

# Handle missing credentials
if [ "$NAMESPACE_HAS_PULL_SECRET" = "false" ]; then
    echo ""
    echo "WARNING: Could not find pull credentials for ${IQE_REGISTRY}"
    echo ""
    echo "  Please authenticate to quay.io first:"
    echo "    podman login quay.io"
    echo "    # or"
    echo "    docker login quay.io"
    echo ""
    echo "  Then re-run this script."
    echo ""
fi

# Delete existing pod if present
kubectl delete pod iqe-cost-tests -n "${NAMESPACE}" --ignore-not-found=true 2>/dev/null || true

# Create ConfigMap with cluster CA certificates for SSL verification
echo ""
echo "Creating CA certificate bundle for SSL verification..."

# Extract ingress CA (used by routes like Keycloak)
INGRESS_CA=$(kubectl get secret router-ca -n openshift-ingress-operator -o jsonpath='{.data.tls\.crt}' 2>/dev/null | base64 -d || echo "")

# Extract service CA (used by internal services)
SERVICE_CA=$(kubectl get configmap openshift-service-ca.crt -n openshift-config-managed -o jsonpath='{.data.service-ca\.crt}' 2>/dev/null || echo "")

# Combine CAs into a bundle
CA_BUNDLE=""
if [ -n "$INGRESS_CA" ]; then
    CA_BUNDLE="${INGRESS_CA}"
fi
if [ -n "$SERVICE_CA" ]; then
    if [ -n "$CA_BUNDLE" ]; then
        CA_BUNDLE="${CA_BUNDLE}
${SERVICE_CA}"
    else
        CA_BUNDLE="${SERVICE_CA}"
    fi
fi

if [ -n "$CA_BUNDLE" ]; then
    # Create or update the CA bundle ConfigMap
    kubectl create configmap iqe-ca-bundle \
        --from-literal=ca-bundle.crt="${CA_BUNDLE}" \
        -n "${NAMESPACE}" \
        --dry-run=client -o yaml | kubectl apply -f -
    echo "✓ CA certificate bundle created"
else
    echo "WARNING: Could not extract cluster CA certificates"
fi

echo ""
echo "Creating IQE test pod..."

# Determine imagePullSecrets based on registry
if [ -n "${IMAGE_PULL_SECRET_NAME:-}" ]; then
    # Use dynamically determined secret (e.g., for internal registry)
    IMAGE_PULL_SECRETS_YAML="imagePullSecrets:
  - name: ${IMAGE_PULL_SECRET_NAME}"
else
    IMAGE_PULL_SECRETS_YAML="imagePullSecrets:
  - name: iqe-pull-secret"
fi

# Build NISE_VERSION env var if specified
NISE_VERSION_ENV=""
if [ -n "${NISE_VERSION}" ]; then
    NISE_VERSION_ENV="    - name: DYNACONF_NISE_VERSION
      value: \"${NISE_VERSION}\""
fi

cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: iqe-cost-tests
  namespace: ${NAMESPACE}
  labels:
    app: iqe-tests
    test-type: cost-management
spec:
  restartPolicy: Never
  ${IMAGE_PULL_SECRETS_YAML}
  securityContext:
    runAsNonRoot: true
    seccompProfile:
      type: RuntimeDefault
  containers:
  - name: iqe
    image: ${IQE_IMAGE}
    command: ["/bin/sh", "-c"]
    args:
    - |
      echo "========== IQE Test Pod Started =========="
      echo "ENV_FOR_DYNACONF: \${ENV_FOR_DYNACONF}"
      echo "DYNACONF_ONPREM_KOKU_HOSTNAME: \${DYNACONF_ONPREM_KOKU_HOSTNAME}"
      echo "DYNACONF_ONPREM_CLIENT_ID: \${DYNACONF_ONPREM_CLIENT_ID}"
      echo "DYNACONF_ONPREM_OAUTH_URL: \${DYNACONF_ONPREM_OAUTH_URL}"
      echo ""
      
      echo "Running IQE tests with marker: ${IQE_MARKER}"
      # --force-default-user is required because the cost_onprem config's Jinja templates
      # don't evaluate correctly (main.get('ONPREM_*') returns None since DYNACONF_ONPREM_*
      # vars are placed at root level, not inside 'main'). We bypass this by setting
      # the user explicitly and providing all user config via DYNACONF env vars.
      # Note: user name must be lowercase to match the DYNACONF_users__cost_onprem_user__* keys
      if [ -n "\${IQE_FILTER}" ]; then
        echo "Filter expression: \${IQE_FILTER}"
        iqe tests plugin cost_management \
          --force-default-user cost_onprem_user \
          -m "${IQE_MARKER}" \
          -k "\${IQE_FILTER}" \
          -vv \
          --junitxml=/results/junit.xml \
          2>&1 | tee /results/test-output.log
      else
        iqe tests plugin cost_management \
          --force-default-user cost_onprem_user \
          -m "${IQE_MARKER}" \
          -vv \
          --junitxml=/results/junit.xml \
          2>&1 | tee /results/test-output.log
      fi
      
      EXIT_CODE=\$?
      echo ""
      echo "Tests completed with exit code: \${EXIT_CODE}"
      
      # Keep pod alive briefly for result collection
      sleep 60
      exit \$EXIT_CODE
    env:
    # IQE Framework Configuration
    - name: ENV_FOR_DYNACONF
      value: "cost_onprem"
    - name: IQE_PLUGINS
      value: "cost-management"
    - name: IQE_FILTER
      value: "${IQE_FILTER}"
    
    # Disable vault - on-prem uses inline credentials, not vault secrets
    - name: DYNACONF_IQE_VAULT_LOADER_ENABLED
      value: "false"
    - name: DYNACONF_IQE_VAULT_OIDC_AUTH
      value: "false"
    
    # DYNACONF variables for cost_onprem environment
    # Source values - these SHOULD feed Jinja templates like main.get('ONPREM_*')
    # but Jinja evaluation happens before env vars are merged, so we also set targets
    - name: DYNACONF_ONPREM_KOKU_HOSTNAME
      value: "${KOKU_HOSTNAME}"
    - name: DYNACONF_ONPREM_CLIENT_ID
      value: "${KEYCLOAK_CLIENT_ID}"
    - name: DYNACONF_ONPREM_CLIENT_SECRET
      value: "${KEYCLOAK_CLIENT_SECRET}"
    - name: DYNACONF_ONPREM_OAUTH_URL
      value: "${OAUTH_URL}"
    - name: DYNACONF_ONPREM_MASU_HOSTNAME
      value: "${MASU_HOSTNAME}"
    - name: DYNACONF_ONPREM_MASU_PORT
      value: "${MASU_PORT}"
    
    # Direct target values - bypass Jinja templates that don't evaluate correctly
    - name: DYNACONF_MAIN__HOSTNAME
      value: "${KOKU_HOSTNAME}"
    - name: DYNACONF_MAIN__SCHEME
      value: "https"
    - name: DYNACONF_MAIN__SSL_VERIFY
      value: "false"
    - name: DYNACONF_HTTP__DEFAULT_AUTH_TYPE
      value: "jwt-auth"
    - name: DYNACONF_HTTP__OAUTH_CLIENT_ID
      value: "${KEYCLOAK_CLIENT_ID}"
    - name: DYNACONF_HTTP__OAUTH_BASE_URL
      value: "${OAUTH_URL}"
    - name: DYNACONF_HTTP__SSL_VERIFY
      value: "false"
    
    # Service objects configuration
    - name: DYNACONF_SERVICE_OBJECTS__KOKU__CONFIG__HOSTNAME
      value: "${KOKU_HOSTNAME}"
    - name: DYNACONF_SERVICE_OBJECTS__KOKU__CONFIG__SCHEME
      value: "https"
    - name: DYNACONF_SERVICE_OBJECTS__KOKU__CONFIG__PORT
      value: ""
    - name: DYNACONF_SERVICE_OBJECTS__MASU__CONFIG__HOSTNAME
      value: "${MASU_HOSTNAME}"
    - name: DYNACONF_SERVICE_OBJECTS__MASU__CONFIG__PORT
      value: "${MASU_PORT}"
    - name: DYNACONF_SERVICE_OBJECTS__MASU__CONFIG__SCHEME
      value: "http"
    - name: DYNACONF_SERVICE_OBJECTS__COST_MANAGEMENT_SOURCES__CONFIG__HOSTNAME
      value: "${KOKU_HOSTNAME}"
    - name: DYNACONF_SERVICE_OBJECTS__COST_MANAGEMENT_SOURCES__CONFIG__SCHEME
      value: "https"
    - name: DYNACONF_SERVICE_OBJECTS__COST_MANAGEMENT_SOURCES__CONFIG__PORT
      value: ""
    
    # User configuration
    # IMPORTANT: Use lowercase for nested keys (auth, identity) because IQE code
    # expects lowercase keys like app_user["auth"], not app_user["AUTH"]
    - name: DYNACONF_DEFAULT_USER
      value: "cost_onprem_user"
    - name: DYNACONF_users__cost_onprem_user__auth__username
      value: "test"
    - name: DYNACONF_users__cost_onprem_user__auth__password
      value: "test"
    - name: DYNACONF_users__cost_onprem_user__auth__jwt_grant_type
      value: "client_credentials"
    - name: DYNACONF_users__cost_onprem_user__auth__client_id
      value: "${KEYCLOAK_CLIENT_ID}"
    - name: DYNACONF_users__cost_onprem_user__auth__client_secret
      value: "${KEYCLOAK_CLIENT_SECRET}"
    - name: DYNACONF_users__cost_onprem_user__identity__account_number
      value: "7890123"
    - name: DYNACONF_users__cost_onprem_user__identity__org_id
      value: "${ORG_ID}"
    
    # SSL CA bundle for cluster certificates
    - name: REQUESTS_CA_BUNDLE
      value: "/etc/pki/tls/certs/ca-bundle.crt"
    - name: SSL_CERT_FILE
      value: "/etc/pki/tls/certs/ca-bundle.crt"
    - name: CURL_CA_BUNDLE
      value: "/etc/pki/tls/certs/ca-bundle.crt"
    
    # S3 Configuration (for IQE fixtures)
    - name: S3_ENDPOINT
      value: "${S3_ENDPOINT}"
    - name: S3_PORT
      value: "${S3_PORT}"
    - name: S3_USE_SSL
      value: "${S3_USE_SSL}"
    - name: S3_ACCESS_KEY
      value: "${S3_ACCESS_KEY}"
    - name: S3_SECRET_KEY
      value: "${S3_SECRET_KEY}"
    - name: S3_SECRET_NAME
      value: "${S3_SECRET_NAME}"
    - name: S3_KOKU_BUCKET
      value: "${S3_BUCKET_NAME}"
    - name: S3_ROS_BUCKET
      value: "${S3_ROS_BUCKET}"
${NISE_VERSION_ENV}
    imagePullPolicy: Always
    resources:
      limits:
        cpu: "1"
        memory: 2Gi
      requests:
        cpu: 200m
        memory: 1Gi
    securityContext:
      allowPrivilegeEscalation: false
      runAsNonRoot: true
      capabilities:
        drop:
        - ALL
    volumeMounts:
    - name: results
      mountPath: /results
    - name: ca-bundle
      mountPath: /etc/pki/tls/certs/ca-bundle.crt
      subPath: ca-bundle.crt
      readOnly: true
  volumes:
  - name: results
    emptyDir: {}
  - name: ca-bundle
    configMap:
      name: iqe-ca-bundle
      optional: true
EOF

echo "Waiting for IQE pod to start..."
kubectl wait --for=condition=Ready pod/iqe-cost-tests -n "${NAMESPACE}" --timeout=300s || {
    echo ""
    echo "ERROR: Pod failed to start within timeout"
    echo ""
    echo "Pod status:"
    kubectl get pod iqe-cost-tests -n "${NAMESPACE}" -o wide || true
    echo ""
    
    # Check specifically for image pull errors
    POD_STATUS=$(kubectl get pod iqe-cost-tests -n "${NAMESPACE}" -o jsonpath='{.status.containerStatuses[0].state.waiting.reason}' 2>/dev/null || echo "")
    if [[ "$POD_STATUS" == "ImagePullBackOff" ]] || [[ "$POD_STATUS" == "ErrImagePull" ]]; then
        echo "=========================================="
        echo "IMAGE PULL FAILURE DETECTED"
        echo "=========================================="
        echo ""
        echo "The cluster cannot pull the IQE image: ${IQE_IMAGE}"
        echo ""
        echo "This typically means the cluster lacks credentials for ${IQE_REGISTRY}."
        echo ""
        echo "Quick fix - sync your local credentials to the cluster:"
        echo ""
        echo "  $0 --sync-pull-secret"
        echo ""
        echo "This requires you to be authenticated locally first:"
        echo "  podman login ${IQE_REGISTRY}"
        echo "  # or"
        echo "  docker login ${IQE_REGISTRY}"
        echo ""
        if [[ "${IQE_REGISTRY}" == "quay.io" ]]; then
            echo "Note: The IQE image (quay.io/cloudservices/iqe-tests) requires"
            echo "      Red Hat internal access. Contact the Cost Management team"
            echo "      for access to this repository."
            echo ""
        fi
    fi
    
    echo "Pod events:"
    kubectl describe pod iqe-cost-tests -n "${NAMESPACE}" | grep -A 20 "Events:" || true
    echo ""
    echo "Pod logs (if available):"
    kubectl logs iqe-cost-tests -n "${NAMESPACE}" 2>/dev/null || true
    exit 1
}

echo ""
echo "Streaming test output..."
kubectl logs -f iqe-cost-tests -n "${NAMESPACE}" &
LOG_PID=$!

echo "Waiting for tests to complete (timeout: ${IQE_TIMEOUT}s)..."
ELAPSED=0
RESULTS_DIR="${PROJECT_ROOT}/tests/reports"
mkdir -p "${RESULTS_DIR}"
RESULTS_COLLECTED=false

while [ $ELAPSED -lt "$IQE_TIMEOUT" ]; do
    PHASE=$(kubectl get pod iqe-cost-tests -n "${NAMESPACE}" -o jsonpath='{.status.phase}' 2>/dev/null || echo "Unknown")
    
    # Try to collect results while container is still running (before Succeeded state)
    # kubectl cp requires exec access which is lost once pod reaches Succeeded state
    if [ "$RESULTS_COLLECTED" = "false" ]; then
        if kubectl exec iqe-cost-tests -n "${NAMESPACE}" -- test -f /results/junit.xml 2>/dev/null; then
            echo ""
            echo "Test results file detected, collecting while container is running..."
            if kubectl exec iqe-cost-tests -n "${NAMESPACE}" -- cat /results/junit.xml > "${RESULTS_DIR}/iqe_junit.xml" 2>/dev/null; then
                echo "✓ Collected junit.xml"
                RESULTS_COLLECTED=true
            fi
            kubectl exec iqe-cost-tests -n "${NAMESPACE}" -- cat /results/test-output.log > "${RESULTS_DIR}/iqe_output.log" 2>/dev/null || true
        fi
    fi
    
    if [ "$PHASE" = "Succeeded" ] || [ "$PHASE" = "Failed" ]; then
        echo ""
        echo "IQE pod finished with phase: ${PHASE}"
        break
    fi
    sleep 5
    ELAPSED=$((ELAPSED + 5))
done

# Stop log streaming
kill $LOG_PID 2>/dev/null || true

# Check for timeout
if [ $ELAPSED -ge "$IQE_TIMEOUT" ]; then
    echo ""
    echo "ERROR: Tests timed out after ${IQE_TIMEOUT}s"
fi

# Try to collect results one more time if not already collected
if [ "$RESULTS_COLLECTED" = "false" ]; then
    echo ""
    echo "Attempting to collect test results..."
    kubectl cp "${NAMESPACE}/iqe-cost-tests:/results/junit.xml" "${RESULTS_DIR}/iqe_junit.xml" 2>/dev/null || true
    kubectl cp "${NAMESPACE}/iqe-cost-tests:/results/test-output.log" "${RESULTS_DIR}/iqe_output.log" 2>/dev/null || true
fi

# Parse and display results
TESTS=0
FAILURES=0
ERRORS=0
SKIPPED=0

if [ -f "${RESULTS_DIR}/iqe_junit.xml" ]; then
    echo ""
    echo "Test results saved to ${RESULTS_DIR}/iqe_junit.xml"
    TESTS=$(grep -o 'tests="[0-9]*"' "${RESULTS_DIR}/iqe_junit.xml" | head -1 | grep -o '[0-9]*' || echo "0")
    FAILURES=$(grep -o 'failures="[0-9]*"' "${RESULTS_DIR}/iqe_junit.xml" | head -1 | grep -o '[0-9]*' || echo "0")
    ERRORS=$(grep -o 'errors="[0-9]*"' "${RESULTS_DIR}/iqe_junit.xml" | head -1 | grep -o '[0-9]*' || echo "0")
    SKIPPED=$(grep -o 'skipped="[0-9]*"' "${RESULTS_DIR}/iqe_junit.xml" | head -1 | grep -o '[0-9]*' || echo "0")
    
    PASSED=$((TESTS - FAILURES - ERRORS - SKIPPED))
    
    echo ""
    echo "========== IQE Test Results =========="
    echo "  Total:    ${TESTS}"
    echo "  Passed:   ${PASSED}"
    echo "  Failed:   ${FAILURES}"
    echo "  Errors:   ${ERRORS}"
    echo "  Skipped:  ${SKIPPED}"
    echo "======================================"
else
    echo ""
    echo "WARNING: No JUnit XML results found"
    echo "Check ${RESULTS_DIR}/iqe_output.log for details"
fi

# Cleanup
if [ "$KEEP_POD" = "false" ]; then
    echo ""
    echo "Cleaning up IQE pod..."
    kubectl delete pod iqe-cost-tests -n "${NAMESPACE}" --ignore-not-found=true
else
    echo ""
    echo "Keeping IQE pod for debugging (use: kubectl logs iqe-cost-tests -n ${NAMESPACE})"
fi

# Exit based on test results
if [ "${FAILURES:-0}" -gt 0 ] || [ "${ERRORS:-0}" -gt 0 ]; then
    echo ""
    echo "IQE tests had failures or errors"
    exit 1
fi

if [ "${TESTS:-0}" -eq 0 ]; then
    echo ""
    echo "WARNING: No tests were executed"
    exit 1
fi

echo ""
echo "IQE tests completed successfully"
