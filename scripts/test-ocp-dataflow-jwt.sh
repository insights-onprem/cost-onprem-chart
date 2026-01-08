#!/bin/bash

# Cost Management & ROS OpenShift Data Flow Test Script with Keycloak JWT Authentication
#
# This script tests the complete end-to-end data flow through the Cost Management (Koku)
# and ROS (Resource Optimization Service) pipeline using Keycloak JWT authentication.
#
# Data Flow Architecture:
# =======================
# 1. Cost Management Operator uploads data via JWT-authenticated ingress
# 2. Ingress (insights-ingress-go) stores files in S3 (koku-bucket)
# 3. Ingress publishes upload notification to Kafka (platform.upload.announce topic)
# 4. Koku Listener consumes from platform.upload.announce
# 5. Koku/MASU processes cost data from koku-bucket
# 6. Koku copies ROS-relevant data to ros-data bucket
# 7. Koku emits events to hccm.ros.events topic
# 8. ROS Processor consumes from hccm.ros.events and sends data to Kruize
# 9. Kruize generates optimization recommendations
# 10. Recommendations are available via the ROS API
#
# Authentication:
# - Ingress: Keycloak JWT (external uploads from Cost Management Operator)
# - Backend API: Keycloak JWT (for API access)

set -e  # Exit on any error

# Cleanup function for trapped exits
cleanup() {
    local exit_code=$?
    if [ -n "${PORT_FORWARD_PID:-}" ]; then
        kill "$PORT_FORWARD_PID" 2>/dev/null || true
    fi
    # Cleanup test source if it was created
    if [ -n "${TEST_SOURCE_ID:-}" ]; then
        echo_info "Cleaning up test source..."
        cleanup_test_source
    fi
    exit $exit_code
}

trap cleanup EXIT INT TERM

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
NAMESPACE=${NAMESPACE:-cost-onprem}
HELM_RELEASE_NAME=${HELM_RELEASE_NAME:-cost-onprem}
KEYCLOAK_NAMESPACE=${KEYCLOAK_NAMESPACE:-keycloak}

# Authentication variables
# Keycloak JWT for ingress (external uploads)
JWT_TOKEN=""
JWT_TOKEN_EXPIRY=""
KEYCLOAK_URL=""
CLIENT_ID=""
CLIENT_SECRET=""

# JWT token is used for both ingress and backend API
PORT_FORWARD_PID=""

# Check prerequisites
check_prerequisites() {
    local missing_deps=()

    command -v oc >/dev/null 2>&1 || missing_deps+=("oc")
    command -v curl >/dev/null 2>&1 || missing_deps+=("curl")
    command -v tar >/dev/null 2>&1 || missing_deps+=("tar")

    # Check for JSON parser (jq required)
    if ! command -v jq >/dev/null 2>&1; then
        missing_deps+=("jq")
    fi

    if [ ${#missing_deps[@]} -gt 0 ]; then
        echo_error "Missing required dependencies: ${missing_deps[*]}"
        return 1
    fi

    return 0
}

echo_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

echo_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

echo_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

echo_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# JSON parsing helper function (uses jq)
parse_json() {
    local json_data="$1"
    local json_path="$2"

    echo "$json_data" | jq -r "$json_path" 2>/dev/null || echo ""
}

# JSON array length helper
json_array_length() {
    local json_data="$1"

    echo "$json_data" | jq 'length' 2>/dev/null || echo "0"
}

# Function to detect Keycloak configuration
detect_keycloak_config() {
    echo_info "=== Detecting Keycloak Configuration ==="

    # Try to find Keycloak route
    local keycloak_route=$(oc get route -n "$KEYCLOAK_NAMESPACE" keycloak -o jsonpath='{.spec.host}' 2>/dev/null || echo "")

    if [ -n "$keycloak_route" ]; then
        KEYCLOAK_URL="https://$keycloak_route"
        echo_success "Found Keycloak route: $KEYCLOAK_URL"
    else
        echo_warning "Keycloak route not found, trying service discovery..."

        # Try to find Keycloak service
        local keycloak_service=$(oc get svc -n "$KEYCLOAK_NAMESPACE" keycloak -o jsonpath='{.metadata.name}' 2>/dev/null || echo "")
        if [ -n "$keycloak_service" ]; then
            KEYCLOAK_URL="http://keycloak.$KEYCLOAK_NAMESPACE.svc.cluster.local:8080"
            echo_success "Found Keycloak service: $KEYCLOAK_URL"
        else
            echo_error "Keycloak not found. Please ensure Keycloak is deployed."
            return 1
        fi
    fi

    # Get client credentials from secret
    CLIENT_ID="cost-management-operator"

    # Try different secret name patterns
    for secret_pattern in "keycloak-client-secret-cost-management-operator" "keycloak-client-secret-cost-management-service-account" "credential-$CLIENT_ID" "keycloak-client-$CLIENT_ID" "$CLIENT_ID-secret"; do
        CLIENT_SECRET=$(oc get secret "$secret_pattern" -n "$KEYCLOAK_NAMESPACE" -o jsonpath='{.data.CLIENT_SECRET}' 2>/dev/null | base64 -d || echo "")
        if [ -n "$CLIENT_SECRET" ]; then
            echo_success "Found client secret in: $secret_pattern"
            break
        fi
    done

    if [ -z "$CLIENT_SECRET" ]; then
        echo_error "Client secret not found. Please check Keycloak client configuration."
        echo_info "Available secrets in $KEYCLOAK_NAMESPACE namespace:"
        oc get secrets -n "$KEYCLOAK_NAMESPACE" | grep -E "(keycloak|client|secret)" || echo "No matching secrets found"
        return 1
    fi

    return 0
}

# Function to validate JWT authentication (preflight smoke test)
validate_jwt_authentication() {
    echo_info "=== Preflight: JWT Authentication Validation ==="
    echo_info "Running smoke tests to verify JWT authentication is working..."
    echo ""

    # Get ingress service URL
    local ingress_url=$(get_service_url "ingress" "")
    echo_info "Testing ingress at: $ingress_url"

    # Check if we can reach the service at all
    local health_check=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$ingress_url/" 2>/dev/null || echo "000")
    if [ "$health_check" = "000" ]; then
        echo_error "Cannot reach ingress service. Skipping JWT validation tests."
        echo_warning "Service may not be ready yet or port-forwarding is required"
        return 1
    fi

    local test_passed=0
    local test_failed=0

    # Test 1: Request without JWT token (should be rejected with 401)
    echo_info "Test 1: Request without JWT token"
    local http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$ingress_url/v1/upload" 2>/dev/null || echo "000")
    if [ "$http_code" = "401" ]; then
        echo_success "  ✓ Correctly rejected request without token (401)"
        test_passed=$((test_passed + 1))
    else
        echo_warning "  ⚠ Expected 401, got $http_code (may indicate route/service issue)"
        # Not failing here as 503 might just mean routing issue, not auth bypass
    fi

    # Test 2: Request with malformed JWT token (should be rejected)
    echo_info "Test 2: Request with malformed JWT token"
    http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
        -H "Authorization: Bearer invalid.malformed.token" \
        "$ingress_url/v1/upload" 2>/dev/null || echo "000")
    if [ "$http_code" = "401" ]; then
        echo_success "  ✓ Correctly rejected malformed token (401)"
        test_passed=$((test_passed + 1))
    else
        echo_warning "  ⚠ Expected 401, got $http_code"
    fi

    # Test 3: Request with self-signed JWT (wrong signature)
    echo_info "Test 3: Request with JWT signed by wrong key"

    if command -v openssl >/dev/null 2>&1; then
        # Generate a fake JWT with valid structure but wrong signature
        local temp_key=$(mktemp)
        openssl genrsa -out "$temp_key" 2048 2>/dev/null

        local header_b64=$(echo -n '{"alg":"RS256","typ":"JWT","kid":"fake-key"}' | openssl base64 -e | tr -d '=' | tr '/+' '_-' | tr -d '\n')
        local payload_b64=$(echo -n '{"sub":"attacker","iss":"https://fake-issuer.com","aud":"cost-management-operator","exp":9999999999}' | openssl base64 -e | tr -d '=' | tr '/+' '_-' | tr -d '\n')
        local signature=$(echo -n "${header_b64}.${payload_b64}" | openssl dgst -sha256 -sign "$temp_key" | openssl base64 -e | tr -d '=' | tr '/+' '_-' | tr -d '\n')
        local fake_jwt="${header_b64}.${payload_b64}.${signature}"

        http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
            -H "Authorization: Bearer $fake_jwt" \
            "$ingress_url/v1/upload" 2>/dev/null || echo "000")

        rm -f "$temp_key"

        if [ "$http_code" = "401" ] || [ "$http_code" = "403" ]; then
            echo_success "  ✓ Correctly rejected JWT with invalid signature ($http_code)"
            test_passed=$((test_passed + 1))
        elif [ "$http_code" = "200" ] || [ "$http_code" = "202" ]; then
            echo_error "  ✗ CRITICAL: JWT with fake signature was ACCEPTED!"
            echo_error "  This is a security vulnerability - any JWT is being accepted!"
            test_failed=$((test_failed + 1))
        else
            echo_warning "  ⚠ Got HTTP $http_code (expected 401/403)"
        fi
    else
        echo_info "  Skipping (openssl not available)"
    fi

    echo ""
    if [ $test_passed -ge 2 ]; then
        echo_success "JWT authentication preflight checks passed ($test_passed tests)"
        echo_info "  → Envoy is properly validating JWT tokens"
        echo_info "  → Ready to proceed with authenticated upload"
        return 0
    else
        echo_warning "JWT authentication validation incomplete ($test_passed passed, $test_failed failed)"
        echo_warning "  This may indicate JWT authentication is not properly configured"
        echo_warning "  Proceeding with upload test, but authentication may fail"
        return 0  # Don't fail completely, just warn
    fi
}

