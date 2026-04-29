#!/bin/bash
# run-iqe-loop.sh - Run IQE tests in a loop with proper cleanup between iterations. This has no CI use; it is only for local testing and putting tests through their paces.
#
# Usage:
#   ./scripts/qe/run-iqe-loop.sh [OPTIONS]
#
# Options:
#   --iterations N      Number of iterations (default: 5)
#   --filter EXPR       Pytest filter expression
#   --cleanup-only      Only run cleanup, don't run tests
#   --no-cleanup        Skip cleanup between iterations
#   --verbose           Show full test output
#   --help              Show this help message
#
# Examples:
#   # Run delta tests 10 times
#   ./scripts/qe/run-iqe-loop.sh --iterations 10 --filter "test_api_ocp_compute_deltas_percentages"
#
#   # Clean up stale test sources
#   ./scripts/qe/run-iqe-loop.sh --cleanup-only
#
#   # Run smoke tests 3 times with verbose output
#   ./scripts/qe/run-iqe-loop.sh --iterations 3 --filter "smoke" --verbose

set -euo pipefail

# Defaults
ITERATIONS=5
FILTER=""
CLEANUP_ONLY=false
NO_CLEANUP=false
VERBOSE=false
NAMESPACE="${NAMESPACE:-cost-onprem}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

usage() {
    grep '^#' "$0" | grep -v '#!/bin/bash' | sed 's/^# //' | sed 's/^#//'
    exit 0
}

log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[PASS]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[FAIL]${NC} $*"; }

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --iterations)
            ITERATIONS="$2"
            shift 2
            ;;
        --filter)
            FILTER="$2"
            shift 2
            ;;
        --cleanup-only)
            CLEANUP_ONLY=true
            shift
            ;;
        --no-cleanup)
            NO_CLEANUP=true
            shift
            ;;
        --verbose)
            VERBOSE=true
            shift
            ;;
        --help|-h)
            usage
            ;;
        *)
            echo "Unknown option: $1"
            usage
            ;;
    esac
done

# Get Keycloak token for API calls
get_token() {
    local keycloak_secret
    keycloak_secret=$(kubectl get secret -n keycloak keycloak-client-secret-cost-management-operator \
        -o jsonpath='{.data.CLIENT_SECRET}' 2>/dev/null | base64 -d)
    
    if [[ -z "$keycloak_secret" ]]; then
        log_error "Could not get Keycloak secret"
        return 1
    fi
    
    local keycloak_host
    keycloak_host=$(kubectl get route -n keycloak keycloak -o jsonpath='{.spec.host}' 2>/dev/null)
    
    curl -sk -X POST "https://${keycloak_host}/realms/kubernetes/protocol/openid-connect/token" \
        -d "grant_type=client_credentials" \
        -d "client_id=cost-management-operator" \
        -d "client_secret=$keycloak_secret" | jq -r '.access_token'
}

# Get API gateway URL
get_gateway_url() {
    local route
    # Try common route names
    route=$(kubectl get route -n "$NAMESPACE" cost-onprem-api -o jsonpath='{.spec.host}' 2>/dev/null) || \
    route=$(kubectl get route -n "$NAMESPACE" cost-onprem-gateway -o jsonpath='{.spec.host}' 2>/dev/null) || \
    route=$(kubectl get route -n "$NAMESPACE" -l app.kubernetes.io/component=ingress -o jsonpath='{.items[0].spec.host}' 2>/dev/null)
    
    if [[ -z "$route" ]]; then
        log_error "Could not find API gateway route"
        return 1
    fi
    echo "https://${route}"
}

# List all test sources (sources with names starting with test_cost_)
list_test_sources() {
    local token gateway_url
    token=$(get_token)
    gateway_url=$(get_gateway_url)
    
    curl -sk -H "Authorization: Bearer $token" \
        "${gateway_url}/api/cost-management/v1/sources/" | \
        jq -r '.data[] | select(.name | startswith("test_cost_")) | "\(.uuid)\t\(.name)"'
}

# Delete a source by UUID
delete_source() {
    local uuid="$1"
    local token gateway_url
    token=$(get_token)
    gateway_url=$(get_gateway_url)
    
    curl -sk -X DELETE -H "Authorization: Bearer $token" \
        "${gateway_url}/api/cost-management/v1/sources/${uuid}/" >/dev/null
}

# Clean up all test sources
cleanup_test_sources() {
    log_info "Checking for stale test sources..."
    
    local sources
    sources=$(list_test_sources)
    
    if [[ -z "$sources" ]]; then
        log_info "No test sources found"
        return 0
    fi
    
    local count=0
    while IFS=$'\t' read -r uuid name; do
        if [[ -n "$uuid" ]]; then
            log_warn "Deleting stale source: $name ($uuid)"
            delete_source "$uuid"
            ((count++))
        fi
    done <<< "$sources"
    
    if [[ $count -gt 0 ]]; then
        log_info "Deleted $count stale test source(s)"
        # Wait a moment for deletion to propagate
        sleep 2
    fi
}

