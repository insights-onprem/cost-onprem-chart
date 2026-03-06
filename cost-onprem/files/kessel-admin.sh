#!/bin/bash

# kessel-admin.sh - Kessel authorization management for Cost Management
#
# Bridges identity (Keycloak) and authorization (Kessel/SpiceDB) by creating
# the tuples that connect principals to roles via role_bindings and tenants.
#
# Resource-level tuples (clusters, projects, integrations) are created
# automatically by Koku's ingestion pipeline when data is processed.
# This script handles the identity-level tuples that grant users access.
#
# Operations:
#   bootstrap   One-shot setup: seed roles + sync all users
#   seed-roles  Seed role permission tuples from seed-roles.yaml
#   sync        Sync all Keycloak users → Kessel (creates role_bindings + tenants)
#   grant       Grant a specific role to a user in an org
#   revoke      Revoke a role from a user in an org
#   check       Check if a user has a specific permission in an org
#   list-users  List Keycloak users with their org_id
#   status      Show current Kessel tuple counts
#
# Prerequisites:
#   - grpcurl (https://github.com/fullstorydev/grpcurl)
#   - jq, python3 (with PyYAML for seed-roles)
#   - oc (logged into the cluster)
#   - Port-forward to Relations API or RELATIONS_URL set
#
# Environment Variables:
#   RELATIONS_URL       Relations API gRPC endpoint (default: localhost:9000)
#   KEYCLOAK_URL        Keycloak admin URL (default: auto-detect from route)
#   KEYCLOAK_REALM      Realm name (default: kubernetes)
#   KEYCLOAK_ADMIN      Admin username (default: admin)
#   KEYCLOAK_PASSWORD   Admin password (default: auto-detect from secret)
#   KESSEL_NAMESPACE    Namespace where Kessel runs (default: kessel)
#   KEYCLOAK_NAMESPACE  Namespace where Keycloak runs (default: keycloak)
#   DEFAULT_ROLE        Default role slug for sync (default: cost-administrator)
#
# Examples:
#   ./kessel-admin.sh bootstrap
#   ./kessel-admin.sh grant test cost-openshift-viewer org1234567
#   ./kessel-admin.sh check test cost_management_openshift_cluster_read org1234567

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
SPICEDB_HOST=${SPICEDB_HOST:-}
SPICEDB_PORT=${SPICEDB_PORT:-50051}
SPICEDB_PRESHARED_KEY=${SPICEDB_PRESHARED_KEY:-}

