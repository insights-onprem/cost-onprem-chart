#!/usr/bin/env bash
# Check for component updates in values.yaml
# Used by .github/workflows/check-components.yml
#
# Usage: MODE=detect-updates ./check-components.sh
#        MODE=list-versions ./check-components.sh
#        MODE=deployment-info ./check-components.sh
#
# Modes:
#   detect-updates: Check for changes, update component-updates.json (default)
#   list-versions:  Report current digests and last modified dates
#   deployment-info: Output deployment metadata for CI traceability
#
# Outputs (via GITHUB_OUTPUT if set):
#   mode, has_updates, updates_summary
#   For deployment-info mode: helm_chart_version, git_sha, git_branch, etc.
#
# Expected runtime: 30-60 seconds (Quay API calls)

set -euo pipefail

# --- Constants and defaults ------------------------------------------------

readonly DEFAULT_VALUES_FILE="cost-onprem/values.yaml"
readonly DEFAULT_CHART_FILE="cost-onprem/Chart.yaml"
readonly DEFAULT_CACHE_DIR=".digest-cache"
readonly DEFAULT_UPDATES_FILE="component-updates.json"
readonly DEFAULT_MODE="detect-updates"

# Allow overrides via environment variables
VALUES_FILE="${VALUES_FILE:-${DEFAULT_VALUES_FILE}}"
CHART_FILE="${CHART_FILE:-${DEFAULT_CHART_FILE}}"
CACHE_DIR="${CACHE_DIR:-${DEFAULT_CACHE_DIR}}"
UPDATES_FILE="${UPDATES_FILE:-${DEFAULT_UPDATES_FILE}}"
MODE="${MODE:-${DEFAULT_MODE}}"

mkdir -p "$CACHE_DIR"

# --- Logging helpers -------------------------------------------------------

log()  { echo "==> $*"; }
info() { echo "    $*"; }
err()  { echo "ERROR: $*" >&2; }

# --- Cleanup handlers ------------------------------------------------------

TEMP_FILES=()

