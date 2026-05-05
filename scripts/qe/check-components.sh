#!/usr/bin/env bash
# Check for component image updates and prepare release-ready PRs.
# Used by .github/workflows/check-components.yml
#
# Usage: MODE=detect-updates ./check-components.sh
#        MODE=list-versions ./check-components.sh
#        MODE=deployment-info ./check-components.sh
#
# Modes:
#   detect-updates: Check for newer images, update values.yaml + Chart.yaml,
#                   write component-updates.json (default)
#   list-versions:  Report current image tags and digests
#   deployment-info: Output deployment metadata for CI traceability
#
# Outputs (via GITHUB_OUTPUT if set):
#   mode, has_updates, updates_summary
#   For deployment-info mode: helm_chart_version, git_sha, git_branch, etc.
#
# Registry support:
#   quay.io                       — Quay v1 REST API (active)
#   registry.redhat.io            — skopeo inspect (scaffolded, skipped at runtime)
#   registry.access.redhat.com    — skopeo inspect (scaffolded, skipped at runtime)
#
# Expected runtime: 30-60 seconds (Quay API calls)

set -euo pipefail

# --- Constants and defaults ------------------------------------------------

readonly DEFAULT_VALUES_FILE="cost-onprem/values.yaml"
readonly DEFAULT_CHART_FILE="cost-onprem/Chart.yaml"
readonly DEFAULT_CACHE_DIR=".digest-cache"
readonly DEFAULT_UPDATES_FILE="component-updates.json"
readonly DEFAULT_MODE="detect-updates"

VALUES_FILE="${VALUES_FILE:-${DEFAULT_VALUES_FILE}}"
CHART_FILE="${CHART_FILE:-${DEFAULT_CHART_FILE}}"
CACHE_DIR="${CACHE_DIR:-${DEFAULT_CACHE_DIR}}"
UPDATES_FILE="${UPDATES_FILE:-${DEFAULT_UPDATES_FILE}}"
MODE="${MODE:-${DEFAULT_MODE}}"

mkdir -p "$CACHE_DIR"

if [[ ! -f "$VALUES_FILE" ]]; then
    echo "ERROR: Values file not found: $VALUES_FILE" >&2
    exit 1
fi

if [[ ! -f "$CHART_FILE" ]]; then
    echo "ERROR: Chart file not found: $CHART_FILE" >&2
    exit 1
fi

# --- Logging helpers -------------------------------------------------------

log()  { echo "==> $*"; }
info() { echo "    $*"; }
warn() { echo "WARN: $*" >&2; }
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

output_var() {
    local name="$1"
    local value="$2"
    if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
        echo "$name=$value" >> "$GITHUB_OUTPUT"
    else
        echo "$name=$value"
    fi
}

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

# --- Registry detection ----------------------------------------------------

