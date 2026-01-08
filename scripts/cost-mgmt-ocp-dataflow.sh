#!/bin/bash
#
# Cost Management OCP Dataflow Validation
# ========================================
# Validates the complete OpenShift Container Platform (OCP) data pipeline:
# Data Generation → Upload → Kafka → Processing → Aggregation → Validation
#
# This script uses NISE-GENERATED DATA for predictable validation.
# Benefits:
#   - Validates complete dataflow end-to-end
#   - Financial correctness verification
#   - Detects processing and aggregation issues
#   - No external dependencies (standalone)
#
# SCENARIO:
#   - Minimal OCP pod-only: 1 pod, 1 node, 24 hours (~60 seconds)
#   - Validates complete pipeline
#   - Suitable for CI/CD and quick validation
#
# See OCP_SMOKE_TEST_GUIDE.md for detailed documentation.
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
NAMESPACE="cost-onprem"
FORCE=true  # Always force for E2E validation (ensures Kafka trigger)
SKIP_TESTS=false
TIMEOUT=300

# Print usage
usage() {
    cat <<EOF
Usage: $0 [OPTIONS]

OCP Dataflow Validation for Cost Management on-prem deployment.

OPTIONS:
    --namespace NAMESPACE       Kubernetes namespace (default: cost-onprem)
    --force                    Force data regeneration
    --skip-tests               Skip validation tests (only validate data pipeline)
    --timeout SECONDS          Processing timeout (default: 300)
    --help                     Show this help message

SCENARIO:
    Minimal OCP pod-only scenario with nise-generated data (~60 seconds)
    - 1 pod, 1 node, 24 hours of usage data
    - Complete pipeline validation
    - Financial correctness verification

EXAMPLES:
    # Run validation (reuse existing data if available)
    $0

    # Force fresh data generation
    $0 --force

    # Pipeline check only (skip validation tests)
    $0 --skip-tests --force

    # See OCP_SMOKE_TEST_GUIDE.md for detailed documentation

EOF
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --namespace)
            NAMESPACE="$2"
            shift 2
            ;;
        --force)
            FORCE=true
            shift
            ;;
        --skip-tests)
            SKIP_TESTS=true
            shift
            ;;
        --timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        --help)
            usage
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            usage
            exit 1
            ;;
    esac
done

# Capture start time
START_TIME=$(date +%s)

# Print banner
echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║                                                               ║${NC}"
echo -e "${BLUE}║  OCP-Only E2E Validation Suite                                ║${NC}"
echo -e "${BLUE}║  Cost Management on-prem - OpenShift Provider Focus          ║${NC}"
echo -e "${BLUE}║                                                               ║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}Configuration:${NC}"
echo "  Namespace:  $NAMESPACE"
echo "  Provider:   OCP (OpenShift Container Platform)"
echo "  Data:       NISE-GENERATED (minimal pod-only scenario)"
echo "  Timeout:    ${TIMEOUT}s"
echo ""

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Check kubectl/oc context is set
echo -e "${BLUE}🔍 Checking Kubernetes context...${NC}"
if ! kubectl config current-context &>/dev/null; then
    echo -e "${RED}❌ No Kubernetes context is set${NC}"
    echo ""
    echo "Please set your kubectl/oc context first:"
    echo "  kubectl config use-context <context-name>"
    echo "  # or for OpenShift:"
    echo "  oc login <cluster-url>"
    echo ""
    echo "Available contexts:"
    kubectl config get-contexts
    exit 1
fi
CURRENT_CONTEXT=$(kubectl config current-context)
echo -e "${GREEN}  ✓ Context: ${CURRENT_CONTEXT}${NC}"
echo ""

# Use dedicated cost-mgmt venv (clean, no IQE dependencies)
VENV_DIR="$SCRIPT_DIR/cost-mgmt-venv"

if [ ! -d "$VENV_DIR" ]; then
    echo -e "${BLUE}🔧 Creating clean venv...${NC}"
    python3 -m venv "$VENV_DIR"
fi

echo -e "${BLUE}🔧 Activating cost-mgmt venv...${NC}"
source "$VENV_DIR/bin/activate"

# Check Python dependencies
echo -e "${BLUE}🔧 Checking Python dependencies...${NC}"
if ! python3 -c "import kubernetes, boto3, yaml" 2>/dev/null; then
    echo -e "${YELLOW}⚠️  Installing Python dependencies...${NC}"
    pip3 install -q -r "$SCRIPT_DIR/requirements-e2e.txt"
fi
echo -e "${GREEN}  ✓ Python dependencies available${NC}"
echo -e "${GREEN}  ✓ Using venv: $VENV_DIR${NC}"
echo ""

# Build Python command
cd "$SCRIPT_DIR"

# Suppress urllib3 SSL warnings (not helpful for our use case)
export PYTHONWARNINGS="ignore:Unverified HTTPS request"

CMD="python3 -u -m e2e_validator.cli"
CMD="$CMD --namespace $NAMESPACE"
CMD="$CMD --provider-type OCP"
CMD="$CMD --timeout $TIMEOUT"
CMD="$CMD --smoke-test"  # Always use minimal scenario

if [ "$FORCE" = true ]; then
    CMD="$CMD --force"
fi

if [ "$SKIP_TESTS" = true ]; then
    CMD="$CMD --skip-tests"
fi

# Run the E2E validation
# Note: Database queries use kubectl exec - no port-forward needed!
echo -e "${BLUE}Starting OCP E2E validation...${NC}"
echo ""

if eval "$CMD"; then
    # Deactivate venv
    deactivate 2>/dev/null || true

    # Calculate total time
    END_TIME=$(date +%s)
    TOTAL_SECONDS=$((END_TIME - START_TIME))
    MINUTES=$((TOTAL_SECONDS / 60))
    SECONDS=$((TOTAL_SECONDS % 60))

    echo ""
    echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║                                                               ║${NC}"
    echo -e "${GREEN}║  ✓ OCP E2E Validation PASSED                                  ║${NC}"
    echo -e "${GREEN}║                                                               ║${NC}"
    echo -e "${GREEN}║  Total time: ${MINUTES}m ${SECONDS}s                                          ║${NC}"
    echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
    exit 0
else
    # Deactivate venv
    deactivate 2>/dev/null || true

    # Calculate total time
    END_TIME=$(date +%s)
    TOTAL_SECONDS=$((END_TIME - START_TIME))
    MINUTES=$((TOTAL_SECONDS / 60))
    SECONDS=$((TOTAL_SECONDS % 60))

    echo ""
    echo -e "${RED}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}║                                                               ║${NC}"
    echo -e "${RED}║  ✗ OCP E2E Validation FAILED                                  ║${NC}"
    echo -e "${RED}║                                                               ║${NC}"
    echo -e "${RED}║  Total time: ${MINUTES}m ${SECONDS}s                                          ║${NC}"
    echo -e "${RED}╚═══════════════════════════════════════════════════════════════╝${NC}"
    exit 1
fi

