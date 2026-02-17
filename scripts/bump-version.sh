#!/bin/bash

# Bump Cost-Onprem Chart Version
# Bumps the Helm chart version according to semantic versioning.
# Updates both 'version' and 'appVersion' fields in Chart.yaml.
#
# Usage:
#   ./bump-version.sh --patch
#   ./bump-version.sh --minor
#   ./bump-version.sh --major
#
# Examples:
#   # Bump patch: 0.2.9 -> 0.2.10
#   ./scripts/bump-version.sh --patch
#
#   # Bump minor: 0.2.9 -> 0.3.0
#   ./scripts/bump-version.sh --minor
#
#   # Bump major: 0.2.9 -> 1.0.0
#   ./scripts/bump-version.sh --major

set -e  # Exit on any error

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

echo_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

echo_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

usage() {
    echo "Usage: $0 --patch | --minor | --major"
    echo ""
    echo "Bump the cost-onprem Helm chart version according to semver."
    echo ""
    echo "Options:"
    echo "  --patch   Bump patch version (e.g., 0.2.9 -> 0.2.10)"
    echo "  --minor   Bump minor version (e.g., 0.2.9 -> 0.3.0)"
    echo "  --major   Bump major version (e.g., 0.2.9 -> 1.0.0)"
    echo "  --help    Show this help message"
}

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHART_FILE="$SCRIPT_DIR/../cost-onprem/Chart.yaml"

# Parse arguments
BUMP_TYPE=""

case "${1:-}" in
    --patch)
        BUMP_TYPE="patch"
        ;;
    --minor)
        BUMP_TYPE="minor"
        ;;
    --major)
        BUMP_TYPE="major"
        ;;
    --help|-h)
        usage
        exit 0
        ;;
    *)
        echo_error "Exactly one of --patch, --minor, or --major is required"
        echo ""
        usage
        exit 1
        ;;
esac

# Validate Chart.yaml exists
if [ ! -f "$CHART_FILE" ]; then
    echo_error "Chart.yaml not found: $CHART_FILE"
    exit 1
fi

# Extract current version
CURRENT_VERSION=$(grep '^version:' "$CHART_FILE" | awk '{print $2}')

if [ -z "$CURRENT_VERSION" ]; then
    echo_error "Could not read 'version' from $CHART_FILE"
    exit 1
fi

# Validate MAJOR.MINOR.PATCH format
if ! echo "$CURRENT_VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    echo_error "Version '$CURRENT_VERSION' is not valid semver (expected MAJOR.MINOR.PATCH)"
    exit 1
fi

# Parse version components
IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT_VERSION"

# Compute new version
case "$BUMP_TYPE" in
    patch)
        PATCH=$((PATCH + 1))
        ;;
    minor)
        MINOR=$((MINOR + 1))
        PATCH=0
        ;;
    major)
        MAJOR=$((MAJOR + 1))
        MINOR=0
        PATCH=0
        ;;
esac

NEW_VERSION="${MAJOR}.${MINOR}.${PATCH}"

echo_info "Chart: $CHART_FILE"
echo_info "Current version: $CURRENT_VERSION"
echo_info "Bump type: $BUMP_TYPE"

# Update Chart.yaml (portable sed: -i.bak works on both macOS and Linux)
sed -i.bak "s/^version: .*/version: $NEW_VERSION/" "$CHART_FILE"
sed -i.bak "s/^appVersion: .*/appVersion: \"$NEW_VERSION\"/" "$CHART_FILE"
rm -f "$CHART_FILE.bak"

echo_success "Version bumped: $CURRENT_VERSION -> $NEW_VERSION"