# Function to get JWT token from Keycloak
get_jwt_token() {
    echo_info "=== Getting JWT Token from Keycloak ==="

    if [ -z "$KEYCLOAK_URL" ] || [ -z "$CLIENT_ID" ] || [ -z "$CLIENT_SECRET" ]; then
        echo_error "Keycloak configuration not available. Run detect_keycloak_config first."
        return 1
    fi

    # Determine the correct realm and token endpoint
    local realm="kubernetes"
    # RHBK v22+ does not use /auth prefix
    local token_url="$KEYCLOAK_URL/realms/$realm/protocol/openid-connect/token"

    echo_info "Getting token from: $token_url"
    echo_info "Client ID: $CLIENT_ID"

    # Request JWT token using client credentials flow
    local token_response=$(curl -s -k -X POST "$token_url" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "grant_type=client_credentials" \
        -d "client_id=$CLIENT_ID" \
        -d "client_secret=$CLIENT_SECRET" 2>/dev/null)

    local curl_exit=$?
    if [ $curl_exit -ne 0 ] || [ -z "$token_response" ]; then
        echo_error "Failed to connect to Keycloak token endpoint (curl exit code: $curl_exit)"
        return 1
    fi

    # Extract access token from response using jq
    JWT_TOKEN=$(echo "$token_response" | jq -r '.access_token // empty' 2>/dev/null)
    local expires_in=$(echo "$token_response" | jq -r '.expires_in // 0' 2>/dev/null)

    if [ -z "$JWT_TOKEN" ] || [ "$JWT_TOKEN" = "null" ]; then
        echo_error "Failed to extract JWT token from response"
        echo_info "Token response: $token_response"
        return 1
    fi

    # Calculate expiry time
    JWT_TOKEN_EXPIRY=$(($(date +%s) + ${expires_in:-300}))

    echo_success "JWT token obtained successfully"
    echo_info "Token length: ${#JWT_TOKEN} characters"
    echo_info "Token expires in: ${expires_in:-300} seconds"

    # Optionally decode and display token info (first part only for security)
    local token_header=$(echo "$JWT_TOKEN" | cut -d'.' -f1)
    echo_info "Token header (base64): ${token_header:0:50}..."

    return 0
}

# Note: JWT token is obtained via get_jwt_token() and used for both ingress and backend API

# Function to test JWT authentication against backend API
test_jwt_backend_auth() {
    echo_info "=== Testing JWT Authentication on Backend API ==="

    if [ -z "$JWT_TOKEN" ]; then
        echo_error "JWT token not available. Run get_jwt_token first."
        return 1
    fi

    # Get backend API URL
    local backend_url=$(get_service_url "ros-api" "")
    echo_info "Testing backend API at: $backend_url"

    local test_passed=0
    local test_failed=0

    # Test 1: Request without JWT token (should be rejected)
    echo_info "Test 1: Request without JWT token"
    local recommendations_endpoint="$backend_url/api/cost-management/v1/recommendations/openshift"
    local http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$recommendations_endpoint" 2>/dev/null || echo "000")
    if [ "$http_code" = "401" ] || [ "$http_code" = "403" ]; then
        echo_success "  ✓ Correctly rejected request without token ($http_code)"
        test_passed=$((test_passed + 1))
    elif [ "$http_code" = "200" ]; then
        echo_warning "  ⚠ Backend returned 200 without auth (may not be behind Envoy+OPA)"
        # Check if this is the direct backend port
        echo_info "  Note: Direct backend access (port 8000) bypasses authentication"
        echo_info "        Use the service/route to test through Envoy proxy"
    else
        echo_warning "  ⚠ Got HTTP $http_code (may indicate routing issue)"
    fi

    # Test 2: Request with invalid JWT token
    echo_info "Test 2: Request with invalid JWT token"
    http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
        -H "Authorization: Bearer invalid.jwt.token" \
        "$recommendations_endpoint" 2>/dev/null || echo "000")
    if [ "$http_code" = "401" ] || [ "$http_code" = "403" ]; then
        echo_success "  ✓ Correctly rejected invalid token ($http_code)"
        test_passed=$((test_passed + 1))
    else
        echo_warning "  ⚠ Expected 401/403, got $http_code"
    fi

    # Test 3: Request with valid JWT token (should succeed)
    echo_info "Test 3: Request with valid Keycloak JWT token"
    local response=$(curl -s -w "\n%{http_code}" --max-time 10 \
        -H "Authorization: Bearer $JWT_TOKEN" \
        "$recommendations_endpoint" 2>/dev/null)

    http_code=$(echo "$response" | tail -n 1)
    local response_body=$(echo "$response" | sed '$d')

    if [ "$http_code" = "200" ] || [ "$http_code" = "404" ]; then
        echo_success "  ✓ Successfully authenticated with JWT token (HTTP $http_code)"
        echo_info "  Response: $response_body"
        test_passed=$((test_passed + 1))
    else
        echo_error "  ✗ Failed to authenticate with valid token (HTTP $http_code)"
        echo_error "  Response: $response_body"
        test_failed=$((test_failed + 1))
    fi

    echo ""
    if [ $test_passed -ge 2 ]; then
        echo_success "JWT backend authentication tests passed ($test_passed tests)"
        return 0
    else
        echo_error "JWT backend authentication tests failed ($test_failed failures)"
        return 1
    fi
}

# Function to query backend API with JWT token
# Returns: Sets global variables QUERY_HTTP_CODE and QUERY_RESPONSE_BODY
query_backend_api() {
    local endpoint="$1"
    local description="$2"

    QUERY_HTTP_CODE=""
    QUERY_RESPONSE_BODY=""

    if [ -z "$JWT_TOKEN" ]; then
        echo_error "JWT token not available"
        return 1
    fi

    local backend_url=$(get_service_url "ros-api" "")
    local full_url="$backend_url$endpoint"

    echo_info "Querying: $description"
    echo_info "  URL: $full_url"

    local response=$(curl -s -w "\n%{http_code}" --max-time 10 \
        -H "Authorization: Bearer $JWT_TOKEN" \
        -H "Accept: application/json" \
        "$full_url" 2>/dev/null)

    QUERY_HTTP_CODE=$(echo "$response" | tail -n 1)
    QUERY_RESPONSE_BODY=$(echo "$response" | sed '$d')

    if [ "$QUERY_HTTP_CODE" = "200" ]; then
        echo_success "  ✓ HTTP $QUERY_HTTP_CODE"
        echo "$QUERY_RESPONSE_BODY" | jq '.' 2>/dev/null || echo "$QUERY_RESPONSE_BODY"
        return 0
    else
        echo_error "  ✗ HTTP $QUERY_HTTP_CODE"
        echo_error "  Response: $QUERY_RESPONSE_BODY"
        return 1
    fi
}

# Function to check if recommendations API response contains data (not empty array)
check_recommendations_has_data() {
    local response_body="$1"

    if [ -z "$response_body" ]; then
        return 1
    fi

    # Count items in the response (supports both {data: [...]} and direct array formats)
    local rec_count=0
    rec_count=$(echo "$response_body" | jq 'if type == "object" and has("data") then (.data | length) elif type == "array" then length else 0 end' 2>/dev/null || echo "0")

    # Return success if count > 0
    [ "$rec_count" -gt 0 ]
}

# Function to get service URL
get_service_url() {
    local service_name="$1"
    local path="$2"

    # Try to get OpenShift route first
    local route_name="$HELM_RELEASE_NAME-$service_name"

    # Special handling for ros-api which has route named "main"
    if [ "$service_name" = "ros-api" ]; then
        # Try the "main" route first (common alias for the backend API)
        route_name="$HELM_RELEASE_NAME-main"
    fi

    local route_host=$(oc get route "$route_name" -n "$NAMESPACE" -o jsonpath='{.spec.host}' 2>/dev/null)

    # If not found and this is ros-api, try the full service name
    if [ -z "$route_host" ] && [ "$service_name" = "ros-api" ]; then
        route_name="$HELM_RELEASE_NAME-ros-api"
        route_host=$(oc get route "$route_name" -n "$NAMESPACE" -o jsonpath='{.spec.host}' 2>/dev/null)
    fi

    if [ -n "$route_host" ]; then
        # Check if route uses TLS
        local tls_termination=$(oc get route "$route_name" -n "$NAMESPACE" -o jsonpath='{.spec.tls.termination}' 2>/dev/null)
        local route_path=$(oc get route "$route_name" -n "$NAMESPACE" -o jsonpath='{.spec.path}' 2>/dev/null)

        # Remove trailing slash from route_path if present to avoid double slashes
        route_path="${route_path%/}"

        if [ -n "$tls_termination" ]; then
            echo "https://$route_host${route_path}$path"
        else
            echo "http://$route_host${route_path}$path"
        fi
    else
        # Fallback to service (for port-forward or internal access)
        # Get the actual service port
        local service_port=$(oc get svc "$HELM_RELEASE_NAME-$service_name" -n "$NAMESPACE" -o jsonpath='{.spec.ports[0].port}' 2>/dev/null || echo "8080")
        echo "http://$HELM_RELEASE_NAME-$service_name.$NAMESPACE.svc.cluster.local:${service_port}$path"
    fi
}

