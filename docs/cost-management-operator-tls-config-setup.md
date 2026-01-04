# Cost Management Operator TLS Configuration for Self-Signed Certificates

This document explains how to configure the Cost Management Metrics Operator to work with self-signed certificates in airgapped or development environments.

## Problem Statement

### Why Self-Signed Certificates Don't Work Out of the Box

The Cost Management Operator fails with self-signed certificates due to TLS verification failures. Here's what happens:

#### Default Behavior
Out of the box, the Cost Management Operator is configured with:
- `SSL_CERT_FILE=/etc/ssl/certs/combined-ca-bundle.crt`
- `REQUESTS_CA_BUNDLE=/etc/ssl/certs/combined-ca-bundle.crt`
- A `combined-ca-bundle` ConfigMap that contains only standard CA certificates

#### The Failure Scenario
When the operator attempts to communicate with services using self-signed certificates, you'll see errors like:

```bash
# JWT Token Generation (Keycloak communication)
ERROR: Get "https://keycloak-keycloak.apps.cluster.local/realms/kubernetes/protocol/openid-connect/token":
x509: certificate signed by unknown authority

# Upload to Ingress
ERROR: Post "https://cost-onprem-ingress-cost-onprem.apps.cluster.local/api/ingress/v1/upload":
tls: failed to verify certificate: x509: certificate signed by unknown authority

# Prometheus Metrics Collection
ERROR: Get "https://thanos-querier.openshift-monitoring.svc:9091":
x509: certificate signed by unknown authority
```

#### Root Cause Analysis
The operator's `combined-ca-bundle.crt` doesn't include:
1. **Custom Keycloak CA certificates** - needed for JWT token requests
2. **OpenShift ingress CA certificates** - needed for upload requests
3. **Custom service CA certificates** - needed for internal service communication
4. **Self-signed root CAs** - used in airgapped environments

#### OLM Management Complication
**CRITICAL**: The Cost Management Operator is deployed and managed by OpenShift's Operator Lifecycle Manager (OLM) via a ClusterServiceVersion (CSV). This creates additional challenges:

- **Deployment Reconciliation**: Any direct changes to the operator deployment are automatically reverted by OLM
- **CSV Specifications**: The deployment configuration is controlled by the CSV, not user modifications
- **Limited Customization**: Standard Kubernetes deployment modification approaches don't work
- **CA Bundle Constraints**: The CA bundle mounting is defined in the CSV and cannot be easily modified

#### Impact on JWT Authentication Flow
In our JWT authentication implementation, this affects:
1. **Token Generation**: Operator → Keycloak (fails with certificate error)
2. **Upload Process**: Operator → Ingress (fails with certificate error)
3. **Metrics Collection**: Operator → Prometheus (fails with certificate error)

The operator logs will show continuous authentication and upload failures, preventing cost management data from being collected and uploaded.

## Overview

The Cost Management Operator communicates with several services that may use self-signed certificates:
1. **Red Hat Build of Keycloak (RHBK)** - for JWT token generation
2. **Ingress** - for uploading metrics data
3. **Prometheus/Thanos** - for collecting metrics
4. **OpenShift API** - for cluster metadata

### What We Need to Fix
To make the operator work with self-signed certificates within OLM constraints, we need to:
1. **Work within OLM limitations** - cannot modify deployment directly
2. **Update the existing combined-ca-bundle ConfigMap** (the one OLM already mounts)
3. **Extract all relevant CA certificates** from the cluster
4. **Ensure ConfigMap updates persist** across operator restarts
5. **Test and validate** TLS connectivity to all services

#### Solution Approach: ConfigMap Update Strategy
Since we cannot modify the deployment due to OLM management, our approach is:
- **Preserve the existing CA bundle mount point** defined in the CSV
- **Update the contents** of the `combined-ca-bundle` ConfigMap that OLM already mounts
- **Ensure comprehensive CA coverage** for all required services
- **Automate the update process** for maintainability

## Current TLS Configuration