log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1" >&2; }

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
check_tools() {
    local missing=()
    for tool in grpcurl jq curl; do
        if ! command -v "$tool" >/dev/null 2>&1; then
            missing+=("$tool")
        fi
    done
    # oc is only required when auto-detecting endpoints (env vars not set)
    local need_oc=false
    if [ -z "$KEYCLOAK_URL" ] || [ -z "$KEYCLOAK_PASSWORD" ]; then
        need_oc=true
    fi
    if [ -z "$SPICEDB_HOST" ] || [ -z "$SPICEDB_PRESHARED_KEY" ]; then
        if [ -z "$RELATIONS_URL" ]; then
            need_oc=true
        fi
    fi
    if $need_oc && ! command -v oc >/dev/null 2>&1; then
        missing+=("oc (or set SPICEDB_HOST+SPICEDB_PRESHARED_KEY, KEYCLOAK_URL, KEYCLOAK_PASSWORD)")
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
    log_info "No RELATIONS_URL set, starting port-forward to Relations API..."
    oc port-forward -n "$KESSEL_NAMESPACE" svc/kessel-relations 9000:9000 &>/dev/null &
    PORT_FORWARD_PID=$!
    sleep 2
    if ! kill -0 "$PORT_FORWARD_PID" 2>/dev/null; then
        log_error "Failed to port-forward to kessel-relations"
        exit 1
    fi
    RELATIONS_URL="localhost:9000"
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
# SpiceDB direct helpers (authenticated via preshared key)
# ---------------------------------------------------------------------------
SPICEDB_URL=""  # resolved lazily

_spicedb_url() {
    if [ -n "$SPICEDB_URL" ]; then
        echo "$SPICEDB_URL"
        return
    fi
    if [ -n "$SPICEDB_HOST" ]; then
        SPICEDB_URL="${SPICEDB_HOST}:${SPICEDB_PORT}"
    fi
    echo "$SPICEDB_URL"
}

detect_spicedb() {
    if [ -n "$SPICEDB_HOST" ] && [ -n "$SPICEDB_PRESHARED_KEY" ]; then
        return
    fi
    if [ -z "$SPICEDB_HOST" ]; then
        SPICEDB_HOST="spicedb.${KESSEL_NAMESPACE}.svc.cluster.local"
        log_info "Using default SpiceDB host: $SPICEDB_HOST"
    fi
    if [ -z "$SPICEDB_PRESHARED_KEY" ]; then
        SPICEDB_PRESHARED_KEY=$(oc get secret spicedb-config -n "$KESSEL_NAMESPACE" \
            -o jsonpath='{.data.preshared-key}' 2>/dev/null | base64 -d 2>/dev/null || true)
        if [ -z "$SPICEDB_PRESHARED_KEY" ]; then
            log_error "Cannot detect SpiceDB preshared key. Set SPICEDB_PRESHARED_KEY."
            exit 1
        fi
    fi
}

spicedb_grpcurl() {
    grpcurl -plaintext \
        -H "authorization: Bearer ${SPICEDB_PRESHARED_KEY}" \
        "$@"
}

spicedb_write_relationships() {
    local payload="$1"
    spicedb_grpcurl -d "$payload" \
        "$(_spicedb_url)" \
        authzed.api.v1.PermissionsService/WriteRelationships 2>/dev/null
}

spicedb_read_relationships() {
    local filter="$1"
    spicedb_grpcurl -d "$filter" \
        "$(_spicedb_url)" \
        authzed.api.v1.PermissionsService/ReadRelationships 2>/dev/null
}

# ---------------------------------------------------------------------------
# Kessel Relations API helpers (no auth, used for grant/revoke/check)
# ---------------------------------------------------------------------------
create_tuples() {
    local tuples_json="$1"
    grpcurl -plaintext -d "$tuples_json" \
        "$RELATIONS_URL" \
        kessel.relations.v1beta1.KesselTupleService/CreateTuples 2>/dev/null
}

delete_tuples() {
    local tuples_json="$1"
    grpcurl -plaintext -d "$tuples_json" \
        "$RELATIONS_URL" \
        kessel.relations.v1beta1.KesselTupleService/DeleteTuples 2>/dev/null
}

check_permission() {
    local resource_type="$1" resource_id="$2" relation="$3" principal="$4"
    grpcurl -plaintext -d "{
        \"resource\": {
            \"type\": {\"namespace\": \"rbac\", \"name\": \"$resource_type\"},
            \"id\": \"$resource_id\"
        },
        \"relation\": \"$relation\",
        \"subject\": {
            \"subject\": {
                \"type\": {\"namespace\": \"rbac\", \"name\": \"principal\"},
                \"id\": \"$principal\"
            }
        }
    }" "$RELATIONS_URL" \
        kessel.relations.v1beta1.KesselCheckService/Check 2>/dev/null
}

read_tuples() {
    local ns="$1" name="$2"
    grpcurl -plaintext -d "{
        \"filter\": {
            \"resource_namespace\": \"$ns\",
            \"resource_type\": \"$name\"
        }
    }" "$RELATIONS_URL" \
        kessel.relations.v1beta1.KesselTupleService/ReadTuples 2>/dev/null
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
    detect_spicedb
    local seed_file=""
    local script_dir
    script_dir="$(cd "$(dirname "$0")" && pwd)"

    for candidate in \
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

    log_info "Seeding roles from $seed_file (SpiceDB direct)"

    local updates="[]"
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

        # kessel_relation values in seed-roles.yaml already include the t_ prefix
        while IFS= read -r relation; do
            updates=$(echo "$updates" | jq --arg slug "$slug" --arg rel "${relation}" \
                '. + [{
                    "operation": "OPERATION_TOUCH",
                    "relationship": {
                        "resource": {"objectType": "rbac/role", "objectId": $slug},
                        "relation": $rel,
                        "subject": {"object": {"objectType": "rbac/principal", "objectId": "*"}}
                    }
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

    local update_count
    update_count=$(echo "$updates" | jq 'length')

    if [ "$update_count" -eq 0 ]; then
        log_error "No relationships generated from seed file"
        exit 1
    fi

    log_info "Writing $update_count permission relationships for $role_count roles..."
    local payload
    payload=$(jq -n --argjson updates "$updates" '{"updates": $updates}')
    spicedb_write_relationships "$payload"
    log_success "Seeded $role_count roles ($update_count relationships)"
}