# Run a single test iteration
run_test_iteration() {
    local iteration="$1"
    local filter_args=""
    
    if [[ -n "$FILTER" ]]; then
        filter_args="--filter \"$FILTER\""
    fi
    
    local start_time end_time duration
    start_time=$(date +%s)
    
    local output
    local exit_code=0
    
    if [[ "$VERBOSE" == "true" ]]; then
        NAMESPACE="$NAMESPACE" ./scripts/run-iqe-tests-local.sh $filter_args || exit_code=$?
    else
        output=$(NAMESPACE="$NAMESPACE" ./scripts/run-iqe-tests-local.sh $filter_args 2>&1) || exit_code=$?
    fi
    
    end_time=$(date +%s)
    duration=$((end_time - start_time))
    
    # Extract results from output
    local passed failed errors xfailed
    if [[ "$VERBOSE" != "true" ]]; then
        passed=$(echo "$output" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' || echo "0")
        failed=$(echo "$output" | grep -oE '[0-9]+ failed' | grep -oE '[0-9]+' || echo "0")
        errors=$(echo "$output" | grep -oE '[0-9]+ error' | grep -oE '[0-9]+' || echo "0")
        xfailed=$(echo "$output" | grep -oE '[0-9]+ xfailed' | grep -oE '[0-9]+' || echo "0")
    fi
    
    # Return results
    echo "${exit_code}|${duration}|${passed:-?}|${failed:-?}|${errors:-?}|${xfailed:-?}"
}

# Main execution
main() {
    log_info "IQE Test Loop Runner"
    log_info "Namespace: $NAMESPACE"
    log_info "Iterations: $ITERATIONS"
    [[ -n "$FILTER" ]] && log_info "Filter: $FILTER"
    echo ""
    
    # Cleanup only mode
    if [[ "$CLEANUP_ONLY" == "true" ]]; then
        cleanup_test_sources
        exit 0
    fi
    
    # Initial cleanup
    if [[ "$NO_CLEANUP" != "true" ]]; then
        cleanup_test_sources
    fi
    
    # Track results
    declare -a results
    local total_passed=0
    local total_failed=0
    local total_errors=0
    
    # Run iterations
    for i in $(seq 1 "$ITERATIONS"); do
        echo ""
        log_info "========== Iteration $i/$ITERATIONS =========="
        
        # Cleanup before each iteration (except first, already done)
        if [[ "$NO_CLEANUP" != "true" && $i -gt 1 ]]; then
            cleanup_test_sources
        fi
        
        # Run tests
        local result
        result=$(run_test_iteration "$i")
        results+=("$result")
        
        # Parse result
        IFS='|' read -r exit_code duration passed failed errors xfailed <<< "$result"
        
        # Display result
        if [[ "$exit_code" -eq 0 ]]; then
            log_success "Iteration $i: PASSED (${duration}s) - passed=$passed failed=$failed errors=$errors xfailed=$xfailed"
            ((total_passed++))
        else
            log_error "Iteration $i: FAILED (${duration}s) - passed=$passed failed=$failed errors=$errors xfailed=$xfailed"
            ((total_failed++))
        fi
    done
    
    # Summary
    echo ""
    echo "=========================================="
    log_info "SUMMARY"
    echo "=========================================="
    echo "Total iterations: $ITERATIONS"
    log_success "Passed: $total_passed"
    [[ $total_failed -gt 0 ]] && log_error "Failed: $total_failed"
    echo ""
    
    # Detailed results table
    echo "Iteration | Status | Duration | Passed | Failed | Errors | XFailed"
    echo "----------|--------|----------|--------|--------|--------|--------"
    for i in "${!results[@]}"; do
        IFS='|' read -r exit_code duration passed failed errors xfailed <<< "${results[$i]}"
        local status="PASS"
        [[ "$exit_code" -ne 0 ]] && status="FAIL"
        printf "    %d     | %s  |   %3ds   |   %s   |   %s   |   %s   |   %s\n" \
            $((i+1)) "$status" "$duration" "$passed" "$failed" "$errors" "$xfailed"
    done
    
    # Final cleanup
    if [[ "$NO_CLEANUP" != "true" ]]; then
        echo ""
        cleanup_test_sources
    fi
    
    # Exit with failure if any iteration failed
    [[ $total_failed -gt 0 ]] && exit 1
    exit 0
}

main