The operator is deployed with the following TLS settings:

```yaml
env:
- name: SSL_CERT_FILE
  value: /etc/ssl/certs/combined-ca-bundle.crt
- name: REQUESTS_CA_BUNDLE
  value: /etc/ssl/certs/combined-ca-bundle.crt

volumeMounts:
- mountPath: /etc/ssl/certs/combined-ca-bundle.crt
  name: combined-ca-bundle
  readOnly: true
  subPath: ca-bundle.crt

volumes:
- configMap:
    name: combined-ca-bundle
  name: combined-ca-bundle
```

## Required Changes for Self-Signed Certificates

### Understanding OLM Constraints

Before making changes, understand that:
- The operator deployment is **controlled by OLM** via ClusterServiceVersion
- **Direct deployment modifications will be reverted** during reconciliation
- We must work **within the existing CA bundle mounting structure**
- The `combined-ca-bundle` ConfigMap is already mounted by the CSV - we update its contents

### 1. Update the combined-ca-bundle ConfigMap (OLM-Compatible Approach)

The `combined-ca-bundle` ConfigMap needs to include all custom CA certificates.
**IMPORTANT**: We update the existing ConfigMap that OLM already mounts, not the deployment configuration:

```bash
# Extract current CA bundle
oc get configmap combined-ca-bundle -n costmanagement-metrics-operator -o jsonpath='{.data.ca-bundle\.crt}' > current-ca-bundle.crt

# Get Keycloak CA certificate
oc get secret keycloak-tls-secret -n keycloak -o jsonpath='{.data.tls\.crt}' | base64 -d > keycloak-ca.crt

# Get OpenShift ingress CA certificate
oc get secret router-ca -n openshift-ingress-operator -o jsonpath='{.data.tls\.crt}' | base64 -d > ingress-ca.crt

# Get OpenShift service CA
oc get configmap service-ca-bundle -n openshift-config -o jsonpath='{.data.service-ca\.crt}' > service-ca.crt

# Combine all CA certificates
cat current-ca-bundle.crt keycloak-ca.crt ingress-ca.crt service-ca.crt > combined-ca-bundle.crt

# Update the ConfigMap
oc create configmap combined-ca-bundle \
  --from-file=ca-bundle.crt=combined-ca-bundle.crt \
  --dry-run=client -o yaml | oc replace -f - -n costmanagement-metrics-operator
```

### 2. Update CostManagementMetricsConfig for Self-Signed Certificates

**Note**: Unlike deployment changes, CostManagementMetricsConfig modifications are safe because they represent user configuration, not OLM-managed resources.

Modify the CostManagementMetricsConfig to handle self-signed certificates:

```yaml
apiVersion: costmanagement-metrics-cfg.openshift.io/v1beta1
kind: CostManagementMetricsConfig
metadata:
  name: costmanagementmetricscfg-sample-v1beta1
  namespace: costmanagement-metrics-operator
spec:
  # Authentication configuration for Keycloak
  authentication:
    type: "token"
    token_url: "https://keycloak-keycloak.apps.cluster.local/realms/kubernetes/protocol/openid-connect/token"
    # Custom client configuration
    client_id: "cost-management-operator"
    # Secret containing client credentials
    secret_name: "cost-management-auth-secret"

  # Upload configuration
  upload:
    upload_toggle: true
    upload_cycle: 360  # 6 hours
    validate_cert: true  # Keep certificate validation enabled
    ingress_path: "/api/ingress/v1/upload"
    # Use internal service URL to avoid external certificate issues
    ingress_url: "http://cost-onprem-ingress.cost-onprem.svc.cluster.local:8080"

  # Prometheus configuration
  prometheus_config:
    service_address: "https://thanos-querier.openshift-monitoring.svc:9091"
    skip_tls_verification: false  # Keep TLS verification enabled
    collect_previous_data: true
    context_timeout: 120

  # Source configuration
  source:
    create_source: false
    check_cycle: 1440
    sources_path: "/api/sources/v1.0/"
```

