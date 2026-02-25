#!/bin/bash
set -euo pipefail

# Label Validation Script for cost-onprem Helm Chart
# Validates that all Kubernetes resources follow label standards

RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

RENDERED_FILE="${1:-/tmp/rendered.yaml}"
EXIT_CODE=0

echo "🔍 Validating Kubernetes labels in rendered templates..."
echo ""

if [[ ! -f "$RENDERED_FILE" ]]; then
  echo -e "${RED}❌ Error: Rendered YAML file not found: $RENDERED_FILE${NC}"
  exit 1
fi

# Track issues
declare -a ISSUES=()

# Function to add an issue
add_issue() {
  local severity="$1"
  local message="$2"
  ISSUES+=("[$severity] $message")
  if [[ "$severity" == "ERROR" ]]; then
    EXIT_CODE=1
  fi
}

# Check 1: Look for duplicate/hardcoded app.kubernetes.io/name labels
echo "📋 Check 1: Scanning for hardcoded app.kubernetes.io/name labels..."
echo "   (These should only come from helpers, not be explicitly set)"

# Extract resources with their labels and check for problematic patterns
# We're looking for app.kubernetes.io/name that appear to override the helper
HARDCODED_NAMES=$(grep -n "app.kubernetes.io/name:" "$RENDERED_FILE" | \
  grep -v "app.kubernetes.io/name: cost-onprem" | \
  grep -E "(database|cache|valkey|storage|ros-api|ros-processor|ros-partition-cleaner|gateway|kruize|kruize-partitions|ingress|ros-rec-poller)" || true)

if [[ -n "$HARDCODED_NAMES" ]]; then
  add_issue "ERROR" "Found hardcoded app.kubernetes.io/name labels that override helper templates:"
  while IFS= read -r line; do
    add_issue "ERROR" "  Line: $line"
  done <<< "$HARDCODED_NAMES"
else
  echo -e "   ${GREEN}✓ No hardcoded app.kubernetes.io/name labels found${NC}"
fi

echo ""

# Check 2: Look for unprefixed custom labels
echo "📋 Check 2: Scanning for unprefixed custom labels..."
echo "   (Custom labels should use cost-onprem.io/ prefix)"

UNPREFIXED_LABELS=$(grep -n -E "^\s+(api-type|celery-type|worker-queue):" "$RENDERED_FILE" || true)

if [[ -n "$UNPREFIXED_LABELS" ]]; then
  add_issue "ERROR" "Found unprefixed custom labels (should be cost-onprem.io/*):"
  while IFS= read -r line; do
    add_issue "ERROR" "  Line: $line"
  done <<< "$UNPREFIXED_LABELS"
else
  echo -e "   ${GREEN}✓ No unprefixed custom labels found${NC}"
fi

echo ""

# Check 3: Verify required labels exist on all resources
echo "📋 Check 3: Checking for required labels on all resources..."
echo "   (Checking Deployments, StatefulSets, Services have component labels)"

# Count resources that should have component labels
TOTAL_RESOURCES=$(grep -c "^# Source: cost-onprem/templates/" "$RENDERED_FILE" || echo "0")

# Improved check: Look at actual rendered YAML structure
# For Deployments/StatefulSets: Check metadata section has component
# Exclude resources that don't need component labels
MISSING_COMPONENT=$(awk '
  BEGIN { in_metadata = 0; in_spec = 0; resource_kind = ""; has_component = 0; current_file = "" }

  /^# Source: cost-onprem\/templates\// {
    # Start of new resource
    if (current_file && resource_kind && !has_component) {
      # Only report if it is a resource type that needs component
      if (resource_kind ~ /Deployment|StatefulSet|Service|CronJob|Job/) {
        if (current_file !~ /secret|configmap|clusterrole|rolebinding|networkpolicy|pvc|serviceaccount/) {
          print current_file " (" resource_kind ") - Missing component label"
        }
      }
    }
    current_file = $0
    resource_kind = ""
    has_component = 0
    in_metadata = 0
    in_spec = 0
    next
  }

  /^kind:/ {
    resource_kind = $2
    next
  }

  /^metadata:/ {
    in_metadata = 1
    in_spec = 0
    next
  }

  /^spec:/ {
    in_metadata = 0
    in_spec = 1
    next
  }

  # Check for component in metadata section (most common location)
  in_metadata && /app\.kubernetes\.io\/component:/ {
    has_component = 1
  }

  # Also check in labels section anywhere in the resource
  /app\.kubernetes\.io\/component:/ {
    has_component = 1
  }

  END {
    # Check last resource
    if (current_file && resource_kind && !has_component) {
      if (resource_kind ~ /Deployment|StatefulSet|Service|CronJob|Job/) {
        if (current_file !~ /secret|configmap|clusterrole|rolebinding|networkpolicy|pvc|serviceaccount/) {
          print current_file " (" resource_kind ") - Missing component label"
        }
      }
    }
  }
' "$RENDERED_FILE" || true)

