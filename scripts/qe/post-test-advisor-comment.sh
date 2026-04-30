#!/usr/bin/env bash
# Post or update a [TEST-ADVISOR] comment on a PR.
# Used by both recommend-tests.yml and check-components.yml workflows.
#
# Usage: PR_NUMBER=123 PROFILE=stable COMPONENT_TABLE="..." ./post-test-advisor-comment.sh
#
# Required environment variables:
#   PR_NUMBER       - The PR number to comment on
#   PROFILE         - Recommended IQE profile (smoke, extended, stable, full)
#   COMPONENT_TABLE - Markdown table of changed components (may be empty)
#   GH_TOKEN        - GitHub token for API access (usually secrets.GITHUB_TOKEN)
#
# Optional:
#   GITHUB_REPOSITORY - Owner/repo (defaults to current repo context)

set -euo pipefail

# --- Logging helpers -------------------------------------------------------

log()  { echo "==> $*"; }
info() { echo "    $*"; }
err()  { echo "ERROR: $*" >&2; }

# --- Validation ------------------------------------------------------------

if [[ -z "${PR_NUMBER:-}" ]]; then
    err "PR_NUMBER is required"
    exit 1
fi

if [[ -z "${PROFILE:-}" ]]; then
    err "PROFILE is required"
    exit 1
fi

if [[ -z "${GH_TOKEN:-}" ]]; then
    err "GH_TOKEN is required"
    exit 1
fi

REPO="${GITHUB_REPOSITORY:-}"
if [[ -z "$REPO" ]]; then
    # Try to detect from git remote
    REPO=$(git remote get-url origin 2>/dev/null | sed -E 's|.*github.com[:/]||; s|\.git$||' || true)
fi

if [[ -z "$REPO" ]]; then
    err "GITHUB_REPOSITORY is required (or must be detectable from git remote)"
    exit 1
fi

TABLE="${COMPONENT_TABLE:-}"

# --- Build comment body ----------------------------------------------------

BODY="[TEST-ADVISOR] This PR includes changes that may benefit from deeper IQE testing beyond the default \`smoke\` profile.

### Changed Components

${TABLE}

**Recommended profile:** \`${PROFILE}\`

<details>
<summary>How to trigger additional tests</summary>

The default \`e2e\` job (smoke profile) runs automatically. To run a deeper profile, post one of these as a **new comment** on this PR:

\`\`\`
/test e2e-iqe-extended
\`\`\`
\`\`\`
/test e2e-iqe-stable
\`\`\`

| Job | Profile | Duration | Use when |
|-----|---------|----------|----------|
| \`e2e\` | smoke | ~17 min | Auto-triggered on all PRs (default) |
| \`e2e-iqe-extended\` | extended | ~33 min | Medium-impact: ingestion, cache, script changes |
| \`e2e-iqe-stable\` | stable | ~40 min | High-impact: koku, postgresql, helm templates |

**Local alternative:**
\`\`\`bash
./scripts/deploy-test-cost-onprem.sh --iqe-only --iqe-profile ${PROFILE}
\`\`\`

</details>"

# --- Post or update comment ------------------------------------------------

log "Checking for existing [TEST-ADVISOR] comment on PR #${PR_NUMBER}"

EXISTING=$(gh api "repos/${REPO}/issues/${PR_NUMBER}/comments" \
    --jq '.[] | select(.body | startswith("[TEST-ADVISOR]")) | .id' 2>/dev/null | head -1 || true)

if [[ -n "$EXISTING" ]]; then
    info "Updating existing comment $EXISTING"
    gh api "repos/${REPO}/issues/comments/${EXISTING}" \
        -X PATCH -f body="$BODY" > /dev/null
    log "Updated existing [TEST-ADVISOR] comment"
else
    info "Posting new comment"
    gh pr comment "$PR_NUMBER" --repo "$REPO" --body "$BODY" > /dev/null
    log "Posted new [TEST-ADVISOR] comment on PR #${PR_NUMBER}"
fi