### 3. Create Custom CA Bundle Script

Create an automated script to maintain the CA bundle:

```bash
#!/bin/bash
# File: scripts/setup-cost-mgmt-tls.sh

NAMESPACE="costmanagement-metrics-operator"
TEMP_DIR="/tmp/cost-mgmt-ca"

echo "🔧 Updating Cost Management Operator CA Bundle"
echo "=============================================="

mkdir -p "$TEMP_DIR"
cd "$TEMP_DIR"

# Get OpenShift system CA bundle
echo "📥 Extracting OpenShift system CA bundle..."
oc get configmap kube-root-ca.crt -n "$NAMESPACE" -o jsonpath='{.data.ca\.crt}' > system-ca.crt

# Get service CA bundle
echo "📥 Extracting service CA bundle..."
oc get configmap service-ca-bundle -n openshift-config -o jsonpath='{.data.service-ca\.crt}' > service-ca.crt 2>/dev/null || echo "# No service CA found" > service-ca.crt

# Get Keycloak CA (if available)
echo "📥 Extracting Keycloak CA certificate..."
KEYCLOAK_NS=$(oc get keycloak -A -o jsonpath='{.items[0].metadata.namespace}' 2>/dev/null || echo "keycloak")
if oc get secret -n "$KEYCLOAK_NS" | grep -q tls; then
    KEYCLOAK_SECRET=$(oc get secret -n "$KEYCLOAK_NS" -o name | grep tls | head -1)
    oc get "$KEYCLOAK_SECRET" -n "$KEYCLOAK_NS" -o jsonpath='{.data.tls\.crt}' | base64 -d > keycloak-ca.crt
else
    echo "# No Keycloak TLS secret found" > keycloak-ca.crt
fi

# Get ingress CA certificate
echo "📥 Extracting ingress CA certificate..."
if oc get secret router-ca -n openshift-ingress-operator &>/dev/null; then
    oc get secret router-ca -n openshift-ingress-operator -o jsonpath='{.data.tls\.crt}' | base64 -d > ingress-ca.crt
else
    # Alternative: extract from route
    oc get route -A -o jsonpath='{.items[0].spec.host}' | head -1 | xargs -I {} sh -c 'echo | openssl s_client -connect {}:443 -servername {} 2>/dev/null | openssl x509 > ingress-ca.crt' || echo "# No ingress CA found" > ingress-ca.crt
fi

# Create combined CA bundle
echo "🔗 Creating combined CA bundle..."
cat > combined-ca-bundle.crt << EOF
# Combined CA Bundle for Cost Management Operator
# Generated on $(date)
# Includes: System CA, Service CA, Keycloak CA, Ingress CA

EOF

cat system-ca.crt >> combined-ca-bundle.crt
echo "" >> combined-ca-bundle.crt
cat service-ca.crt >> combined-ca-bundle.crt
echo "" >> combined-ca-bundle.crt
cat keycloak-ca.crt >> combined-ca-bundle.crt
echo "" >> combined-ca-bundle.crt
cat ingress-ca.crt >> combined-ca-bundle.crt

# Update ConfigMap
echo "📤 Updating combined-ca-bundle ConfigMap..."
oc create configmap combined-ca-bundle \
  --from-file=ca-bundle.crt=combined-ca-bundle.crt \
  --dry-run=client -o yaml | oc apply -f - -n "$NAMESPACE"

echo "✅ CA bundle updated successfully"

# Restart operator to pick up new CA bundle
echo "🔄 Restarting Cost Management Operator..."
oc rollout restart deployment costmanagement-metrics-operator -n "$NAMESPACE"

echo "✅ Cost Management Operator CA bundle update complete!"

# Cleanup
cd /
rm -rf "$TEMP_DIR"
```

### 4. Validation and Troubleshooting