if [[ -n "$MISSING_COMPONENT" ]]; then
  add_issue "ERROR" "Resources missing app.kubernetes.io/component labels:"
  while IFS= read -r line; do
    add_issue "ERROR" "  $line"
  done <<< "$MISSING_COMPONENT"
else
  echo -e "   ${GREEN}✓ All resources have app.kubernetes.io/component labels${NC}"
fi

echo ""

# Check 4: Look for component naming inconsistencies
echo "📋 Check 4: Checking component naming standards..."
echo "   (Looking for non-standard component names)"

# Check for old-style component names that should be updated per the spec
# Allowed component names:
#   - cost-* prefix: cost-management-api, cost-processor, cost-scheduler, cost-worker
#   - ros-* prefix: ros-api, ros-processor, ros-housekeeper, ros-recommendation-poller, ros-optimization, ros-optimization-maintenance, ros-database-maintenance
#   - Shared infrastructure: database, cache, storage, ingress, ui, networkpolicy
# Flag deprecated names that should no longer be used
OLD_COMPONENT_NAMES=$(grep -n "app.kubernetes.io/component:" "$RENDERED_FILE" | \
  grep -E "(cost-management-celery|partition-cleaner|\\boptimization\\b|\\bprocessor\\b|\\bmaintenance\\b)" | \
  grep -Ev "(cost-processor|ros-processor|ros-optimization|ros-optimization-maintenance|ros-database-maintenance)" || true)

if [[ -n "$OLD_COMPONENT_NAMES" ]]; then
  add_issue "ERROR" "Found non-standard component names (see label-standardization-task.md):"
  while IFS= read -r line; do
    add_issue "ERROR" "  Line: $line"
  done <<< "$OLD_COMPONENT_NAMES"
else
  echo -e "   ${GREEN}✓ All component names follow standards${NC}"
fi

echo ""

# Check 5: Verify custom labels have proper prefix
echo "📋 Check 5: Verifying custom label prefixes..."

PREFIXED_LABELS=$(grep -c "cost-onprem.io/" "$RENDERED_FILE" || echo "0")

if [[ "$PREFIXED_LABELS" -gt 0 ]]; then
  echo -e "   ${GREEN}✓ Found $PREFIXED_LABELS uses of cost-onprem.io/ prefix${NC}"
else
  # Check if we have custom labels that should be prefixed
  HAS_CUSTOM=$(grep -E "(api-type|celery-type|worker-queue):" "$RENDERED_FILE" || true)
  if [[ -n "$HAS_CUSTOM" ]]; then
    add_issue "WARNING" "Custom labels found but no cost-onprem.io/ prefix detected"
  else
    echo -e "   ${YELLOW}⚠ No custom labels found (this may be expected)${NC}"
  fi
fi

echo ""
echo "=================================="
echo "📊 Validation Summary"
echo "=================================="

if [[ ${#ISSUES[@]} -eq 0 ]]; then
  echo -e "${GREEN}✅ All label validations passed!${NC}"
  echo ""
  echo "The rendered templates follow all label standards:"
  echo "  ✓ No hardcoded app.kubernetes.io/name labels"
  echo "  ✓ All custom labels are properly prefixed"
  echo "  ✓ All resources have required component labels"
  echo "  ✓ Component names follow standards"
  echo ""
  exit 0
else
  ERROR_COUNT=$(printf '%s\n' "${ISSUES[@]}" | grep -c "^\[ERROR\]" || echo "0")
  WARNING_COUNT=$(printf '%s\n' "${ISSUES[@]}" | grep -c "^\[WARNING\]" || echo "0")

  if [[ "$ERROR_COUNT" -eq 0 ]]; then
    # No errors, only warnings
    echo -e "${YELLOW}⚠️  Found ${WARNING_COUNT} warnings (no errors)${NC}"
    echo ""
    echo "Warning details:"
    echo "----------------"
    for issue in "${ISSUES[@]}"; do
      if [[ "$issue" == \[WARNING\]* ]]; then
        echo -e "${YELLOW}$issue${NC}"
      fi
    done
    echo ""
    echo -e "${GREEN}✅ Validation PASSED${NC} - All critical checks successful"
    echo "   Warnings are informational and do not block the build."
    echo ""
    exit 0
  else
    # Has errors
    echo -e "${RED}❌ Found ${ERROR_COUNT} errors and ${WARNING_COUNT} warnings${NC}"
    echo ""
    echo "Issues found:"
    echo "-------------"

    # Show errors first
    for issue in "${ISSUES[@]}"; do
      if [[ "$issue" == \[ERROR\]* ]]; then
        echo -e "${RED}$issue${NC}"
      fi
    done

    # Then warnings
    if [[ "$WARNING_COUNT" -gt 0 ]]; then
      echo ""
      echo "Warnings:"
      for issue in "${ISSUES[@]}"; do
        if [[ "$issue" == \[WARNING\]* ]]; then
          echo -e "${YELLOW}$issue${NC}"
        fi
      done
    fi

    echo ""
    echo "📚 See label-standardization-task.md for remediation guidance"
    echo ""

    exit 1
  fi
fi
