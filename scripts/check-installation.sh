#!/bin/bash
set -eo pipefail

#=============================================================================#
# Post-Installation Health Check Script
#=============================================================================#
# Validates pod health and resource status after Helm chart installation
#
# Usage:
#   ./check-installation.sh [namespace]
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
CHECK_TIMEOUT=300  # 5 minutes default timeout
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

check_namespace_exists() {
    print_section "📦 Checking Namespace"
    
    if ! kubectl get namespace "$NAMESPACE" &>/dev/null; then
        log_error "Namespace '$NAMESPACE' does not exist"
        return 1
    fi
    
    log_success "Namespace '$NAMESPACE' exists"
}

check_helm_release() {
    print_section "🎯 Checking Helm Release"
    
    if ! helm status "$RELEASE_NAME" -n "$NAMESPACE" &>/dev/null; then
        log_error "Helm release '$RELEASE_NAME' not found in namespace '$NAMESPACE'"
        return 1
    fi
    
    RELEASE_STATUS=$(helm status "$RELEASE_NAME" -n "$NAMESPACE" -o json | jq -r '.info.status')
    
    if [ "$RELEASE_STATUS" != "deployed" ]; then
        log_error "Helm release status is '$RELEASE_STATUS' (expected: deployed)"
        return 1
    fi
    
    log_success "Helm release '$RELEASE_NAME' is deployed"
}

check_pod_status() {
    print_section "🔍 Checking Pod Status"
    
    # Get all pods for the release
    PODS=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" --no-headers 2>/dev/null || true)
    
    if [ -z "$PODS" ]; then
        log_error "No pods found for release '$RELEASE_NAME'"
        return 1
    fi
    
    local total_pods=0
    local running_pods=0
    local pending_pods=0
    local failed_pods=0
    local crashloop_pods=0
    
    while IFS= read -r line; do
        ((total_pods++))
        POD_NAME=$(echo "$line" | awk '{print $1}')
        POD_STATUS=$(echo "$line" | awk '{print $3}')
        
        case "$POD_STATUS" in
            Running|Completed)
                ((running_pods++))
                ;;
            Pending|ContainerCreating|PodInitializing)
                ((pending_pods++))
                log_warning "Pod '$POD_NAME' is $POD_STATUS"
                ;;
            CrashLoopBackOff)
                ((crashloop_pods++))
                log_error "Pod '$POD_NAME' is in CrashLoopBackOff"
                ;;
            Error|Failed|ImagePullBackOff|ErrImagePull)
                ((failed_pods++))
                log_error "Pod '$POD_NAME' is $POD_STATUS"
                ;;
            *)
                log_warning "Pod '$POD_NAME' has unknown status: $POD_STATUS"
                ;;
        esac
    done <<< "$PODS"
    
    echo ""
    log_info "Total pods: $total_pods"
    log_info "Running/Completed: $running_pods"
    [ $pending_pods -gt 0 ] && log_warning "Pending: $pending_pods"
    [ $failed_pods -gt 0 ] && log_error "Failed: $failed_pods"
    [ $crashloop_pods -gt 0 ] && log_error "CrashLoopBackOff: $crashloop_pods"
    
    if [ $failed_pods -gt 0 ] || [ $crashloop_pods -gt 0 ]; then
        return 1
    fi
}