# Cross-platform date function
cross_platform_date_ago() {
    local minutes_ago="$1"
    local format="${2:-+%Y-%m-%d %H:%M:%S -0000 UTC}"
    local seconds_ago=$((minutes_ago * 60))
    local target_epoch=$(($(date +%s) - seconds_ago))

    # Try BSD date format first (macOS)
    if TZ=UTC date -r "$target_epoch" "$format" 2>/dev/null; then
        return 0
    # Try GNU date format (Linux)
    elif TZ=UTC date -d "@$target_epoch" "$format" 2>/dev/null; then
        return 0
    else
        # Fallback: use epoch time directly
        echo "$target_epoch"
        return 1
    fi
}

# Function to create test data (same as original)
create_test_data() {
    echo_info "Creating test data with current timestamps..." >&2

    # Generate dynamic timestamps for current data (multiple intervals for better recommendations)
    local now_date=$(date -u +%Y-%m-%d)
    local interval_start_1=$(cross_platform_date_ago 75)  # 75 minutes ago
    local interval_end_1=$(cross_platform_date_ago 60)    # 60 minutes ago
    local interval_start_2=$(cross_platform_date_ago 60)  # 60 minutes ago
    local interval_end_2=$(cross_platform_date_ago 45)    # 45 minutes ago
    local interval_start_3=$(cross_platform_date_ago 45)  # 45 minutes ago
    local interval_end_3=$(cross_platform_date_ago 30)    # 30 minutes ago
    local interval_start_4=$(cross_platform_date_ago 30)  # 30 minutes ago
    local interval_end_4=$(cross_platform_date_ago 15)    # 15 minutes ago

    echo_info "Using timestamps:" >&2
    echo_info "  Report date: $now_date" >&2
    echo_info "  Interval 1: $interval_start_1 to $interval_end_1" >&2
    echo_info "  Interval 2: $interval_start_2 to $interval_end_2" >&2
    echo_info "  Interval 3: $interval_start_3 to $interval_end_3" >&2
    echo_info "  Interval 4: $interval_start_4 to $interval_end_4" >&2

    # Create a temporary CSV file with proper ROS format and current timestamps
    local test_csv=$(mktemp)
    cat > "$test_csv" << EOF
report_period_start,report_period_end,interval_start,interval_end,container_name,pod,owner_name,owner_kind,workload,workload_type,namespace,image_name,node,resource_id,cpu_request_container_avg,cpu_request_container_sum,cpu_limit_container_avg,cpu_limit_container_sum,cpu_usage_container_avg,cpu_usage_container_min,cpu_usage_container_max,cpu_usage_container_sum,cpu_throttle_container_avg,cpu_throttle_container_max,cpu_throttle_container_sum,memory_request_container_avg,memory_request_container_sum,memory_limit_container_avg,memory_limit_container_sum,memory_usage_container_avg,memory_usage_container_min,memory_usage_container_max,memory_usage_container_sum,memory_rss_usage_container_avg,memory_rss_usage_container_min,memory_rss_usage_container_max,memory_rss_usage_container_sum
$now_date,$now_date,$interval_start_1,$interval_end_1,test-container,test-pod-123,test-deployment,Deployment,test-workload,deployment,test-namespace,quay.io/test/image:latest,worker-node-1,resource-123,0.5,0.5,1.0,1.0,0.247832,0.185671,0.324131,0.247832,0.001,0.002,0.001,536870912,536870912,1073741824,1073741824,413587266.064516,410009344,420900544,413587266.064516,393311537.548387,390293568,396371392,393311537.548387
$now_date,$now_date,$interval_start_2,$interval_end_2,test-container,test-pod-123,test-deployment,Deployment,test-workload,deployment,test-namespace,quay.io/test/image:latest,worker-node-1,resource-123,0.5,0.5,1.0,1.0,0.265423,0.198765,0.345678,0.265423,0.0012,0.0025,0.0012,536870912,536870912,1073741824,1073741824,427891456.123456,422014016,435890624,427891456.123456,407654321.987654,403627568,411681024,407654321.987654
$now_date,$now_date,$interval_start_3,$interval_end_3,test-container,test-pod-123,test-deployment,Deployment,test-workload,deployment,test-namespace,quay.io/test/image:latest,worker-node-1,resource-123,0.5,0.5,1.0,1.0,0.289567,0.210987,0.367890,0.289567,0.0008,0.0018,0.0008,536870912,536870912,1073741824,1073741824,445678901.234567,441801728,449556074,445678901.234567,425987654.321098,421960800,430014256,425987654.321098
$now_date,$now_date,$interval_start_4,$interval_end_4,test-container,test-pod-123,test-deployment,Deployment,test-workload,deployment,test-namespace,quay.io/test/image:latest,worker-node-1,resource-123,0.5,0.5,1.0,1.0,0.234567,0.189012,0.298765,0.234567,0.0005,0.0012,0.0005,536870912,536870912,1073741824,1073741824,398765432.101234,394887168,402643696,398765432.101234,378654321.098765,374627568,382681024,378654321.098765
EOF

    echo "$test_csv"
}

# Global variables for source registration
SOURCES_API_URL=""
TEST_SOURCE_ID=""
TEST_SOURCE_NAME=""
ORG_ID="12345"

