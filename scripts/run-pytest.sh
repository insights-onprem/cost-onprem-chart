#!/bin/bash

# Run pytest test suite for Cost On-Prem
#
# This script orchestrates the pytest test suite, handling:
# - Virtual environment setup
# - Dependency installation
# - Test execution with JUnit XML reporting
# - Exit code propagation
#
# Usage:
#   ./run-pytest.sh [OPTIONS] [PYTEST_ARGS...]
#
# Suite Options (run specific test suites):
#   --helm              Run Helm chart validation tests
#   --auth              Run JWT authentication tests
#   --infrastructure    Run infrastructure health tests (DB, S3, Kafka)
#   --cost-management   Run Cost Management (Koku) pipeline tests
#   --ros               Run ROS/Kruize recommendation tests
#   --e2e               Run end-to-end tests
#
# Filter Options:
#   --smoke             Run only smoke tests (quick validation)
#   --slow              Include slow tests (processing, recommendations)
#   --ui                Run UI tests (Playwright browser automation)
#
# Setup Options:
#   --setup-only        Only setup the environment, don't run tests
#   --no-venv           Skip virtual environment (use system Python)
#   --help              Show this help message
#
# Environment Variables:
#   NAMESPACE              Target namespace (default: cost-onprem)
#   HELM_RELEASE_NAME      Helm release name (default: cost-onprem)
#   KEYCLOAK_NAMESPACE     Keycloak namespace (default: keycloak)
#   PYTHON                 Python interpreter (default: python3)
#
# Examples:
#   ./run-pytest.sh                         # Run all tests (excludes UI by default)
#   ./run-pytest.sh --smoke                 # Run smoke tests only
#   ./run-pytest.sh --helm                  # Run Helm suite only
#   ./run-pytest.sh --auth --ros            # Run auth and ROS suites
#   ./run-pytest.sh --e2e --smoke           # Run E2E smoke tests
#   ./run-pytest.sh --e2e                   # Run full E2E flow
#   ./run-pytest.sh --ui                    # Run UI tests (requires Playwright deps)
#   ./run-pytest.sh -k "test_jwt"           # Run tests matching pattern
#   ./run-pytest.sh suites/helm/            # Run specific suite directory
#   ./run-pytest.sh -m "smoke and auth"     # Custom marker expression
#
# Note: UI tests are excluded by default because they require Playwright system
# dependencies. Use --ui to run them explicitly. This wil change in the future.

set -e

# Script configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TESTS_DIR="${PROJECT_ROOT}/tests"
VENV_DIR="${TESTS_DIR}/.venv"
REPORTS_DIR="${TESTS_DIR}/reports"

# Default configuration
PYTHON="${PYTHON:-python3}"
USE_VENV=true
SETUP_ONLY=false

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $*"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

show_help() {
    sed -n '/^# Usage:/,/^set -e$/p' "$0" | grep '^#' | sed 's/^# \?//'
    echo ""
    echo "Available Test Suites:"
    echo "  helm              Helm chart lint, template, deployment health"
    echo "  auth              Keycloak, JWT ingress/backend authentication"
    echo "  infrastructure    Database, S3, Kafka health checks"
    echo "  cost-management   Sources API, upload, Koku processing"
    echo "  ros               Kruize, recommendations API"
    echo "  e2e               Complete end-to-end data flow"
    echo "  ui                Browser-based UI tests (Playwright)"
    echo ""
    echo "Markers:"
    echo "  smoke             Quick validation tests (~1 min)"
    echo "  slow              Long-running tests (processing, recommendations)"
    exit 0
}

check_prerequisites() {
    log_info "Checking prerequisites..."

    # Check Python
    if ! command -v "$PYTHON" &> /dev/null; then
        log_error "Python not found: $PYTHON"
        log_error "Please install Python 3.10+ or set PYTHON environment variable"
        exit 1
    fi

    local python_version
    python_version=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    log_info "Using Python $python_version"

    # Check if we're logged into OpenShift
    if ! command -v oc &> /dev/null; then
        log_error "oc CLI not found. Please install OpenShift CLI."
        exit 1
    fi

    if ! oc whoami &> /dev/null; then
        log_error "Not logged into OpenShift. Please run 'oc login' first."
        exit 1
    fi

    log_success "Prerequisites check passed"
}

setup_venv() {
    if [[ "$USE_VENV" != "true" ]]; then
        log_info "Skipping virtual environment setup (--no-venv)"
        return 0
    fi

    log_info "Setting up virtual environment..."

    if [[ ! -d "$VENV_DIR" ]]; then
        log_info "Creating virtual environment at $VENV_DIR"
        "$PYTHON" -m venv "$VENV_DIR"
    fi

    # Activate virtual environment
    # shellcheck source=/dev/null
    source "$VENV_DIR/bin/activate"

    # Upgrade pip
    pip install --quiet --upgrade pip

    # Install dependencies
    log_info "Installing test dependencies..."
    pip install --quiet -r "$TESTS_DIR/requirements.txt"

    log_success "Virtual environment ready"
}