#### Check TLS Configuration
```bash
# Verify CA bundle is mounted correctly
oc exec -n costmanagement-metrics-operator deployment/costmanagement-metrics-operator -- ls -la /etc/ssl/certs/

# Check environment variables
oc exec -n costmanagement-metrics-operator deployment/costmanagement-metrics-operator -- printenv | grep -E "(SSL_CERT_FILE|REQUESTS_CA_BUNDLE)"

# Test Keycloak connectivity
oc exec -n costmanagement-metrics-operator deployment/costmanagement-metrics-operator -- curl -v https://keycloak-keycloak.apps.cluster.local/realms/kubernetes/.well-known/openid_configuration
```

#### Common TLS Errors and Solutions

Based on our real-world testing, here are the exact errors you'll encounter and their solutions:

| Error | When It Occurs | Root Cause | Solution |
|-------|---------------|------------|----------|
| `x509: certificate signed by unknown authority` | JWT token generation from Keycloak | Keycloak CA not in bundle | Add Keycloak CA to combined-ca-bundle |
| `tls: failed to verify certificate: x509: certificate signed by unknown authority` | Upload to ingress | Ingress route CA not in bundle | Add OpenShift ingress CA to bundle |
| `SSL: CERTIFICATE_VERIFY_FAILED` | Any HTTPS communication | Outdated or incomplete CA bundle | Run CA bundle update script |
| `dial tcp: lookup keycloak-keycloak.apps.cluster.local: no such host` | DNS resolution failure | Network/DNS issue | Verify service names and DNS |
| `connection refused` | Service connectivity | Service not available | Check pod status: `oc get pods -n keycloak` |
| `context deadline exceeded` | Request timeout | Network or TLS handshake issues | Check network connectivity and certificates |

#### Real-World Debugging Experience

During our JWT authentication implementation, we encountered this exact sequence:

1. **Initial Error**: `x509: certificate signed by unknown authority` when generating JWT tokens
2. **Symptom**: Cost Management Operator logs showed continuous authentication failures
3. **Impact**: No data uploads, JWT authentication completely broken
4. **First Attempt**: Tried to modify deployment directly → **Failed due to OLM reconciliation**
5. **Working Solution**: Updated existing `combined-ca-bundle` ConfigMap with Keycloak's CA certificate
6. **Operator Restart**: Required restart to pick up new CA bundle
7. **Result**: JWT token generation successful, uploads resumed

**Key Lessons**:
- The operator's default CA bundle only includes standard public CAs, not custom OpenShift CAs
- **OLM prevents direct deployment modifications** - work within existing structure
- ConfigMap updates + operator restart is the only viable approach
- Always verify changes persist after OLM reconciliation cycles

#### Logs to Monitor
```bash
# Cost Management Operator logs
oc logs -n costmanagement-metrics-operator deployment/costmanagement-metrics-operator -f

# Look for specific TLS errors
oc logs -n costmanagement-metrics-operator deployment/costmanagement-metrics-operator | grep -i "tls\|ssl\|certificate\|x509"
```

## Integration with JWT Authentication

**IMPORTANT**: This TLS configuration is essential for the JWT authentication flow documented in [native-jwt-authentication.md](native-jwt-authentication.md).

### Why TLS Matters for JWT Authentication
Our JWT authentication implementation requires the Cost Management Operator to:
1. **Generate JWT tokens** by calling Keycloak's token endpoint
2. **Upload data** to the ingress with JWT authentication
3. **Validate certificates** for all HTTPS communications

Without proper TLS configuration, the JWT authentication flow fails at the token generation step.

When using JWT authentication (as documented in native-jwt-authentication.md), ensure:

1. **Keycloak Communication**: Cost Management Operator must trust Keycloak's certificate
2. **Token Validation**: Tokens are properly signed and validated
3. **Ingress Communication**: Upload requests include proper CA certificates

### Complete Configuration Example