check_pod_restarts() {
    print_section "🔄 Checking Pod Restart Counts"
    
    HIGH_RESTART_THRESHOLD=5
    HIGH_RESTART_PODS=()
    
    while IFS= read -r line; do
        POD_NAME=$(echo "$line" | awk '{print $1}')
        RESTARTS=$(echo "$line" | awk '{print $4}')
        
        if [ "$RESTARTS" -ge "$HIGH_RESTART_THRESHOLD" ]; then
            HIGH_RESTART_PODS+=("$POD_NAME ($RESTARTS restarts)")
            log_warning "Pod '$POD_NAME' has restarted $RESTARTS times"
        fi
    done < <(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" --no-headers)
    
    if [ ${#HIGH_RESTART_PODS[@]} -eq 0 ]; then
        log_success "No pods with excessive restarts (threshold: $HIGH_RESTART_THRESHOLD)"
    else
        log_warning "Found ${#HIGH_RESTART_PODS[@]} pod(s) with high restart counts"
    fi
}

check_pod_events() {
    print_section "📋 Checking Recent Pod Events"
    
    # Get events from last 10 minutes
    EVENTS=$(kubectl get events -n "$NAMESPACE" \
        --field-selector involvedObject.kind=Pod \
        --sort-by='.lastTimestamp' \
        --no-headers 2>/dev/null | tail -20 || true)
    
    if [ -z "$EVENTS" ]; then
        log_info "No recent pod events found"
        return 0
    fi
    
    local error_events=0
    local warning_events=0
    
    while IFS= read -r line; do
        EVENT_TYPE=$(echo "$line" | awk '{print $2}')
        EVENT_REASON=$(echo "$line" | awk '{print $3}')
        
        case "$EVENT_TYPE" in
            Warning)
                ((warning_events++))
                if [[ "$EVENT_REASON" =~ (Failed|Error|BackOff|Unhealthy) ]]; then
                    log_warning "Event: $EVENT_REASON"
                fi
                ;;
            Error)
                ((error_events++))
                log_error "Event: $EVENT_REASON"
                ;;
        esac
    done <<< "$EVENTS"
    
    if [ $error_events -eq 0 ] && [ $warning_events -eq 0 ]; then
        log_success "No error or warning events in recent history"
    else
        [ $warning_events -gt 0 ] && log_warning "Found $warning_events warning event(s)"
        [ $error_events -gt 0 ] && log_error "Found $error_events error event(s)"
    fi
}

check_pvc_status() {
    print_section "💾 Checking Persistent Volume Claims"
    
    PVCS=$(kubectl get pvc -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" --no-headers 2>/dev/null || true)
    
    if [ -z "$PVCS" ]; then
        log_info "No PVCs found"
        return 0
    fi
    
    local unbound_pvcs=0
    
    while IFS= read -r line; do
        PVC_NAME=$(echo "$line" | awk '{print $1}')
        PVC_STATUS=$(echo "$line" | awk '{print $2}')
        
        if [ "$PVC_STATUS" != "Bound" ]; then
            ((unbound_pvcs++))
            log_error "PVC '$PVC_NAME' is $PVC_STATUS (expected: Bound)"
        fi
    done <<< "$PVCS"
    
    if [ $unbound_pvcs -eq 0 ]; then
        log_success "All PVCs are bound"
    fi
}

check_service_endpoints() {
    print_section "🌐 Checking Service Endpoints"
    
    SERVICES=$(kubectl get svc -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" --no-headers 2>/dev/null || true)
    
    if [ -z "$SERVICES" ]; then
        log_warning "No services found"
        return 0
    fi
    
    local services_without_endpoints=0
    
    while IFS= read -r line; do
        SVC_NAME=$(echo "$line" | awk '{print $1}')
        SVC_TYPE=$(echo "$line" | awk '{print $2}')
        
        # Skip headless services
        CLUSTER_IP=$(echo "$line" | awk '{print $3}')
        if [ "$CLUSTER_IP" = "None" ]; then
            continue
        fi
        
        # Check if service has endpoints
        ENDPOINTS=$(kubectl get endpoints "$SVC_NAME" -n "$NAMESPACE" -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || true)
        
        if [ -z "$ENDPOINTS" ]; then
            ((services_without_endpoints++))
            log_error "Service '$SVC_NAME' has no endpoints"
        fi
    done <<< "$SERVICES"
    
    if [ $services_without_endpoints -eq 0 ]; then
        log_success "All services have endpoints"
    fi
}

print_summary() {
    print_section "📊 Health Check Summary"
    
    if [ $FAILED_CHECKS -eq 0 ]; then
        echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${GREEN}✓ All health checks passed!${NC}"
        echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""
        echo "The installation appears to be healthy."
        return 0
    else
        echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${RED}✗ Health check failed with $FAILED_CHECKS error(s)${NC}"
        echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""
        echo "Please review the errors above and check pod logs for details:"
        echo "  kubectl logs -n $NAMESPACE <pod-name>"
        echo ""
        echo "To view all pod status:"
        echo "  kubectl get pods -n $NAMESPACE -l app.kubernetes.io/instance=${RELEASE_NAME}"
        return 1
    fi
}

#=============================================================================#
# Main Execution
#=============================================================================#

main() {
    echo ""
    echo -e "${BLUE}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║        Cost Management On-Premise - Health Check            ║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    log_info "Namespace: $NAMESPACE"
    log_info "Release: $RELEASE_NAME"
    echo ""
    
    # Run all checks
    check_namespace_exists
    check_helm_release
    check_pod_status
    check_pod_restarts
    check_pod_events
    check_pvc_status
    check_service_endpoints
    
    # Print summary
    echo ""
    print_summary
    
    exit $FAILED_CHECKS
}

# Run main function
main "$@"
