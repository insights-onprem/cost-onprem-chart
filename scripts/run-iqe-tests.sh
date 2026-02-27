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
IQE_TIMEOUT="${IQE_TIMEOUT:-1800}"
IQE_IMAGE="${IQE_IMAGE:-quay.io/cloudservices/iqe-tests:cost-management}"
KEEP_POD=false
KEYCLOAK_SECRET_NS="${KEYCLOAK_SECRET_NS:-keycloak}"
KEYCLOAK_SECRET_NAME="${KEYCLOAK_SECRET_NAME:-cost-management-auth-secret}"

show_help() {
    cat << EOF
Run IQE cost-management tests against a deployed cost-onprem chart

Usage: $(basename "$0") [OPTIONS]

Options:
    --namespace NAME     Target namespace (default: cost-onprem)
    --marker EXPR        Pytest marker expression (default: cost_ocp_on_prem)
    --timeout SECONDS    Test timeout (default: 1800)
    --keep-pod           Don't delete the IQE pod after tests
    --help               Show this help message

Environment Variables:
    IQE_IMAGE            IQE container image
    HELM_RELEASE_NAME    Helm release name (default: cost-onprem)
    KEYCLOAK_SECRET_NS   Namespace containing Keycloak secret (default: keycloak)

Examples:
    # Run all on-prem tests
    ./scripts/run-iqe-tests.sh

    # Run specific marker with custom namespace
    ./scripts/run-iqe-tests.sh --namespace my-ns --marker "cost_ocp_on_prem and not slow"

    # Keep pod for debugging
    ./scripts/run-iqe-tests.sh --keep-pod
EOF
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --namespace) NAMESPACE="$2"; shift 2 ;;
        --marker) IQE_MARKER="$2"; shift 2 ;;
        --timeout) IQE_TIMEOUT="$2"; shift 2 ;;
        --keep-pod) KEEP_POD=true; shift ;;
        --help) show_help; exit 0 ;;
        *) echo "Unknown option: $1"; show_help; exit 1 ;;
    esac
done

echo "========== Running IQE Cost Management Tests =========="
echo "Namespace: ${NAMESPACE}"
echo "Marker: ${IQE_MARKER}"
echo "Timeout: ${IQE_TIMEOUT}s"
echo "Image: ${IQE_IMAGE}"

# Get S3 credentials from the deployed chart
S3_SECRET_NAME="${HELM_RELEASE_NAME}-storage-credentials"
echo ""
echo "Extracting configuration from cluster..."

S3_ACCESS_KEY=$(kubectl get secret "$S3_SECRET_NAME" -n "$NAMESPACE" -o jsonpath='{.data.access-key}' 2>/dev/null | base64 -d || echo "")
S3_SECRET_KEY=$(kubectl get secret "$S3_SECRET_NAME" -n "$NAMESPACE" -o jsonpath='{.data.secret-key}' 2>/dev/null | base64 -d || echo "")

# Get S3 endpoint from MASU pod
S3_ENDPOINT=$(kubectl exec -n "$NAMESPACE" deploy/${HELM_RELEASE_NAME}-koku-masu -c masu -- printenv S3_ENDPOINT 2>/dev/null || echo "")

# Get Keycloak credentials from the auth secret
KEYCLOAK_CLIENT_ID=$(kubectl get secret "$KEYCLOAK_SECRET_NAME" -n "$KEYCLOAK_SECRET_NS" -o jsonpath='{.data.client_id}' 2>/dev/null | base64 -d || echo "cost-management")
KEYCLOAK_CLIENT_SECRET=$(kubectl get secret "$KEYCLOAK_SECRET_NAME" -n "$KEYCLOAK_SECRET_NS" -o jsonpath='{.data.client_secret}' 2>/dev/null | base64 -d || echo "")

# Get Keycloak route for OAuth URL
KEYCLOAK_HOST=$(kubectl get route keycloak -n keycloak -o jsonpath='{.spec.host}' 2>/dev/null || echo "")
OAUTH_URL="https://${KEYCLOAK_HOST}/realms/kubernetes/protocol/openid-connect"

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

