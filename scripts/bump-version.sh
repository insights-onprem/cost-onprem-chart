#!/bin/bash

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

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
    echo "       $0 --rc [--patch|--minor|--major]   (from stable: start RC cycle, default: --patch)"
    echo "       $0 --rc                             (from RC: increment RC number)"
    echo ""
    echo "Bump the cost-onprem Helm chart version according to semver."
    echo ""
    echo "Options:"
    echo "  --patch   Bump patch version (e.g., 0.2.9 -> 0.2.10)"
    echo "            From RC: promotes to stable (e.g., 0.2.10-rc3 -> 0.2.10)"
    echo "  --minor   Bump minor version (e.g., 0.2.9 -> 0.3.0)"
    echo "  --major   Bump major version (e.g., 0.2.9 -> 1.0.0)"
    echo "  --rc      Create or increment release candidate"
    echo "            From stable: start RC cycle (default scope: patch)"
    echo "              --rc           0.2.9 -> 0.2.10-rc1"
    echo "              --rc --patch   0.2.9 -> 0.2.10-rc1"
    echo "              --rc --minor   0.2.9 -> 0.3.0-rc1"
    echo "              --rc --major   0.2.9 -> 1.0.0-rc1"
    echo "            From RC: increment RC number (scope qualifiers not allowed)"
    echo "              --rc           0.2.10-rc1 -> 0.2.10-rc2"
    echo "  --help    Show this help message"
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHART_FILE="$SCRIPT_DIR/../cost-onprem/Chart.yaml"

RC_MODE=false
SCOPE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --patch)
            SCOPE="patch"
            shift
            ;;
        --minor)
            SCOPE="minor"
            shift
            ;;
        --major)
            SCOPE="major"
            shift
            ;;
        --rc)
            RC_MODE=true
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo_error "Unknown argument: $1"
            echo ""
            usage
            exit 1
            ;;
    esac
done

if [ "$RC_MODE" = false ] && [ -z "$SCOPE" ]; then
    echo_error "Exactly one of --patch, --minor, --major, or --rc is required"
    echo ""
    usage
    exit 1
fi

if [ ! -f "$CHART_FILE" ]; then
    echo_error "Chart.yaml not found: $CHART_FILE"
    exit 1
fi

CURRENT_VERSION=$(grep '^version:' "$CHART_FILE" | awk '{print $2}')

if [ -z "$CURRENT_VERSION" ]; then
    echo_error "Could not read 'version' from $CHART_FILE"
    exit 1
fi

VERSION_REGEX='^([0-9]+)\.([0-9]+)\.([0-9]+)(-rc([0-9]+))?$'
if ! [[ "$CURRENT_VERSION" =~ $VERSION_REGEX ]]; then
    echo_error "Version '$CURRENT_VERSION' is not valid semver (expected MAJOR.MINOR.PATCH or MAJOR.MINOR.PATCH-rcN)"
    exit 1
fi

MAJOR="${BASH_REMATCH[1]}"
MINOR="${BASH_REMATCH[2]}"
PATCH="${BASH_REMATCH[3]}"
RC_SUFFIX="${BASH_REMATCH[4]}"
RC_NUM="${BASH_REMATCH[5]}"

IS_RC=false
if [ -n "$RC_SUFFIX" ]; then
    IS_RC=true
fi

if [ "$RC_MODE" = true ]; then
    if [ "$IS_RC" = true ]; then
        if [ -n "$SCOPE" ]; then
            echo_error "Cannot use --$SCOPE with --rc when already on an RC version ($CURRENT_VERSION). The target version is already set — use --rc alone to increment the RC number."
            exit 1
        fi
        NEW_RC=$((RC_NUM + 1))
        NEW_VERSION="${MAJOR}.${MINOR}.${PATCH}-rc${NEW_RC}"
    else
        if [ -z "$SCOPE" ]; then
            SCOPE="patch"
        fi
        case "$SCOPE" in
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
        NEW_VERSION="${MAJOR}.${MINOR}.${PATCH}-rc1"
    fi
else
    case "$SCOPE" in
        patch)
            if [ "$IS_RC" = true ]; then
                NEW_VERSION="${MAJOR}.${MINOR}.${PATCH}"
            else
                PATCH=$((PATCH + 1))
                NEW_VERSION="${MAJOR}.${MINOR}.${PATCH}"
            fi
            ;;
        minor)
            MINOR=$((MINOR + 1))
            PATCH=0
            NEW_VERSION="${MAJOR}.${MINOR}.${PATCH}"
            ;;
        major)
            MAJOR=$((MAJOR + 1))
            MINOR=0
            PATCH=0
            NEW_VERSION="${MAJOR}.${MINOR}.${PATCH}"
            ;;
    esac
fi

echo_info "Chart: $CHART_FILE"
echo_info "Current version: $CURRENT_VERSION"
if [ "$RC_MODE" = true ]; then
    echo_info "Bump type: rc"
else
    echo_info "Bump type: $SCOPE"
fi

sed -i.bak "s/^version: .*/version: $NEW_VERSION/" "$CHART_FILE"
sed -i.bak "s/^appVersion: .*/appVersion: \"$NEW_VERSION\"/" "$CHART_FILE"
rm -f "$CHART_FILE.bak"

echo_success "Version bumped: $CURRENT_VERSION -> $NEW_VERSION"
