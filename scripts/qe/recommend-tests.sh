#!/usr/bin/env bash
# Analyze PR diff and recommend IQE test profiles.
# Used by .github/workflows/recommend-tests.yml and check-components.yml
#
# Reads component/path mappings from scripts/qe/test-impact-map.yaml
#
# Usage: BASE_BRANCH=main ./recommend-tests.sh
#
# Outputs (via GITHUB_OUTPUT if set):
#   suggested_profile, component_table, needs_deeper_testing
#
# Expected runtime: <1 second (local), <2 seconds (CI)

set -euo pipefail

# --- Constants and defaults ------------------------------------------------

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly DEFAULT_IMPACT_MAP="${SCRIPT_DIR}/test-impact-map.yaml"
readonly DEFAULT_BASE_BRANCH="main"

# Allow overrides via environment variables
IMPACT_MAP="${IMPACT_MAP:-${DEFAULT_IMPACT_MAP}}"
BASE_BRANCH="${BASE_BRANCH:-${DEFAULT_BASE_BRANCH}}"

# --- Logging helpers -------------------------------------------------------

log()  { echo "==> $*"; }
info() { echo "    $*"; }
err()  { echo "ERROR: $*" >&2; }

if [[ ! -f "$IMPACT_MAP" ]]; then
    err "Impact map not found: $IMPACT_MAP"
    exit 1
fi

# --- Validation ------------------------------------------------------------

# Validate test impact map structure
# Exits with error if map is malformed
validate_impact_map() {
    local required_sections=("components" "paths")
    
    for section in "${required_sections[@]}"; do
        if ! grep -q "^${section}:" "$IMPACT_MAP"; then
            err "Impact map missing required section: ${section}"
            return 1
        fi
    done
    
    # Validate at least one component exists
    if ! list_components | grep -q .; then
        err "Impact map has no components defined"
        return 1
    fi
    
    return 0
}

# --- Helper functions ------------------------------------------------------

# Output a variable to GITHUB_OUTPUT or stdout
# Args:
#   $1 - variable name
#   $2 - variable value
output_var() {
    local name="$1"
    local value="$2"
    if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
        echo "$name=$value" >> "$GITHUB_OUTPUT"
    else
        echo "$name=$value"
    fi
}

# Output a multi-line variable to GITHUB_OUTPUT or stdout
# Args:
#   $1 - variable name
#   $2 - variable value (may contain newlines)
output_multiline() {
    local name="$1"
    local value="$2"
    if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
        {
            echo "${name}<<EOF"
            echo -e "$value"
            echo "EOF"
        } >> "$GITHUB_OUTPUT"
    else
        echo "$name:"
        echo -e "$value"
    fi
}

# Return numeric rank for profile comparison
# Args:
#   $1 - profile name (smoke, extended, stable, full)
# Returns: integer rank (0-3)
profile_rank() {
    case "$1" in
        smoke)    echo 0 ;;
        extended) echo 1 ;;
        stable)   echo 2 ;;
        full)     echo 3 ;;
        *)        echo 0 ;;
    esac
}

# Read a component field from the YAML
# Args: $1 - component name, $2 - field name
# Returns: field value or empty string
get_component_field() {
    local name="$1" field="$2"
    
    # Prefer yq if available (robust YAML parsing)
    if command -v yq &>/dev/null; then
        yq eval ".components.${name}.${field}" "$IMPACT_MAP" 2>/dev/null | \
            sed 's/^null$//' || true
        return
    fi
    
    # Fallback: minimal YAML parser using awk (works for simple structures only)
    awk -v comp="$name" -v fld="$field" '
        /^  [a-zA-Z]/ { in_comp = ($1 == comp":") }
        in_comp && $1 == fld":" {
            val = $0
            sub(/^[^:]+:[ \t]*/, "", val)
            gsub(/^["'\''"]|["'\''"]$/, "", val)
            print val
            exit
        }
    ' "$IMPACT_MAP"
}

# Read a path rule field
# Args: $1 - rule name, $2 - field name
# Returns: field value or empty string
get_path_field() {
    local rule="$1" field="$2"
    
    # Prefer yq if available
    if command -v yq &>/dev/null; then
        yq eval ".paths.${rule}.${field}" "$IMPACT_MAP" 2>/dev/null | \
            sed 's/^null$//' || true
        return
    fi
    
    # Fallback: minimal YAML parser using awk
    awk -v rule="$rule" -v fld="$field" '
        /^paths:/ { in_paths=1; next }
        in_paths && /^[a-zA-Z]/ { in_paths=0 }
        in_paths && /^  [a-zA-Z]/ { in_rule = ($1 == rule":") }
        in_rule && $1 == fld":" {
            val = $0
            sub(/^[^:]+:[ \t]*/, "", val)
            gsub(/^["'\''"]|["'\''"]$/, "", val)
            print val
            exit
        }
    ' "$IMPACT_MAP"
}

# List all component names from the YAML
# Returns: component names, one per line
list_components() {
    if command -v yq &>/dev/null; then
        yq eval '.components | keys | .[]' "$IMPACT_MAP" 2>/dev/null || true
        return
    fi
    
    awk '/^components:/ { in_c=1; next }
         in_c && /^[a-zA-Z]/ { in_c=0 }
         in_c && /^  [a-zA-Z]/ { sub(/:$/, "", $1); print $1 }
    ' "$IMPACT_MAP"
}