# Determine the registry type from a full image repository URL.
# Args: $1 - image repository (e.g., quay.io/org/repo)
# Returns: "quay", "redhat", or "unknown"
detect_registry() {
    local repo="$1"
    case "$repo" in
        quay.io/*)
            echo "quay"
            ;;
        registry.redhat.io/*|registry.access.redhat.com/*)
            echo "redhat"
            ;;
        *)
            echo "unknown"
            ;;
    esac
}

# --- Quay API functions ----------------------------------------------------

# Get the digest for a specific tag via Quay API.
# Args: $1 - repo path (org/repo), $2 - tag name
# Returns: manifest digest on stdout, or empty string
quay_get_digest() {
    local repo_path="$1"
    local tag="$2"
    local api_url="https://quay.io/api/v1/repository/${repo_path}/tag/?limit=1&specificTag=${tag}"

    local response
    response=$(curl -sf --connect-timeout 10 --max-time 30 "$api_url" 2>/dev/null) || return 0
    echo "$response" | jq -r '.tags[0].manifest_digest // empty'
}

# Resolve a digest to a concrete (non-latest) tag via Quay API.
# Prefers short commit-like tags (e.g., "df40716") over sha256-prefixed or
# attestation tags (.att, .sig, .sbom, .src).
# Args: $1 - repo path (org/repo), $2 - target digest
# Returns: tag name on stdout, or empty string
quay_resolve_tag() {
    local repo_path="$1"
    local target_digest="$2"
    local api_url="https://quay.io/api/v1/repository/${repo_path}/tag/?limit=50"

    local response
    response=$(curl -sf --connect-timeout 10 --max-time 30 "$api_url" 2>/dev/null) || return 0

    # Filter to tags matching the digest, excluding "latest" and attestation artifacts
    # Then prefer shortest tag (commit SHAs are shorter than sha256-prefixed tags)
    echo "$response" | jq -r --arg digest "$target_digest" '
        [.tags[]
         | select(.manifest_digest == $digest
                  and .name != "latest"
                  and (.name | test("\\.(att|sig|sbom|src)$") | not)
                  and (.name | startswith("sha256-") | not))
        ] | sort_by(.name | length) | first | .name // empty'
}

# --- Red Hat Registry functions (scaffolded, not active) -------------------

# Check for updates via skopeo inspect.
# Args: $1 - full image ref (registry.redhat.io/org/image), $2 - current tag
# Returns: new tag on stdout if update available, empty otherwise
# NOTE: Requires skopeo + registry auth. Currently skipped at runtime.
redhat_check_update() {
    local image="$1"
    local current_tag="$2"

    if ! command -v skopeo &>/dev/null; then
        info "SKIP: skopeo not available for $image"
        return 0
    fi

    if [[ -z "${REDHAT_REGISTRY_TOKEN:-}" ]]; then
        info "SKIP: REDHAT_REGISTRY_TOKEN not set for $image"
        return 0
    fi

    # Get digest of current tag
    local current_digest
    current_digest=$(skopeo inspect --creds ":" \
        "docker://${image}:${current_tag}" 2>/dev/null | \
        jq -r '.Digest // empty') || return 0

    # Get digest of latest tag
    local latest_digest
    latest_digest=$(skopeo inspect --creds ":" \
        "docker://${image}:latest" 2>/dev/null | \
        jq -r '.Digest // empty') || return 0

    if [[ -n "$latest_digest" && "$latest_digest" != "$current_digest" ]]; then
        # For Red Hat registry, the "latest" tag IS the concrete tag
        # since they use version-based tags (e.g., "10.1"), not commit SHAs
        echo "latest"
    fi
}

# --- Image extraction ------------------------------------------------------

# Extract all image repository + tag pairs from values.yaml.
# Returns lines of: repo|tag
extract_images() {
    local repo=""
    while IFS= read -r line; do
        if [[ "$line" =~ repository:\ *(.+) ]]; then
            repo="${BASH_REMATCH[1]//\"/}"
            repo="${repo//\'/}"
            repo="${repo#"${repo%%[![:space:]]*}"}"
        elif [[ "$line" =~ tag:\ *(.+) ]]; then
            local tag="${BASH_REMATCH[1]//\"/}"
            tag="${tag//\'/}"
            tag="${tag#"${tag%%[![:space:]]*}"}"
            if [[ -n "$repo" && -n "$tag" ]]; then
                echo "${repo}|${tag}"
            fi
            repo=""
        fi
    done < "$VALUES_FILE"
}

# Extract all component images with their tags from values.yaml as JSON.
extract_all_components() {
    local components_json="{"
    local first=true
    local repo=""
    local tag=""

    while IFS= read -r line; do
        if [[ "$line" =~ repository:\ *(.+) ]]; then
            repo="${BASH_REMATCH[1]//\"/}"
            repo="${repo//\'/}"
            repo="${repo#"${repo%%[![:space:]]*}"}"
        fi
        if [[ "$line" =~ tag:\ *(.+) ]]; then
            tag="${BASH_REMATCH[1]//\"/}"
            tag="${tag//\'/}"
            tag="${tag#"${tag%%[![:space:]]*}"}"
            if [[ -n "$repo" ]]; then
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

# --- values.yaml patching --------------------------------------------------

# Update an image tag in values.yaml for a given repository.
# Args: $1 - full image repository, $2 - new tag
patch_values_tag() {
    local image="$1"
    local new_tag="$2"

    # Match the repository line, then update the next tag: line
    local repo_escaped
    repo_escaped=$(echo "$image" | sed 's/[\/&]/\\&/g')

    if command -v sed &>/dev/null; then
        sed -i.bak "/${repo_escaped}/{n;s/tag:.*/tag: \"${new_tag}\"/;}" "$VALUES_FILE"
        rm -f "${VALUES_FILE}.bak"
    fi

    # Verify the change
    local verify
    verify=$(grep -A1 "$image" "$VALUES_FILE" | grep "tag:" | head -1 | sed 's/.*tag: *//; s/[" ]//g')
    if [[ "$verify" != "$new_tag" ]]; then
        err "Failed to patch $image to $new_tag in $VALUES_FILE (got: $verify)"
        return 1
    fi
}