# ---------------------------------------------------------------------------
# Bootstrap: seed roles + sync users (one-shot setup)
# ---------------------------------------------------------------------------
do_bootstrap() {
    log_info "=== Kessel Authorization Bootstrap ==="
    log_info ""
    log_info "Step 1/2: Seeding role permission tuples..."
    do_seed_roles
    log_info ""
    log_info "Step 2/2: Syncing Keycloak users to Kessel..."
    do_sync
    log_info ""
    log_success "=== Bootstrap complete ==="
    log_info ""
    log_info "Verify with:"
    log_info "  $0 status"
    log_info "  $0 check test cost_management_openshift_cluster_read org1234567"
}

# ---------------------------------------------------------------------------
# Grant a role to a user within an org
#
# Creates the full authorization chain:
#   1. role_binding:{rb_id} --t_granted--> role:{role_slug}
#   2. role_binding:{rb_id} --t_subject--> principal:{username}
#   3. workspace:{org_id}   --t_parent --> tenant:{org_id}   (idempotent)
#   4. workspace:{org_id}   --t_binding--> role_binding:{rb_id}
#   5. tenant:{org_id}      --t_binding--> role_binding:{rb_id}
# ---------------------------------------------------------------------------
do_grant() {
    local username="$1" role_slug="$2" org_id="$3"
    local rb_id="${org_id}--${username}--${role_slug}"

    log_info "Granting role '$role_slug' to '$username' in org '$org_id'"
    log_info "  role_binding ID: $rb_id"

    local tuples
    tuples=$(build_grant_tuples "$username" "$role_slug" "$org_id")
    local payload
    payload=$(jq -n --argjson tuples "$tuples" '{"upsert": true, "tuples": $tuples}')
    create_tuples "$payload"

    log_success "Granted '$role_slug' to '$username' in org '$org_id'"
}

# ---------------------------------------------------------------------------
# Revoke a role from a user within an org
# ---------------------------------------------------------------------------
do_revoke() {
    local username="$1" role_slug="$2" org_id="$3"
    local rb_id="${org_id}--${username}--${role_slug}"

    log_info "Revoking role '$role_slug' from '$username' in org '$org_id'"

    delete_tuples "$(cat <<TUPLES
{
    "tuples": [
        {
            "resource": {"type": {"namespace": "rbac", "name": "role_binding"}, "id": "$rb_id"},
            "relation": "t_granted",
            "subject": {"subject": {"type": {"namespace": "rbac", "name": "role"}, "id": "$role_slug"}}
        },
        {
            "resource": {"type": {"namespace": "rbac", "name": "role_binding"}, "id": "$rb_id"},
            "relation": "t_subject",
            "subject": {"subject": {"type": {"namespace": "rbac", "name": "principal"}, "id": "redhat/$username"}}
        },
        {
            "resource": {"type": {"namespace": "rbac", "name": "workspace"}, "id": "$org_id"},
            "relation": "t_binding",
            "subject": {"subject": {"type": {"namespace": "rbac", "name": "role_binding"}, "id": "$rb_id"}}
        },
        {
            "resource": {"type": {"namespace": "rbac", "name": "tenant"}, "id": "$org_id"},
            "relation": "t_binding",
            "subject": {"subject": {"type": {"namespace": "rbac", "name": "role_binding"}, "id": "$rb_id"}}
        }
    ]
}
TUPLES
    )"

    log_success "Revoked '$role_slug' from '$username' in org '$org_id'"
}

# ---------------------------------------------------------------------------
# Check a user's permission
# ---------------------------------------------------------------------------
do_check() {
    local username="$1" permission="$2" org_id="$3"

    log_info "Checking '$permission' for '$username' in tenant '$org_id'"

    local result
    result=$(check_permission "workspace" "$org_id" "$permission" "redhat/$username")

    local allowed
    allowed=$(echo "$result" | jq -r '.allowed // "ALLOWED_UNSPECIFIED"')

    if [ "$allowed" = "ALLOWED_TRUE" ] || echo "$result" | grep -q "ALLOWED_TRUE"; then
        log_success "ALLOWED: '$username' has '$permission' in '$org_id'"
    else
        log_warning "DENIED: '$username' does NOT have '$permission' in '$org_id'"
        echo "$result" | jq . 2>/dev/null || echo "$result"
    fi
}

