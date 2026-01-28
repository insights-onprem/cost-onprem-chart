#!/bin/bash
set -eo pipefail

#=============================================================================#
# Post-Test Health Check Script
#=============================================================================#
# Validates pod health and captures diagnostic info after E2E test execution
#
# Usage:
#   ./check-post-test.sh [namespace]
#
# Arguments:
#   namespace - Optional. If not provided, uses current kubectl context namespace
#
# Exit Codes:
#   0 - All checks passed
#   1 - One or more checks failed
#=============================================================================#

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
NAMESPACE="${1:-$(kubectl config view --minify --output 'jsonpath={..namespace}')}"
RELEASE_NAME="${RELEASE_NAME:-cost-onprem}"
LOG_DIR="${LOG_DIR:-/tmp/post-test-logs}"
FAILED_CHECKS=0

#=============================================================================#
# Helper Functions
#=============================================================================#

log_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

log_success() {
    echo -e "${GREEN}✓${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

log_error() {
    echo -e "${RED}✗${NC} $1"
    ((FAILED_CHECKS++))
}

print_section() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

#=============================================================================#
# Validation Functions
#=============================================================================#

check_pod_status() {
    print_section "🔍 Checking Pod Status After Tests"
    
    PODS=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" --no-headers 2>/dev/null || true)
    
    if [ -z "$PODS" ]; then
        log_error "No pods found for release '$RELEASE_NAME'"
        return 1
    fi
    
    local total_pods=0
    local healthy_pods=0
    local unhealthy_pods=()
    
    while IFS= read -r line; do
        ((total_pods++))
        POD_NAME=$(echo "$line" | awk '{print $1}')
        POD_STATUS=$(echo "$line" | awk '{print $3}')
        RESTARTS=$(echo "$line" | awk '{print $4}')
        
        case "$POD_STATUS" in
            Running|Completed)
                ((healthy_pods++))
                ;;
            CrashLoopBackOff|Error|Failed|ImagePullBackOff|ErrImagePull)
                unhealthy_pods+=("$POD_NAME ($POD_STATUS, $RESTARTS restarts)")
                log_error "Pod '$POD_NAME' is $POD_STATUS"
                ;;
            *)
                log_warning "Pod '$POD_NAME' has status: $POD_STATUS"
                ;;
        esac
    done <<< "$PODS"
    
    echo ""
    log_info "Total pods: $total_pods"
    log_info "Healthy: $healthy_pods"
    
    if [ ${#unhealthy_pods[@]} -gt 0 ]; then
        log_error "Unhealthy pods: ${#unhealthy_pods[@]}"
        for pod in "${unhealthy_pods[@]}"; do
            echo "  - $pod"
        done
        return 1
    else
        log_success "All pods are healthy"
    fi
}

check_oom_kills() {
    print_section "💥 Checking for OOM (Out of Memory) Kills"
    
    # Check for OOMKilled events
    OOM_EVENTS=$(kubectl get events -n "$NAMESPACE" \
        --field-selector reason=OOMKilled \
        --sort-by='.lastTimestamp' \
        --no-headers 2>/dev/null || true)
    
    if [ -z "$OOM_EVENTS" ]; then
        log_success "No OOM kills detected"
        return 0
    fi
    
    local oom_count=0
    
    while IFS= read -r line; do
        ((oom_count++))
        POD_NAME=$(echo "$line" | awk '{print $4}')
        log_error "OOM Kill detected: $POD_NAME"
    done <<< "$OOM_EVENTS"
    
    log_error "Found $oom_count OOM kill(s) during test execution"
    log_info "Consider increasing memory limits for affected pods"
}

check_resource_constraints() {
    print_section "⚡ Checking for Resource Constraint Events"
    
    # Check for CPU/Memory throttling or other resource issues
    RESOURCE_EVENTS=$(kubectl get events -n "$NAMESPACE" \
        --field-selector reason=FailedScheduling \
        --sort-by='.lastTimestamp' \
        --no-headers 2>/dev/null | tail -10 || true)
    
    if [ -z "$RESOURCE_EVENTS" ]; then
        log_success "No resource constraint events found"
        return 0
    fi
    
    local constraint_count=0
    
    while IFS= read -r line; do
        ((constraint_count++))
        MESSAGE=$(echo "$line" | awk '{for(i=5;i<=NF;i++) printf "%s ", $i}')
        log_warning "Resource constraint: $MESSAGE"
    done <<< "$RESOURCE_EVENTS"
    
    if [ $constraint_count -gt 0 ]; then
        log_warning "Found $constraint_count resource constraint event(s)"
    fi
}

capture_failing_pod_logs() {
    print_section "📝 Capturing Logs from Failing Pods"
    
    mkdir -p "$LOG_DIR"
    log_info "Log directory: $LOG_DIR"
    
    # Find pods that are not Running or Completed
    FAILING_PODS=$(kubectl get pods -n "$NAMESPACE" \
        -l "app.kubernetes.io/instance=${RELEASE_NAME}" \
        --no-headers 2>/dev/null | \
        grep -v -E 'Running|Completed' || true)
    
    if [ -z "$FAILING_PODS" ]; then
        log_success "No failing pods to capture logs from"
        return 0
    fi
    
    local captured=0
    
    while IFS= read -r line; do
        POD_NAME=$(echo "$line" | awk '{print $1}')
        POD_STATUS=$(echo "$line" | awk '{print $3}')
        
        log_info "Capturing logs for $POD_NAME ($POD_STATUS)..."
        
        # Current logs
        kubectl logs "$POD_NAME" -n "$NAMESPACE" --all-containers=true \
            > "$LOG_DIR/${POD_NAME}.log" 2>&1 || true
        
        # Previous logs if pod restarted
        kubectl logs "$POD_NAME" -n "$NAMESPACE" --all-containers=true --previous \
            > "$LOG_DIR/${POD_NAME}.previous.log" 2>&1 || true
        
        # Pod description
        kubectl describe pod "$POD_NAME" -n "$NAMESPACE" \
            > "$LOG_DIR/${POD_NAME}.describe.txt" 2>&1 || true
        
        ((captured++))
    done <<< "$FAILING_PODS"
    
    if [ $captured -gt 0 ]; then
        log_success "Captured logs for $captured pod(s) in $LOG_DIR"
    fi
}

check_test_artifacts() {
    print_section "📊 Checking Test Artifacts"
    
    # Check if test reports exist
    TEST_REPORT_PATHS=(
        "./test-results"
        "./pytest_results"
        "/tmp/test-results"
    )
    
    local found_reports=0
    
    for path in "${TEST_REPORT_PATHS[@]}"; do
        if [ -d "$path" ] && [ "$(ls -A "$path" 2>/dev/null)" ]; then
            log_success "Test artifacts found: $path"
            ((found_reports++))
        fi
    done
    
    if [ $found_reports -eq 0 ]; then
        log_warning "No test artifacts found in common locations"
    fi
}

generate_health_report() {
    print_section "📋 Generating Health Report"
    
    REPORT_FILE="$LOG_DIR/health-report.txt"
    
    {
        echo "======================================================================"
        echo "Post-Test Health Report"
        echo "======================================================================"
        echo "Generated: $(date)"
        echo "Namespace: $NAMESPACE"
        echo "Release: $RELEASE_NAME"
        echo ""
        echo "Pod Status:"
        kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" || true
        echo ""
        echo "Recent Events:"
        kubectl get events -n "$NAMESPACE" --sort-by='.lastTimestamp' | tail -30 || true
        echo ""
        echo "Resource Usage (if metrics-server available):"
        kubectl top pods -n "$NAMESPACE" 2>/dev/null || echo "Metrics not available"
        echo ""
        echo "======================================================================"
    } > "$REPORT_FILE"
    
    log_success "Health report saved to: $REPORT_FILE"
}

print_summary() {
    print_section "📊 Post-Test Health Check Summary"
    
    if [ $FAILED_CHECKS -eq 0 ]; then
        echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${GREEN}✓ All post-test health checks passed!${NC}"
        echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""
        echo "The system is healthy after test execution."
        return 0
    else
        echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${RED}✗ Post-test health check found $FAILED_CHECKS issue(s)${NC}"
        echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""
        echo "Diagnostic logs have been captured to: $LOG_DIR"
        echo ""
        echo "To investigate further:"
        echo "  1. Review logs in: $LOG_DIR"
        echo "  2. Check pod logs: kubectl logs -n $NAMESPACE <pod-name>"
        echo "  3. Describe failing pods: kubectl describe pod -n $NAMESPACE <pod-name>"
        echo "  4. Check events: kubectl get events -n $NAMESPACE --sort-by='.lastTimestamp'"
        return 1
    fi
}

#=============================================================================#
# Main Execution
#=============================================================================#

main() {
    echo ""
    echo -e "${BLUE}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║       Post-Test Health Check - Cost Management              ║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    log_info "Namespace: $NAMESPACE"
    log_info "Release: $RELEASE_NAME"
    log_info "Log Directory: $LOG_DIR"
    echo ""
    
    # Run all checks
    check_pod_status
    check_oom_kills
    check_resource_constraints
    capture_failing_pod_logs
    check_test_artifacts
    generate_health_report
    
    # Print summary
    echo ""
    print_summary
    
    exit $FAILED_CHECKS
}

# Run main function
main "$@"