# --- Chart.yaml version bump -----------------------------------------------

# Increment the RC suffix in Chart.yaml version and appVersion.
# e.g., 0.2.20-rc4 → 0.2.20-rc5, 0.2.20 → 0.2.20-rc1
bump_chart_rc() {
    if [[ ! -f "$CHART_FILE" ]]; then
        err "Chart file not found: $CHART_FILE"
        return 1
    fi

    local current_version
    current_version=$(grep -E "^version:" "$CHART_FILE" | head -1 | awk '{print $2}' | tr -d '"' | tr -d "'")

    local new_version
    if [[ "$current_version" =~ ^(.+)-rc([0-9]+)$ ]]; then
        local base="${BASH_REMATCH[1]}"
        local rc_num="${BASH_REMATCH[2]}"
        new_version="${base}-rc$((rc_num + 1))"
    else
        new_version="${current_version}-rc1"
    fi

    sed -i.bak "s/^version:.*/version: ${new_version}/" "$CHART_FILE"
    sed -i.bak "s/^appVersion:.*/appVersion: \"${new_version}\"/" "$CHART_FILE"
    rm -f "${CHART_FILE}.bak"

    log "Chart version: $current_version → $new_version"
    output_var "chart_version" "$new_version"
}

# --- Deployment info -------------------------------------------------------

get_deployment_info() {
    local helm_chart_version=""
    local deployed_chart_version=""
    local helm_release_name="${HELM_RELEASE_NAME:-cost-onprem}"
    local namespace="${NAMESPACE:-cost-onprem}"
    local git_sha=""
    local git_branch=""
    local git_tag=""
    local deployment_timestamp=""

    if [[ -f "$CHART_FILE" ]]; then
        helm_chart_version=$(grep -E "^version:" "$CHART_FILE" | head -1 | awk '{print $2}' | tr -d '"' | tr -d "'")
    fi

    if command -v helm &> /dev/null; then
        deployed_chart_version=$(helm list -n "$namespace" -o json 2>/dev/null | \
            jq -r --arg name "$helm_release_name" '.[] | select(.name==$name) | .chart' 2>/dev/null | \
            sed 's/.*-//' || echo "")
    fi

    if command -v git &> /dev/null && git rev-parse --git-dir &> /dev/null; then
        git_sha=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
        git_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
        git_tag=$(git describe --tags --exact-match 2>/dev/null || echo "")
        if [[ -n "${GITHUB_SHA:-}" ]]; then
            git_sha="$GITHUB_SHA"
        fi
        if [[ -n "${GITHUB_REF_NAME:-}" ]]; then
            git_branch="$GITHUB_REF_NAME"
        fi
    fi

    deployment_timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    output_var "helm_chart_version" "${helm_chart_version:-unknown}"
    output_var "deployed_chart_version" "${deployed_chart_version:-}"
    output_var "git_sha" "${git_sha:-unknown}"
    output_var "git_sha_short" "${git_sha:0:7}"
    output_var "git_branch" "${git_branch:-unknown}"
    output_var "git_tag" "${git_tag:-}"
    output_var "deployment_timestamp" "$deployment_timestamp"

    echo "=== Deployment Metadata ==="
    echo "Chart Version (source):   ${helm_chart_version:-unknown}"
    [[ -n "$deployed_chart_version" ]] && echo "Chart Version (deployed): $deployed_chart_version"
    echo "Git SHA:                  ${git_sha:-unknown}"
    echo "Git Branch:               ${git_branch:-unknown}"
    [[ -n "$git_tag" ]] && echo "Git Tag:                  $git_tag"
    echo "Timestamp:                $deployment_timestamp"
    echo "==========================="

    local components_json
    components_json=$(extract_all_components)

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

    local version_info_file="${VERSION_INFO_FILE:-version_info.json}"
    echo "$metadata_json" > "$version_info_file"
    echo "Version info written to: $version_info_file"
    output_var "version_info_file" "$version_info_file"
}

# --- detect-updates mode ---------------------------------------------------

