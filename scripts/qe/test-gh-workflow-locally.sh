#!/bin/bash
# Test GitHub workflows locally using act
# Usage: ./scripts/qe/test-workflow-locally.sh [workflow-file] [-- act-args...]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

WORKFLOW=".github/workflows/check-components.yml"
ACT_ARGS=()

log() { echo "[INFO] $*"; }
error() { echo "[ERROR] $*" >&2; exit 1; }
warn() { echo "[WARN] $*" >&2; }

show_usage() {
    cat <<EOF
Usage: $0 [workflow-file] [-- act-args...]

Arguments:
  workflow-file     Path to workflow file (default: .github/workflows/check-components.yml)
  act-args          Arguments passed directly to act (after --)

Examples:
  $0                                           # Run default workflow
  $0 .github/workflows/lint.yml                # Run specific workflow
  $0 -- --input mode=list-versions             # Pass input to workflow
  $0 -- -n                                     # Dry run
  $0 .github/workflows/check-components.yml -- --input mode=list-versions

EOF
}

# Parse arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -h|--help)
                show_usage
                exit 0
                ;;
            --)
                shift
                ACT_ARGS=("$@")
                break
                ;;
            -*)
                error "Unknown option: $1. Use -- to pass args to act."
                ;;
            *)
                WORKFLOW="$1"
                shift
                ;;
        esac
    done
}

# Detect platform
detect_platform() {
    case "$(uname -s)" in
        Darwin) echo "macos" ;;
        Linux)  echo "linux" ;;
        *)      error "Unsupported platform: $(uname -s)" ;;
    esac
}

# Check if act is installed
check_act() {
    if command -v act &>/dev/null; then
        log "act is installed: $(act --version)"
        return 0
    fi
    return 1
}

# Install act
install_act() {
    local platform
    platform=$(detect_platform)
    
    log "Installing act for $platform..."
    
    case "$platform" in
        macos)
            if command -v brew &>/dev/null; then
                brew install act
            else
                error "Homebrew not found. Install from: https://brew.sh"
            fi
            ;;
        linux)
            # Use official install script for all Linux distros
            curl -s https://raw.githubusercontent.com/nektos/act/master/install.sh | sudo bash
            ;;
    esac
    
    if ! check_act; then
        error "Failed to install act"
    fi
}

# Setup environment
setup_env() {
    # GITHUB_TOKEN: Required for actions/github-script to create issues
    # Generate at: https://github.com/settings/tokens
    # Required scopes: repo (for private repos) or public_repo (for public repos)
    if [[ -z "${GITHUB_TOKEN:-}" ]]; then
        if [[ -f "$HOME/.github_token" ]]; then
            export GITHUB_TOKEN
            GITHUB_TOKEN=$(cat "$HOME/.github_token")
            log "Loaded GITHUB_TOKEN from ~/.github_token"
        elif command -v gh &>/dev/null && gh auth status &>/dev/null 2>&1; then
            export GITHUB_TOKEN
            GITHUB_TOKEN=$(gh auth token)
            log "Loaded GITHUB_TOKEN from gh CLI"
        else
            warn "GITHUB_TOKEN not set. The github-script step will fail."
            warn "Set it via:"
            warn "  export GITHUB_TOKEN=<your-token>"
            warn "  Or save to ~/.github_token"
            warn "  Or authenticate with: gh auth login"
            warn ""
            warn "Generate a token at: https://github.com/settings/tokens"
            warn "Required scopes: repo (private) or public_repo (public)"
        fi
    else
        log "Using GITHUB_TOKEN from environment"
    fi
}

# Run workflow
run_workflow() {
    local workflow_path="$PROJECT_ROOT/$WORKFLOW"
    
    if [[ ! -f "$workflow_path" ]]; then
        error "Workflow not found: $workflow_path"
    fi
    
    log "Running workflow: $WORKFLOW"
    
    cd "$PROJECT_ROOT"
    
    # Build act arguments
    local act_args=(
        "-W" "$WORKFLOW"
        "workflow_dispatch"
    )
    
    # Pass GITHUB_TOKEN if set
    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
        act_args+=("-s" "GITHUB_TOKEN=$GITHUB_TOKEN")
    fi
    
    # Append user-provided args
    if [[ ${#ACT_ARGS[@]} -gt 0 ]]; then
        act_args+=("${ACT_ARGS[@]}")
    fi
    
    log "act ${act_args[*]}"
    act "${act_args[@]}"
}

# Main
main() {
    parse_args "$@"
    
    log "Platform: $(detect_platform)"
    
    if ! check_act; then
        install_act
    fi
    
    setup_env
    run_workflow
}

main "$@"