# Delete existing pod if present
kubectl delete pod iqe-cost-tests -n "${NAMESPACE}" --ignore-not-found=true 2>/dev/null || true

echo ""
echo "Creating IQE test pod..."

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
      echo "DYNACONF_SERVICE_OBJECTS__MASU__CONFIG__HOSTNAME: \${DYNACONF_SERVICE_OBJECTS__MASU__CONFIG__HOSTNAME}"
      echo ""
      
      echo "Running IQE tests with marker: ${IQE_MARKER}"
      iqe tests plugin cost_management \
        -m "${IQE_MARKER}" \
        -vv \
        --junitxml=/results/junit.xml \
        2>&1 | tee /results/test-output.log
      
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
    
    # DYNACONF variables (used by Jinja templates in cost_management.default.yaml)
    - name: DYNACONF_ONPREM_KOKU_HOSTNAME
      value: "${KOKU_HOSTNAME}"
    - name: DYNACONF_ONPREM_CLIENT_ID
      value: "${KEYCLOAK_CLIENT_ID}"
    - name: DYNACONF_ONPREM_CLIENT_SECRET
      value: "${KEYCLOAK_CLIENT_SECRET}"
    - name: DYNACONF_ONPREM_OAUTH_URL
      value: "${OAUTH_URL}"
    
    # Override MASU config (default uses localhost for port-forward)
    - name: DYNACONF_SERVICE_OBJECTS__MASU__CONFIG__HOSTNAME
      value: "${MASU_HOSTNAME}"
    - name: DYNACONF_SERVICE_OBJECTS__MASU__CONFIG__PORT
      value: "${MASU_PORT}"
    - name: DYNACONF_SERVICE_OBJECTS__MASU__CONFIG__SCHEME
      value: "http"
    
    # S3 Configuration
    - name: S3_ENDPOINT
      value: "${S3_ENDPOINT}"
    - name: S3_ACCESS_KEY
      value: "${S3_ACCESS_KEY}"
    - name: S3_SECRET_KEY
      value: "${S3_SECRET_KEY}"
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
  volumes:
  - name: results
    emptyDir: {}
EOF

echo "Waiting for IQE pod to start..."
kubectl wait --for=condition=Ready pod/iqe-cost-tests -n "${NAMESPACE}" --timeout=300s || {
    echo ""
    echo "ERROR: Pod failed to start within timeout"
    echo ""
    echo "Pod status:"
    kubectl get pod iqe-cost-tests -n "${NAMESPACE}" -o wide || true
    echo ""
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
while [ $ELAPSED -lt "$IQE_TIMEOUT" ]; do
    PHASE=$(kubectl get pod iqe-cost-tests -n "${NAMESPACE}" -o jsonpath='{.status.phase}' 2>/dev/null || echo "Unknown")
    if [ "$PHASE" = "Succeeded" ] || [ "$PHASE" = "Failed" ]; then
        echo ""
        echo "IQE pod finished with phase: ${PHASE}"
        break
    fi
    sleep 10
    ELAPSED=$((ELAPSED + 10))
done

# Stop log streaming
kill $LOG_PID 2>/dev/null || true

# Check for timeout
if [ $ELAPSED -ge "$IQE_TIMEOUT" ]; then
    echo ""
    echo "ERROR: Tests timed out after ${IQE_TIMEOUT}s"
fi

# Collect results
RESULTS_DIR="${PROJECT_ROOT}/tests/reports"
mkdir -p "${RESULTS_DIR}"

echo ""
echo "Collecting test results..."
kubectl cp "${NAMESPACE}/iqe-cost-tests:/results/junit.xml" "${RESULTS_DIR}/iqe_junit.xml" 2>/dev/null || true
kubectl cp "${NAMESPACE}/iqe-cost-tests:/results/test-output.log" "${RESULTS_DIR}/iqe_output.log" 2>/dev/null || true

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