cleanup() {
    local exit_code=$?
    if [[ ${#TEMP_FILES[@]} -gt 0 ]]; then
        for temp_file in "${TEMP_FILES[@]}"; do
            [[ -f "$temp_file" ]] && rm -f "$temp_file"
        done
    fi
    return $exit_code
}

trap cleanup EXIT INT TERM

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

# Get deployment metadata for CI traceability
get_deployment_info() {
    local helm_chart_version=""
    local deployed_chart_version=""
    local helm_release_name="${HELM_RELEASE_NAME:-cost-onprem}"
    local namespace="${NAMESPACE:-cost-onprem}"
    local git_sha=""
    local git_branch=""
    local git_tag=""
    local deployment_timestamp=""
    
    # Extract chart version from Chart.yaml (source version)
    if [[ -f "$CHART_FILE" ]]; then
        helm_chart_version=$(grep -E "^version:" "$CHART_FILE" | head -1 | awk '{print $2}' | tr -d '"' | tr -d "'")
    fi
    
    # Try to get the actually deployed chart version from the cluster
    if command -v helm &> /dev/null; then
        deployed_chart_version=$(helm list -n "$namespace" -o json 2>/dev/null | \
            jq -r --arg name "$helm_release_name" '.[] | select(.name==$name) | .chart' 2>/dev/null | \
            sed 's/.*-//' || echo "")
    fi
    
    # Get git information
    if command -v git &> /dev/null && git rev-parse --git-dir &> /dev/null; then
        git_sha=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
        git_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
        git_tag=$(git describe --tags --exact-match 2>/dev/null || echo "")
        
        # Use GitHub Actions environment variables if available
        if [[ -n "${GITHUB_SHA:-}" ]]; then
            git_sha="$GITHUB_SHA"
        fi
        if [[ -n "${GITHUB_REF_NAME:-}" ]]; then
            git_branch="$GITHUB_REF_NAME"
        fi
    fi
    
    # Timestamp
    deployment_timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    
    # Output all metadata
    output_var "helm_chart_version" "${helm_chart_version:-unknown}"
    output_var "deployed_chart_version" "${deployed_chart_version:-}"
    output_var "git_sha" "${git_sha:-unknown}"
    output_var "git_sha_short" "${git_sha:0:7}"
    output_var "git_branch" "${git_branch:-unknown}"
    output_var "git_tag" "${git_tag:-}"
    output_var "deployment_timestamp" "$deployment_timestamp"
    
    # Also output a summary for logging
    echo "=== Deployment Metadata ==="
    echo "Chart Version (source):   ${helm_chart_version:-unknown}"
    [[ -n "$deployed_chart_version" ]] && echo "Chart Version (deployed): $deployed_chart_version"
    echo "Git SHA:                  ${git_sha:-unknown}"
    echo "Git Branch:               ${git_branch:-unknown}"
    [[ -n "$git_tag" ]] && echo "Git Tag:                  $git_tag"
    echo "Timestamp:                $deployment_timestamp"
    echo "==========================="
    
    # Get component details
    local components_json
    components_json=$(extract_all_components)
    
    # Output as JSON for easy parsing
    local metadata_json
    metadata_json=$(cat <<EOF
{
  "helm_chart_version": "${helm_chart_version:-unknown}",
  "deployed_chart_version": "${deployed_chart_version:-}",
  "git_sha": "${git_sha:-unknown}",
  "git_sha_short": "${git_sha:0:7}",
  "git_branch": "${git_branch:-unknown}",
  "git_tag": "${git_tag:-}",
  "deployment_timestamp": "$deployment_timestamp",
  "components": $components_json
}
EOF
)
    output_var "metadata_json" "$(echo "$metadata_json" | tr -d '\n' | tr -s ' ')"
    
    # Write to version_info.json file
    local version_info_file="${VERSION_INFO_FILE:-version_info.json}"
    echo "$metadata_json" > "$version_info_file"
    echo "Version info written to: $version_info_file"
    output_var "version_info_file" "$version_info_file"
}

# Extract latest-tagged images from values.yaml
# Returns lines of: repo|tag
extract_images() {
    local repo=""
    while IFS= read -r line; do
        if [[ "$line" =~ repository:\ *(.+) ]]; then
            repo="${BASH_REMATCH[1]//\"/}"
            repo="${repo//\'/}"
        elif [[ "$line" =~ tag:\ *(.+) ]]; then
            local tag="${BASH_REMATCH[1]//\"/}"
            tag="${tag//\'/}"
            if [[ -n "$repo" && "$tag" == "latest" ]]; then
                echo "$repo"
            fi
            repo=""
        fi
    done < "$VALUES_FILE"
}

# Extract all component images with their tags from values.yaml
# Returns JSON object of components
extract_all_components() {
    local components_json="{"
    local first=true
    local repo=""
    local tag=""
    
    while IFS= read -r line; do
        # Match image repository
        if [[ "$line" =~ repository:\ *(.+) ]]; then
            repo="${BASH_REMATCH[1]//\"/}"
            repo="${repo//\'/}"
            repo="${repo#"${repo%%[![:space:]]*}"}"  # trim leading whitespace
        fi
        
        # Match image tag
        if [[ "$line" =~ tag:\ *(.+) ]]; then
            tag="${BASH_REMATCH[1]//\"/}"
            tag="${tag//\'/}"
            tag="${tag#"${tag%%[![:space:]]*}"}"  # trim leading whitespace
            
            if [[ -n "$repo" ]]; then
                # Extract component name from repo path
                local component_name
                component_name=$(basename "$repo")
                
                if [[ "$first" == "true" ]]; then
                    first=false
                else
                    components_json+=","
                fi
                components_json+="\"${component_name}\":{\"repository\":\"${repo}\",\"tag\":\"${tag}\"}"
            fi
            repo=""
            tag=""
        fi
    done < "$VALUES_FILE"
    
    components_json+="}"
    echo "$components_json"
}

# Resolve a latest digest to a concrete (non-latest) tag via Quay API.
# Returns the tag name on stdout, or empty string if no match found.
resolve_concrete_tag() {
    local repo_path="$1"
    local target_digest="$2"

    local api_url="https://quay.io/api/v1/repository/${repo_path}/tag/?limit=50"
    local response
    response=$(curl -sf --connect-timeout 10 --max-time 30 "$api_url" 2>/dev/null) || return 0

    echo "$response" | jq -r --arg digest "$target_digest" \
        '[.tags[] | select(.manifest_digest == $digest and .name != "latest")] | first | .name // empty'
}


# detect-updates mode: detect changes, update component-updates.json, output summary.
# Does NOT modify values.yaml or Chart.yaml - version bumps are left to reviewers.
run_detect_updates() {
    local summary=""
    local has_updates="false"
    local timestamp
    timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    # Load existing component-updates.json or initialize
    local existing_json="{}"
    if [[ -f "$UPDATES_FILE" ]]; then
        existing_json=$(cat "$UPDATES_FILE")
    fi

    # Build new pending_updates array
    local pending_updates="[]"

    while IFS= read -r image; do
        [[ -z "$image" ]] && continue
        [[ "$image" != quay.io/* ]] && continue

        repo_path="${image#quay.io/}"
        cache_file="$CACHE_DIR/${repo_path//\//_}.digest"
        api_url="https://quay.io/api/v1/repository/${repo_path}/tag/?limit=1&specificTag=latest"

        response=$(curl -sf --connect-timeout 10 --max-time 30 "$api_url" 2>/dev/null) || continue
        current_digest=$(echo "$response" | jq -r '.tags[0].manifest_digest // empty')
        [[ -z "$current_digest" ]] && continue

        previous_digest=""
        if [[ -f "$cache_file" ]]; then
            previous_digest=$(cat "$cache_file")
        fi

        # Get current tag in values.yaml
        local current_tag
        current_tag=$(awk -v repo="$image" '
            /repository:/ && index($0, repo) { found=1; next }
            found && /tag:/ { gsub(/.*tag:[ \t]*/, ""); gsub(/["'\'']/, ""); print; found=0 }
        ' "$VALUES_FILE")

        if [[ "$current_digest" != "$previous_digest" ]] || [[ -z "$previous_digest" ]]; then
            local concrete_tag
            concrete_tag=$(resolve_concrete_tag "$repo_path" "$current_digest")

            if [[ -z "$concrete_tag" ]]; then
                log "WARNING: Could not resolve concrete tag for $image (digest: ${current_digest:0:20}...)"
                echo "$current_digest" > "$cache_file"
                continue
            fi

            # Skip if values.yaml already has this tag (manually updated)
            if [[ "$current_tag" == "$concrete_tag" ]]; then
                info "SKIP: $image already at $concrete_tag"
                echo "$current_digest" > "$cache_file"
                continue
            fi

            local component_name
            component_name=$(basename "$image")
            has_updates="true"

            # Add to pending_updates array
            pending_updates=$(echo "$pending_updates" | jq \
                --arg img "$image" \
                --arg old "$current_tag" \
                --arg new "$concrete_tag" \
                --arg digest "$current_digest" \
                --arg ts "$timestamp" \
                '. + [{
                    "image": $img,
                    "current_tag": $old,
                    "latest_tag": $new,
                    "digest": $digest,
                    "detected_at": $ts,
                    "test_status": "pending"
                }]')

            summary="${summary}| ${component_name} | ${current_tag} | ${concrete_tag} |\n"
            log "DETECTED: $image: $current_tag -> $concrete_tag"

            echo "$current_digest" > "$cache_file"
        fi
    done < <(extract_images)

    if [[ "$has_updates" == "true" ]]; then
        # Update component-updates.json
        local new_json
        new_json=$(jq -n \
            --argjson pending "$pending_updates" \
            --arg updated "$timestamp" \
            '{
                "last_check": $updated,
                "pending_updates": $pending,
                "update_history": []
            }')

        # Preserve update_history from existing file if present
        if echo "$existing_json" | jq -e '.update_history' > /dev/null 2>&1; then
            new_json=$(echo "$new_json" | jq \
                --argjson history "$(echo "$existing_json" | jq '.update_history')" \
                '.update_history = $history')
        fi

        echo "$new_json" | jq '.' > "$UPDATES_FILE"
        log "Updated $UPDATES_FILE with ${#pending_updates[@]} pending updates"

        output_var "has_updates" "true"

        local full_summary
        full_summary="| Component | Current Tag | Latest Tag |\n|-----------|-------------|------------|"
        full_summary="${full_summary}\n${summary}"
        output_multiline "updates_summary" "$full_summary"
    else
        output_var "has_updates" "false"
        log "All components are up to date"
    fi
}

# list-versions: iterate images, report current digests and last modified dates
run_list_versions() {
    local output=""

    while IFS= read -r image; do
        [[ -z "$image" ]] && continue
        [[ "$image" != quay.io/* ]] && continue

        repo_path="${image#quay.io/}"
        api_url="https://quay.io/api/v1/repository/${repo_path}/tag/?limit=1&specificTag=latest"

        response=$(curl -sf --connect-timeout 10 --max-time 30 "$api_url" 2>/dev/null) || continue
        current_digest=$(echo "$response" | jq -r '.tags[0].manifest_digest // empty')
        last_modified=$(echo "$response" | jq -r '.tags[0].last_modified // empty')

        [[ -z "$current_digest" ]] && continue

        output="${output}${image}:latest\n  digest: ${current_digest}\n  updated: ${last_modified}\n\n"
    done < <(extract_images)

    get_deployment_info
    echo ""
    output_multiline "versions" "$output"
}

# --- Mode dispatch ---
output_var "mode" "$MODE"

case "$MODE" in
    detect-updates)
        run_detect_updates
        ;;
    deployment-info)
        get_deployment_info
        ;;
    list-versions)
        run_list_versions
        ;;
    *)
        err "Unknown mode: $MODE"
        err "Valid modes: detect-updates, list-versions, deployment-info"
        exit 1
        ;;
esac