# List all path rule names from the YAML
# Returns: rule names, one per line
list_path_rules() {
    if command -v yq &>/dev/null; then
        yq eval '.paths | keys | .[]' "$IMPACT_MAP" 2>/dev/null || true
        return
    fi
    
    awk '/^paths:/ { in_p=1; next }
         in_p && /^[a-zA-Z]/ { in_p=0 }
         in_p && /^  [a-zA-Z]/ { sub(/:$/, "", $1); print $1 }
    ' "$IMPACT_MAP"
}

# --- Analysis functions ----------------------------------------------------

# Upgrade the suggested profile to a higher tier if needed
# Args:
#   $1 - candidate profile
# Globals:
#   max_profile - updated if candidate ranks higher
upgrade_profile() {
    local candidate="$1"
    if [ "$(profile_rank "$candidate")" -gt "$(profile_rank "$max_profile")" ]; then
        max_profile="$candidate"
    fi
}

# Add a row to the component impact table
# Args:
#   $1 - component/rule name
#   $2 - impact level
#   $3 - description
# Globals:
#   component_rows - appended with new row
#   has_component - set to true
add_row() {
    local name="$1" impact="$2" desc="$3"
    component_rows="${component_rows}| ${name} | ${impact} | ${desc} |\n"
    has_component=true
}

# --- Main analysis ---------------------------------------------------------

# Validate impact map before processing
validate_impact_map || exit 1

max_profile="smoke"
component_rows=""
has_component=false

changed_files=$(git diff --name-only "${BASE_BRANCH}...HEAD" 2>/dev/null || git diff --name-only HEAD~1 2>/dev/null || echo "")

if [[ -z "$changed_files" ]]; then
    info "No changed files detected"
    output_var "needs_deeper_testing" "false"
    output_var "suggested_profile" "smoke"
    exit 0
fi

# 1) Check values.yaml for image tag changes — look up each in components map
values_diff=$(git diff "${BASE_BRANCH}...HEAD" -- cost-onprem/values.yaml 2>/dev/null || echo "")

if [[ -n "$values_diff" ]]; then
    while IFS= read -r tag_line; do
        [[ -z "$tag_line" ]] && continue

        repo_line=$(echo "$values_diff" | grep -B5 "$(echo "$tag_line" | sed 's/^+//')" | grep 'repository:' | tail -1 || true)
        [[ -z "$repo_line" ]] && continue

        repo=$(echo "$repo_line" | sed 's/.*repository: *//' | tr -d '"' | tr -d "'")
        component=$(basename "$repo")

        profile=$(get_component_field "$component" "profile")
        if [[ -n "$profile" ]]; then
            impact=$(get_component_field "$component" "impact")
            desc=$(get_component_field "$component" "description")
            upgrade_profile "$profile"
            add_row "$component" "$impact" "$desc"
        else
            add_row "$component" "unknown" "Image updated (no mapping in test-impact-map.yaml)"
        fi
    done < <(echo "$values_diff" | grep -E '^\+.*tag:' | grep -v '^\+\+\+')
fi

# 2) Evaluate path-based rules
while IFS= read -r rule; do
    [[ -z "$rule" ]] && continue

    pattern=$(get_path_field "$rule" "pattern")
    diff_pattern=$(get_path_field "$rule" "diff_pattern")
    profile=$(get_path_field "$rule" "profile")
    impact=$(get_path_field "$rule" "impact")
    desc=$(get_path_field "$rule" "description")

    [[ -z "$pattern" ]] && continue

    if [[ -n "$diff_pattern" ]]; then
        # This rule requires a specific diff content match (not just file path)
        if [[ -n "$values_diff" ]]; then
            match_count=$(echo "$values_diff" | grep -cE "$diff_pattern" || true)
            if [ "$match_count" -gt 0 ] 2>/dev/null; then
                upgrade_profile "$profile"
                add_row "$rule" "$impact" "$desc"
            fi
        fi
    else
        if echo "$changed_files" | grep -qE "$pattern"; then
            upgrade_profile "$profile"
            add_row "$rule" "$impact" "$desc"
        fi
    fi
done < <(list_path_rules)

needs_deeper="false"
if [[ "$max_profile" != "smoke" ]]; then
    needs_deeper="true"
fi

output_var "suggested_profile" "$max_profile"
output_var "needs_deeper_testing" "$needs_deeper"

# Map profile to Prow /test command
case "$max_profile" in
    smoke)    prow_cmd="/test e2e" ;;
    extended) prow_cmd="/test e2e-iqe-extended" ;;
    stable)   prow_cmd="/test e2e-iqe-stable" ;;
    full)     prow_cmd="/test e2e-iqe-stable" ;;
    *)        prow_cmd="/test e2e" ;;
esac
output_var "prow_command" "$prow_cmd"

if [[ "$has_component" == "true" ]]; then
    table_header="| Component | Impact | Description |\n|-----------|--------|-------------|"
    output_multiline "component_table" "${table_header}\n${component_rows}"
else
    output_multiline "component_table" ""
fi

log "Analysis complete"
info "Suggested profile: $max_profile"
info "Needs deeper testing: $needs_deeper"