run_detect_updates() {
    local summary=""
    local has_updates="false"
    local timestamp
    timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    local pending_updates="[]"

    while IFS='|' read -r image current_tag; do
        [[ -z "$image" ]] && continue

        local registry
        registry=$(detect_registry "$image")

        case "$registry" in
            quay)
                local repo_path="${image#quay.io/}"
                local cache_file="$CACHE_DIR/${repo_path//\//_}.digest"

                local latest_digest
                latest_digest=$(quay_get_digest "$repo_path" "latest")
                [[ -z "$latest_digest" ]] && continue

                local previous_digest=""
                if [[ -f "$cache_file" ]]; then
                    previous_digest=$(cat "$cache_file")
                fi

                if [[ "$latest_digest" != "$previous_digest" ]] || [[ -z "$previous_digest" ]]; then
                    local concrete_tag
                    concrete_tag=$(quay_resolve_tag "$repo_path" "$latest_digest")

                    if [[ -z "$concrete_tag" ]]; then
                        warn "Could not resolve concrete tag for $image (digest: ${latest_digest:0:20}...)"
                        # Don't cache — retry resolution on next run
                        continue
                    fi

                    if [[ "$current_tag" == "$concrete_tag" ]]; then
                        info "SKIP: $image already at $concrete_tag"
                        echo "$latest_digest" > "$cache_file"
                        continue
                    fi

                    local component_name
                    component_name=$(basename "$image")

                    # Patch values.yaml with the new tag
                    if patch_values_tag "$image" "$concrete_tag"; then
                        has_updates="true"
                        log "UPDATED: $image: $current_tag → $concrete_tag"
                    else
                        err "FAILED to patch $image, skipping"
                        continue
                    fi

                    pending_updates=$(echo "$pending_updates" | jq \
                        --arg img "$image" \
                        --arg old "$current_tag" \
                        --arg new "$concrete_tag" \
                        --arg digest "$latest_digest" \
                        --arg ts "$timestamp" \
                        '. + [{
                            "image": $img,
                            "previous_tag": $old,
                            "new_tag": $new,
                            "digest": $digest,
                            "detected_at": $ts
                        }]')

                    summary="${summary}| ${component_name} | ${current_tag} | ${concrete_tag} |\n"
                    echo "$latest_digest" > "$cache_file"
                fi
                ;;
            redhat)
                info "SKIP: Red Hat registry not active — $image:$current_tag"
                ;;
            *)
                info "SKIP: unsupported registry — $image:$current_tag"
                ;;
        esac
    done < <(extract_images)

    if [[ "$has_updates" == "true" ]]; then
        # Bump Chart.yaml RC version
        bump_chart_rc

        # Write component-updates.json (no update_history)
        local new_json
        new_json=$(jq -n \
            --argjson pending "$pending_updates" \
            --arg updated "$timestamp" \
            '{
                "last_check": $updated,
                "pending_updates": $pending
            }')

        echo "$new_json" | jq '.' > "$UPDATES_FILE"
        log "Updated $UPDATES_FILE"

        output_var "has_updates" "true"

        local full_summary
        full_summary="| Component | Previous Tag | New Tag |\n|-----------|-------------|---------|"
        full_summary="${full_summary}\n${summary}"
        output_multiline "updates_summary" "$full_summary"
    else
        output_var "has_updates" "false"
        log "All components are up to date"
    fi
}

# --- list-versions mode ----------------------------------------------------

run_list_versions() {
    local output=""

    while IFS='|' read -r image tag; do
        [[ -z "$image" ]] && continue

        local registry
        registry=$(detect_registry "$image")
        local component_name
        component_name=$(basename "$image")

        case "$registry" in
            quay)
                local repo_path="${image#quay.io/}"
                local digest
                digest=$(quay_get_digest "$repo_path" "$tag")
                output="${output}${component_name} (${image}:${tag})\n  digest: ${digest:-unknown}\n\n"
                ;;
            redhat)
                output="${output}${component_name} (${image}:${tag})\n  registry: redhat (skopeo check not active)\n\n"
                ;;
            *)
                output="${output}${component_name} (${image}:${tag})\n  registry: unknown\n\n"
                ;;
        esac
    done < <(extract_images)

    get_deployment_info
    echo ""
    output_multiline "versions" "$output"
}

# --- Mode dispatch ---------------------------------------------------------

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