install_playwright_browsers() {
    # Install Playwright browsers (required for UI tests)
    # Note: Playwright requires system libraries (libnspr4, libnss3, etc.)
    # In CI, these must be installed by the CI step before running tests.
    # See: https://playwright.dev/python/docs/browsers#install-system-dependencies
    if ! command -v playwright &> /dev/null; then
        log_error "Playwright not found in PATH"
        return 1
    fi
    
    log_info "Installing Playwright browsers for UI tests..."
    # Try with system deps first (requires root), fall back to browser-only install
    if playwright install chromium --with-deps 2>/dev/null; then
        log_success "Playwright browsers installed with system dependencies"
    elif playwright install chromium 2>/dev/null; then
        log_warning "Playwright browsers installed WITHOUT system deps - may fail at runtime"
        log_warning "Linux requires: dnf install -y nspr nss nss-util atk cups-libs libdrm libXcomposite libXdamage libXrandr mesa-libgbm pango alsa-lib"
    else
        log_error "Failed to install Playwright browsers"
        return 1
    fi
}

setup_reports_dir() {
    log_info "Setting up reports directory..."
    mkdir -p "$REPORTS_DIR"
    log_success "Reports will be written to: $REPORTS_DIR"
}

run_pytest() {
    local pytest_args=("$@")

    log_info "Running pytest..."
    log_info "  Namespace: ${NAMESPACE:-cost-onprem}"
    log_info "  Helm Release: ${HELM_RELEASE_NAME:-cost-onprem}"
    log_info "  Keycloak Namespace: ${KEYCLOAK_NAMESPACE:-keycloak}"
    echo ""

    # Export environment variables for tests
    export NAMESPACE="${NAMESPACE:-cost-onprem}"
    export HELM_RELEASE_NAME="${HELM_RELEASE_NAME:-cost-onprem}"
    export KEYCLOAK_NAMESPACE="${KEYCLOAK_NAMESPACE:-keycloak}"

    # Change to tests directory
    cd "$TESTS_DIR"

    # Log the full pytest command being executed (critical for CI debugging)
    echo ""
    echo "============================================================"
    echo "PYTEST COMMAND"
    echo "============================================================"
    echo "pytest ${pytest_args[*]}"
    echo ""
    echo "Working directory: $(pwd)"
    echo "NAMESPACE=${NAMESPACE}"
    echo "HELM_RELEASE_NAME=${HELM_RELEASE_NAME}"
    echo "KEYCLOAK_NAMESPACE=${KEYCLOAK_NAMESPACE}"
    echo "============================================================"
    echo ""

    # Run pytest with JUnit XML output
    local exit_code=0
    pytest "${pytest_args[@]}" || exit_code=$?

    return $exit_code
}

main() {
    local pytest_markers=()
    local pytest_extra_args=()
    local include_ui=false

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            # Suite options
            --helm)
                pytest_markers+=("helm")
                shift
                ;;
            --auth)
                pytest_markers+=("auth")
                shift
                ;;
            --infrastructure)
                pytest_markers+=("infrastructure")
                shift
                ;;
            --cost-management)
                pytest_markers+=("cost_management")
                shift
                ;;
            --ros)
                pytest_markers+=("ros")
                shift
                ;;
            --e2e)
                pytest_markers+=("e2e")
                shift
                ;;
            --ui)
                pytest_markers+=("ui")
                include_ui=true
                shift
                ;;
            # Filter options
            --smoke)
                pytest_markers+=("smoke")
                shift
                ;;
            --slow)
                pytest_markers+=("slow")
                shift
                ;;
            # Setup options
            --setup-only)
                SETUP_ONLY=true
                shift
                ;;
            --no-venv)
                USE_VENV=false
                shift
                ;;
            --help|-h)
                show_help
                ;;
            -m)
                # Check if marker expression includes UI or is empty (all tests)
                if [[ -z "$2" ]] || [[ "$2" == *"ui"* ]]; then
                    include_ui=true
                fi
                pytest_extra_args+=("$1" "$2")
                shift 2
                ;;
            *)
                # Pass through to pytest
                pytest_extra_args+=("$1")
                shift
                ;;
        esac
    done

    echo ""
    echo -e "${BLUE}Cost On-Prem Test Suite${NC}"
    echo "========================"
    echo ""

    # Check prerequisites
    check_prerequisites

    # Setup virtual environment
    setup_venv

    # Install Playwright browsers only if UI tests are requested
    if [[ "$include_ui" == "true" ]]; then
        install_playwright_browsers
    fi

    # Setup reports directory
    setup_reports_dir

    if [[ "$SETUP_ONLY" == "true" ]]; then
        log_success "Environment setup complete"
        exit 0
    fi

    # Build pytest arguments
    local pytest_args=()

    # Handle marker filtering
    if [[ ${#pytest_markers[@]} -gt 0 ]]; then
        local marker_expr
        marker_expr=$(IFS=" or "; echo "${pytest_markers[*]}")
        pytest_args+=("-m" "$marker_expr")
    fi

    # Add any extra arguments
    if [[ ${#pytest_extra_args[@]} -gt 0 ]]; then
        pytest_args+=("${pytest_extra_args[@]}")
    fi

    # Run tests
    local exit_code=0
    run_pytest "${pytest_args[@]}" || exit_code=$?

    echo ""
    if [[ $exit_code -eq 0 ]]; then
        log_success "All tests passed!"
    else
        log_error "Some tests failed (exit code: $exit_code)"
    fi

    log_info "JUnit report: $REPORTS_DIR/junit.xml"

    exit $exit_code
}

main "$@"
