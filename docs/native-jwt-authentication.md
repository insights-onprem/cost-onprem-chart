# Native JWT Authentication

## Overview

The Cost Management On-Premise Helm Chart uses **Envoy's native JWT authentication filter** for validating JWT tokens from Keycloak (RHBK). This provides secure, low-latency authentication for file uploads and API requests.

## Architecture

### Centralized Gateway Authentication Flow

All external API traffic routes through a single centralized Envoy gateway that handles JWT validation:

```mermaid
graph TB
    Client["Client<br/>(with JWT)"]
    Gateway["Centralized Gateway<br/>(Port 9080)<br/><br/>1. jwt_authn filter<br/>   - Fetches JWKS from Keycloak<br/>   - Validates JWT signature<br/>   - Extracts claims to metadata<br/><br/>2. Lua filter<br/>   - Reads JWT claims<br/>   - Injects X-Rh-Identity header<br/><br/>3. Routes to backend based on path"]
    Ingress["Ingress Service<br/>(Port 8081)"]
    Koku["Koku API<br/>(Port 8000)"]
    Sources["Sources API<br/>(Port 8000)"]
    RosApi["ROS API<br/>(Port 8000)"]

    Client -->|"Authorization: Bearer &lt;JWT&gt;"| Gateway
    Gateway -->|"/api/ingress/*<br/>X-Rh-Identity header"| Ingress
    Gateway -->|"/api/cost-management/*<br/>X-Rh-Identity header"| Koku
    Gateway -->|"/api/sources/*<br/>X-Rh-Identity header"| Sources
    Gateway -->|"/api/cost-management/v1/recommendations/openshift<br/>X-Rh-Identity header"| RosApi

    style Client fill:#90caf9,stroke:#333,stroke-width:2px,color:#000
    style Gateway fill:#fff59d,stroke:#333,stroke-width:2px,color:#000
    style Ingress fill:#a5d6a7,stroke:#333,stroke-width:2px,color:#000
    style Koku fill:#a5d6a7,stroke:#333,stroke-width:2px,color:#000
    style Sources fill:#a5d6a7,stroke:#333,stroke-width:2px,color:#000
    style RosApi fill:#a5d6a7,stroke:#333,stroke-width:2px,color:#000
```

### Gateway Routing

The centralized gateway handles JWT authentication and routes requests to backend services based on path and HTTP method:

| Priority | Path | Method | Backend | Description |
|----------|------|--------|---------|-------------|
| 1 | `/api/cost-management/v1/recommendations/openshift` | GET | ROS API (port 8000) | Resource optimization recommendations |
| 2 | `/api/cost-management/*` | GET, HEAD | Koku API Reads (port 8000) | Cost management read operations |
| 3 | `/api/cost-management/*` | POST, PUT, DELETE, PATCH | Koku API Writes (port 8000) | Cost management write operations |
| 4 | `/api/sources/*` | ALL | Sources API (port 8000) | Provider and source management |
| 5 | `/api/ingress/*` | ALL | Ingress (port 8081) | File uploads |

### Backend Services

All backend services receive pre-authenticated traffic from the gateway:

| Service | Port | Authentication Method |
|---------|------|----------------------|
| **Koku API (Reads)** | 8000 | X-Rh-Identity header from gateway |
| **Koku API (Writes)** | 8000 | X-Rh-Identity header from gateway |
| **Sources API** | 8000 | X-Rh-Identity header from gateway or internal access |
| **Ingress** | 8081 | X-Rh-Identity header from gateway |
| **ROS API** | 8000 | X-Rh-Identity header from gateway |
| **Kruize** | 8080 | Internal service (accessed via ROS API) |
| **ROS Processor** | N/A | Kafka consumer (no HTTP API) |
| **ROS Recommendation Poller** | N/A | Internal service (no external API) |
| **Housekeeper** | N/A | Internal service (no external API) |

**How It Works:**
1. **Centralized Gateway**: Validates JWT from Keycloak, extracts claims (`org_id`, `account_number`, `access`), builds `X-Rh-Identity` header, and routes to appropriate backend based on path and HTTP method
2. **Backend Services**: Receive pre-authenticated requests with `X-Rh-Identity` header for tenant identification and RBAC
3. **Sources API**: Has per-endpoint protection; certain endpoints (e.g., `/application_types`) are unauthenticated for internal service access
4. **Internal Services**: Communicate with each other using service accounts or inherit authentication context

### X-Rh-Identity Header Format

The Envoy Lua filter constructs the `X-Rh-Identity` header from JWT claims:

```json
{
  "org_id": "<from JWT org_id claim>",
  "identity": {
    "org_id": "<from JWT org_id claim>",
    "account_number": "<from JWT account_number claim>",
    "type": "User",
    "user": {
      "username": "<from JWT preferred_username claim>",
      "access": <from JWT access claim, JSON object>
    }
  },
  "entitlements": {
    "cost_management": {
      "is_entitled": "True"
    }
  }
}
```

> **⚠️ Note: Dual org_id Placement**
>
> The `org_id` field appears at **both** the top level and inside `identity`. This is a workaround
> for a bug in Koku's `dev_middleware.py` (line 63) which incorrectly reads `org_id` from the
> top level instead of `identity.org_id`. See [Part 6: Technical Notes](keycloak-jwt-authentication-setup.md#part-6-technical-notes) for details.

**Required JWT Claims:**
- `org_id`: Organization ID (e.g., `org1234567`)
- `account_number`: Account number (e.g., `7890123`)
- `preferred_username`: Username for logging
- `access`: JSON object defining resource-level permissions (see [Keycloak JWT Setup](keycloak-jwt-authentication-setup.md))

**Network Security:**
- Network policies restrict external access to backend services
- The centralized gateway provides JWT authentication for all external-facing APIs
- Metrics endpoints remain accessible to Prometheus on dedicated ports (see Network Policies section below)

## Why Native JWT?

Envoy's native JWT authentication provides:

- ✅ **Multipart upload support** - Inline validation works with all request types including file uploads
- ✅ **Low latency** - Sub-millisecond authentication overhead (<1ms)
- ✅ **Simple architecture** - Single gateway component with no external dependencies
- ✅ **Easy debugging** - All authentication configuration in one place
- ✅ **Battle-tested** - Envoy's JWT filter is production-ready and widely used
- ✅ **Secure TLS validation** - Full certificate verification prevents MITM attacks

## TLS Certificate Validation

### Overview

Envoy validates Keycloak's TLS certificate when fetching JWKS (JSON Web Key Set) to prevent Man-in-the-Middle (MITM) attacks.

**Why This Matters:**
- JWKS contains public keys used to verify JWT signatures
- Without certificate validation, an attacker could intercept the JWKS request
- Attacker could provide fake public keys
- Attacker could then forge JWT tokens that would be accepted as valid
- **Result**: Complete authentication bypass

### How It Works

**1. CA Bundle Preparation (Init Container)**

An init container (`prepare-ca-bundle`) runs before Envoy starts and combines multiple CA sources:

```bash
# Combined from:
- System CA bundle (/etc/ssl/certs/ca-bundle.crt)
- Kubernetes service account CA
- OpenShift service CA (if available)
- Custom Keycloak CA (for self-signed certificates)

# Output: /etc/ssl/certs/ca-bundle.crt (mounted to Envoy)
```

**2. Envoy TLS Configuration**

```yaml
transport_socket:
  name: envoy.transport_sockets.tls
  typed_config:
    "@type": ...UpstreamTlsContext
    sni: keycloak.example.com                    # Server Name Indication
    common_tls_context:
      validation_context:                        # Certificate validation
        trusted_ca:
          filename: /etc/ssl/certs/ca-bundle.crt # Trust anchor
        match_typed_subject_alt_names:           # Hostname verification
          - san_type: DNS
            matcher:
              exact: keycloak.example.com
```

**3. Certificate Validation Steps**

When Envoy connects to Keycloak to fetch JWKS:

1. ✅ **Certificate Chain Validation**: Verify certificate is signed by a trusted CA
2. ✅ **Hostname Verification**: Verify certificate's SAN matches Keycloak hostname
3. ✅ **Expiration Check**: Verify certificate is not expired
4. ✅ **Revocation Check**: Verify certificate has not been revoked
5. ✅ **TLS Handshake**: Establish encrypted connection only if all checks pass

**Without validation_context:**
```
❌ Envoy accepts ANY certificate (self-signed, expired, wrong hostname)
❌ Attacker can intercept and provide fake JWKS
❌ Attacker can forge JWT tokens
❌ Complete authentication bypass
```

**With validation_context:**
```
✅ Envoy only accepts certificates from trusted CAs
✅ Envoy verifies hostname matches
✅ MITM attacks are prevented
✅ JWKS authenticity guaranteed
✅ JWT validation is trustworthy
```

### Custom CA Certificates

For self-signed or internal CA certificates, add your CA certificate to the Helm values:

```yaml
jwt_auth:
  keycloak:
    tls:
      caCert: |
        -----BEGIN CERTIFICATE-----
        MIIDXTCCAkWgAwIBAgIJAKLnUhVP3GVDMA0GCSqGSIb3DQEBCwUAMEUxCzAJBgNV
        ... (your CA certificate) ...
        -----END CERTIFICATE-----
```

This certificate will be included in the CA bundle used by Envoy for validation.

### Troubleshooting TLS Validation

**Issue**: Envoy fails to validate Keycloak certificate

**Symptoms**:
```
upstream connect error or disconnect/reset before headers. TLS error:
268435581:SSL routines:OPENSSL_internal:CERTIFICATE_VERIFY_FAILED
```

**Solutions**:

1. **For self-signed certificates**: Add the CA certificate to `jwtAuth.keycloak.tls.caCert`

2. **For OpenShift service CA certificates**: Ensure the annotation is present:
   ```yaml
   metadata:
     annotations:
       service.beta.openshift.io/inject-cabundle: "true"
   ```

3. **For hostname mismatch**: Verify Keycloak URL matches certificate SAN:
   ```bash
   # Check certificate SAN
   echo | openssl s_client -connect keycloak.example.com:443 2>/dev/null | \
     openssl x509 -noout -text | grep -A1 "Subject Alternative Name"
   ```

4. **Debug CA bundle**: Check what CAs are included:
   ```bash
   kubectl exec -n cost-onprem deployment/cost-onprem-ros-api -c envoy-proxy -- \
     cat /etc/ssl/certs/ca-bundle.crt | grep -c "BEGIN CERTIFICATE"
   ```

## Configuration

### Helm Values

```yaml
jwt_auth:

  envoy:
    image:
      repository: registry.redhat.io/openshift-service-mesh/proxyv2-rhel9
      tag: "2.6"
    port: 8080
    adminPort: 9901

  keycloak:
    url: ""  # Leave empty for auto-detection (recommended)
    realm: kubernetes
    audiences:
      - account
      - cost-management-operator
```

**Automatic Configuration:**
- **Keycloak URL**: Auto-detected from Keycloak routes
- **Override**: Set `jwtAuth.keycloak.url` only for custom/external Keycloak

### JWT Claims to Headers Mapping

The Lua filter extracts JWT claims and injects these headers:

| JWT Claim            | HTTP Header         | Description                |
|----------------------|---------------------|----------------------------|
| `sub`                | `X-ROS-User-ID`     | User/service account ID    |
| `preferred_username` | `X-ROS-User-Name`   | Username                   |
| `azp` or `client_id` | `X-Client-ID`       | Client ID                  |
| (authentication)     | `X-ROS-Authenticated` | Always "true" when valid   |
| (method)             | `X-ROS-Auth-Method` | "Envoy-Native-JWT"         |
| Authorization header | `X-Bearer-Token`    | JWT token without "Bearer" |

## Testing

### Upload with JWT

```bash
# Get JWT token from Keycloak
TOKEN=$(curl -s -k -X POST \
  "https://keycloak-keycloak.apps.example.com/auth/realms/kubernetes/protocol/openid-connect/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials" \
  -d "client_id=cost-management-operator" \
  -d "client_secret=$CLIENT_SECRET" \
  | jq -r '.access_token')

# Upload file with JWT
curl -F "file=@payload.tar.gz;type=application/vnd.redhat.hccm.filename+tgz" \
  -H "Authorization: Bearer $TOKEN" \
  "http://cost-onprem-ingress-cost-onprem.apps.example.com/api/ingress/v1/upload"
```

### Automated Test Suite

Use the pytest test suite to verify end-to-end JWT authentication:

```bash
# Run authentication tests
NAMESPACE=cost-onprem ./scripts/run-pytest.sh --auth

# Run full E2E tests (includes authentication)
NAMESPACE=cost-onprem ./scripts/run-pytest.sh --e2e
```

The test suite:
1. Auto-detects Red Hat Build of Keycloak configuration
2. Obtains JWT token using client credentials
3. Verifies gateway JWT validation (valid/invalid tokens)
4. Tests API routing through centralized gateway
5. Validates end-to-end data upload and processing

## Payload Requirements

The ingress service expects tar.gz archives with:

1. **manifest.json** (required):
```json
{
  "uuid": "<unique-id>",
  "cluster_id": "<cluster-id>",
  "cluster_alias": "test-cluster",
  "date": "2025-10-14T00:00:00Z",
  "files": ["openshift_usage_report.csv"],
  "resource_optimization_files": ["openshift_usage_report.csv"],
  "certified": true,
  "operator_version": "1.0.0"
}
```

2. **CSV file(s)**: Listed in `resource_optimization_files`

## Network Policies

### Overview

Network policies are automatically deployed on OpenShift to secure service-to-service communication and restrict unauthorized access to backend services.

### Purpose

1. **Enforce Authentication**: Only traffic through the centralized gateway (port 9080) is allowed for external requests
2. **Isolate Backend Ports**: Direct access to application containers (ports 8000, 8001, 8081) is restricted
3. **Enable Metrics Collection**: Prometheus can access metrics endpoints without authentication
4. **Service Communication**: Internal services can communicate with each other as needed

### Network Policy Impact

| Service | Port | Access Policy |
|---------|------|---------------|
| **API Gateway** | 9080 | ✅ External access allowed (authenticated) |
| **Application Container** | 8000-8001, 8081 | ⚠️ Restricted to gateway and internal services only |
| **Metrics Endpoint** | 9000, 9901 `/metrics` | ✅ Prometheus access allowed |
| **Database** | 5432 | ⚠️ Backend services only |
| **Kafka** | 29092 | ⚠️ Backend services only |

### Key Network Policies

#### 1. Kruize Network Policy

**File**: `cost-onprem/templates/kruize/networkpolicy.yaml`

- **Allows**:
  - Ingress from `cost-onprem` namespace (for internal service communication)
  - Ingress from `openshift-monitoring` namespace (for Prometheus metrics scraping on port 8080)
- **Blocks**:
  - All other external ingress traffic

#### 2. API Gateway Network Policy

**File**: `cost-onprem/templates/gateway/networkpolicy.yaml`

- **Allows**:
  - Ingress from `openshift-ingress` namespace (for external API access via gateway on port 9080)
  - Ingress from UI pods (for API proxying)
  - Ingress from `openshift-monitoring` namespace (for Prometheus metrics on port 9901)
- **Applies to**: API Gateway (all external API traffic)

#### 3. Cost Management On-Premise Metrics Network Policies

**File**: `cost-onprem/templates/ros/networkpolicies.yaml`

- **Allows**:
  - Ingress from `openshift-monitoring` namespace (for Prometheus metrics scraping on port 9000)
- **Applies to**: Cost Management On-Premise API, Processor, Recommendation Poller (separate policies for each)

#### 4. Cost Management On-Premise API Access Network Policy

**File**: `cost-onprem/templates/ros/networkpolicies.yaml`

- **Allows**:
  - Ingress from centralized gateway (for pre-authenticated API requests on port 8000)
- **Applies to**: Cost Management On-Premise API (ROS API)

#### 5. Sources API Network Policy

**File**: `cost-onprem/templates/sources-api/networkpolicy.yaml`

- **Allows**:
  - Ingress from `cost-onprem` namespace (for internal service communication on port 8000)
- **Applies to**: Sources API (accessed by cost-onprem-housekeeper)

### Prometheus Metrics Access

**Important**: Network policies specifically allow Prometheus (running in `openshift-monitoring` namespace) to scrape metrics endpoints:

```yaml
# Example: Allow Prometheus to access metrics
- namespaceSelector:
    matchLabels:
      name: openshift-monitoring
  podSelector:
    matchLabels:
      app.kubernetes.io/name: prometheus
```

**Metrics Endpoints**:
- **Cost Management On-Premise API**: `http://ros-api:9000/metrics`
- **Cost Management On-Premise Processor**: `http://ros-processor:9000/metrics`
- **Cost Management On-Premise Recommendation Poller**: `http://ros-recommendation-poller:9000/metrics`

### Sources API Authentication Details

**Architecture**: Sources API is a Go application using the Echo web framework with middleware-based authentication.

**Authentication Model**:
- **Has X-Rh-Identity validation middleware**: The application includes middleware to parse and validate `X-Rh-Identity` headers for protected endpoints
- **Per-endpoint protection**: Authentication is applied selectively via middleware configuration in `routes.go`
- **BYPASS_RBAC configuration**: When `BYPASS_RBAC=true`, RBAC authorization checks are skipped (useful for development)

**Endpoint Categories**:

1. **Protected Endpoints** (require `X-Rh-Identity` header):
   - Source management: `POST/PATCH/DELETE /sources`
   - Application management: `POST/PATCH/DELETE /applications`
   - Authentication management: `POST/PATCH/DELETE /authentications`
   - Uses `permissionMiddleware` and `tenancyMiddleware`

2. **Unauthenticated Endpoints** (no header required):
   - Application types: `GET /application_types` - Used by cost-onprem-housekeeper for internal lookups
   - Source types: `GET /source_types`
   - Metadata: `GET /app_meta_data`
   - Health check: `GET /health`

**Internal Service Communication**:
- **cost-onprem-housekeeper** → Sources API: Calls unauthenticated `/application_types` endpoint to look up cost-management application type ID
- No `X-Rh-Identity` header needed for this specific endpoint
- Network policies restrict access to internal `cost-onprem` namespace

### Troubleshooting Network Policies

#### Check Active Network Policies

```bash
# List all network policies
oc get networkpolicies -n cost-onprem

# Describe specific policy
oc describe networkpolicy kruize-allow-ingress -n cost-onprem
```

#### Test Connectivity

```bash
# From within the namespace (should work)
oc exec -n cost-onprem deployment/cost-onprem-ros-processor -- \
  curl -s http://cost-onprem-kruize:8080/listApplications

# From outside the namespace (should be blocked to main port, allowed to Envoy)
oc exec -n default deployment/test-pod -- \
  curl -s http://cost-onprem-kruize.cost-onprem.svc:8081/listApplications  # Blocked

oc exec -n default deployment/test-pod -- \
  curl -s -H "X-Rh-Identity: ..." http://cost-onprem-kruize.cost-onprem.svc:8080/listApplications  # Allowed via Envoy
```

#### Verify Prometheus Metrics Access

```bash
# Check if Prometheus can scrape metrics
oc exec -n openshift-monitoring prometheus-k8s-0 -- \
  curl -s http://cost-onprem-kruize.cost-onprem.svc:8080/metrics

# Expected: Prometheus metrics output
```

#### Common Issues

**Issue**: Service-to-service communication failing
- **Cause**: Network policy blocking legitimate traffic
- **Fix**: Verify both services are in the `cost-onprem` namespace and network policy allows gateway traffic

**Issue**: Prometheus not scraping metrics
- **Cause**: Network policy missing `openshift-monitoring` namespace selector
- **Fix**: Verify network policy includes `namespaceSelector` for `openshift-monitoring`

**Issue**: External access via gateway failing
- **Cause**: Route or ingress not configured correctly
- **Fix**: Check OpenShift routes are pointing to gateway service on port 9080

## Troubleshooting

### Check Gateway logs

```bash
oc logs -n cost-onprem -l app.kubernetes.io/component=gateway
```

Look for:
- `JWT authenticated: <user-id>` - Successful authentication
- `Jwt verification fails` - Invalid token or signature
- `Jwks remote fetch is failed` - Cannot reach Keycloak JWKS endpoint

### Check ingress logs

```bash
oc logs -n cost-onprem -l app.kubernetes.io/component=ingress
```

Look for:
- `"account":"<id>","org_id":"<id>"` - Authentication headers received
- `"X-Rh-Identity header missing"` - Gateway didn't inject headers
- `"no identity found"` - Identity header not forwarded

### Verify JWKS connectivity

```bash
# Port-forward to Gateway admin
oc port-forward -n cost-onprem deployment/cost-onprem-gateway 9901:9901

# Check cluster status
curl http://localhost:9901/clusters | grep keycloak_jwks
```

Should show `cx_active` connections to Keycloak.

### Test JWT manually

```bash
# Decode JWT to check claims
echo "$TOKEN" | cut -d'.' -f2 | base64 -d | jq

# Verify audience matches configuration
# Should contain "account" or "cost-management-operator"
```

## Security Considerations

### Production Deployments

1. **Use proper TLS certificates** - Don't skip certificate verification
2. **Restrict audiences** - Only allow expected client IDs
3. **Monitor token expiry** - Default is 5 minutes (300 seconds)
4. **Rotate client secrets** - Regularly update Keycloak client credentials

### Development/Testing

- The current configuration accepts self-signed certificates from Keycloak
- In production, use properly signed certificates from your PKI

## References

- [Helm Templates Reference](./helm-templates-reference.md) - Technical details about Envoy and JWT templates
- [Envoy JWT Authentication](https://www.envoyproxy.io/docs/envoy/latest/api-v3/extensions/filters/http/jwt_authn/v3/config.proto)
- [Envoy Lua Filter](https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/lua_filter)
- [Keycloak Client Credentials Flow](https://www.keycloak.org/docs/latest/securing_apps/index.html#_client_credentials_grant)

