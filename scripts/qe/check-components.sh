#!/usr/bin/env bash
# Check for component updates in values.yaml
# Used by .github/workflows/check-components.yml
#
# Usage: MODE=check-updates ./check-components.sh
#        MODE=list-versions ./check-components.sh
#
# Outputs (via GITHUB_OUTPUT if set):
#   mode, has_updates, updates, versions, digests_json

set -euo pipefail

VALUES_FILE="${VALUES_FILE:-cost-onprem/values.yaml}"
CACHE_DIR="${CACHE_DIR:-.digest-cache}"
MODE="${MODE:-check-updates}"

mkdir -p "$CACHE_DIR"

# Output helpers
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

output=""
has_updates="false"
digests_json="{"
first_digest=true

while IFS= read -r image; do
    [[ -z "$image" ]] && continue
    
    # Only check quay.io images
    if [[ "$image" == quay.io/* ]]; then
        repo_path="${image#quay.io/}"
        cache_file="$CACHE_DIR/${repo_path//\//_}.digest"
        api_url="https://quay.io/api/v1/repository/${repo_path}/tag/?limit=1&specificTag=latest"
        
        response=$(curl -sf "$api_url" 2>/dev/null) || continue
        current_digest=$(echo "$response" | jq -r '.tags[0].manifest_digest // empty')
        last_modified=$(echo "$response" | jq -r '.tags[0].last_modified // empty')
        
        [[ -z "$current_digest" ]] && continue
        
        if [[ "$MODE" == "list-versions" ]]; then
            output="${output}${image}:latest\n  digest: ${current_digest}\n  updated: ${last_modified}\n\n"
        else
            if [[ -f "$cache_file" ]]; then
                previous_digest=$(cat "$cache_file")
                if [[ "$current_digest" != "$previous_digest" ]]; then
                    output="${output}${image}:latest (digest changed)\n"
                    has_updates="true"
                    
                    # Build digests JSON incrementally
                    if [[ "$first_digest" == "true" ]]; then
                        first_digest=false
                    else
                        digests_json+=","
                    fi
                    digests_json+="\"$image\":\"$current_digest\""
                fi
            fi
            echo "$current_digest" > "$cache_file"
        fi
    fi
done < <(extract_images)

digests_json+="}"

# Output results
output_var "mode" "$MODE"

if [[ "$MODE" == "list-versions" ]]; then
    output_multiline "versions" "$output"
elif [[ "$has_updates" == "true" ]]; then
    output_var "has_updates" "true"
    output_multiline "updates" "$output"
    output_var "digests_json" "$digests_json"
else
    output_var "has_updates" "false"
    echo "All components are up to date"
fi
