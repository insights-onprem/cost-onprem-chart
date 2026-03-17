#!/bin/bash
# Build and push a custom IQE container with local plugin changes
#
# Usage:
#   ./scripts/build-custom-iqe.sh [OPTIONS]
#
# Options:
#   --registry URL       Target registry (default: internal OpenShift registry)
#   --tag TAG            Image tag (default: custom)
#   --plugin-path PATH   Path to iqe-cost-management-plugin (default: ../iqe-cost-management-plugin)
#   --no-push            Build only, don't push
#   --help               Show this help message

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Defaults
NAMESPACE="${NAMESPACE:-cost-onprem}"
TAG="${TAG:-custom}"
PLUGIN_PATH="${PLUGIN_PATH:-${PROJECT_ROOT}/../iqe-cost-management-plugin}"
NO_PUSH=false
REGISTRY=""

show_help() {
    cat << EOF
Build and push a custom IQE container with local plugin changes

Usage: $(basename "$0") [OPTIONS]

Options:
    --registry URL       Target registry URL
                         Default: internal OpenShift registry route
    --tag TAG            Image tag (default: custom)
    --plugin-path PATH   Path to iqe-cost-management-plugin
                         Default: ../iqe-cost-management-plugin
    --no-push            Build only, don't push to registry
    --help               Show this help message

Examples:
    # Build and push to internal OpenShift registry
    ./scripts/build-custom-iqe.sh

    # Build with custom tag
    ./scripts/build-custom-iqe.sh --tag my-feature

    # Build only (no push)
    ./scripts/build-custom-iqe.sh --no-push

    # Push to external registry
    ./scripts/build-custom-iqe.sh --registry quay.io/myuser/iqe-tests

Prerequisites:
    - podman installed and running
    - oc logged in to target cluster (for internal registry)
    - iqe-cost-management-plugin cloned locally

macOS Notes:
    If using Podman on macOS, ensure the Podman machine is running:
        podman machine start
    
    The build happens inside the Podman VM, so paths are mounted automatically.
EOF
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --registry) REGISTRY="$2"; shift 2 ;;
        --tag) TAG="$2"; shift 2 ;;
        --plugin-path) PLUGIN_PATH="$2"; shift 2 ;;
        --no-push) NO_PUSH=true; shift ;;
        --help) show_help; exit 0 ;;
        *) echo "Unknown option: $1"; show_help; exit 1 ;;
    esac
done

# Validate plugin path
if [[ ! -d "${PLUGIN_PATH}" ]]; then
    echo "ERROR: IQE plugin not found at: ${PLUGIN_PATH}"
    echo ""
    echo "Clone the plugin repository:"
    echo "  git clone git@github.com:RedHatQE/iqe-cost-management-plugin.git ${PLUGIN_PATH}"
    exit 1
fi

# Validate podman
if ! command -v podman &>/dev/null; then
    echo "ERROR: podman is required but not found"
    exit 1
fi

# Determine registry
if [[ -z "${REGISTRY}" ]]; then
    # Try to get internal OpenShift registry route
    if command -v oc &>/dev/null && oc whoami &>/dev/null; then
        REGISTRY=$(oc get route default-route -n openshift-image-registry -o jsonpath='{.spec.host}' 2>/dev/null || echo "")
        if [[ -z "${REGISTRY}" ]]; then
            echo "ERROR: Could not determine OpenShift registry route"
            echo ""
            echo "Ensure the registry is exposed:"
            echo "  oc patch configs.imageregistry.operator.openshift.io/cluster --type merge -p '{\"spec\":{\"defaultRoute\":true}}'"
            exit 1
        fi
        echo "Using internal OpenShift registry: ${REGISTRY}"
    else
        echo "ERROR: No registry specified and not logged into OpenShift"
        echo ""
        echo "Either:"
        echo "  1. Log in to OpenShift: oc login ..."
        echo "  2. Specify a registry: --registry quay.io/myuser/iqe-tests"
        exit 1
    fi
fi

FULL_IMAGE="${REGISTRY}/${NAMESPACE}/iqe-cost-management:${TAG}"

echo "========== Building Custom IQE Container =========="
echo "Plugin path: ${PLUGIN_PATH}"
echo "Target image: ${FULL_IMAGE}"
echo ""

# Create temporary build context
BUILD_DIR=$(mktemp -d)
trap "rm -rf ${BUILD_DIR}" EXIT

echo "Creating build context..."

# Copy plugin to build context
cp -r "${PLUGIN_PATH}" "${BUILD_DIR}/iqe-cost-management-plugin"

# Create Dockerfile
cat > "${BUILD_DIR}/Dockerfile" << 'DOCKERFILE'
FROM quay.io/cloudservices/iqe-tests:cost-management

USER root

# Remove existing plugin and install local version
RUN pip uninstall -y iqe-cost-management || true

COPY iqe-cost-management-plugin /tmp/iqe-cost-management-plugin
RUN pip install /tmp/iqe-cost-management-plugin && \
    rm -rf /tmp/iqe-cost-management-plugin

USER 1001
DOCKERFILE

echo "Building image..."
podman build -t "${FULL_IMAGE}" "${BUILD_DIR}"

if [[ "${NO_PUSH}" == "true" ]]; then
    echo ""
    echo "Build complete (no push requested)"
    echo "Image: ${FULL_IMAGE}"
    exit 0
fi

# Login to registry if internal OpenShift
if [[ "${REGISTRY}" == *"openshift-image-registry"* ]]; then
    echo ""
    echo "Logging in to OpenShift registry..."
    podman login -u kubeadmin -p "$(oc whoami -t)" "${REGISTRY}" --tls-verify=false
fi

echo ""
echo "Pushing image..."
podman push "${FULL_IMAGE}" --tls-verify=false

echo ""
echo "========== Build Complete =========="
echo "Image: ${FULL_IMAGE}"
echo ""
echo "To use this image for tests:"
echo "  export IQE_IMAGE=\"${FULL_IMAGE}\""
echo "  ./scripts/run-iqe-tests.sh"
echo ""
echo "Or directly:"
echo "  IQE_IMAGE=\"${FULL_IMAGE}\" ./scripts/run-iqe-tests.sh"