# ---------------------------------------------------------------------------
# Build grant tuples as SpiceDB RelationshipUpdate objects (OPERATION_TOUCH).
# Returns a JSON array suitable for WriteRelationships.updates[].
#
# Relation names match the schema.zed definitions exactly (t_ prefix is
# already part of the schema relation names, NOT added by Relations API).
# ---------------------------------------------------------------------------
build_spicedb_grant_updates() {
    local username="$1" role_slug="$2" org_id="$3"
    local rb_id="${org_id}--${username}--${role_slug}"

    cat <<EOF
[
  {"operation":"OPERATION_TOUCH","relationship":{"resource":{"objectType":"rbac/role_binding","objectId":"$rb_id"},"relation":"t_granted","subject":{"object":{"objectType":"rbac/role","objectId":"$role_slug"}}}},
  {"operation":"OPERATION_TOUCH","relationship":{"resource":{"objectType":"rbac/role_binding","objectId":"$rb_id"},"relation":"t_subject","subject":{"object":{"objectType":"rbac/principal","objectId":"redhat/$username"}}}},
  {"operation":"OPERATION_TOUCH","relationship":{"resource":{"objectType":"rbac/workspace","objectId":"$org_id"},"relation":"t_parent","subject":{"object":{"objectType":"rbac/tenant","objectId":"$org_id"}}}},
  {"operation":"OPERATION_TOUCH","relationship":{"resource":{"objectType":"rbac/workspace","objectId":"$org_id"},"relation":"t_binding","subject":{"object":{"objectType":"rbac/role_binding","objectId":"$rb_id"}}}},
  {"operation":"OPERATION_TOUCH","relationship":{"resource":{"objectType":"rbac/tenant","objectId":"$org_id"},"relation":"t_binding","subject":{"object":{"objectType":"rbac/role_binding","objectId":"$rb_id"}}}}
]
EOF
}

# Relations API format (for grant/revoke individual operations)
build_grant_tuples() {
    local username="$1" role_slug="$2" org_id="$3"
    local rb_id="${org_id}--${username}--${role_slug}"

    cat <<EOF
[
  {"resource":{"type":{"namespace":"rbac","name":"role_binding"},"id":"$rb_id"},"relation":"t_granted","subject":{"subject":{"type":{"namespace":"rbac","name":"role"},"id":"$role_slug"}}},
  {"resource":{"type":{"namespace":"rbac","name":"role_binding"},"id":"$rb_id"},"relation":"t_subject","subject":{"subject":{"type":{"namespace":"rbac","name":"principal"},"id":"redhat/$username"}}},
  {"resource":{"type":{"namespace":"rbac","name":"workspace"},"id":"$org_id"},"relation":"t_parent","subject":{"subject":{"type":{"namespace":"rbac","name":"tenant"},"id":"$org_id"}}},
  {"resource":{"type":{"namespace":"rbac","name":"workspace"},"id":"$org_id"},"relation":"t_binding","subject":{"subject":{"type":{"namespace":"rbac","name":"role_binding"},"id":"$rb_id"}}},
  {"resource":{"type":{"namespace":"rbac","name":"tenant"},"id":"$org_id"},"relation":"t_binding","subject":{"subject":{"type":{"namespace":"rbac","name":"role_binding"},"id":"$rb_id"}}}
]
EOF
}

# ---------------------------------------------------------------------------
# Sync all Keycloak users → Kessel (batched: one gRPC call)
#
# Collects tuples for all users and service accounts, then sends them in
# a single CreateTuples call with upsert=true. This is idempotent.
# ---------------------------------------------------------------------------
do_sync() {
    log_info "Syncing Keycloak users from realm '$KEYCLOAK_REALM' to SpiceDB (direct)..."
    detect_spicedb

    local users_json
    users_json=$(keycloak_list_users)

    local all_updates="[]"
    local synced=0
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

        local user_updates
        user_updates=$(build_spicedb_grant_updates "$username" "$DEFAULT_ROLE" "$org_id")
        all_updates=$(echo "$all_updates" | jq --argjson t "$user_updates" '. + $t')
        synced=$((synced + 1))
        log_info "  Queued '$username' (org: $org_id)"
    done < <(echo "$users_json" | jq -c '.[]')

    local sa_updates
    sa_updates=$(collect_service_account_updates_spicedb)
    if [ -n "$sa_updates" ] && [ "$sa_updates" != "[]" ]; then
        all_updates=$(echo "$all_updates" | jq --argjson t "$sa_updates" '. + $t')
    fi

    local update_count
    update_count=$(echo "$all_updates" | jq 'length')

    if [ "$update_count" -eq 0 ]; then
        log_warning "No tuples to sync"
        return
    fi

    log_info "Writing $update_count relationships to SpiceDB (OPERATION_TOUCH, atomic)..."
    local payload
    payload=$(jq -n --argjson updates "$all_updates" '{"updates": $updates}')

    local result
    result=$(spicedb_write_relationships "$payload" 2>&1)
    if [ $? -eq 0 ]; then
        log_success "Batch sync complete: $synced users, $update_count relationships"
    else
        log_error "Batch sync failed: $result"
        exit 1
    fi
}