# Function to register OCP source via Sources API
# This creates a source that Koku will recognize when processing uploads
register_ocp_source() {
    local cluster_id="$1"

    if [ -z "$cluster_id" ]; then
        echo_error "Cluster ID is required for source registration"
        return 1
    fi

    echo_info "=== Registering OCP Source via Sources API ==="

    # Get Sources API service URL (internal cluster service)
    local sources_svc="${HELM_RELEASE_NAME}-sources-api.${NAMESPACE}.svc.cluster.local:8000"
    SOURCES_API_URL="http://${sources_svc}/api/sources/v1.0"

    echo_info "Sources API URL: $SOURCES_API_URL"
    echo_info "Cluster ID: $cluster_id"
    echo_info "Org ID: $ORG_ID"

    # Find a pod to execute curl from (use sources-listener or any koku pod)
    local exec_pod=$(oc get pods -n "$NAMESPACE" -l "app.kubernetes.io/component=sources-listener" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    if [ -z "$exec_pod" ]; then
        exec_pod=$(oc get pods -n "$NAMESPACE" -l "app.kubernetes.io/component=listener" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    fi
    if [ -z "$exec_pod" ]; then
        echo_error "No suitable pod found to execute Sources API calls"
        return 1
    fi
    echo_info "Using pod for API calls: $exec_pod"

    # Step 1: Get OpenShift source type ID
    echo_info "Getting OpenShift source type ID..."
    local source_types_response=$(oc exec -n "$NAMESPACE" "$exec_pod" -- \
        curl -s "${SOURCES_API_URL}/source_types" \
        -H "Content-Type: application/json" \
        -H "x-rh-sources-org-id: $ORG_ID" 2>/dev/null)

    local ocp_source_type_id=$(echo "$source_types_response" | jq -r '.data[] | select(.name == "openshift") | .id' 2>/dev/null)
    if [ -z "$ocp_source_type_id" ] || [ "$ocp_source_type_id" = "null" ]; then
        echo_error "Failed to find OpenShift source type"
        echo_info "Available source types: $(echo "$source_types_response" | jq -r '.data[].name' 2>/dev/null | tr '\n' ', ')"
        return 1
    fi
    echo_info "OpenShift source type ID: $ocp_source_type_id"

    # Step 2: Get Cost Management application type ID
    echo_info "Getting Cost Management application type ID..."
    local app_types_response=$(oc exec -n "$NAMESPACE" "$exec_pod" -- \
        curl -s "${SOURCES_API_URL}/application_types" \
        -H "Content-Type: application/json" \
        -H "x-rh-sources-org-id: $ORG_ID" 2>/dev/null)

    local cost_mgmt_app_type_id=$(echo "$app_types_response" | jq -r '.data[] | select(.name == "/insights/platform/cost-management") | .id' 2>/dev/null)
    if [ -z "$cost_mgmt_app_type_id" ] || [ "$cost_mgmt_app_type_id" = "null" ]; then
        echo_error "Failed to find Cost Management application type"
        echo_info "Available app types: $(echo "$app_types_response" | jq -r '.data[].name' 2>/dev/null | tr '\n' ', ')"
        return 1
    fi
    echo_info "Cost Management application type ID: $cost_mgmt_app_type_id"

    # Step 3: Create the OCP source
    TEST_SOURCE_NAME="E2E Test OCP Source $(date +%s)"
    echo_info "Creating OCP source: $TEST_SOURCE_NAME"

    local create_source_payload=$(cat <<EOF
{"name": "$TEST_SOURCE_NAME", "source_type_id": "$ocp_source_type_id", "source_ref": "$cluster_id"}
EOF
)

    local source_response=$(oc exec -n "$NAMESPACE" "$exec_pod" -- \
        curl -s -X POST "${SOURCES_API_URL}/sources" \
        -H "Content-Type: application/json" \
        -H "x-rh-sources-org-id: $ORG_ID" \
        -d "$create_source_payload" 2>/dev/null)

    TEST_SOURCE_ID=$(echo "$source_response" | jq -r '.id' 2>/dev/null)
    if [ -z "$TEST_SOURCE_ID" ] || [ "$TEST_SOURCE_ID" = "null" ]; then
        echo_error "Failed to create source"
        echo_info "Response: $source_response"
        return 1
    fi
    echo_success "Source created with ID: $TEST_SOURCE_ID"

    # Step 4: Create authentication for the source
    echo_info "Creating authentication for source..."
    local auth_payload=$(cat <<EOF
{"resource_type": "Source", "resource_id": "$TEST_SOURCE_ID", "authtype": "token", "username": "$cluster_id"}
EOF
)

    local auth_response=$(oc exec -n "$NAMESPACE" "$exec_pod" -- \
        curl -s -X POST "${SOURCES_API_URL}/authentications" \
        -H "Content-Type: application/json" \
        -H "x-rh-sources-org-id: $ORG_ID" \
        -d "$auth_payload" 2>/dev/null)

    local auth_id=$(echo "$auth_response" | jq -r '.id' 2>/dev/null)
    if [ -z "$auth_id" ] || [ "$auth_id" = "null" ]; then
        echo_warning "Authentication creation may have failed (non-critical)"
        echo_info "Response: $auth_response"
    else
        echo_success "Authentication created with ID: $auth_id"
    fi

    # Step 5: Create Cost Management application
    echo_info "Creating Cost Management application..."
    local app_payload=$(cat <<EOF
{"source_id": "$TEST_SOURCE_ID", "application_type_id": "$cost_mgmt_app_type_id", "extra": {"bucket": "koku-bucket", "cluster_id": "$cluster_id"}}
EOF
)

    local app_response=$(oc exec -n "$NAMESPACE" "$exec_pod" -- \
        curl -s -X POST "${SOURCES_API_URL}/applications" \
        -H "Content-Type: application/json" \
        -H "x-rh-sources-org-id: $ORG_ID" \
        -d "$app_payload" 2>/dev/null)

    local app_id=$(echo "$app_response" | jq -r '.id' 2>/dev/null)
    if [ -z "$app_id" ] || [ "$app_id" = "null" ]; then
        echo_error "Failed to create Cost Management application"
        echo_info "Response: $app_response"
        return 1
    fi
    echo_success "Cost Management application created with ID: $app_id"

    # Step 6: Wait for Koku to process the source (via Kafka event)
    # The Sources API publishes to Kafka, then sources-listener creates the provider
    echo_info "Waiting for Koku to process the new source via Kafka..."

    local db_pod=$(oc get pods -n "$NAMESPACE" -l "app.kubernetes.io/name=database" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    if [ -z "$db_pod" ]; then
        # Try default database pod name
        db_pod="cost-onprem-database-0"
    fi

    local max_wait=120  # 2 minutes max
    local wait_interval=5
    local elapsed=0
    local provider_found=false

    local provider_count
    while [ $elapsed -lt $max_wait ]; do
        # Check if provider exists in Koku database for this cluster
        provider_count=$(oc exec -n "$NAMESPACE" "$db_pod" -- \
            psql -U koku -d koku -t -c \
            "SELECT COUNT(*) FROM api_provider p
             JOIN api_providerauthentication a ON p.authentication_id = a.id
             WHERE a.credentials->>'cluster_id' = '$cluster_id'
                OR p.additional_context->>'cluster_id' = '$cluster_id';" 2>/dev/null | tr -d ' \n\t')
        # Default to 0 if empty or not a number
        provider_count=${provider_count:-0}

        if [ "$provider_count" -gt 0 ] 2>/dev/null; then
            provider_found=true
            echo_success "Provider created in Koku database"
            break
        fi

        echo_info "  Waiting for provider to be created... ($elapsed/$max_wait seconds)"
        sleep $wait_interval
        elapsed=$((elapsed + wait_interval))
    done

    if [ "$provider_found" = "false" ]; then
        echo_error "Timeout waiting for provider to be created in Koku database"
        echo_info "Check sources-listener logs: oc logs -n $NAMESPACE deployment/cost-onprem-sources-listener --tail=50"
        return 1
    fi

    echo_success "OCP source registered successfully"
    echo_info "  Source ID: $TEST_SOURCE_ID"
    echo_info "  Source Name: $TEST_SOURCE_NAME"
    echo_info "  Cluster ID: $cluster_id"

    return 0
}

# Function to cleanup test source after test
cleanup_test_source() {
    if [ -z "$TEST_SOURCE_ID" ]; then
        return 0
    fi

    echo_info "Cleaning up test source: $TEST_SOURCE_ID"

    # Find a pod to execute curl from
    local exec_pod=$(oc get pods -n "$NAMESPACE" -l "app.kubernetes.io/component=sources-listener" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    if [ -z "$exec_pod" ]; then
        exec_pod=$(oc get pods -n "$NAMESPACE" -l "app.kubernetes.io/component=listener" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    fi

    if [ -n "$exec_pod" ] && [ -n "$SOURCES_API_URL" ]; then
        oc exec -n "$NAMESPACE" "$exec_pod" -- \
            curl -s -X DELETE "${SOURCES_API_URL}/sources/${TEST_SOURCE_ID}" \
            -H "x-rh-sources-org-id: $ORG_ID" 2>/dev/null || true
        echo_info "Test source deleted"
    fi

    TEST_SOURCE_ID=""
}

# Function to upload test data with JWT authentication
upload_test_data_jwt() {
    echo_info "=== STEP 6: Upload Test Data with JWT Authentication ===="

    if [ -z "$JWT_TOKEN" ]; then
        echo_error "JWT token not available. Please run get_jwt_token first."
        return 1
    fi

    local test_csv=$(create_test_data)
    local test_dir=$(mktemp -d)
    local csv_filename="openshift_usage_report.csv"
    local tar_filename="cost-mgmt.tar.gz"

    # Copy CSV to temporary directory with expected filename
    if ! cp "$test_csv" "$test_dir/$csv_filename"; then
        echo_error "Failed to copy CSV file to temporary directory"
        rm -f "$test_csv"
        rm -rf "$test_dir"
        return 1
    fi

    # Verify the file exists and has content
    if [ ! -f "$test_dir/$csv_filename" ] || [ ! -s "$test_dir/$csv_filename" ]; then
        echo_error "CSV file not found or is empty in temporary directory"
        rm -f "$test_csv"
        rm -rf "$test_dir"
        return 1
    fi

    # Use the pre-generated cluster ID (set before calling this function)
    if [ -z "$UPLOAD_CLUSTER_ID" ]; then
        echo_error "UPLOAD_CLUSTER_ID not set. Source must be registered first."
        rm -f "$test_csv"
        rm -rf "$test_dir"
        return 1
    fi
    echo_info "Using registered cluster ID: $UPLOAD_CLUSTER_ID"

    # Create manifest.json (required by ingress service)
    local manifest_file="$test_dir/manifest.json"
    cat > "$manifest_file" << EOF
{
  "uuid": "$(uuidgen | tr '[:upper:]' '[:lower:]')",
  "cluster_id": "$UPLOAD_CLUSTER_ID",
  "cluster_alias": "test-cluster",
  "date": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "files": ["$csv_filename"],
  "resource_optimization_files": ["$csv_filename"],
  "certified": true,
  "operator_version": "1.0.0",
  "daily_reports": false
}
EOF

    # Create tar.gz file with both CSV and manifest.json
    echo_info "Creating tar.gz archive..."
    if ! (cd "$test_dir" && tar -czf "$tar_filename" "$csv_filename" "manifest.json"); then
        echo_error "Failed to create tar.gz archive"
        rm -f "$test_csv"
        rm -rf "$test_dir"
        return 1
    fi

    # Verify tar.gz file was created
    if [ ! -f "$test_dir/$tar_filename" ]; then
        echo_error "tar.gz file was not created"
        rm -f "$test_csv"
        rm -rf "$test_dir"
        return 1
    fi

    echo_info "Uploading tar.gz file with JWT authentication..."

    # Check if JWT token is still valid
    if [ -n "$JWT_TOKEN_EXPIRY" ]; then
        local now=$(date +%s)
        if [ "$now" -ge "$JWT_TOKEN_EXPIRY" ]; then
            echo_warning "JWT token has expired. Please refresh the token."
        fi
    fi

    # Upload the tar.gz file using curl with JWT Bearer token
    local upload_url=$(get_service_url "ingress" "/v1/upload")
    echo_info "Uploading to: $upload_url"
    echo_info "Using JWT token authentication"

    local response=$(curl -s -w "\n%{http_code}" \
        -F "file=@${test_dir}/${tar_filename};type=application/vnd.redhat.hccm.filename+tgz" \
        -H "Authorization: Bearer $JWT_TOKEN" \
        -H "x-rh-request-id: test-request-$(date +%s)" \
        "$upload_url" 2>&1)

    local curl_exit=$?

    # Extract HTTP code from last line (more portable way)
    local http_code=$(echo "$response" | tail -n 1)
    local response_body=$(echo "$response" | sed '$d')

    # Cleanup
    rm -f "$test_csv"
    rm -rf "$test_dir"

    if [ $curl_exit -ne 0 ]; then
        echo_error "Upload failed - curl error (exit code: $curl_exit)"
        echo_error "Response: $response_body"
        return 1
    fi

    if [ "$http_code" != "202" ] && [ "$http_code" != "200" ]; then
        echo_error "Upload failed with HTTP $http_code"
        echo_error "Response: $response_body"
        return 1
    fi

    echo_success "Upload successful! HTTP $http_code"
    echo_info "Response: $response_body"

    return 0
}

# Function to verify upload was processed
verify_upload_processing() {
    echo_info "=== STEP 7: Verify Upload Processing ===="

    # Step 7a: Check ingress logs for upload activity
    echo_info "--- Step 7a: Ingress Upload Verification ---"
    echo_info "Checking ingress logs for upload processing..."
    local ingress_pod=$(oc get pods -n "$NAMESPACE" -l app.kubernetes.io/name=ingress -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

    if [ -n "$ingress_pod" ]; then
        # Get container names to find the right one
        local containers=$(oc get pod "$ingress_pod" -n "$NAMESPACE" -o jsonpath='{.spec.containers[*].name}' 2>/dev/null)
        echo_info "Found pod: $ingress_pod with containers: $containers"

        # Try to get logs from the ingress container
        echo_info "Recent ingress logs:"
        if echo "$containers" | grep -q "ingress"; then
            oc logs -n "$NAMESPACE" "$ingress_pod" -c ingress --tail=10 2>/dev/null | grep -i "upload\|jwt\|auth\|kafka\|error" || echo "No relevant log messages found"
        else
            # Fall back to first container
            local first_container=$(echo "$containers" | awk '{print $1}')
            oc logs -n "$NAMESPACE" "$ingress_pod" -c "$first_container" --tail=10 2>/dev/null | grep -i "upload\|jwt\|auth\|kafka\|error" || echo "No relevant log messages found"
        fi
    else
        echo_warning "Ingress pod not found (tried label: app.kubernetes.io/name=ingress)"
    fi

    # Step 7b: Check Koku Listener logs for Kafka message consumption
    echo_info ""
    echo_info "--- Step 7b: Koku Listener Verification ---"
    echo_info "Checking Koku listener for Kafka message consumption (platform.upload.announce)..."
    local listener_pod=$(oc get pods -n "$NAMESPACE" -l "app.kubernetes.io/component=listener" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

    if [ -n "$listener_pod" ]; then
        echo_info "Found Koku listener pod: $listener_pod"
        echo_info "Recent Koku listener logs:"
        oc logs -n "$NAMESPACE" "$listener_pod" --tail=15 2>/dev/null | grep -i "kafka\|message\|upload\|announce\|processing\|error" || echo "No relevant log messages found"
    else
        echo_warning "Koku listener pod not found (tried label: app.kubernetes.io/component=listener)"
        echo_info "Trying alternative label..."
        listener_pod=$(oc get pods -n "$NAMESPACE" -l "app.kubernetes.io/name=listener" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
        if [ -n "$listener_pod" ]; then
            echo_info "Found Koku listener pod: $listener_pod"
            oc logs -n "$NAMESPACE" "$listener_pod" --tail=15 2>/dev/null | grep -i "kafka\|message\|upload\|announce\|processing\|error" || echo "No relevant log messages found"
        fi
    fi

    # Step 7c: Check Koku/MASU worker logs for cost data processing
    echo_info ""
    echo_info "--- Step 7c: Koku/MASU Worker Verification ---"
    echo_info "Checking Koku workers for cost data processing..."

    # Check MASU pod for processing activity
    local masu_pod=$(oc get pods -n "$NAMESPACE" -l "app.kubernetes.io/component=masu" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    if [ -n "$masu_pod" ]; then
        echo_info "Found MASU pod: $masu_pod"
        echo_info "Recent MASU logs:"
        oc logs -n "$NAMESPACE" "$masu_pod" --tail=10 2>/dev/null | grep -i "processing\|download\|report\|complete\|error" || echo "No relevant log messages found"
    fi

    # Check Celery worker pods for task processing
    local worker_pods=$(oc get pods -n "$NAMESPACE" -l "app.kubernetes.io/component=worker" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)
    if [ -n "$worker_pods" ]; then
        local first_worker=$(echo "$worker_pods" | awk '{print $1}')
        echo_info "Found Celery worker pod: $first_worker"
        echo_info "Recent worker logs (first worker):"
        oc logs -n "$NAMESPACE" "$first_worker" --tail=10 2>/dev/null | grep -i "task\|download\|process\|complete\|error" || echo "No relevant log messages found"
    else
        echo_info "No Celery worker pods found - Koku may use a different worker configuration"
    fi

    # Step 7d: Check ROS Processor for downstream processing
    echo_info ""
    echo_info "--- Step 7d: ROS Processor Verification ---"
    local processor_pod=$(oc get pods -n "$NAMESPACE" -l "app.kubernetes.io/name=ros-processor" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

    if [ -n "$processor_pod" ]; then
        echo_info "Found ROS processor pod: $processor_pod"
        echo_info "Checking ROS processor logs for data processing (hccm.ros.events consumption)..."
        oc logs -n "$NAMESPACE" "$processor_pod" --tail=10 | grep -i "processing\|kafka\|event\|complete\|error" || echo "No processing messages found"
    else
        echo_info "ROS processor not deployed - checking if Koku processing is sufficient"
    fi

    return 0
}

# Function to verify Koku manifest processing
# Checks that the uploaded file was processed successfully through the Koku pipeline
verify_koku_manifest_processing() {
    local cluster_id="$1"
    local max_wait="${2:-120}"  # Default 2 minutes

    if [ -z "$cluster_id" ]; then
        echo_error "Cluster ID is required for manifest verification"
        return 1
    fi

    echo_info "=== Verifying Koku Manifest Processing ==="
    echo_info "Checking manifest and file processing status for cluster: $cluster_id"

    # Find postgres pod
    local db_pod=$(oc get pods -n "$NAMESPACE" -l "app.kubernetes.io/name=database" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    if [ -z "$db_pod" ]; then
        db_pod="cost-onprem-database-0"
    fi

    local wait_interval=10
    local elapsed=0
    local manifest_found=false
    local files_processed=false

    while [ $elapsed -lt $max_wait ]; do
        # Check for manifest associated with this cluster
        # The cluster_id is stored on the manifest table (from manifest.json in the uploaded tarball)
        local manifest_query="
            SELECT m.id, m.assembly_id, m.num_total_files, m.completed_datetime,
                   m.state::jsonb->'processing'->>'end' as processing_end,
                   m.state::jsonb->'summary'->>'failed' as summary_failed
            FROM reporting_common_costusagereportmanifest m
            WHERE m.cluster_id = '$cluster_id'
            ORDER BY m.creation_datetime DESC
            LIMIT 1;
        "

        local manifest_result=$(oc exec -n "$NAMESPACE" "$db_pod" -- \
            psql -U koku -d koku -t -A -F'|' -c "$manifest_query" 2>/dev/null | head -1)

        if [ -n "$manifest_result" ] && [ "$manifest_result" != "" ]; then
            manifest_found=true
            local manifest_id=$(echo "$manifest_result" | cut -d'|' -f1)
            local assembly_id=$(echo "$manifest_result" | cut -d'|' -f2)
            local num_files=$(echo "$manifest_result" | cut -d'|' -f3)
            local completed=$(echo "$manifest_result" | cut -d'|' -f4)
            local processing_end=$(echo "$manifest_result" | cut -d'|' -f5)
            local summary_failed=$(echo "$manifest_result" | cut -d'|' -f6)

            echo_info "  Manifest found (ID: $manifest_id)"
            echo_info "    Assembly ID: $assembly_id"
            echo_info "    Total files: $num_files"
            echo_info "    Completed: ${completed:-pending}"
            if [ -n "$processing_end" ] && [ "$processing_end" != "" ]; then
                echo_info "    Processing end: $processing_end"
            fi
            if [ "$summary_failed" = "true" ]; then
                echo_warning "    ⚠ Summary failed flag is set"
            fi

            # Check file processing status
            local status_query="
                SELECT report_name, status, completed_datetime
                FROM reporting_common_costusagereportstatus
                WHERE manifest_id = $manifest_id
                ORDER BY id;
            "

            local status_result=$(oc exec -n "$NAMESPACE" "$db_pod" -- \
                psql -U koku -d koku -t -A -F'|' -c "$status_query" 2>/dev/null)

            if [ -n "$status_result" ]; then
                local all_completed=true
                echo_info "  File processing status:"

                while IFS='|' read -r report_name status file_completed; do
                    # Status: 1 = SUCCESS, 2 = FAILED, 0 or empty = PENDING
                    local status_text="PENDING"
                    local status_icon="⏳"
                    if [ "$status" = "1" ]; then
                        status_text="SUCCESS"
                        status_icon="✓"
                    elif [ "$status" = "2" ]; then
                        status_text="FAILED"
                        status_icon="✗"
                        all_completed=false
                    else
                        all_completed=false
                    fi

                    echo_info "    $status_icon $report_name: $status_text"
                done <<< "$status_result"

                if [ "$all_completed" = "true" ]; then
                    files_processed=true
                    echo_success "All files processed successfully!"
                    return 0
                fi
            fi
        else
            echo_info "  [$elapsed/${max_wait}s] Waiting for manifest to be created..."
        fi

        sleep $wait_interval
        elapsed=$((elapsed + wait_interval))
    done

    if [ "$manifest_found" = "false" ]; then
        echo_error "❌ No manifest found for cluster: $cluster_id after ${max_wait}s"
        echo_info "Troubleshooting:"
        echo_info "  - Check Koku listener logs: oc logs -n $NAMESPACE -l app.kubernetes.io/component=listener --tail=50"
        echo_info "  - Check if upload notification reached Kafka"
        echo_info "  - Check all manifests and their cluster_ids:"
        echo_info "    oc exec -n $NAMESPACE $db_pod -- psql -U koku -d koku -c \\"
        echo_info "      \"SELECT cluster_id, creation_datetime FROM reporting_common_costusagereportmanifest ORDER BY creation_datetime DESC LIMIT 5;\""
        return 1
    fi

    if [ "$files_processed" = "false" ]; then
        echo_warning "⚠ Manifest found but file processing not complete after ${max_wait}s"
        echo_info "  Processing may still be in progress"
        echo_info "  Check MASU logs: oc logs -n $NAMESPACE -l app.kubernetes.io/component=masu --tail=50"
        return 1
    fi

    return 0
}

# Function to verify Koku summary tables are populated
# Checks that cost data has been aggregated into summary tables
verify_koku_summary_tables() {
    local cluster_id="$1"
    local max_wait="${2:-180}"  # Default 3 minutes (summary takes longer)

    if [ -z "$cluster_id" ]; then
        echo_error "Cluster ID is required for summary verification"
        return 1
    fi

    echo_info "=== Verifying Koku Summary Tables ==="
    echo_info "Checking OCP usage summary data for cluster: $cluster_id"

    # Find postgres pod
    local db_pod=$(oc get pods -n "$NAMESPACE" -l "app.kubernetes.io/name=database" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    if [ -z "$db_pod" ]; then
        db_pod="cost-onprem-database-0"
    fi

    # Get the tenant schema via the manifest table's cluster_id
    # The manifest is linked to provider -> customer, so we can get the schema name
    # Note: Koku adds 'org' prefix to org_id when creating schema (e.g., org_id='12345' -> schema='org12345')
    local schema_query="
        SELECT c.schema_name
        FROM reporting_common_costusagereportmanifest m
        JOIN api_provider p ON m.provider_id = p.uuid
        JOIN api_customer c ON p.customer_id = c.id
        WHERE m.cluster_id = '$cluster_id'
        LIMIT 1;
    "

    local schema_name=$(oc exec -n "$NAMESPACE" "$db_pod" -- \
        psql -U koku -d koku -t -A -c "$schema_query" 2>/dev/null | tr -d ' ')

    if [ -z "$schema_name" ]; then
        echo_error "Could not find tenant schema for cluster: $cluster_id"
        echo_info "  This may indicate no manifest exists yet for this cluster"
        echo_info "  Check: oc exec -n $NAMESPACE $db_pod -- psql -U koku -d koku -c \\"
        echo_info "    \"SELECT cluster_id FROM reporting_common_costusagereportmanifest WHERE cluster_id = '$cluster_id';\""
        return 1
    fi

    echo_info "  Tenant schema: $schema_name"

    local wait_interval=15
    local elapsed=0

    while [ $elapsed -lt $max_wait ]; do
        # Check for data in the OCP daily summary table
        local summary_query="
            SELECT
                COUNT(*) as row_count,
                COALESCE(SUM(pod_usage_cpu_core_hours), 0) as total_cpu_hours,
                COALESCE(SUM(pod_usage_memory_gigabyte_hours), 0) as total_memory_gb_hours,
                COUNT(DISTINCT namespace) as namespace_count,
                COUNT(DISTINCT node) as node_count
            FROM ${schema_name}.reporting_ocpusagelineitem_daily_summary
            WHERE cluster_id = '$cluster_id'
            AND namespace NOT LIKE '%unallocated%';
        "

        local summary_result=$(oc exec -n "$NAMESPACE" "$db_pod" -- \
            psql -U koku -d koku -t -A -F'|' -c "$summary_query" 2>/dev/null | head -1)

        if [ -n "$summary_result" ]; then
            local row_count=$(echo "$summary_result" | cut -d'|' -f1)
            local cpu_hours=$(echo "$summary_result" | cut -d'|' -f2)
            local memory_gb_hours=$(echo "$summary_result" | cut -d'|' -f3)
            local namespace_count=$(echo "$summary_result" | cut -d'|' -f4)
            local node_count=$(echo "$summary_result" | cut -d'|' -f5)

            if [ "$row_count" -gt 0 ] 2>/dev/null; then
                echo_success "Summary data found!"
                echo_info "  Summary statistics:"
                echo_info "    Rows: $row_count"
                echo_info "    CPU hours: $cpu_hours"
                echo_info "    Memory GB-hours: $memory_gb_hours"
                echo_info "    Unique namespaces: $namespace_count"
                echo_info "    Unique nodes: $node_count"

                # Show sample data
                local sample_query="
                    SELECT
                        usage_start::date,
                        namespace,
                        pod_usage_cpu_core_hours,
                        pod_usage_memory_gigabyte_hours
                    FROM ${schema_name}.reporting_ocpusagelineitem_daily_summary
                    WHERE cluster_id = '$cluster_id'
                    AND namespace NOT LIKE '%unallocated%'
                    ORDER BY usage_start DESC
                    LIMIT 3;
                "

                echo_info "  Sample data:"
                local sample_result=$(oc exec -n "$NAMESPACE" "$db_pod" -- \
                    psql -U koku -d koku -t -A -F'|' -c "$sample_query" 2>/dev/null)

                while IFS='|' read -r usage_date namespace cpu_hrs mem_hrs; do
                    if [ -n "$usage_date" ]; then
                        echo_info "    $usage_date | ${namespace:0:25} | CPU: ${cpu_hrs}h | Mem: ${mem_hrs}GB"
                    fi
                done <<< "$sample_result"

                return 0
            fi
        fi

        echo_info "  [$elapsed/${max_wait}s] Waiting for summary data to be populated..."
        sleep $wait_interval
        elapsed=$((elapsed + wait_interval))
    done

    echo_warning "⚠ No summary data found after ${max_wait}s"
    echo_info "  Summary table population may still be in progress"
    echo_info "  This is handled by Celery tasks which may take additional time"
    echo_info ""
    echo_info "  To check manually later (looking for cluster_id='$cluster_id'):"
    echo_info "    oc exec -n $NAMESPACE $db_pod -- psql -U koku -d koku -c \\"
    echo_info "      \"SELECT COUNT(*) FROM ${schema_name}.reporting_ocpusagelineitem_daily_summary WHERE cluster_id = '$cluster_id';\""
    echo_info ""
    echo_info "  Schema '$schema_name' was looked up via manifest->provider->customer relationship"
    return 1
}

# Function to verify Koku processed the upload end-to-end
# This combines manifest, file processing, and summary verification
verify_koku_processing() {
    local cluster_id="$1"

    if [ -z "$cluster_id" ]; then
        echo_error "Cluster ID is required for Koku verification"
        return 1
    fi

    echo_info "=== STEP 8: Verify Koku Cost Management Processing ==="
    echo_info "Validating the complete Koku data processing pipeline"
    echo ""

    local koku_passed=0
    local koku_failed=0

    # Step 1: Verify manifest and file processing
    echo_info "--- Step 8a: Manifest & File Processing ---"
    if verify_koku_manifest_processing "$cluster_id" 120; then
        koku_passed=$((koku_passed + 1))
        echo_success "✓ Koku manifest processing verified"
    else
        koku_failed=$((koku_failed + 1))
        echo_error "✗ Koku manifest processing failed"
    fi
    echo ""

    # Step 2: Verify summary tables (may take longer)
    echo_info "--- Step 8b: Summary Table Population ---"
    if verify_koku_summary_tables "$cluster_id" 180; then
        koku_passed=$((koku_passed + 1))
        echo_success "✓ Koku summary tables populated"
    else
        koku_failed=$((koku_failed + 1))
        echo_warning "⚠ Summary tables not yet populated (async process)"
    fi
    echo ""

    # Summary
    echo_info "Koku Verification Summary:"
    echo_info "  Passed: $koku_passed"
    echo_info "  Failed/Pending: $koku_failed"
    echo ""

    # Manifest processing is critical; summary is async and may take time
    if [ $koku_passed -ge 1 ]; then
        echo_success "✓ Koku cost data processing verified (at least manifest processing passed)"
        return 0
    else
        echo_error "✗ Koku cost data processing verification failed"
        return 1
    fi
}

# Function to check for recommendations for a specific upload
check_for_recommendations() {
    local cluster_id="$1"

    if [ -z "$cluster_id" ]; then
        echo_error "Cluster ID not provided to check_for_recommendations"
        return 1
    fi

    echo_info "=== STEP 3: Check for Recommendations ===="
    echo_info "Checking recommendations for cluster: $cluster_id"

    # Wait for Kruize to process data and generate recommendations
    echo_info "Waiting for Kruize to process data and generate recommendations (45 seconds)..."
    echo_info "Kruize needs time to analyze metrics and create optimization recommendations..."
    sleep 45

    # Check if Kruize is accessible
    local kruize_pod=$(oc get pods -n "$NAMESPACE" -l "app.kubernetes.io/name=kruize" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

    if [ -z "$kruize_pod" ]; then
        echo_error "Kruize pod not found"
        echo_info "Use './query-kruize.sh --cluster $cluster_id' to check recommendations later"
        return 1
    fi

    echo_info "Kruize pod found: $kruize_pod"

    # Check Kruize database for experiments (listExperiments API has known issues)
    echo_info "Checking Kruize experiments via database..."
    local db_pod=$(oc get pods -n "$NAMESPACE" -l "app.kubernetes.io/name=database" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

    if [ -z "$db_pod" ]; then
        echo_error "Database pod not found"
        echo_info "Use './query-kruize.sh --cluster $cluster_id' to check recommendations later"
        return 1
    fi

    # Extract Kruize database credentials from secret
    local db_secret_name="${HELM_RELEASE_NAME}-db-credentials"
    local kruize_user=$(oc get secret -n "$NAMESPACE" "$db_secret_name" -o jsonpath='{.data.kruize-user}' 2>/dev/null | base64 -d)
    local kruize_password=$(oc get secret -n "$NAMESPACE" "$db_secret_name" -o jsonpath='{.data.kruize-password}' 2>/dev/null | base64 -d)
    local kruize_db="kruize_db"

    if [ -z "$kruize_user" ] || [ -z "$kruize_password" ]; then
        echo_error "Unable to retrieve Kruize database credentials from secret '$db_secret_name'"
        echo_info "Use './query-kruize.sh --cluster $cluster_id' to check recommendations later"
        return 1
    fi

    # Query for experiments specific to this cluster (cluster_name format: "1;cluster-id")
    echo_info "Querying for experiments matching cluster: $cluster_id"
    local exp_count=$(oc exec -n "$NAMESPACE" "$db_pod" -- \
        env PGPASSWORD="$kruize_password" psql -U "$kruize_user" -d "$kruize_db" -t -c \
        "SELECT COUNT(*) FROM kruize_experiments WHERE cluster_name LIKE '%${cluster_id}%';" 2>/dev/null | tr -d ' ' || echo "0")

    if [ "$exp_count" -eq 0 ]; then
        echo_error "❌ No Kruize experiments found for cluster: $cluster_id"
        echo_info "This indicates the data was not processed by the backend or sent to Kruize"
        echo_info "Troubleshooting:"
        echo_info "  - Check processor logs: oc logs -n $NAMESPACE deployment/cost-onprem-ros-processor --tail=50"
        echo_info "  - Verify upload was successful in ingress logs"
        echo_info "  - Check Kafka messages were processed"
        return 1
    fi

    echo_success "Found $exp_count Kruize experiment(s) for this upload"

    # Show experiment details for this cluster
    echo_info "Experiment details for cluster $cluster_id:"
    oc exec -n "$NAMESPACE" "$db_pod" -- \
        env PGPASSWORD="$kruize_password" psql -U "$kruize_user" -d "$kruize_db" -c \
        "SELECT experiment_name, status, cluster_name FROM kruize_experiments
         WHERE cluster_name LIKE '%${cluster_id}%'
         ORDER BY experiment_name DESC LIMIT 3;" 2>/dev/null || true

    # Check for recommendations specific to this cluster
    echo_info "Querying for recommendations for cluster: $cluster_id"
    local rec_count=$(oc exec -n "$NAMESPACE" "$db_pod" -- \
        env PGPASSWORD="$kruize_password" psql -U "$kruize_user" -d "$kruize_db" -t -c \
        "SELECT COUNT(*) FROM kruize_recommendations WHERE cluster_name LIKE '%${cluster_id}%';" 2>/dev/null | tr -d ' ' || echo "0")

    if [ "$rec_count" -eq 0 ]; then
        echo_error "❌ No recommendations found for cluster: $cluster_id"
        echo_warning "Experiments exist but no recommendations were generated for this upload"
        echo_info ""
        echo_info "Possible reasons:"
        echo_info "  - Not enough data points (Kruize needs multiple intervals over time)"
        echo_info "  - Data quality issues (check if metrics have valid values)"
        echo_info "  - Kruize is still processing (may take several minutes)"
        echo_info ""
        echo_info "Troubleshooting steps:"
        echo_info "  1. Check Kruize logs: oc logs -n $NAMESPACE $kruize_pod | tail -100"
        echo_info "  2. Query later: ./query-kruize.sh --cluster $cluster_id"
        echo_info "  3. Check if experiments show 'IN_PROGRESS' status (query shown above)"
        echo_info ""
        echo_info "Note: Kruize typically requires multiple data uploads over time to generate"
        echo_info "      meaningful recommendations. A single upload may not be sufficient."
        return 1
    fi

    echo_success "✓ Found $rec_count recommendation(s) for cluster: $cluster_id!"

    # Show recommendation details for this cluster
    echo_info "Recommendation details for cluster $cluster_id:"
    oc exec -n "$NAMESPACE" "$db_pod" -- \
        env PGPASSWORD="$kruize_password" psql -U "$kruize_user" -d "$kruize_db" -c \
        "SELECT experiment_name, interval_end_time
         FROM kruize_recommendations
         WHERE cluster_name LIKE '%${cluster_id}%'
         ORDER BY interval_end_time DESC LIMIT 3;" 2>/dev/null || true

    return 0
}

# Store recommendations check result for main function
check_recommendations_with_retry() {
    local cluster_id="$1"
    local max_retries=3
    local retry_interval=60
    local attempt=1

    if [ -z "$cluster_id" ]; then
        echo_error "Cluster ID not provided to check_recommendations_with_retry"
        return 1
    fi

    while [ $attempt -le $max_retries ]; do
        if [ $attempt -gt 1 ]; then
            echo_info "Retry attempt $attempt of $max_retries (waiting ${retry_interval}s)..."
            sleep $retry_interval
        fi

        if check_for_recommendations "$cluster_id"; then
            return 0
        fi

        attempt=$((attempt + 1))
    done

    echo_error "Failed to find recommendations for cluster $cluster_id after $max_retries attempts"
    return 1
}

# Main execution
main() {
    echo_info "Cost Management & ROS Data Flow Test"
    echo_info "============================================"
    echo_info "This test validates the complete data flow through:"
    echo_info "  1. Ingress (JWT authenticated upload)"
    echo_info "  2. Koku/MASU (cost data processing)"
    echo_info "  3. ROS Processor (resource optimization)"
    echo_info "  4. Kruize (recommendation generation)"
    echo_info "  5. ROS Backend API (JWT authenticated access)"
    echo ""

    # Check prerequisites first
    if ! check_prerequisites; then
        echo_error "Prerequisites check failed"
        exit 1
    fi

    echo_info "Configuration:"
    echo_info "  Platform: OpenShift"
    echo_info "  Namespace: $NAMESPACE"
    echo_info "  Helm Release: $HELM_RELEASE_NAME"
    echo_info "  Keycloak Namespace: $KEYCLOAK_NAMESPACE"
    echo ""

    # Step 1: Detect Keycloak configuration
    if ! detect_keycloak_config; then
        echo_error "Failed to detect Keycloak configuration"
        exit 1
    fi
    echo ""

    # Step 2: Get JWT token (used for both ingress and backend API)
    if ! get_jwt_token; then
        echo_error "Failed to obtain JWT token"
        exit 1
    fi
    echo ""

    # Step 3: Validate JWT authentication is working (preflight smoke test)
    validate_jwt_authentication
    echo ""

    # Step 4: Test JWT authentication on backend API
    echo_info "Testing JWT authentication on backend API..."
    if test_jwt_backend_auth; then
        echo_success "✓ JWT backend authentication validated"
    else
        echo_warning "JWT backend auth tests incomplete - may need route/firewall config"
        echo_info "Main functionality will be tested during API queries..."
    fi
    echo ""

    # Step 5: Register OCP source before upload
    # Generate unique cluster ID for this test run
    UPLOAD_CLUSTER_ID="test-cluster-$(date +%s)"
    echo_info "Generated cluster ID: $UPLOAD_CLUSTER_ID"

    if ! register_ocp_source "$UPLOAD_CLUSTER_ID"; then
        echo_error "Failed to register OCP source"
        exit 1
    fi
    echo ""

    # Step 6: Upload test data with JWT authentication
    if ! upload_test_data_jwt; then
        echo_error "Upload with JWT authentication failed"
        exit 1
    fi
    echo ""

    # Step 7: Verify processing (log-based checks)
    verify_upload_processing
    echo ""

    # Step 8: Verify Koku cost management processing (database verification)
    # This validates that Koku properly processed the uploaded data
    if ! verify_koku_processing "$UPLOAD_CLUSTER_ID"; then
        echo_warning "Koku processing verification incomplete - some async tasks may still be running"
        echo_info "Continuing with ROS verification..."
    fi
    echo ""

    # Step 9: Query ROS backend API with JWT token to verify authenticated access
    echo_info "=== STEP 9: Querying ROS Backend API with Keycloak JWT Token ==="
    echo_info "Testing API access using Keycloak JWT token..."
    echo ""

    # Refresh JWT token before API queries (previous steps may have exceeded token lifetime)
    if ! get_jwt_token; then
        echo_error "Failed to refresh JWT token"
        exit 1
    fi
    echo ""

    local backend_url=$(get_service_url "ros-api" "")
    local backend_queries_passed=0
    local backend_queries_failed=0

    # Test: Status endpoint (health check - verifies JWT auth works)
    echo_info "Query: Health Status Check"
    if query_backend_api "/status" "API Status"; then
        echo_success "  ✓ Status endpoint accessible"
        backend_queries_passed=$((backend_queries_passed + 1))
    else
        echo_warning "  ⚠ Status endpoint failed"
        backend_queries_failed=$((backend_queries_failed + 1))
    fi
    echo ""

    # Test 2: Recommendations endpoint (list recommendations)
    echo_info "Query 2: List Recommendations"
    if query_backend_api "/api/cost-management/v1/recommendations/openshift" "Recommendations List"; then
        # Check if response contains actual data (not empty array)
        if check_recommendations_has_data "$QUERY_RESPONSE_BODY"; then
            echo_success "  ✓ Recommendations endpoint contains data"
            backend_queries_passed=$((backend_queries_passed + 1))
        else
            echo_error "  ✗ No recommendations found in API response"
            echo_info ""
            echo_info "Troubleshooting steps:"
            echo_info "  1. Check if data was uploaded and processed:"
            echo_info "     oc logs -n $NAMESPACE deployment/cost-onprem-ros-processor --tail=100"
            echo_info "  2. Check Kruize logs:"
            echo_info "     oc logs -n $NAMESPACE deployment/cost-onprem-kruize --tail=100"
            echo_info "  3. Query recommendations directly:"
            echo_info "     ./query-kruize.sh --recommendations"
            exit 1
        fi
    else
        echo_warning "  ⚠ Recommendations endpoint failed"
        backend_queries_failed=$((backend_queries_failed + 1))
    fi
    echo ""

    echo_info "Backend API Access Summary:"
    echo_info "  Successful queries: $backend_queries_passed"
    echo_info "  Failed queries: $backend_queries_failed"
    echo ""

    if [ $backend_queries_passed -gt 0 ]; then
        echo_success "✓ JWT token authentication successful on backend API!"
        echo_info "  The Keycloak JWT token was validated"
        echo_info "  The backend API accepted the authenticated requests"
    else
        echo_warning "Backend API queries had issues - authentication may work but endpoints may be unavailable"
    fi
    echo ""

    # Step 10: Check for ROS recommendations with retries (for the specific cluster we just uploaded)
    if [ -z "$UPLOAD_CLUSTER_ID" ]; then
        echo_error "UPLOAD_CLUSTER_ID not set. This should have been set during upload."
        exit 1
    fi

    if ! check_recommendations_with_retry "$UPLOAD_CLUSTER_ID"; then
        echo ""
        echo_error "❌ Cost Management & ROS Data Flow Test FAILED!"
        echo_error "The upload was successful but no recommendations were generated for cluster: $UPLOAD_CLUSTER_ID"
        echo_info ""
        echo_info "Troubleshooting steps:"
        echo_info "  1. Check Koku listener for Kafka message consumption:"
        echo_info "     oc logs -n $NAMESPACE -l app.kubernetes.io/component=listener --tail=100"
        echo_info "  2. Check Koku/MASU workers for cost data processing:"
        echo_info "     oc logs -n $NAMESPACE -l app.kubernetes.io/component=masu --tail=100"
        echo_info "  3. Check if data reached Kruize for this cluster:"
        echo_info "     ./query-kruize.sh --cluster $UPLOAD_CLUSTER_ID"
        echo_info "  4. Check ROS processor logs for errors:"
        echo_info "     oc logs -n $NAMESPACE deployment/cost-onprem-ros-processor --tail=100"
        echo_info "  5. Check Kruize logs:"
        echo_info "     oc logs -n $NAMESPACE deployment/cost-onprem-kruize --tail=100"
        echo_info "  6. Query all recommendations:"
        echo_info "     ./query-kruize.sh --recommendations"
        exit 1
    fi

    echo ""
    echo_success "✅ Cost Management & ROS Data Flow Test completed successfully!"
    echo_success "Test cluster ID: $UPLOAD_CLUSTER_ID"
    echo_info ""
    echo_info "The test demonstrated the complete data flow:"
    echo_info ""
    echo_info "  Authentication & Upload:"
    echo_info "    ✓ Keycloak JWT token generation"
    echo_info "    ✓ OCP source registered via Sources API"
    echo_info "    ✓ Authenticated file upload using JWT Bearer token (ingress)"
    echo_info "    ✓ Ingress: File stored in S3 (koku-bucket)"
    echo_info "    ✓ Ingress: Kafka message published to platform.upload.announce"
    echo_info ""
    echo_info "  Koku Cost Management Processing:"
    echo_info "    ✓ Koku Listener: Consumed Kafka message from platform.upload.announce"
    echo_info "    ✓ Koku/MASU: Created manifest and processed report files"
    echo_info "    ✓ Koku: File processing completed (verified via database)"
    echo_info "    ✓ Koku: OCP usage summary tables populated (CPU/memory hours)"
    echo_info "    ✓ Koku: Copied ROS data to ros-data bucket"
    echo_info "    ✓ Koku: Published event to hccm.ros.events topic"
    echo_info ""
    echo_info "  ROS Resource Optimization:"
    echo_info "    ✓ ROS Processor: Consumed event and sent data to Kruize"
    echo_info "    ✓ Kruize: Generated optimization recommendations"
    echo_info "    ✓ ROS API: Recommendations accessible via JWT-authenticated API"
    echo_info ""
    echo_info "This confirms both pipelines are working:"
    echo_info "  1. Cost Management (Koku): Upload → Process → Summary tables"
    echo_info "  2. Resource Optimization (ROS): Koku events → Kruize → Recommendations"
    echo_info ""
    echo_info "To query recommendations for this specific upload later:"
    echo_info "  ./query-kruize.sh --cluster $UPLOAD_CLUSTER_ID"
}

# Handle script arguments
case "${1:-}" in
    "help"|"-h"|"--help")
        echo "Usage: $0 [command]"
        echo ""
        echo "Commands:"
        echo "  (no command)    Run complete Cost Management & ROS data flow test"
        echo "  help            Show this help message"
        echo ""
        echo "Environment Variables:"
        echo "  NAMESPACE              Target namespace (default: cost-onprem)"
        echo "  HELM_RELEASE_NAME      Helm release name (default: cost-onprem)"
        echo "  KEYCLOAK_NAMESPACE     Keycloak namespace (default: keycloak)"
        echo ""
        echo "This script tests the complete Cost Management (Koku) and ROS data flow:"
        echo ""
        echo "Data Flow Architecture:"
        echo "  1. Upload: Cost Management Operator -> Ingress (JWT authenticated)"
        echo "  2. Storage: Ingress stores files in S3 (koku-bucket)"
        echo "  3. Notification: Ingress publishes to Kafka (platform.upload.announce)"
        echo "  4. Koku Processing: Listener consumes message, MASU processes cost data"
        echo "  5. ROS Forwarding: Koku copies ROS data to ros-data bucket"
        echo "  6. ROS Event: Koku publishes to Kafka (hccm.ros.events)"
        echo "  7. ROS Processing: ROS Processor consumes event, sends to Kruize"
        echo "  8. Recommendations: Kruize generates optimization recommendations"
        echo "  9. API Access: Recommendations available via JWT-authenticated API"
        echo ""
        echo "Test Steps:"
        echo "  1. Detects Keycloak configuration automatically"
        echo "  2. Obtains JWT token using client credentials flow"
        echo "  3. Validates JWT authentication (preflight checks)"
        echo "  4. Tests JWT authentication on backend API"
        echo "  5. Registers OCP source via Sources API"
        echo "  6. Uploads sample data using JWT Bearer authentication (ingress)"
        echo "  7. Verifies upload processing (log-based checks)"
        echo "  8. Verifies Koku processing (database verification):"
        echo "     - Manifest creation and file processing status"
        echo "     - OCP usage summary tables (CPU/memory hours)"
        echo "  9. Queries ROS backend API using JWT Bearer authentication"
        echo "  10. Validates ROS recommendations were generated by Kruize"
        echo ""
        echo "Requirements:"
        echo "  - Active OpenShift session (oc login completed)"
        echo "  - Keycloak deployed with cost-management-operator client"
        echo "  - Cost Management (Koku) services deployed and running"
        echo "  - ROS services deployed and running"
        echo "  - Ingress with JWT authentication enabled"
        echo "  - Backend API with JWT authentication enabled"
        echo "  - User must have access to the cost-onprem namespace"
        exit 0
        ;;
    "")
        main
        ;;
    *)
        echo_error "Unknown command: $1"
        echo_info "Use '$0 help' for usage information"
        exit 1
        ;;
esac
