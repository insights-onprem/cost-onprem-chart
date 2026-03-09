#!/bin/bash

# kessel-admin.sh - Kessel authorization management for Cost Management
#
# Bridges identity (Keycloak) and authorization (Kessel) by creating
# the tuples that connect principals to roles via role_bindings and
# workspaces.  Implements an OPT-IN access model: users see nothing
# until explicitly granted access to a team workspace.
#
# Resource-level tuples (clusters, projects, integrations) are created
# automatically by Koku's ingestion pipeline when data is processed.
# This script handles the identity-level tuples that grant users access.
#
# Access Model (opt-in):
#   - Org admins are bound at the org workspace → see all resources
#   - Regular users are bound at team workspaces → see only resources
#     assigned to their team workspace(s)
#   - Resources land in the org workspace via Koku's resource_reporter
#   - Admin assigns resources to team workspaces for scoped visibility
#
# Operations:
#   bootstrap                                One-shot setup: seed roles + sync admin users
#   seed-roles                               Seed role permission tuples from seed-roles.yaml
#   sync                                     Sync admin users → Kessel (org-level bindings only)
#   grant    <user> <role> <workspace_id>    Grant a role to a user at a workspace
#   revoke   <user> <role> <workspace_id>    Revoke a role from a user at a workspace
#   check    <user> <perm> <workspace_id>    Check if user has permission at workspace
#   create-workspace  <ws_id> <parent_id>    Create a team workspace under a parent
#   delete-workspace  <ws_id> <parent_id>    Delete a team workspace
#   assign-resource   <type> <id> <ws_id>    Assign a resource to a workspace
#   unassign-resource <type> <id> <ws_id>    Remove a resource from a workspace
#   link-resource  <ptype> <pid> <rel> <ctype> <cid>  Create structural relationship
#   add-group-member    <group_id> <username>          Add user to group
#   remove-group-member <group_id> <username>          Remove user from group
#   grant-group  <group_id> <role> <ws_id>   Grant role to group at workspace
#   revoke-group <group_id> <role> <ws_id>   Revoke role from group at workspace
#   list-users                               List Keycloak users with their org_id
#   status                                   Show current Kessel connectivity and sample check
#   demo                                     Set up a complete opt-in demo scenario
#
# Prerequisites:
#   - curl, jq, python3 (with PyYAML for seed-roles)
#   - oc (logged into the cluster, only for auto-detection when env vars are not set)
#
# Environment Variables:
#   RELATIONS_URL       Relations API HTTP base URL (default: auto-detect)
#                       e.g. http://kessel-relations.kessel.svc.cluster.local:8000
#   KEYCLOAK_URL        Keycloak admin URL (default: auto-detect from route)
#   KEYCLOAK_REALM      Realm name (default: kubernetes)
#   KEYCLOAK_ADMIN      Admin username (default: admin)
#   KEYCLOAK_PASSWORD   Admin password (default: auto-detect from secret)
#   KESSEL_NAMESPACE    Namespace where Kessel runs (default: kessel)
#   KEYCLOAK_NAMESPACE  Namespace where Keycloak runs (default: keycloak)
#   DEFAULT_ROLE        Default role slug for admin sync (default: cost-administrator)
#   ADMIN_USERS         Comma-separated admin usernames for org-level binding
#                       (default: empty — all Keycloak users get org-level admin)
#   RESOURCE_NAMESPACE  Kessel resource namespace (default: cost_management)
#
# Examples:
#   ./kessel-admin.sh bootstrap
#   ./kessel-admin.sh create-workspace team-infra org1234567
#   ./kessel-admin.sh assign-resource openshift_cluster cluster-1 team-infra
#   ./kessel-admin.sh grant alice cost-openshift-viewer team-infra
#   ./kessel-admin.sh check alice cost_management_openshift_cluster_read team-infra
#   ./kessel-admin.sh demo

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

KESSEL_NAMESPACE=${KESSEL_NAMESPACE:-kessel}
KEYCLOAK_NAMESPACE=${KEYCLOAK_NAMESPACE:-keycloak}
KEYCLOAK_REALM=${KEYCLOAK_REALM:-kubernetes}
KEYCLOAK_ADMIN=${KEYCLOAK_ADMIN:-admin}
KEYCLOAK_PASSWORD=${KEYCLOAK_PASSWORD:-}
RELATIONS_URL=${RELATIONS_URL:-}
KEYCLOAK_URL=${KEYCLOAK_URL:-}
DEFAULT_ROLE=${DEFAULT_ROLE:-cost-administrator}
ADMIN_USERS=${ADMIN_USERS:-}
RESOURCE_NAMESPACE=${RESOURCE_NAMESPACE:-cost_management}
RELATIONS_API_PREFIX="/api/authz"