```yaml
# CostManagementMetricsConfig for JWT + Self-Signed Certs
apiVersion: costmanagement-metrics-cfg.openshift.io/v1beta1
kind: CostManagementMetricsConfig
metadata:
  name: costmanagementmetricscfg-jwt-tls
  namespace: costmanagement-metrics-operator
spec:
  authentication:
    type: "token"
    token_url: "https://keycloak-keycloak.apps.cluster.local/realms/kubernetes/protocol/openid-connect/token"
    client_id: "cost-management-operator"
    secret_name: "cost-management-auth-secret"

  upload:
    upload_toggle: true
    upload_cycle: 360
    validate_cert: true  # Certificate validation enabled with custom CA bundle
    ingress_path: "/api/ingress/v1/upload"
    # Use external route for JWT authentication flow
    ingress_url: "https://cost-onprem-ingress-cost-onprem.apps.cluster.local"

  prometheus_config:
    service_address: "https://thanos-querier.openshift-monitoring.svc:9091"
    skip_tls_verification: false
    collect_previous_data: true
    context_timeout: 120
```

## OLM Limitations and Workarounds

### What Doesn't Work (Due to OLM Management)

❌ **Direct Deployment Modification**:
```bash
# This will be reverted by OLM reconciliation
oc patch deployment costmanagement-metrics-operator -n costmanagement-metrics-operator --patch '...'
```

❌ **Adding New Volume Mounts**:
```bash
# OLM will revert any changes to volumes/volumeMounts
oc set volume deployment/costmanagement-metrics-operator --add --name=custom-ca
```

❌ **Environment Variable Modifications**:
```bash
# Environment variables defined in CSV cannot be modified
oc set env deployment/costmanagement-metrics-operator SSL_CERT_FILE=/custom/path
```

### What Does Work (OLM-Compatible)

✅ **ConfigMap Content Updates**:
```bash
# Update the contents of existing ConfigMaps that are already mounted
oc create configmap combined-ca-bundle --from-file=ca-bundle.crt=new-bundle.crt --dry-run=client -o yaml | oc apply -f -
```

✅ **CostManagementMetricsConfig Modifications**:
```bash
# User configuration CRs are not managed by CSV
oc apply -f custom-metricsconfig.yaml
```

✅ **Secret Content Updates**:
```bash
# Update existing secrets (like authentication secrets)
oc create secret generic cost-management-auth-secret --from-literal=client_secret=new-secret --dry-run=client -o yaml | oc apply -f -
```

### Verification After OLM Reconciliation

Always verify your changes survive OLM reconciliation:

```bash
# Trigger OLM reconciliation by updating operator
oc patch csv costmanagement-metrics-operator.4.1.0 -n costmanagement-metrics-operator --type='json' -p='[{"op": "add", "path": "/metadata/annotations/test", "value": "trigger-reconcile"}]'

# Wait and verify your ConfigMap changes are still present
oc get configmap combined-ca-bundle -n costmanagement-metrics-operator -o yaml | grep "your-custom-ca"
```

## Production Considerations

1. **Certificate Rotation**: Monitor certificate expiration and update CA bundle accordingly
2. **OLM Reconciliation**: Ensure CA bundle updates persist across operator upgrades
3. **Automation**: Use the update script in CI/CD pipelines, accounting for OLM behavior
4. **Monitoring**: Set up alerts for TLS-related failures and OLM reconciliation events
5. **Backup**: Keep backup of working CA bundle configuration before OLM operations
6. **Testing**: Validate TLS configuration after any certificate changes and OLM updates

## Quick Setup Commands

```bash
# 1. Deploy Cost Management Operator with CA bundle support
./scripts/setup-cost-mgmt-tls.sh

# 2. Apply TLS-compatible CostManagementMetricsConfig
oc apply -f examples/costmanagementmetricscfg-tls.yaml

# 3. Wait for operator restart
oc rollout status deployment costmanagement-metrics-operator -n costmanagement-metrics-operator

# 4. Validate TLS connectivity
oc logs -n costmanagement-metrics-operator deployment/costmanagement-metrics-operator --tail=50
```

This configuration ensures the Cost Management Operator can successfully communicate with all services using self-signed certificates while maintaining security best practices.