collect_service_account_updates_spicedb() {
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

    local sa_updates="[]"

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

        log_info "  Queued service account '$sa_username' (org: $org_id)"
        local user_updates
        user_updates=$(build_spicedb_grant_updates "$sa_username" "$DEFAULT_ROLE" "$org_id")
        sa_updates=$(echo "$sa_updates" | jq --argjson t "$user_updates" '. + $t')
    done < <(echo "$clients_json" | jq -c '.[] | select(.serviceAccountsEnabled == true)' 2>/dev/null)

    echo "$sa_updates"
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
# Status: show tuple counts
# ---------------------------------------------------------------------------
do_status() {
    log_info "Kessel tuple status:"
    echo ""

    for resource in "rbac/role" "rbac/role_binding" "rbac/tenant" "rbac/workspace" "rbac/group" \
                     "cost_management/openshift_cluster" "cost_management/openshift_project" \
                     "cost_management/openshift_node" "cost_management/integration"; do
        local ns name count
        ns="${resource%%/*}"
        name="${resource##*/}"
        count=$(read_tuples "$ns" "$name" 2>/dev/null | grep -c '"tuple"' 2>/dev/null || echo "0")
        if [ "$count" -gt 0 ] 2>/dev/null; then
            printf "  %-45s %s tuples\n" "$resource" "$count"
        fi
    done

    echo ""
    log_info "Role permission tuples (rbac/role):"
    read_tuples "rbac" "role" 2>/dev/null | jq -r '
        select(.tuple) | .tuple.resource.id + " → " + .tuple.relation
    ' 2>/dev/null | sort | while IFS= read -r line; do
        printf "    %s\n" "$line"
    done

    echo ""
    log_info "User role bindings (rbac/role_binding):"
    read_tuples "rbac" "role_binding" 2>/dev/null | jq -r '
        select(.tuple) |
        .tuple.resource.id + " #" + .tuple.relation + " → " +
        .tuple.subject.subject.type.name + ":" + .tuple.subject.subject.id
    ' 2>/dev/null | sort | while IFS= read -r line; do
        printf "    %s\n" "$line"
    done
    echo ""
}

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    cat <<USAGE
Usage: $(basename "$0") <command> [args...]

Commands:
  bootstrap                         One-shot setup: seed roles + sync all users
  seed-roles                        Seed role permission tuples from seed-roles.yaml
  sync                              Sync all Keycloak users to Kessel with DEFAULT_ROLE
  grant  <user> <role> <org_id>     Grant a role to a user in an org
  revoke <user> <role> <org_id>     Revoke a role from a user in an org
  check  <user> <perm> <org_id>     Check if user has permission in org
  list-users                        List Keycloak users with attributes
  status                            Show Kessel tuple counts and role details

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

Examples:
  # First-time setup (seed roles + sync all Keycloak users)
  ./kessel-admin.sh bootstrap

  # Grant a specific role to a user
  ./kessel-admin.sh grant test cost-openshift-viewer org1234567

  # Verify the permission was granted
  ./kessel-admin.sh check test cost_management_openshift_cluster_read org1234567
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
            [ $# -lt 3 ] && { log_error "Usage: grant <user> <role> <org_id>"; exit 1; }
            detect_relations_url
            do_grant "$1" "$2" "$3"
            ;;
        revoke)
            [ $# -lt 3 ] && { log_error "Usage: revoke <user> <role> <org_id>"; exit 1; }
            detect_relations_url
            do_revoke "$1" "$2" "$3"
            ;;
        check)
            [ $# -lt 3 ] && { log_error "Usage: check <user> <permission> <org_id>"; exit 1; }
            detect_relations_url
            do_check "$1" "$2" "$3"
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
