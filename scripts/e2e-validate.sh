#!/bin/bash
# e2e-validate.sh
# Thin wrapper for Python E2E validator

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
IQE_DIR="/Users/jgil/go/src/github.com/insights-onprem/iqe-cost-management-plugin"
VENV_DIR="$IQE_DIR/iqe-venv"

# Check if IQE venv exists
if [ ! -d "$VENV_DIR" ]; then
    echo "❌ IQE venv not found at $VENV_DIR"
    echo "Please set up IQE venv first"
    exit 1
fi

# Activate IQE venv
echo "🔧 Activating IQE venv..."
source "$VENV_DIR/bin/activate"

# Check if dependencies are installed in this venv
if ! python3 -c "import kubernetes" 2>/dev/null; then
    echo "⚠️  Python dependencies not installed in IQE venv"
    echo "Installing requirements..."
    pip3 install -q -r "$SCRIPT_DIR/requirements-e2e.txt"
fi

# Run Python CLI
cd "$SCRIPT_DIR"
python3 -m e2e_validator.cli "$@"

# Deactivate venv on exit
deactivate 2>/dev/null || true