log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1" >&2; }

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
check_tools() {
    local missing=()
    for tool in jq curl; do
        if ! command -v "$tool" >/dev/null 2>&1; then
            missing+=("$tool")
        fi
    done
    local need_oc=false
    if [ -z "$KEYCLOAK_URL" ] || [ -z "$KEYCLOAK_PASSWORD" ]; then
        need_oc=true
    fi
    if [ -z "$RELATIONS_URL" ]; then
        need_oc=true
    fi
    if $need_oc && ! command -v oc >/dev/null 2>&1; then
        missing+=("oc (or set RELATIONS_URL, KEYCLOAK_URL, KEYCLOAK_PASSWORD)")
    fi
    if [ ${#missing[@]} -gt 0 ]; then
        log_error "Missing required tools: ${missing[*]}"
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Auto-detect endpoints
# ---------------------------------------------------------------------------
detect_relations_url() {
    if [ -n "$RELATIONS_URL" ]; then
        return
    fi
    local in_cluster_host="kessel-relations.${KESSEL_NAMESPACE}.svc.cluster.local"
    if getent hosts "$in_cluster_host" >/dev/null 2>&1 || \
       nslookup "$in_cluster_host" >/dev/null 2>&1; then
        RELATIONS_URL="http://${in_cluster_host}:8000"
        log_success "Using in-cluster Relations API: $RELATIONS_URL"
        return
    fi
    log_info "No RELATIONS_URL set, starting port-forward to Relations API (HTTP)..."
    oc port-forward -n "$KESSEL_NAMESPACE" svc/kessel-relations 8000:8000 &>/dev/null &
    PORT_FORWARD_PID=$!
    sleep 2
    if ! kill -0 "$PORT_FORWARD_PID" 2>/dev/null; then
        log_error "Failed to port-forward to kessel-relations"
        exit 1
    fi
    RELATIONS_URL="http://localhost:8000"
    trap 'kill $PORT_FORWARD_PID 2>/dev/null || true' EXIT
    log_success "Port-forward active: $RELATIONS_URL (PID $PORT_FORWARD_PID)"
}

detect_keycloak() {
    if [ -n "$KEYCLOAK_URL" ]; then
        return
    fi
    KEYCLOAK_URL="https://$(oc get route keycloak -n "$KEYCLOAK_NAMESPACE" -o jsonpath='{.spec.host}' 2>/dev/null)"
    if [ -z "$KEYCLOAK_URL" ] || [ "$KEYCLOAK_URL" = "https://" ]; then
        log_error "Cannot detect Keycloak URL. Set KEYCLOAK_URL."
        exit 1
    fi
    log_info "Detected Keycloak: $KEYCLOAK_URL"
}

detect_keycloak_password() {
    if [ -n "$KEYCLOAK_PASSWORD" ]; then
        return
    fi
    KEYCLOAK_PASSWORD=$(oc get secret keycloak-initial-admin -n "$KEYCLOAK_NAMESPACE" \
        -o jsonpath='{.data.password}' 2>/dev/null | base64 -d 2>/dev/null || true)
    if [ -z "$KEYCLOAK_PASSWORD" ]; then
        log_error "Cannot detect Keycloak admin password. Set KEYCLOAK_PASSWORD."
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Keycloak admin API helpers
# ---------------------------------------------------------------------------
keycloak_token() {
    local token
    token=$(curl -sk -X POST "$KEYCLOAK_URL/realms/master/protocol/openid-connect/token" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "username=$KEYCLOAK_ADMIN" \
        -d "password=$KEYCLOAK_PASSWORD" \
        -d "grant_type=password" \
        -d "client_id=admin-cli" 2>/dev/null | jq -r '.access_token // empty')
    if [ -z "$token" ]; then
        log_error "Failed to obtain Keycloak admin token"
        exit 1
    fi
    echo "$token"
}

keycloak_list_users() {
    local token
    token=$(keycloak_token)
    curl -sk "$KEYCLOAK_URL/admin/realms/$KEYCLOAK_REALM/users?max=500" \
        -H "Authorization: Bearer $token" 2>/dev/null
}

# ---------------------------------------------------------------------------
# Kessel Relations API REST helpers (HTTP, no auth required)
#
# The Relations API applies a path prefix from its config (default /api/authz).
# Endpoints (gRPC-Gateway on port 8000):
#   POST   /api/authz/v1beta1/tuples   - CreateTuples  (body: {upsert, tuples})
#   DELETE /api/authz/v1beta1/tuples   - DeleteTuples  (query: filter.*)
#   POST   /api/authz/v1beta1/check    - Check         (body: {resource, relation, subject})
#   GET    /api/authz/readyz            - Health check
#
# NOTE: ReadTuples (GET /v1beta1/tuples) is a streaming RPC -- the Kratos
#       HTTP layer does not register a route for it, so it always 404s.
# ---------------------------------------------------------------------------
relations_api_call() {
    local method="$1"; shift
    local url="$1"; shift
    local tmpfile
    tmpfile=$(mktemp)
    local http_code
    http_code=$(curl -s -o "$tmpfile" -w "%{http_code}" -X "$method" "$url" "$@" 2>&1)
    local body
    body=$(cat "$tmpfile")
    rm -f "$tmpfile"
    if [[ "$http_code" -ge 200 && "$http_code" -lt 300 ]]; then
        echo "$body"
        return 0
    fi
    log_error "Relations API ${method} ${url} returned HTTP ${http_code}"
    log_error "Response: ${body}"
    return 1
}

relations_create_tuples() {
    local payload="$1"
    relations_api_call POST "${RELATIONS_URL}${RELATIONS_API_PREFIX}/v1beta1/tuples" \
        -H "Content-Type: application/json" \
        -d "$payload"
}

relations_delete_tuple() {
    local res_ns="$1" res_type="$2" res_id="$3" relation="$4"
    local subj_ns="$5" subj_type="$6" subj_id="$7"
    local url="${RELATIONS_URL}${RELATIONS_API_PREFIX}/v1beta1/tuples"
    relations_api_call DELETE "$url" -G \
        --data-urlencode "filter.resourceNamespace=${res_ns}" \
        --data-urlencode "filter.resourceType=${res_type}" \
        --data-urlencode "filter.resourceId=${res_id}" \
        --data-urlencode "filter.relation=${relation}" \
        --data-urlencode "filter.subjectFilter.subjectNamespace=${subj_ns}" \
        --data-urlencode "filter.subjectFilter.subjectType=${subj_type}" \
        --data-urlencode "filter.subjectFilter.subjectId=${subj_id}"
}

relations_check() {
    local payload="$1"
    relations_api_call POST "${RELATIONS_URL}${RELATIONS_API_PREFIX}/v1beta1/check" \
        -H "Content-Type: application/json" \
        -d "$payload"
}

# ---------------------------------------------------------------------------
# Tuple builders
#
# Each builder returns a JSON array of Relationship objects suitable for
# CreateTuples.  Relation names match schema.zed exactly (t_ prefix is
# part of the schema, NOT added by the Relations API).
# ---------------------------------------------------------------------------

# Org workspace structure: workspace:{org_id}#t_parent → tenant:{org_id}
build_org_workspace_tuples() {
    local org_id="$1"
    cat <<EOF
[
  {"resource":{"type":{"namespace":"rbac","name":"workspace"},"id":"$org_id"},"relation":"t_parent","subject":{"subject":{"type":{"namespace":"rbac","name":"tenant"},"id":"$org_id"}}}
]
EOF
}

# Team workspace: workspace:{ws_id}#t_parent → workspace:{parent_id}
build_team_workspace_tuples() {
    local workspace_id="$1" parent_id="$2"
    cat <<EOF
[
  {"resource":{"type":{"namespace":"rbac","name":"workspace"},"id":"$workspace_id"},"relation":"t_parent","subject":{"subject":{"type":{"namespace":"rbac","name":"workspace"},"id":"$parent_id"}}}
]
EOF
}

# Grant: role_binding → workspace-level binding (3 tuples).
# Works for both org workspaces and team workspaces.
build_grant_tuples() {
    local username="$1" role_slug="$2" workspace_id="$3"
    local rb_id="${workspace_id}--${username}--${role_slug}"

    cat <<EOF
[
  {"resource":{"type":{"namespace":"rbac","name":"role_binding"},"id":"$rb_id"},"relation":"t_granted","subject":{"subject":{"type":{"namespace":"rbac","name":"role"},"id":"$role_slug"}}},
  {"resource":{"type":{"namespace":"rbac","name":"role_binding"},"id":"$rb_id"},"relation":"t_subject","subject":{"subject":{"type":{"namespace":"rbac","name":"principal"},"id":"redhat/$username"}}},
  {"resource":{"type":{"namespace":"rbac","name":"workspace"},"id":"$workspace_id"},"relation":"t_binding","subject":{"subject":{"type":{"namespace":"rbac","name":"role_binding"},"id":"$rb_id"}}}
]
EOF
}

# Org-admin: additional tenant-level binding for org-wide visibility.
# Call AFTER build_grant_tuples for the org workspace.
build_org_admin_tuples() {
    local username="$1" role_slug="$2" org_id="$3"
    local rb_id="${org_id}--${username}--${role_slug}"

    cat <<EOF
[
  {"resource":{"type":{"namespace":"rbac","name":"tenant"},"id":"$org_id"},"relation":"t_binding","subject":{"subject":{"type":{"namespace":"rbac","name":"role_binding"},"id":"$rb_id"}}}
]
EOF
}

# Resource → workspace assignment: {ns}/{type}:{id}#t_workspace → workspace:{ws_id}
build_resource_workspace_tuples() {
    local resource_type="$1" resource_id="$2" workspace_id="$3"
    cat <<EOF
[
  {"resource":{"type":{"namespace":"$RESOURCE_NAMESPACE","name":"$resource_type"},"id":"$resource_id"},"relation":"t_workspace","subject":{"subject":{"type":{"namespace":"rbac","name":"workspace"},"id":"$workspace_id"}}}
]
EOF
}

# Group membership: group:{id}#t_member → principal:redhat/{username}
build_group_member_tuples() {
    local group_id="$1" username="$2"
    cat <<EOF
[
  {"resource":{"type":{"namespace":"rbac","name":"group"},"id":"$group_id"},"relation":"t_member","subject":{"subject":{"type":{"namespace":"rbac","name":"principal"},"id":"redhat/$username"}}}
]
EOF
}

# Group grant: role_binding → workspace-level binding with group as subject.
# Uses group#member (the permission) as the subject reference, per schema:
#   relation t_subject: rbac/principal | rbac/group#member
build_group_grant_tuples() {
    local group_id="$1" role_slug="$2" workspace_id="$3"
    local rb_id="${workspace_id}--grp-${group_id}--${role_slug}"

    cat <<EOF
[
  {"resource":{"type":{"namespace":"rbac","name":"role_binding"},"id":"$rb_id"},"relation":"t_granted","subject":{"subject":{"type":{"namespace":"rbac","name":"role"},"id":"$role_slug"}}},
  {"resource":{"type":{"namespace":"rbac","name":"role_binding"},"id":"$rb_id"},"relation":"t_subject","subject":{"subject":{"type":{"namespace":"rbac","name":"group"},"id":"$group_id"},"relation":"member"}},
  {"resource":{"type":{"namespace":"rbac","name":"workspace"},"id":"$workspace_id"},"relation":"t_binding","subject":{"subject":{"type":{"namespace":"rbac","name":"role_binding"},"id":"$rb_id"}}}
]
EOF
}

# Structural relationship: parent_type:parent_id#relation → child_type:child_id
# Used for has_project, has_cluster, etc.
build_structural_tuples() {
    local parent_type="$1" parent_id="$2" relation="$3" child_type="$4" child_id="$5"
    cat <<EOF
[
  {"resource":{"type":{"namespace":"$RESOURCE_NAMESPACE","name":"$parent_type"},"id":"$parent_id"},"relation":"$relation","subject":{"subject":{"type":{"namespace":"$RESOURCE_NAMESPACE","name":"$child_type"},"id":"$child_id"}}}
]
EOF
}

# ---------------------------------------------------------------------------
# Seed role permission tuples from seed-roles.yaml
#
# Each role needs tuples like:
#   rbac/role:{slug}#{kessel_relation}@rbac/principal:*
#
# This is normally done by Koku's kessel_seed_roles migration job, but
# this command provides a standalone fallback using the Relations API.
# ---------------------------------------------------------------------------
do_seed_roles() {
    local seed_file=""
    local script_dir
    script_dir="$(cd "$(dirname "$0")" && pwd)"

    for candidate in \
        "$script_dir/seed-roles.yaml" \
        "$script_dir/../cost-onprem/files/seed-roles.yaml" \
        "$script_dir/kessel/seed-roles.yaml" \
        "$script_dir/../../koku/dev/kessel/seed-roles.yaml"; do
        if [ -f "$candidate" ]; then
            seed_file="$candidate"
            break
        fi
    done

    if [ -z "$seed_file" ]; then
        log_error "Cannot find seed-roles.yaml. Place it in scripts/kessel/ or cost-onprem/files/"
        exit 1
    fi

    log_info "Seeding roles from $seed_file via Relations API"

    local tuples="[]"
    local role_count=0

    while IFS= read -r slug; do
        local relations
        relations=$(python3 -c "
import yaml, sys
with open('$seed_file') as f:
    data = yaml.safe_load(f)
for role in data['roles']:
    if role['slug'] == '$slug':
        for p in role['permissions']:
            print(p['kessel_relation'])
        break
" 2>/dev/null)

        if [ -z "$relations" ]; then
            log_warning "No permissions found for role '$slug'"
            continue
        fi

        while IFS= read -r relation; do
            tuples=$(echo "$tuples" | jq --arg slug "$slug" --arg rel "${relation}" \
                '. + [{
                    "resource": {"type": {"namespace": "rbac", "name": "role"}, "id": $slug},
                    "relation": $rel,
                    "subject": {"subject": {"type": {"namespace": "rbac", "name": "principal"}, "id": "*"}}
                }]')
        done <<< "$relations"

        role_count=$((role_count + 1))
        log_info "  role '$slug': $(echo "$relations" | wc -l | tr -d ' ') permissions"
    done < <(python3 -c "
import yaml
with open('$seed_file') as f:
    data = yaml.safe_load(f)
for role in data['roles']:
    print(role['slug'])
" 2>/dev/null)

    local tuple_count
    tuple_count=$(echo "$tuples" | jq 'length')

    if [ "$tuple_count" -eq 0 ]; then
        log_error "No tuples generated from seed file"
        exit 1
    fi

    log_info "Writing $tuple_count permission tuples for $role_count roles..."
    local payload
    payload=$(jq -n --argjson tuples "$tuples" '{"upsert": true, "tuples": $tuples}')
    relations_create_tuples "$payload"
    log_success "Seeded $role_count roles ($tuple_count tuples)"
}

# ---------------------------------------------------------------------------
# Bootstrap: seed roles + create org workspace + sync admin users
# ---------------------------------------------------------------------------
do_bootstrap() {
    log_info "=== Kessel Authorization Bootstrap (opt-in model) ==="
    log_info ""
    log_info "Step 1/3: Seeding role permission tuples..."
    do_seed_roles
    log_info ""
    log_info "Step 2/3: Creating org workspace structures..."
    ensure_org_workspaces
    log_info ""
    log_info "Step 3/3: Syncing admin users to Kessel (org-level binding)..."
    do_sync
    log_info ""
    log_success "=== Bootstrap complete (opt-in model) ==="
    log_info ""
    log_info "Next steps (opt-in access):"
    log_info "  1. Create team workspaces:"
    log_info "     $0 create-workspace team-infra <org_id>"
    log_info "  2. Assign resources to team workspaces:"
    log_info "     $0 assign-resource openshift_cluster <cluster_id> team-infra"
    log_info "  3. Grant users access to team workspaces:"
    log_info "     $0 grant <user> cost-openshift-viewer team-infra"
    log_info ""
    log_info "Or run '$0 demo' for a complete example scenario."
}

# ---------------------------------------------------------------------------
# Ensure org workspace structures exist for all known orgs
# ---------------------------------------------------------------------------
ensure_org_workspaces() {
    local users_json
    users_json=$(keycloak_list_users)

    local org_ids
    org_ids=$(echo "$users_json" | jq -r '.[].attributes.org_id[0] // empty' | sort -u)

    if [ -z "$org_ids" ]; then
        log_warning "No org_ids found in Keycloak users"
        return
    fi

    local all_tuples="[]"
    while IFS= read -r org_id; do
        [ -z "$org_id" ] && continue
        local org_tuples
        org_tuples=$(build_org_workspace_tuples "$org_id")
        all_tuples=$(echo "$all_tuples" | jq --argjson t "$org_tuples" '. + $t')
        log_info "  Org workspace: $org_id"
    done <<< "$org_ids"

    local tuple_count
    tuple_count=$(echo "$all_tuples" | jq 'length')
    if [ "$tuple_count" -gt 0 ]; then
        local payload
        payload=$(jq -n --argjson tuples "$all_tuples" '{"upsert": true, "tuples": $tuples}')
        relations_create_tuples "$payload"
        log_success "Created $tuple_count org workspace structure(s)"
    fi
}

# ---------------------------------------------------------------------------
# Grant a role to a user at a workspace
#
# Creates the authorization chain:
#   1. role_binding:{rb_id} --t_granted--> role:{role_slug}
#   2. role_binding:{rb_id} --t_subject--> principal:{username}
#   3. workspace:{ws_id}    --t_binding--> role_binding:{rb_id}
# ---------------------------------------------------------------------------
do_grant() {
    local username="$1" role_slug="$2" workspace_id="$3"
    local rb_id="${workspace_id}--${username}--${role_slug}"

    log_info "Granting role '$role_slug' to '$username' at workspace '$workspace_id'"
    log_info "  role_binding ID: $rb_id"

    local tuples
    tuples=$(build_grant_tuples "$username" "$role_slug" "$workspace_id")
    local payload
    payload=$(jq -n --argjson tuples "$tuples" '{"upsert": true, "tuples": $tuples}')
    relations_create_tuples "$payload"

    log_success "Granted '$role_slug' to '$username' at workspace '$workspace_id'"
}

# ---------------------------------------------------------------------------
# Grant a role to a user at the org level (admin — sees all resources)
#
# Same as do_grant, plus a tenant-level binding for org-wide visibility.
# ---------------------------------------------------------------------------
do_grant_admin() {
    local username="$1" role_slug="$2" org_id="$3"
    local rb_id="${org_id}--${username}--${role_slug}"

    log_info "Granting ADMIN role '$role_slug' to '$username' at org '$org_id'"

    local tuples
    tuples=$(build_grant_tuples "$username" "$role_slug" "$org_id")
    local admin_tuples
    admin_tuples=$(build_org_admin_tuples "$username" "$role_slug" "$org_id")
    tuples=$(echo "$tuples" | jq --argjson t "$admin_tuples" '. + $t')

    local payload
    payload=$(jq -n --argjson tuples "$tuples" '{"upsert": true, "tuples": $tuples}')
    relations_create_tuples "$payload"

    log_success "Granted ADMIN '$role_slug' to '$username' at org '$org_id'"
}

# ---------------------------------------------------------------------------
# Revoke a role from a user at a workspace
# ---------------------------------------------------------------------------
do_revoke() {
    local username="$1" role_slug="$2" workspace_id="$3"
    local rb_id="${workspace_id}--${username}--${role_slug}"

    log_info "Revoking role '$role_slug' from '$username' at workspace '$workspace_id'"

    relations_delete_tuple "rbac" "role_binding" "$rb_id" "t_granted" \
        "rbac" "role" "$role_slug"
    relations_delete_tuple "rbac" "role_binding" "$rb_id" "t_subject" \
        "rbac" "principal" "redhat/$username"
    relations_delete_tuple "rbac" "workspace" "$workspace_id" "t_binding" \
        "rbac" "role_binding" "$rb_id"
    # Also remove tenant-level binding if it exists (safe — no-op if absent)
    relations_delete_tuple "rbac" "tenant" "$workspace_id" "t_binding" \
        "rbac" "role_binding" "$rb_id" 2>/dev/null || true

    log_success "Revoked '$role_slug' from '$username' at workspace '$workspace_id'"
}

# ---------------------------------------------------------------------------
# Check a user's permission at a workspace
# ---------------------------------------------------------------------------
do_check() {
    local username="$1" permission="$2" workspace_id="$3"

    log_info "Checking '$permission' for '$username' at workspace '$workspace_id'"

    local payload
    payload=$(cat <<EOF
{
    "resource": {
        "type": {"namespace": "rbac", "name": "workspace"},
        "id": "$workspace_id"
    },
    "relation": "$permission",
    "subject": {
        "subject": {
            "type": {"namespace": "rbac", "name": "principal"},
            "id": "redhat/$username"
        }
    }
}
EOF
    )

    local result
    result=$(relations_check "$payload")

    local allowed
    allowed=$(echo "$result" | jq -r '.allowed // "ALLOWED_UNSPECIFIED"')

    if [ "$allowed" = "ALLOWED_TRUE" ]; then
        log_success "ALLOWED: '$username' has '$permission' at workspace '$workspace_id'"
    else
        log_warning "DENIED: '$username' does NOT have '$permission' at workspace '$workspace_id'"
        echo "$result" | jq . 2>/dev/null || echo "$result"
    fi
}

# ---------------------------------------------------------------------------
# Create a team workspace under a parent workspace (or org workspace)
# ---------------------------------------------------------------------------
do_create_workspace() {
    local workspace_id="$1" parent_id="$2"

    log_info "Creating workspace '$workspace_id' under parent '$parent_id'"

    local tuples
    tuples=$(build_team_workspace_tuples "$workspace_id" "$parent_id")
    local payload
    payload=$(jq -n --argjson tuples "$tuples" '{"upsert": true, "tuples": $tuples}')
    relations_create_tuples "$payload"

    log_success "Created workspace '$workspace_id' (parent: '$parent_id')"
}

# ---------------------------------------------------------------------------
# Delete a team workspace
# ---------------------------------------------------------------------------
do_delete_workspace() {
    local workspace_id="$1" parent_id="$2"

    log_info "Deleting workspace '$workspace_id' (parent: '$parent_id')"

    relations_delete_tuple "rbac" "workspace" "$workspace_id" "t_parent" \
        "rbac" "workspace" "$parent_id"

    log_success "Deleted workspace '$workspace_id'"
    log_warning "Note: role_bindings and resource assignments for this workspace are now orphaned."
    log_warning "Consider revoking grants and unassigning resources first."
}

# ---------------------------------------------------------------------------
# Assign a resource to a (team) workspace
# ---------------------------------------------------------------------------
do_assign_resource() {
    local resource_type="$1" resource_id="$2" workspace_id="$3"

    log_info "Assigning ${RESOURCE_NAMESPACE}/${resource_type}:${resource_id} to workspace '$workspace_id'"

    local tuples
    tuples=$(build_resource_workspace_tuples "$resource_type" "$resource_id" "$workspace_id")
    local payload
    payload=$(jq -n --argjson tuples "$tuples" '{"upsert": true, "tuples": $tuples}')
    relations_create_tuples "$payload"

    log_success "Assigned ${RESOURCE_NAMESPACE}/${resource_type}:${resource_id} → workspace:${workspace_id}"
}

# ---------------------------------------------------------------------------
# Remove a resource from a workspace
# ---------------------------------------------------------------------------
do_unassign_resource() {
    local resource_type="$1" resource_id="$2" workspace_id="$3"

    log_info "Unassigning ${RESOURCE_NAMESPACE}/${resource_type}:${resource_id} from workspace '$workspace_id'"

    relations_delete_tuple "$RESOURCE_NAMESPACE" "$resource_type" "$resource_id" "t_workspace" \
        "rbac" "workspace" "$workspace_id"

    log_success "Unassigned ${RESOURCE_NAMESPACE}/${resource_type}:${resource_id} from workspace:${workspace_id}"
}

# ---------------------------------------------------------------------------
# Add a user to a group
# ---------------------------------------------------------------------------
do_add_group_member() {
    local group_id="$1" username="$2"

    log_info "Adding '$username' to group '$group_id'"

    local tuples
    tuples=$(build_group_member_tuples "$group_id" "$username")
    local payload
    payload=$(jq -n --argjson tuples "$tuples" '{"upsert": true, "tuples": $tuples}')
    relations_create_tuples "$payload"

    log_success "Added '$username' to group '$group_id'"
}

# ---------------------------------------------------------------------------
# Remove a user from a group
# ---------------------------------------------------------------------------
do_remove_group_member() {
    local group_id="$1" username="$2"

    log_info "Removing '$username' from group '$group_id'"

    relations_delete_tuple "rbac" "group" "$group_id" "t_member" \
        "rbac" "principal" "redhat/$username"

    log_success "Removed '$username' from group '$group_id'"
}

# ---------------------------------------------------------------------------
# Grant a role to a group at a workspace
# ---------------------------------------------------------------------------
do_grant_group() {
    local group_id="$1" role_slug="$2" workspace_id="$3"

    log_info "Granting role '$role_slug' to group '$group_id' at workspace '$workspace_id'"

    local tuples
    tuples=$(build_group_grant_tuples "$group_id" "$role_slug" "$workspace_id")
    local payload
    payload=$(jq -n --argjson tuples "$tuples" '{"upsert": true, "tuples": $tuples}')
    relations_create_tuples "$payload"

    log_success "Granted '$role_slug' to group '$group_id' at workspace '$workspace_id'"
}

# ---------------------------------------------------------------------------
# Revoke a role from a group at a workspace
# ---------------------------------------------------------------------------
do_revoke_group() {
    local group_id="$1" role_slug="$2" workspace_id="$3"
    local rb_id="${workspace_id}--grp-${group_id}--${role_slug}"

    log_info "Revoking role '$role_slug' from group '$group_id' at workspace '$workspace_id'"

    relations_delete_tuple "rbac" "role_binding" "$rb_id" "t_granted" \
        "rbac" "role" "$role_slug"
    relations_delete_tuple "rbac" "role_binding" "$rb_id" "t_subject" \
        "rbac" "group" "$group_id"
    relations_delete_tuple "rbac" "workspace" "$workspace_id" "t_binding" \
        "rbac" "role_binding" "$rb_id"

    log_success "Revoked '$role_slug' from group '$group_id' at workspace '$workspace_id'"
}

# ---------------------------------------------------------------------------
# Create a structural relationship between resources (e.g. has_project)
# ---------------------------------------------------------------------------
do_link_resource() {
    local parent_type="$1" parent_id="$2" relation="$3" child_type="$4" child_id="$5"

    log_info "Linking ${RESOURCE_NAMESPACE}/${parent_type}:${parent_id}#${relation} → ${RESOURCE_NAMESPACE}/${child_type}:${child_id}"

    local tuples
    tuples=$(build_structural_tuples "$parent_type" "$parent_id" "$relation" "$child_type" "$child_id")
    local payload
    payload=$(jq -n --argjson tuples "$tuples" '{"upsert": true, "tuples": $tuples}')
    relations_create_tuples "$payload"

    log_success "Linked ${parent_type}:${parent_id}#${relation} → ${child_type}:${child_id}"
}

# ---------------------------------------------------------------------------
# Sync admin users → Kessel (org-level bindings only)
#
# If ADMIN_USERS is set, only those users get org-level admin binding.
# If ADMIN_USERS is empty, ALL Keycloak users get org-level admin binding
# (backward-compatible flat mode for clusters not yet using team workspaces).
# ---------------------------------------------------------------------------
do_sync() {
    log_info "Syncing Keycloak users from realm '$KEYCLOAK_REALM' to Kessel..."

    local users_json
    users_json=$(keycloak_list_users)

    local admin_list=""
    if [ -n "$ADMIN_USERS" ]; then
        admin_list=",$ADMIN_USERS,"
        log_info "ADMIN_USERS set — only admin users get org-level binding"
    else
        log_info "ADMIN_USERS not set — all users get org-level binding (flat mode)"
    fi

    local all_tuples="[]"
    local admin_count=0
    local skipped=0

    while IFS= read -r user; do
        local username org_id
        username=$(echo "$user" | jq -r '.username')
        org_id=$(echo "$user" | jq -r '.attributes.org_id[0] // empty')

        if [ -z "$org_id" ]; then
            log_warning "Skipping '$username': no org_id attribute"
            skipped=$((skipped + 1))
            continue
        fi

        local is_admin=false
        if [ -z "$ADMIN_USERS" ]; then
            is_admin=true
        elif [[ "$admin_list" == *",$username,"* ]]; then
            is_admin=true
        fi

        if $is_admin; then
            local user_tuples
            user_tuples=$(build_grant_tuples "$username" "$DEFAULT_ROLE" "$org_id")
            local admin_tuples
            admin_tuples=$(build_org_admin_tuples "$username" "$DEFAULT_ROLE" "$org_id")
            user_tuples=$(echo "$user_tuples" | jq --argjson t "$admin_tuples" '. + $t')
            all_tuples=$(echo "$all_tuples" | jq --argjson t "$user_tuples" '. + $t')
            admin_count=$((admin_count + 1))
            log_info "  Queued ADMIN '$username' (org: $org_id)"
        else
            log_info "  Skipped '$username' (not in ADMIN_USERS — use 'grant' for team access)"
            skipped=$((skipped + 1))
        fi
    done < <(echo "$users_json" | jq -c '.[]')

    local sa_tuples
    sa_tuples=$(collect_service_account_tuples)
    if [ -n "$sa_tuples" ] && [ "$sa_tuples" != "[]" ]; then
        all_tuples=$(echo "$all_tuples" | jq --argjson t "$sa_tuples" '. + $t')
    fi

    local tuple_count
    tuple_count=$(echo "$all_tuples" | jq 'length')

    if [ "$tuple_count" -eq 0 ]; then
        log_warning "No tuples to sync"
        return
    fi

    all_tuples=$(echo "$all_tuples" | jq -c 'unique_by(tostring)')
    tuple_count=$(echo "$all_tuples" | jq 'length')

    log_info "Writing $tuple_count tuples via Relations API (upsert, deduped)..."
    local payload
    payload=$(jq -n --argjson tuples "$all_tuples" '{"upsert": true, "tuples": $tuples}')

    local result
    if result=$(relations_create_tuples "$payload" 2>&1); then
        log_success "Sync complete: $admin_count admin(s), $skipped skipped, $tuple_count tuples"
    else
        log_error "Batch sync failed: $result"
        exit 1
    fi
}

collect_service_account_tuples() {
    local admin_token
    admin_token=$(keycloak_token 2>/dev/null)
    if [ -z "$admin_token" ]; then
        echo "[]"
        return
    fi

    local clients_json
    clients_json=$(curl -sk \
        -H "Authorization: Bearer $admin_token" \
        "${KEYCLOAK_URL}/admin/realms/${KEYCLOAK_REALM}/clients?max=100" 2>/dev/null)

    local sa_tuples="[]"

    while IFS= read -r client; do
        [ -z "$client" ] && continue
        local client_uuid
        client_uuid=$(echo "$client" | jq -r '.id')

        local sa_user
        sa_user=$(curl -sk \
            -H "Authorization: Bearer $admin_token" \
            "${KEYCLOAK_URL}/admin/realms/${KEYCLOAK_REALM}/clients/${client_uuid}/service-account-user" 2>/dev/null)

        local sa_username org_id
        sa_username=$(echo "$sa_user" | jq -r '.username // empty')
        org_id=$(echo "$sa_user" | jq -r '.attributes.org_id[0] // empty')

        [ -z "$sa_username" ] && continue

        if [ -z "$org_id" ]; then
            org_id="${DEFAULT_ORG_ID:-org1234567}"
        fi

        log_info "  Queued service account '$sa_username' (org: $org_id)" >&2
        local user_tuples
        user_tuples=$(build_grant_tuples "$sa_username" "$DEFAULT_ROLE" "$org_id")
        local admin_tuples
        admin_tuples=$(build_org_admin_tuples "$sa_username" "$DEFAULT_ROLE" "$org_id")
        user_tuples=$(echo "$user_tuples" | jq --argjson t "$admin_tuples" '. + $t')
        sa_tuples=$(echo "$sa_tuples" | jq --argjson t "$user_tuples" '. + $t')
    done < <(echo "$clients_json" | jq -c '.[] | select(.serviceAccountsEnabled == true)' 2>/dev/null)

    echo "$sa_tuples"
}

# ---------------------------------------------------------------------------
# Demo: comprehensive opt-in access model scenario
#
# Resources:
#   Cluster A (ns: demo), Cluster B (ns: demo, payment), Cluster C (ns: test, payment)
#
# Groups and access:
#   admin  → org-level admin (sees everything)
#   group:demo    (test1) → Cluster A
#   group:infra   (test2) → Clusters A and C
#   group:payment (test3) → namespace payment from Clusters B and C
#   test1 direct  → Cluster B (personal workspace)
#
# Expected visibility:
#   admin → all clusters, all namespaces
#   test1 → Cluster A (group demo) + Cluster B (direct)
#   test2 → Clusters A and C (group infra)
#   test3 → ns payment-b, payment-c (group payment) + Clusters B, C (has_project cascade)
# ---------------------------------------------------------------------------
do_demo() {
    local org_id="${1:-org1234567}"
    local role="cost-openshift-viewer"
    local pass=0
    local fail=0

    log_info "=== Opt-In Access Model Demo ==="
    log_info ""
    log_info "Org: $org_id"
    log_info "Resources: 3 clusters, 5 namespaces"
    log_info "Workspaces: org + ws-infra, ws-demo, ws-payment, ws-test1"
    log_info "Groups: infra (test2), demo (test1), payment (test3)"
    log_info "Direct: test1 → ws-test1 (Cluster B)"
    log_info ""

    # ------------------------------------------------------------------
    # Step 1: Seed roles
    # ------------------------------------------------------------------
    log_info "Step 1/10: Seeding roles..."
    do_seed_roles
    log_info ""

    # ------------------------------------------------------------------
    # Step 2: Create org workspace
    # ------------------------------------------------------------------
    log_info "Step 2/10: Creating org workspace '$org_id'..."
    local org_tuples
    org_tuples=$(build_org_workspace_tuples "$org_id")
    local payload
    payload=$(jq -n --argjson tuples "$org_tuples" '{"upsert": true, "tuples": $tuples}')
    relations_create_tuples "$payload"
    log_success "Org workspace created"
    log_info ""

    # ------------------------------------------------------------------
    # Step 3: Create team workspaces
    # ------------------------------------------------------------------
    log_info "Step 3/10: Creating team workspaces..."
    do_create_workspace "ws-infra" "$org_id"
    do_create_workspace "ws-demo" "$org_id"
    do_create_workspace "ws-payment" "$org_id"
    do_create_workspace "ws-test1" "$org_id"
    log_info ""

    # ------------------------------------------------------------------
    # Step 4: Register resources (primary org workspace)
    #
    # In production, resource_reporter.py does this via Inventory API.
    # For the demo, we create the t_workspace tuples directly.
    # ------------------------------------------------------------------
    log_info "Step 4/10: Registering resources in org workspace..."
    local resources=(
        "openshift_cluster:cluster-a"
        "openshift_cluster:cluster-b"
        "openshift_cluster:cluster-c"
        "openshift_project:demo-a"
        "openshift_project:demo-b"
        "openshift_project:payment-b"
        "openshift_project:test-c"
        "openshift_project:payment-c"
    )
    local all_res_tuples="[]"
    for res in "${resources[@]}"; do
        local rtype="${res%%:*}"
        local rid="${res##*:}"
        local t
        t=$(build_resource_workspace_tuples "$rtype" "$rid" "$org_id")
        all_res_tuples=$(echo "$all_res_tuples" | jq --argjson t "$t" '. + $t')
        log_info "  ${RESOURCE_NAMESPACE}/${rtype}:${rid} → workspace:${org_id}"
    done
    payload=$(jq -n --argjson tuples "$all_res_tuples" '{"upsert": true, "tuples": $tuples}')
    relations_create_tuples "$payload"
    log_success "Registered ${#resources[@]} resources"
    log_info ""

    # ------------------------------------------------------------------
    # Step 5: Create structural relationships (has_project)
    # ------------------------------------------------------------------
    log_info "Step 5/10: Creating structural relationships (has_project)..."
    local struct_tuples="[]"
    local links=(
        "openshift_cluster:cluster-a:has_project:openshift_project:demo-a"
        "openshift_cluster:cluster-b:has_project:openshift_project:demo-b"
        "openshift_cluster:cluster-b:has_project:openshift_project:payment-b"
        "openshift_cluster:cluster-c:has_project:openshift_project:test-c"
        "openshift_cluster:cluster-c:has_project:openshift_project:payment-c"
    )
    for link in "${links[@]}"; do
        IFS=':' read -r ptype pid rel ctype cid <<< "$link"
        local t
        t=$(build_structural_tuples "$ptype" "$pid" "$rel" "$ctype" "$cid")
        struct_tuples=$(echo "$struct_tuples" | jq --argjson t "$t" '. + $t')
        log_info "  ${ptype}:${pid}#${rel} → ${ctype}:${cid}"
    done
    payload=$(jq -n --argjson tuples "$struct_tuples" '{"upsert": true, "tuples": $tuples}')
    relations_create_tuples "$payload"
    log_success "Created ${#links[@]} structural relationships"
    log_info ""

    # ------------------------------------------------------------------
    # Step 6: Assign resources to team workspaces
    # ------------------------------------------------------------------
    log_info "Step 6/10: Assigning resources to team workspaces..."

    # ws-infra: Cluster A (all) + Cluster C (all)
    local infra_assignments=(
        "openshift_cluster:cluster-a"
        "openshift_project:demo-a"
        "openshift_cluster:cluster-c"
        "openshift_project:test-c"
        "openshift_project:payment-c"
    )
    local team_tuples="[]"
    log_info "  ws-infra: Clusters A and C (full)"
    for res in "${infra_assignments[@]}"; do
        local rtype="${res%%:*}" rid="${res##*:}"
        local t
        t=$(build_resource_workspace_tuples "$rtype" "$rid" "ws-infra")
        team_tuples=$(echo "$team_tuples" | jq --argjson t "$t" '. + $t')
    done

    # ws-demo: Cluster A (all)
    local demo_assignments=(
        "openshift_cluster:cluster-a"
        "openshift_project:demo-a"
    )
    log_info "  ws-demo: Cluster A (full)"
    for res in "${demo_assignments[@]}"; do
        local rtype="${res%%:*}" rid="${res##*:}"
        local t
        t=$(build_resource_workspace_tuples "$rtype" "$rid" "ws-demo")
        team_tuples=$(echo "$team_tuples" | jq --argjson t "$t" '. + $t')
    done

    # ws-payment: namespace payment from B and C only (not whole clusters)
    local payment_assignments=(
        "openshift_project:payment-b"
        "openshift_project:payment-c"
    )
    log_info "  ws-payment: ns payment-b, payment-c (namespace-level only)"
    for res in "${payment_assignments[@]}"; do
        local rtype="${res%%:*}" rid="${res##*:}"
        local t
        t=$(build_resource_workspace_tuples "$rtype" "$rid" "ws-payment")
        team_tuples=$(echo "$team_tuples" | jq --argjson t "$t" '. + $t')
    done

    # ws-test1: Cluster B (all) — test1's direct personal access
    local test1_assignments=(
        "openshift_cluster:cluster-b"
        "openshift_project:demo-b"
        "openshift_project:payment-b"
    )
    log_info "  ws-test1: Cluster B (full) — test1 direct access"
    for res in "${test1_assignments[@]}"; do
        local rtype="${res%%:*}" rid="${res##*:}"
        local t
        t=$(build_resource_workspace_tuples "$rtype" "$rid" "ws-test1")
        team_tuples=$(echo "$team_tuples" | jq --argjson t "$t" '. + $t')
    done

    payload=$(jq -n --argjson tuples "$team_tuples" '{"upsert": true, "tuples": $tuples}')
    relations_create_tuples "$payload"
    log_success "Assigned resources to team workspaces"
    log_info ""

    # ------------------------------------------------------------------
    # Step 7: Create groups and add members
    # ------------------------------------------------------------------
    log_info "Step 7/10: Creating groups and adding members..."
    local group_tuples="[]"
    local members=(
        "demo:test1"
        "infra:test2"
        "payment:test3"
    )
    for m in "${members[@]}"; do
        local gid="${m%%:*}" user="${m##*:}"
        local t
        t=$(build_group_member_tuples "$gid" "$user")
        group_tuples=$(echo "$group_tuples" | jq --argjson t "$t" '. + $t')
        log_info "  group:${gid} ← ${user}"
    done
    payload=$(jq -n --argjson tuples "$group_tuples" '{"upsert": true, "tuples": $tuples}')
    relations_create_tuples "$payload"
    log_success "Created 3 groups with members"
    log_info ""

    # ------------------------------------------------------------------
    # Step 8: Grant groups to team workspaces
    # ------------------------------------------------------------------
    log_info "Step 8/10: Granting group access to workspaces..."
    do_grant_group "infra" "$role" "ws-infra"
    do_grant_group "demo" "$role" "ws-demo"
    do_grant_group "payment" "$role" "ws-payment"
    log_info ""

    # ------------------------------------------------------------------
    # Step 9: Grant direct user access
    # ------------------------------------------------------------------
    log_info "Step 9/10: Granting direct user access..."
    do_grant_admin "admin" "cost-administrator" "$org_id"
    do_grant "test1" "$role" "ws-test1"
    log_info ""

    # ------------------------------------------------------------------
    # Step 10: Verification matrix
    #
    # SpiceDB uses revision quantization (default 5s) which can cause
    # Checks to use a pre-write snapshot. Wait to ensure consistency.
    # ------------------------------------------------------------------
    sleep 1
    log_info "Step 10/10: Running verification matrix..."
    log_info ""
    log_info "Checking resource-level access via Relations API Check..."
    log_info "(Cluster checks use workspace-level check; namespace checks"
    log_info " verify the t_workspace assignment resolves correctly)"
    log_info ""

    # Helper: run a check and track pass/fail
    demo_check() {
        local user="$1" resource_ns="$2" resource_type="$3" resource_id="$4" expected="$5" reason="$6"
        local check_payload
        check_payload=$(cat <<EOFCHECK
{
    "resource": {
        "type": {"namespace": "$resource_ns", "name": "$resource_type"},
        "id": "$resource_id"
    },
    "relation": "read",
    "subject": {
        "subject": {
            "type": {"namespace": "rbac", "name": "principal"},
            "id": "redhat/$user"
        }
    }
}
EOFCHECK
        )
        local result allowed
        result=$(relations_check "$check_payload" 2>/dev/null) || true
        allowed=$(echo "$result" | jq -r '.allowed // "ALLOWED_UNSPECIFIED"' 2>/dev/null)

        if [ "$expected" = "ALLOWED" ] && [ "$allowed" = "ALLOWED_TRUE" ]; then
            log_success "  $user → ${resource_type}:${resource_id}: ALLOWED ($reason)"
            pass=$((pass + 1))
        elif [ "$expected" = "DENIED" ] && [ "$allowed" != "ALLOWED_TRUE" ]; then
            log_success "  $user → ${resource_type}:${resource_id}: DENIED ($reason)"
            pass=$((pass + 1))
        else
            log_error "  $user → ${resource_type}:${resource_id}: expected $expected, got $allowed ($reason)"
            fail=$((fail + 1))
        fi
    }

    log_info "--- admin (org-level, sees everything) ---"
    demo_check admin "$RESOURCE_NAMESPACE" openshift_cluster cluster-a ALLOWED "org admin"
    demo_check admin "$RESOURCE_NAMESPACE" openshift_cluster cluster-b ALLOWED "org admin"
    demo_check admin "$RESOURCE_NAMESPACE" openshift_cluster cluster-c ALLOWED "org admin"
    demo_check admin "$RESOURCE_NAMESPACE" openshift_project demo-a    ALLOWED "org admin"
    demo_check admin "$RESOURCE_NAMESPACE" openshift_project payment-b ALLOWED "org admin"
    demo_check admin "$RESOURCE_NAMESPACE" openshift_project test-c    ALLOWED "org admin"
    log_info ""

    log_info "--- test1 (group:demo + direct ws-test1 → Clusters A and B) ---"
    demo_check test1 "$RESOURCE_NAMESPACE" openshift_cluster cluster-a ALLOWED "group:demo → ws-demo"
    demo_check test1 "$RESOURCE_NAMESPACE" openshift_cluster cluster-b ALLOWED "direct → ws-test1"
    demo_check test1 "$RESOURCE_NAMESPACE" openshift_cluster cluster-c DENIED  "not in any of test1's workspaces"
    demo_check test1 "$RESOURCE_NAMESPACE" openshift_project demo-a    ALLOWED "group:demo → ws-demo"
    demo_check test1 "$RESOURCE_NAMESPACE" openshift_project demo-b    ALLOWED "direct → ws-test1"
    demo_check test1 "$RESOURCE_NAMESPACE" openshift_project payment-b ALLOWED "direct → ws-test1"
    demo_check test1 "$RESOURCE_NAMESPACE" openshift_project test-c    DENIED  "not in ws-demo or ws-test1"
    demo_check test1 "$RESOURCE_NAMESPACE" openshift_project payment-c DENIED  "not in ws-demo or ws-test1"
    log_info ""

    log_info "--- test2 (group:infra → Clusters A and C) ---"
    demo_check test2 "$RESOURCE_NAMESPACE" openshift_cluster cluster-a ALLOWED "group:infra → ws-infra"
    demo_check test2 "$RESOURCE_NAMESPACE" openshift_cluster cluster-b DENIED  "not in ws-infra"
    demo_check test2 "$RESOURCE_NAMESPACE" openshift_cluster cluster-c ALLOWED "group:infra → ws-infra"
    demo_check test2 "$RESOURCE_NAMESPACE" openshift_project demo-a    ALLOWED "group:infra → ws-infra"
    demo_check test2 "$RESOURCE_NAMESPACE" openshift_project demo-b    DENIED  "not in ws-infra"
    demo_check test2 "$RESOURCE_NAMESPACE" openshift_project payment-b DENIED  "not in ws-infra"
    demo_check test2 "$RESOURCE_NAMESPACE" openshift_project test-c    ALLOWED "group:infra → ws-infra"
    demo_check test2 "$RESOURCE_NAMESPACE" openshift_project payment-c ALLOWED "group:infra → ws-infra"
    log_info ""

    log_info "--- test3 (group:payment → ns payment from B and C) ---"
    demo_check test3 "$RESOURCE_NAMESPACE" openshift_cluster cluster-a DENIED  "no payment ns in cluster-a"
    demo_check test3 "$RESOURCE_NAMESPACE" openshift_cluster cluster-b ALLOWED "has_project cascade from payment-b"
    demo_check test3 "$RESOURCE_NAMESPACE" openshift_cluster cluster-c ALLOWED "has_project cascade from payment-c"
    demo_check test3 "$RESOURCE_NAMESPACE" openshift_project demo-a    DENIED  "not in ws-payment"
    demo_check test3 "$RESOURCE_NAMESPACE" openshift_project demo-b    DENIED  "not in ws-payment"
    demo_check test3 "$RESOURCE_NAMESPACE" openshift_project payment-b ALLOWED "group:payment → ws-payment"
    demo_check test3 "$RESOURCE_NAMESPACE" openshift_project test-c    DENIED  "not in ws-payment"
    demo_check test3 "$RESOURCE_NAMESPACE" openshift_project payment-c ALLOWED "group:payment → ws-payment"
    log_info ""

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    log_info "============================================"
    if [ "$fail" -eq 0 ]; then
        log_success "All $pass checks passed (0 failures)"
    else
        log_error "$fail checks FAILED out of $((pass + fail))"
    fi
    log_info "============================================"
    log_info ""
    log_info "Access model summary:"
    log_info "  admin  → everything (org-level admin)"
    log_info "  test1  → Cluster A (group:demo) + Cluster B (direct ws-test1)"
    log_info "  test2  → Clusters A and C (group:infra)"
    log_info "  test3  → ns payment from B and C (group:payment) + clusters via has_project"
    log_info ""
    log_info "Key behaviors demonstrated:"
    log_info "  1. Workspace scoping: users only see resources in their workspace(s)"
    log_info "  2. Group access: group members inherit workspace bindings"
    log_info "  3. Direct access: test1 has personal workspace for Cluster B"
    log_info "  4. Namespace-level scoping: test3 sees payment ns but not demo/test ns"
    log_info "  5. has_project cascade: test3 sees clusters B/C through namespace access"
    log_info "  6. Koku needs zero changes: resource_reporter is unchanged"

    return "$fail"
}

# ---------------------------------------------------------------------------
# List Keycloak users
# ---------------------------------------------------------------------------
do_list_users() {
    log_info "Listing users from Keycloak realm '$KEYCLOAK_REALM'..."

    local users_json
    users_json=$(keycloak_list_users)

    printf "\n%-20s %-15s %-15s %-10s\n" "USERNAME" "ORG_ID" "ACCOUNT_NUM" "ENABLED"
    printf "%-20s %-15s %-15s %-10s\n" "--------" "------" "-----------" "-------"

    echo "$users_json" | jq -c '.[]' | while IFS= read -r user; do
        local username org_id account_number enabled
        username=$(echo "$user" | jq -r '.username')
        org_id=$(echo "$user" | jq -r '.attributes.org_id[0] // "N/A"')
        account_number=$(echo "$user" | jq -r '.attributes.account_number[0] // "N/A"')
        enabled=$(echo "$user" | jq -r '.enabled')
        printf "%-20s %-15s %-15s %-10s\n" "$username" "$org_id" "$account_number" "$enabled"
    done
    echo ""
}

# ---------------------------------------------------------------------------
# Status: health check + connectivity probe
#
# ReadTuples is a streaming RPC with no HTTP route, so we cannot enumerate
# tuples via REST.  Instead we verify connectivity and run a sample check.
# ---------------------------------------------------------------------------
do_status() {
    log_info "Kessel Relations API status:"
    echo ""

    local health_url="${RELATIONS_URL}${RELATIONS_API_PREFIX}/readyz"
    local health
    if health=$(relations_api_call GET "$health_url" 2>&1); then
        local status_str
        status_str=$(echo "$health" | jq -r '.status // "unknown"' 2>/dev/null)
        log_success "Health: $status_str ($health_url)"
    else
        log_error "Health check failed: $health"
    fi

    echo ""
    log_info "Sample permission check (cost_management_openshift_cluster_read for test/org1234567):"
    local payload
    payload=$(cat <<EOF
{
    "resource": {"type": {"namespace": "rbac", "name": "workspace"}, "id": "org1234567"},
    "relation": "cost_management_openshift_cluster_read",
    "subject": {"subject": {"type": {"namespace": "rbac", "name": "principal"}, "id": "redhat/test"}}
}
EOF
    )
    local result
    if result=$(relations_check "$payload" 2>&1); then
        local allowed
        allowed=$(echo "$result" | jq -r '.allowed // "ALLOWED_UNSPECIFIED"' 2>/dev/null)
        log_success "Check result: $allowed"
    else
        log_warning "Check failed (this is expected if no tuples are seeded yet)"
    fi

    echo ""
    log_info "Note: tuple enumeration requires gRPC (ReadTuples is a streaming RPC)."
    log_info "Use 'grpcurl' or the SpiceDB CLI for full tuple listing."
    echo ""
}

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    cat <<USAGE
Usage: $(basename "$0") <command> [args...]

Access Model: OPT-IN
  Users see nothing until explicitly granted access to a team workspace.
  Org admins (bound at org workspace) see all resources.

Commands:
  bootstrap                                     One-shot setup: seed roles + sync admin users
  seed-roles                                    Seed role permission tuples from seed-roles.yaml
  sync                                          Sync admin users to Kessel with DEFAULT_ROLE
  demo [org_id]                                 Set up a complete opt-in demo scenario

  Workspace Management:
    create-workspace  <ws_id> <parent_id>       Create a team workspace under a parent
    delete-workspace  <ws_id> <parent_id>       Delete a team workspace

  Resource Assignment:
    assign-resource   <type> <id> <ws_id>       Assign a resource to a team workspace
    unassign-resource <type> <id> <ws_id>       Remove a resource from a team workspace
    link-resource <ptype> <pid> <rel> <ctype> <cid>  Create structural relationship (e.g. has_project)

  User Access:
    grant   <user> <role> <workspace_id>        Grant a role to a user at a workspace
    revoke  <user> <role> <workspace_id>        Revoke a role from a user at a workspace
    check   <user> <perm> <workspace_id>        Check if user has permission at workspace

  Group Access:
    add-group-member    <group_id> <username>              Add user to group
    remove-group-member <group_id> <username>              Remove user from group
    grant-group  <group_id> <role> <workspace_id>          Grant role to group at workspace
    revoke-group <group_id> <role> <workspace_id>          Revoke role from group at workspace

  Info:
    list-users                                  List Keycloak users with attributes
    status                                      Show Kessel connectivity and sample check

Available roles (from seed-roles.yaml):
  cost-administrator              Full read + write for all resource types
  cost-cloud-viewer               Read-only for AWS, GCP, Azure
  cost-openshift-viewer           Read-only for OpenShift cluster/node/project
  cost-price-list-administrator   Read + write for cost models
  cost-price-list-viewer          Read-only for cost models

Environment:
  RELATIONS_URL=$RELATIONS_URL
  KEYCLOAK_URL=$KEYCLOAK_URL
  KEYCLOAK_REALM=$KEYCLOAK_REALM
  KESSEL_NAMESPACE=$KESSEL_NAMESPACE
  DEFAULT_ROLE=$DEFAULT_ROLE
  ADMIN_USERS=$ADMIN_USERS
  RESOURCE_NAMESPACE=$RESOURCE_NAMESPACE

Examples:
  # First-time setup (seed roles + sync admin users at org level)
  ./kessel-admin.sh bootstrap

  # Run the full opt-in demo
  ./kessel-admin.sh demo

  # Create a team workspace
  ./kessel-admin.sh create-workspace team-infra org1234567

  # Assign a resource to a team workspace
  ./kessel-admin.sh assign-resource openshift_cluster my-cluster team-infra

  # Grant a user access to a team workspace
  ./kessel-admin.sh grant alice cost-openshift-viewer team-infra

  # Verify the user can see resources in that workspace
  ./kessel-admin.sh check alice cost_management_openshift_cluster_read team-infra
USAGE
    exit 1
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    local cmd="${1:-}"
    shift || true

    if [ -z "$cmd" ]; then
        usage
    fi

    check_tools

    case "$cmd" in
        bootstrap)
            detect_relations_url
            detect_keycloak
            detect_keycloak_password
            do_bootstrap
            ;;
        seed-roles)
            detect_relations_url
            do_seed_roles
            ;;
        sync)
            detect_relations_url
            detect_keycloak
            detect_keycloak_password
            do_sync
            ;;
        grant)
            [ $# -lt 3 ] && { log_error "Usage: grant <user> <role> <workspace_id>"; exit 1; }
            detect_relations_url
            do_grant "$1" "$2" "$3"
            ;;
        revoke)
            [ $# -lt 3 ] && { log_error "Usage: revoke <user> <role> <workspace_id>"; exit 1; }
            detect_relations_url
            do_revoke "$1" "$2" "$3"
            ;;
        check)
            [ $# -lt 3 ] && { log_error "Usage: check <user> <permission> <workspace_id>"; exit 1; }
            detect_relations_url
            do_check "$1" "$2" "$3"
            ;;
        create-workspace)
            [ $# -lt 2 ] && { log_error "Usage: create-workspace <workspace_id> <parent_id>"; exit 1; }
            detect_relations_url
            do_create_workspace "$1" "$2"
            ;;
        delete-workspace)
            [ $# -lt 2 ] && { log_error "Usage: delete-workspace <workspace_id> <parent_id>"; exit 1; }
            detect_relations_url
            do_delete_workspace "$1" "$2"
            ;;
        assign-resource)
            [ $# -lt 3 ] && { log_error "Usage: assign-resource <type> <resource_id> <workspace_id>"; exit 1; }
            detect_relations_url
            do_assign_resource "$1" "$2" "$3"
            ;;
        unassign-resource)
            [ $# -lt 3 ] && { log_error "Usage: unassign-resource <type> <resource_id> <workspace_id>"; exit 1; }
            detect_relations_url
            do_unassign_resource "$1" "$2" "$3"
            ;;
        link-resource)
            [ $# -lt 5 ] && { log_error "Usage: link-resource <parent_type> <parent_id> <relation> <child_type> <child_id>"; exit 1; }
            detect_relations_url
            do_link_resource "$1" "$2" "$3" "$4" "$5"
            ;;
        add-group-member)
            [ $# -lt 2 ] && { log_error "Usage: add-group-member <group_id> <username>"; exit 1; }
            detect_relations_url
            do_add_group_member "$1" "$2"
            ;;
        remove-group-member)
            [ $# -lt 2 ] && { log_error "Usage: remove-group-member <group_id> <username>"; exit 1; }
            detect_relations_url
            do_remove_group_member "$1" "$2"
            ;;
        grant-group)
            [ $# -lt 3 ] && { log_error "Usage: grant-group <group_id> <role> <workspace_id>"; exit 1; }
            detect_relations_url
            do_grant_group "$1" "$2" "$3"
            ;;
        revoke-group)
            [ $# -lt 3 ] && { log_error "Usage: revoke-group <group_id> <role> <workspace_id>"; exit 1; }
            detect_relations_url
            do_revoke_group "$1" "$2" "$3"
            ;;
        demo)
            detect_relations_url
            detect_keycloak
            detect_keycloak_password
            do_demo "${1:-org1234567}"
            ;;
        list-users)
            detect_keycloak
            detect_keycloak_password
            do_list_users
            ;;
        status)
            detect_relations_url
            do_status
            ;;
        *)
            log_error "Unknown command: $cmd"
            usage
            ;;
    esac
}

main "$@"
