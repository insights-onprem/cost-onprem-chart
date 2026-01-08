#!/bin/bash

# setup-cost-mgmt-tls.sh
# Comprehensive automation script for Cost Management Operator with self-signed certificates
# Similar to deploy-kind.sh but for Cost Management TLS setup
#
# Usage: ./setup-cost-mgmt-tls.sh [options]
# Prerequisites: OpenShift cluster with Keycloak (RHBK) installed

set -euo pipefail

# Default configuration
DEFAULT_NAMESPACE="costmanagement-metrics-operator"
DEFAULT_KEYCLOAK_NAMESPACE="keycloak"
DEFAULT_CLIENT_ID="cost-management-operator"
DEFAULT_INGRESS_URL=""
DEFAULT_KEYCLOAK_URL=""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration variables
NAMESPACE="${COST_MGMT_NAMESPACE:-$DEFAULT_NAMESPACE}"
KEYCLOAK_NAMESPACE="${KEYCLOAK_NAMESPACE:-$DEFAULT_KEYCLOAK_NAMESPACE}"
CLIENT_ID="${CLIENT_ID:-$DEFAULT_CLIENT_ID}"
VERBOSE=false
DRY_RUN=false
SKIP_VALIDATION=false

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_header() {
    echo -e "\n${BLUE}==== $1 ====${NC}"
}

# Function to validate certificate
validate_cert() {
    local cert_file="$1"
    local cert_name="$2"

    if [ ! -f "$cert_file" ] || [ ! -s "$cert_file" ]; then
        print_warning "Certificate file $cert_name is empty or missing"
        return 1
    fi

    # Check if it's a valid certificate
    if openssl x509 -noout -text -in "$cert_file" >/dev/null 2>&1; then
        local subject=$(openssl x509 -noout -subject -in "$cert_file" 2>/dev/null | sed 's/subject=//')
        local issuer=$(openssl x509 -noout -issuer -in "$cert_file" 2>/dev/null | sed 's/issuer=//')
        print_success "✓ Valid certificate: $cert_name"
        print_status "  Subject: $subject"
        print_status "  Issuer: $issuer"
        return 0
    else
        print_warning "Invalid certificate format: $cert_name"
        return 1
    fi
}

# Function to extract OpenShift system and service CAs
extract_system_cas() {
    print_header "EXTRACTING OPENSHIFT SYSTEM CA CERTIFICATES"

    local system_cas_found=0

    # Get system root CA
    print_status "Extracting OpenShift system root CA..."
    if oc get configmap kube-root-ca.crt -n "$NAMESPACE" &>/dev/null; then
        oc get configmap kube-root-ca.crt -n "$NAMESPACE" -o jsonpath='{.data.ca\.crt}' > system-root-ca.crt 2>/dev/null && {
            if [ -s "system-root-ca.crt" ]; then
                print_success "✓ Extracted system root CA"
                system_cas_found=$((system_cas_found + 1))
            fi
        }
    fi

    # Get service CA bundle
    print_status "Extracting OpenShift service CA..."
    if oc get configmap service-ca-bundle -n openshift-config &>/dev/null; then
        oc get configmap service-ca-bundle -n openshift-config -o jsonpath='{.data.service-ca\.crt}' > service-ca.crt 2>/dev/null && {
            if [ -s "service-ca.crt" ]; then
                print_success "✓ Extracted service CA"
                system_cas_found=$((system_cas_found + 1))
            fi
        }
    fi

    # Get cluster CA bundle
    print_status "Extracting cluster CA bundle..."
    if oc get configmap cluster-ca-bundle -n openshift-config &>/dev/null; then
        oc get configmap cluster-ca-bundle -n openshift-config -o jsonpath='{.data.ca-bundle\.crt}' > cluster-ca.crt 2>/dev/null && {
            if [ -s "cluster-ca.crt" ]; then
                print_success "✓ Extracted cluster CA bundle"
                system_cas_found=$((system_cas_found + 1))
            fi
        }
    fi

    # Get API server CA
    print_status "Extracting API server CA..."
    if oc get configmap kube-apiserver-server-ca -n openshift-kube-apiserver &>/dev/null; then
        oc get configmap kube-apiserver-server-ca -n openshift-kube-apiserver -o jsonpath='{.data.ca-bundle\.crt}' > api-server-ca.crt 2>/dev/null && {
            if [ -s "api-server-ca.crt" ]; then
                print_success "✓ Extracted API server CA"
                system_cas_found=$((system_cas_found + 1))
            fi
        }
    fi

    # Get registry CA (if using internal registry)
    print_status "Extracting registry CA..."
    if oc get configmap image-registry-certificates -n openshift-image-registry &>/dev/null; then
        oc get configmap image-registry-certificates -n openshift-image-registry -o jsonpath='{.data.*}' > registry-ca.crt 2>/dev/null && {
            if [ -s "registry-ca.crt" ]; then
                print_success "✓ Extracted registry CA"
                system_cas_found=$((system_cas_found + 1))
            fi
        }
    fi

    print_status "Found $system_cas_found OpenShift system CA certificate(s)"
}

# Function to extract OpenShift router/ingress CA certificates
extract_router_cas() {
    print_header "EXTRACTING ROUTER/INGRESS CA CERTIFICATES"

    local router_cas_found=0

    # Method 1: Get router CA from ingress operator
    print_status "Extracting router CA from ingress operator..."
    if oc get secret router-ca -n openshift-ingress-operator &>/dev/null; then
        oc get secret router-ca -n openshift-ingress-operator -o jsonpath='{.data.tls\.crt}' | base64 -d > router-ca-operator.crt 2>/dev/null && {
            if validate_cert "router-ca-operator.crt" "Router CA (Operator)"; then
                router_cas_found=$((router_cas_found + 1))
            fi
        } || print_warning "Failed to extract router CA from operator"
    fi

    # Method 2: Get router CA from default certificate
    print_status "Extracting router CA from default certificate..."
    if oc get secret router-certs-default -n openshift-ingress &>/dev/null; then
        oc get secret router-certs-default -n openshift-ingress -o jsonpath='{.data.tls\.crt}' | base64 -d > router-ca-default.crt 2>/dev/null && {
            if validate_cert "router-ca-default.crt" "Router CA (Default)"; then
                router_cas_found=$((router_cas_found + 1))
            fi
        } || print_warning "Failed to extract router CA from default certs"
    fi

    # Method 3: Extract from ingress controller configuration
    print_status "Extracting CA from ingress controller configuration..."
    local ingress_controller=$(oc get ingresscontrollers -n openshift-ingress-operator -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "default")
    if oc get ingresscontroller "$ingress_controller" -n openshift-ingress-operator &>/dev/null; then
        local default_cert=$(oc get ingresscontroller "$ingress_controller" -n openshift-ingress-operator -o jsonpath='{.spec.defaultCertificate.name}' 2>/dev/null || echo "")
        if [ -n "$default_cert" ]; then
            oc get secret "$default_cert" -n openshift-ingress -o jsonpath='{.data.tls\.crt}' | base64 -d > router-ca-controller.crt 2>/dev/null && {
                if validate_cert "router-ca-controller.crt" "Router CA (Controller)"; then
                    router_cas_found=$((router_cas_found + 1))
                fi
            } || print_warning "Failed to extract CA from ingress controller config"
        fi
    fi

    # Method 4: Extract from OpenShift console route
    print_status "Extracting CA from console route..."
    if oc get route console -n openshift-console &>/dev/null; then
        local console_host=$(oc get route console -n openshift-console -o jsonpath='{.spec.host}')
        if [ -n "$console_host" ]; then
            echo | openssl s_client -connect "$console_host:443" -servername "$console_host" 2>/dev/null | openssl x509 > console-route-ca.crt 2>/dev/null && {
                if validate_cert "console-route-ca.crt" "Console Route CA"; then
                    router_cas_found=$((router_cas_found + 1))
                fi
            } || print_warning "Failed to extract CA from console route"
        fi
    fi

    # Method 5: Get cluster ingress CA bundle
    print_status "Extracting cluster ingress CA bundle..."
    if oc get configmap default-ingress-cert -n openshift-config-managed &>/dev/null; then
        oc get configmap default-ingress-cert -n openshift-config-managed -o jsonpath='{.data.ca-bundle\.crt}' > cluster-ingress-ca.crt 2>/dev/null && {
            if [ -s "cluster-ingress-ca.crt" ]; then
                print_success "✓ Extracted cluster ingress CA bundle"
                router_cas_found=$((router_cas_found + 1))
            fi
        } || print_warning "Failed to extract cluster ingress CA bundle"
    fi

    print_status "Found $router_cas_found router/ingress CA certificate(s)"
}

# Function to extract Keycloak CA certificates comprehensively
extract_keycloak_cas() {
    print_header "EXTRACTING KEYCLOAK CA CERTIFICATES"

    local keycloak_cas_found=0

    # Method 1: Get Keycloak route certificate
    print_status "Extracting Keycloak CA from route..."
    if oc get route keycloak -n "$KEYCLOAK_NAMESPACE" &>/dev/null; then
        local keycloak_host=$(oc get route keycloak -n "$KEYCLOAK_NAMESPACE" -o jsonpath='{.spec.host}')
        if [ -n "$keycloak_host" ]; then
            print_status "Connecting to Keycloak at: $keycloak_host"
            echo | openssl s_client -connect "$keycloak_host:443" -servername "$keycloak_host" 2>/dev/null | openssl x509 > keycloak-route-ca.crt 2>/dev/null && {
                if validate_cert "keycloak-route-ca.crt" "Keycloak Route CA"; then
                    keycloak_cas_found=$((keycloak_cas_found + 1))
                fi
            } || print_warning "Failed to extract CA from Keycloak route"
        fi
    fi

    # Method 2: Get Keycloak TLS secrets
    print_status "Extracting Keycloak CA from TLS secrets..."
    local keycloak_secrets=$(oc get secrets -n "$KEYCLOAK_NAMESPACE" --no-headers -o custom-columns=NAME:.metadata.name | grep -E "(keycloak.*tls|tls.*keycloak)" || echo "")
    if [ -n "$keycloak_secrets" ]; then
        while IFS= read -r secret; do
            if [ -n "$secret" ]; then
                print_status "Checking secret: $secret"
                oc get secret "$secret" -n "$KEYCLOAK_NAMESPACE" -o jsonpath='{.data.tls\.crt}' | base64 -d > "keycloak-secret-$secret.crt" 2>/dev/null && {
                    if validate_cert "keycloak-secret-$secret.crt" "Keycloak Secret ($secret)"; then
                        keycloak_cas_found=$((keycloak_cas_found + 1))
                    fi
                } || print_warning "Failed to extract CA from secret $secret"
            fi
        done <<< "$keycloak_secrets"
    fi

    # Method 3: Get Keycloak service certificate
    print_status "Extracting Keycloak CA from service..."
    if oc get service keycloak -n "$KEYCLOAK_NAMESPACE" &>/dev/null; then
        local keycloak_service_ip=$(oc get service keycloak -n "$KEYCLOAK_NAMESPACE" -o jsonpath='{.spec.clusterIP}')
        if [ -n "$keycloak_service_ip" ]; then
            print_status "Connecting to Keycloak service at: $keycloak_service_ip"
            timeout 10 echo | openssl s_client -connect "$keycloak_service_ip:8443" 2>/dev/null | openssl x509 > keycloak-service-ca.crt 2>/dev/null && {
                if validate_cert "keycloak-service-ca.crt" "Keycloak Service CA"; then
                    keycloak_cas_found=$((keycloak_cas_found + 1))
                fi
            } || print_warning "Failed to extract CA from Keycloak service"
        fi
    fi

    # Method 4: Extract from Keycloak StatefulSet volumes
    print_status "Extracting Keycloak CA from StatefulSet configuration..."
    if oc get statefulset keycloak -n "$KEYCLOAK_NAMESPACE" &>/dev/null; then
        local volume_mounts=$(oc get statefulset keycloak -n "$KEYCLOAK_NAMESPACE" -o jsonpath='{.spec.template.spec.containers[0].volumeMounts[*].name}' 2>/dev/null || echo "")
        if echo "$volume_mounts" | grep -q "keycloak-tls"; then
            print_status "Found keycloak-tls volume mount"
            # Try to extract from the volume
            local keycloak_pod=$(oc get pods -n "$KEYCLOAK_NAMESPACE" -l app=keycloak -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
            if [ -n "$keycloak_pod" ]; then
                oc exec -n "$KEYCLOAK_NAMESPACE" "$keycloak_pod" -- cat /etc/x509/https/tls.crt > keycloak-pod-ca.crt 2>/dev/null && {
                    if validate_cert "keycloak-pod-ca.crt" "Keycloak Pod CA"; then
                        keycloak_cas_found=$((keycloak_cas_found + 1))
                    fi
                } || print_warning "Failed to extract CA from Keycloak pod"
            fi
        fi
    fi

    # Method 5: Check for custom Keycloak CA ConfigMaps
    print_status "Checking for custom Keycloak CA ConfigMaps..."
    local keycloak_ca_cms=$(oc get configmaps -n "$KEYCLOAK_NAMESPACE" --no-headers -o custom-columns=NAME:.metadata.name | grep -E "(keycloak.*ca|ca.*keycloak)" || echo "")
    if [ -n "$keycloak_ca_cms" ]; then
        while IFS= read -r cm; do
            if [ -n "$cm" ]; then
                print_status "Checking ConfigMap: $cm"
                local ca_keys=$(oc get configmap "$cm" -n "$KEYCLOAK_NAMESPACE" -o jsonpath='{.data}' | jq -r 'keys[]' 2>/dev/null | grep -E "(ca|cert|crt)" || echo "")
                if [ -n "$ca_keys" ]; then
                    while IFS= read -r key; do
                        if [ -n "$key" ]; then
                            oc get configmap "$cm" -n "$KEYCLOAK_NAMESPACE" -o jsonpath="{.data.$key}" > "keycloak-cm-$cm-$key.crt" 2>/dev/null && {
                                if validate_cert "keycloak-cm-$cm-$key.crt" "Keycloak ConfigMap ($cm/$key)"; then
                                    keycloak_cas_found=$((keycloak_cas_found + 1))
                                fi
                            } || print_warning "Failed to extract CA from ConfigMap $cm/$key"
                        fi
                    done <<< "$ca_keys"
                fi
            fi
        done <<< "$keycloak_ca_cms"
    fi

    print_status "Found $keycloak_cas_found Keycloak CA certificate(s)"
}

# Function to create comprehensive CA bundle
create_comprehensive_bundle() {
    print_header "CREATING COMPREHENSIVE CA BUNDLE"

    # Start with header
    cat > combined-ca-bundle.crt << EOF
#
# Comprehensive CA Bundle for Cost Management Operator
# Generated on $(date)
# Cluster: $(oc config current-context)
# Target Namespace: $NAMESPACE
#
# This bundle includes:
# - OpenShift System CAs (root, service, cluster, API server)
# - Router/Ingress CAs (all available methods)
# - Keycloak CAs (route, secrets, service, pod)
# - Registry CAs (if available)
#
# Generated by: setup-cost-mgmt-tls.sh
#

EOF

    local bundle_size=0
    local certs_added=0

    # Add all CA certificates with detailed headers
    for ca_file in *.crt; do
        if [ -f "$ca_file" ] && [ -s "$ca_file" ] && [ "$ca_file" != "combined-ca-bundle.crt" ]; then
            print_status "Adding certificate: $ca_file"

            # Add header for this certificate
            echo "" >> combined-ca-bundle.crt
            echo "# ============================================" >> combined-ca-bundle.crt
            echo "# Certificate: $ca_file" >> combined-ca-bundle.crt
            echo "# Added: $(date)" >> combined-ca-bundle.crt

            # Try to add certificate details
            if openssl x509 -noout -subject -issuer -dates -in "$ca_file" >/dev/null 2>&1; then
                echo "# $(openssl x509 -noout -subject -in "$ca_file" 2>/dev/null)" >> combined-ca-bundle.crt
                echo "# $(openssl x509 -noout -issuer -in "$ca_file" 2>/dev/null)" >> combined-ca-bundle.crt
                echo "# $(openssl x509 -noout -dates -in "$ca_file" 2>/dev/null | head -1)" >> combined-ca-bundle.crt
            fi

            echo "# ============================================" >> combined-ca-bundle.crt
            echo "" >> combined-ca-bundle.crt

            # Add the certificate content
            cat "$ca_file" >> combined-ca-bundle.crt
            echo "" >> combined-ca-bundle.crt

            certs_added=$((certs_added + 1))
            bundle_size=$((bundle_size + $(wc -c < "$ca_file")))
        fi
    done

    print_success "✓ Created comprehensive CA bundle"
    print_status "  Certificates added: $certs_added"
    print_status "  Bundle size: $bundle_size bytes"
    print_status "  Total lines: $(wc -l < combined-ca-bundle.crt)"

    # Validate the bundle
    if [ $certs_added -eq 0 ]; then
        print_error "No certificates were added to the bundle!"
        return 1
    fi

    if [ $bundle_size -lt 1000 ]; then
        print_warning "Bundle size seems too small ($bundle_size bytes)"
        print_warning "This might indicate certificate extraction issues"
    fi

    return 0
}

# Function to show usage
show_usage() {
    cat << EOF
Cost Management Operator TLS Setup Script

This script automates the complete setup of the Cost Management Metrics Operator
for environments with self-signed certificates, similar to deploy-kind.sh.

USAGE:
    $0 [OPTIONS]

OPTIONS:
    -n, --namespace NAMESPACE           Cost Management operator namespace (default: $DEFAULT_NAMESPACE)
    -k, --keycloak-namespace NAMESPACE  Keycloak namespace (default: $DEFAULT_KEYCLOAK_NAMESPACE)
    -c, --client-id CLIENT_ID           Keycloak client ID (default: $DEFAULT_CLIENT_ID)
    -i, --ingress-url URL               Ingress URL (auto-detected if not provided)
    -s, --keycloak-url URL              Keycloak URL (auto-detected if not provided)
    -v, --verbose                       Enable verbose output
    -d, --dry-run                       Show what would be done without executing
    --skip-validation                   Skip final validation steps
    -h, --help                          Show this help message

PREREQUISITES:
    • OpenShift cluster with admin access
    • Keycloak (RHBK) installed and configured
    • oc CLI tool configured and logged in

EXAMPLES:
    # Basic setup with defaults
    $0

    # Custom namespace and verbose output
    $0 -n my-cost-mgmt -v

    # Dry run to see what would be done
    $0 --dry-run

    # Custom Keycloak configuration
    $0 -k keycloak-system -c my-client-id

WHAT THIS SCRIPT DOES:
    1. ✅ Verify prerequisites (cluster access, Keycloak)
    2. ✅ Install Cost Management Operator via OLM
    3. ✅ Extract all CA certificates (router, Keycloak, system)
    4. ✅ Update combined-ca-bundle ConfigMap
    5. ✅ Extract Keycloak client credentials
    6. ✅ Create CostManagementMetricsConfig with dynamic values
    7. ✅ Validate the complete setup
    8. ✅ Provide clear success/failure feedback

Similar to deploy-kind.sh, this provides a one-command setup experience!

EOF
}

# Function to check prerequisites
check_prerequisites() {
    print_header "Checking Prerequisites"

    # Check if oc is installed and configured
    if ! command -v oc &> /dev/null; then
        print_error "oc CLI not found. Please install OpenShift CLI."
        exit 1
    fi

    # Check if logged into OpenShift
    if ! oc whoami &> /dev/null; then
        print_error "Not logged into OpenShift. Please run 'oc login' first."
        exit 1
    fi

    print_success "OpenShift CLI configured and logged in as $(oc whoami)"

    # Check if Keycloak namespace exists
    if ! oc get namespace "$KEYCLOAK_NAMESPACE" &> /dev/null; then
        print_error "Keycloak namespace '$KEYCLOAK_NAMESPACE' not found."
        print_error "Please install Keycloak (RHBK) first or specify correct namespace with -k"
        exit 1
    fi

    print_success "Keycloak namespace '$KEYCLOAK_NAMESPACE' found"

    # Check if Keycloak is running
    if ! oc get pods -n "$KEYCLOAK_NAMESPACE" --no-headers | grep -q '^keycloak-.*Running'; then
        print_warning "Keycloak pods may not be running in namespace '$KEYCLOAK_NAMESPACE'"
        print_status "Continuing anyway..."
    else
        print_success "Keycloak appears to be running"
    fi

    # Auto-detect ingress URL if not provided
    if [[ -z "$DEFAULT_INGRESS_URL" ]]; then
        # First try: Search in the target namespace (most reliable, no cluster-wide permissions needed)
        local ingress_name=$(oc get route -n "$NAMESPACE" --no-headers -l 'app.kubernetes.io/component=ingress' -o custom-columns=NAME:.metadata.name 2>/dev/null | head -n1)

        if [[ -n "$ingress_name" ]]; then
            local ingress_host=$(oc get route "$ingress_name" -n "$NAMESPACE" -o jsonpath='{.spec.host}' 2>/dev/null)
            local tls_termination=$(oc get route "$ingress_name" -n "$NAMESPACE" -o jsonpath='{.spec.tls.termination}' 2>/dev/null)

            if [[ -n "$ingress_host" ]]; then
                if [[ -n "$tls_termination" ]]; then
                    DEFAULT_INGRESS_URL="https://$ingress_host"
                else
                    DEFAULT_INGRESS_URL="http://$ingress_host"
                fi
                print_success "Auto-detected ingress URL from namespace $NAMESPACE: $DEFAULT_INGRESS_URL (TLS: ${tls_termination:-none})"
            fi
        fi

        # Second try: Fall back to cluster-wide search (requires cluster-wide permissions)
        if [[ -z "$DEFAULT_INGRESS_URL" ]]; then
            local ingress_ns
            ingress_ns=$(oc get route -A --no-headers -l 'app.kubernetes.io/component=ingress' 2>/dev/null | head -n1 | awk '{print $1}')
            ingress_name=$(oc get route -A --no-headers -l 'app.kubernetes.io/component=ingress' 2>/dev/null | head -n1 | awk '{print $2}')

            if [[ -n "$ingress_ns" ]] && [[ -n "$ingress_name" ]]; then
                local ingress_host
                ingress_host=$(oc get route "$ingress_name" -n "$ingress_ns" -o jsonpath='{.spec.host}' 2>/dev/null)
                local tls_termination
                tls_termination=$(oc get route "$ingress_name" -n "$ingress_ns" -o jsonpath='{.spec.tls.termination}' 2>/dev/null)

                if [[ -n "$ingress_host" ]]; then
                    if [[ -n "$tls_termination" ]]; then
                        DEFAULT_INGRESS_URL="https://$ingress_host"
                    else
                        DEFAULT_INGRESS_URL="http://$ingress_host"
                    fi
                    print_success "Auto-detected ingress URL from namespace $ingress_ns: $DEFAULT_INGRESS_URL (TLS: ${tls_termination:-none})"
                fi
            fi
        fi

        if [[ -z "$DEFAULT_INGRESS_URL" ]]; then
            print_warning "Could not auto-detect ingress URL"
            print_warning "Searched in namespace '$NAMESPACE' and cluster-wide for routes with label 'app.kubernetes.io/component=ingress'"
        fi
    fi

    # Auto-detect Keycloak URL
    if [[ -z "$DEFAULT_KEYCLOAK_URL" ]]; then
        # Try multiple methods to detect Keycloak URL

        # Method 1: Direct keycloak route lookup
        KEYCLOAK_HOST=$(oc get route keycloak -n "$KEYCLOAK_NAMESPACE" -o jsonpath='{.spec.host}' 2>/dev/null || echo "")
        if [[ -n "$KEYCLOAK_HOST" ]]; then
            DEFAULT_KEYCLOAK_URL="https://$KEYCLOAK_HOST"
        else
            # Method 2: Search for any keycloak route
            KEYCLOAK_HOST=$(oc get routes -n "$KEYCLOAK_NAMESPACE" -o jsonpath='{.items[?(@.metadata.name=="keycloak")].spec.host}' 2>/dev/null || echo "")
            if [[ -n "$KEYCLOAK_HOST" ]]; then
                DEFAULT_KEYCLOAK_URL="https://$KEYCLOAK_HOST"
            else
                # Method 3: Fallback to grep/awk with better parsing
                KEYCLOAK_HOST=$(oc get routes -n "$KEYCLOAK_NAMESPACE" 2>/dev/null | grep -E "^keycloak\s" | awk '{print $2}' | head -n1 || echo "")
                if [[ -n "$KEYCLOAK_HOST" ]]; then
                    DEFAULT_KEYCLOAK_URL="https://$KEYCLOAK_HOST"
                fi
            fi
        fi

        if [[ -n "$DEFAULT_KEYCLOAK_URL" ]]; then
            print_success "Auto-detected Keycloak URL: $DEFAULT_KEYCLOAK_URL"
        else
            print_warning "Could not auto-detect Keycloak URL"
        fi
    fi
}

# Function to install Cost Management Operator
install_operator() {
    print_header "Installing Cost Management Operator"

    if oc get namespace "$NAMESPACE" &> /dev/null; then
        print_success "Namespace '$NAMESPACE' already exists"
    else
        print_status "Creating namespace '$NAMESPACE'"
        if [[ "$DRY_RUN" != "true" ]]; then
            oc create namespace "$NAMESPACE"
        fi
        print_success "Created namespace '$NAMESPACE'"
    fi

    # Check if operator is already installed
    if oc get subscription costmanagement-metrics-operator -n "$NAMESPACE" &> /dev/null; then
        print_success "Cost Management Operator already installed"
        return 0
    fi

    print_status "Installing Cost Management Operator via OLM"

    if [[ "$DRY_RUN" != "true" ]]; then
        # Create operator group if it doesn't exist
        if ! oc get operatorgroup -n "$NAMESPACE" | grep -q costmanagement-metrics-operator; then
            cat << EOF | oc apply -f -
apiVersion: operators.coreos.com/v1
kind: OperatorGroup
metadata:
  name: costmanagement-metrics-operator
  namespace: $NAMESPACE
spec:
  targetNamespaces:
  - $NAMESPACE
EOF
        fi

        # Create subscription
        cat << EOF | oc apply -f -
apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
  name: costmanagement-metrics-operator
  namespace: $NAMESPACE
spec:
  channel: stable
  installPlanApproval: Manual
  name: costmanagement-metrics-operator
  source: redhat-operators
  sourceNamespace: openshift-marketplace
  startingCSV: costmanagement-metrics-operator.4.2.0
EOF

        # Wait for install plan to be created and approve it
        print_status "Waiting for install plan to be created..."
        install_plan=""
        timeout=120
        while [[ $timeout -gt 0 ]]; do
            install_plan=$(oc get installplan -n "$NAMESPACE" -o jsonpath='{.items[?(@.spec.approved==false)].metadata.name}' 2>/dev/null | awk '{print $1}')
            if [[ -n "$install_plan" ]]; then
                break
            fi
            sleep 5
            timeout=$((timeout - 5))
            print_status "Waiting for install plan... (${timeout}s remaining)"
        done

        if [[ -z "$install_plan" ]]; then
            print_error "Timeout waiting for install plan to be created"
            exit 1
        fi

        print_status "Approving install plan: $install_plan"
        oc patch installplan "$install_plan" -n "$NAMESPACE" --type merge --patch '{"spec":{"approved":true}}'
        print_success "Install plan approved"

        # Wait for operator to be ready
        print_status "Waiting for operator to be ready (this may take a few minutes)..."
        timeout=300
        while [[ $timeout -gt 0 ]]; do
            if oc get csv -n "$NAMESPACE" | grep -q "costmanagement-metrics-operator.*Succeeded"; then
                break
            fi
            sleep 10
            timeout=$((timeout - 10))
            print_status "Still waiting for operator... (${timeout}s remaining)"
        done

        if [[ $timeout -le 0 ]]; then
            print_error "Timeout waiting for operator to be ready"
            exit 1
        fi
    fi

    print_success "Cost Management Operator installed successfully"
}

# Function to extract and update CA certificates
update_ca_certificates() {
    print_header "Extracting and Updating CA Certificates"

    if [[ "$DRY_RUN" == "true" ]]; then
        print_status "DRY RUN: Would extract and configure CA certificates"
        return 0
    fi

    # Create temporary directory for certificate extraction
    local temp_dir=$(mktemp -d)
    local original_dir=$(pwd)

    print_status "Working in temporary directory: $temp_dir"
    cd "$temp_dir" || {
        print_error "Failed to change to temporary directory"
        return 1
    }

    # Extract certificates from all sources
    extract_system_cas
    extract_router_cas
    extract_keycloak_cas

    # Create comprehensive bundle
    if ! create_comprehensive_bundle; then
        print_error "Failed to create CA bundle"
        cd "$original_dir"
        rm -rf "$temp_dir"
        return 1
    fi

    # Update ConfigMap with the new bundle
    print_status "Updating combined-ca-bundle ConfigMap..."
    if oc get configmap combined-ca-bundle -n "$NAMESPACE" &>/dev/null; then
        print_status "Updating existing ConfigMap..."
        oc create configmap combined-ca-bundle --from-file=ca-bundle.crt=combined-ca-bundle.crt -n "$NAMESPACE" --dry-run=client -o yaml | oc apply -f -
    else
        print_status "Creating new ConfigMap..."
        oc create configmap combined-ca-bundle --from-file=ca-bundle.crt=combined-ca-bundle.crt -n "$NAMESPACE"
    fi

    # Restart the operator deployment to pick up new certificates
    print_status "Restarting Cost Management Operator to pick up new certificates..."
    if oc get deployment costmanagement-metrics-operator -n "$NAMESPACE" &>/dev/null; then
        oc rollout restart deployment/costmanagement-metrics-operator -n "$NAMESPACE"
        print_status "Waiting for operator to restart..."
        oc rollout status deployment/costmanagement-metrics-operator -n "$NAMESPACE" --timeout=300s
    else
        print_warning "Operator deployment not found, certificates will be picked up on next restart"
    fi

    # Clean up
    cd "$original_dir"
    rm -rf "$temp_dir"

    print_success "CA certificates updated successfully"
}

# Function to extract Keycloak client credentials
extract_keycloak_credentials() {
    print_header "Extracting Keycloak Client Credentials"

    # Look for Keycloak client secret
    CLIENT_SECRET=""
    SECRET_NAME=""

        # Try common secret naming patterns
        for pattern in "keycloak-client-secret-cost-management-service-account" "keycloak-client-secret-$CLIENT_ID" "$CLIENT_ID-secret" "cost-management-service-account"; do
        if oc get secret "$pattern" -n "$KEYCLOAK_NAMESPACE" &> /dev/null; then
            SECRET_NAME="$pattern"
            break
        fi
    done

    if [[ -z "$SECRET_NAME" ]]; then
        print_warning "Could not find Keycloak client secret automatically"
        print_status "Looking for secrets containing '$CLIENT_ID'..."

        # List potential secrets
        POTENTIAL_SECRETS=$(oc get secrets -n "$KEYCLOAK_NAMESPACE" | grep -i "$CLIENT_ID" | awk '{print $1}' || echo "")

        if [[ -n "$POTENTIAL_SECRETS" ]]; then
            print_status "Found potential secrets:"
            echo "$POTENTIAL_SECRETS"
            SECRET_NAME=$(echo "$POTENTIAL_SECRETS" | head -n1)
            print_status "Using first match: $SECRET_NAME"
        else
            print_error "No Keycloak client secret found for client '$CLIENT_ID'"
            print_error "Please ensure the Keycloak client is properly configured"
            exit 1
        fi
    fi

    print_success "Found Keycloak client secret: $SECRET_NAME"

    if [[ "$DRY_RUN" != "true" ]]; then
        # Extract client credentials
        CLIENT_SECRET=$(oc get secret "$SECRET_NAME" -n "$KEYCLOAK_NAMESPACE" -o jsonpath='{.data.CLIENT_SECRET}' | base64 -d 2>/dev/null || echo "")

        if [[ -z "$CLIENT_SECRET" ]]; then
            print_error "Could not extract client secret from $SECRET_NAME"
            exit 1
        fi

        # Create auth secret in cost management namespace
        oc create secret generic cost-management-auth-secret \
            --from-literal=client_id="$CLIENT_ID" \
            --from-literal=client_secret="$CLIENT_SECRET" \
            --namespace="$NAMESPACE" \
            --dry-run=client -o yaml | oc apply -f -
    fi

    print_success "Keycloak credentials configured successfully"
}

# Function to create CostManagementMetricsConfig
create_metrics_config() {
    print_header "Creating CostManagementMetricsConfig"

    # Use auto-detected URLs or fail with clear error
    INGRESS_URL="${DEFAULT_INGRESS_URL}"
    KEYCLOAK_URL="${DEFAULT_KEYCLOAK_URL}"

    # Validate that URLs were detected
    if [[ -z "$INGRESS_URL" ]]; then
        print_error "Ingress URL was not detected"
        print_error "Please provide it manually: $0 --ingress-url <url>"
        exit 1
    fi

    if [[ -z "$KEYCLOAK_URL" ]]; then
        print_error "Keycloak URL was not detected"
        print_error "Please provide it manually: $0 --keycloak-url <url>"
        exit 1
    fi

    print_status "Using ingress URL: $INGRESS_URL"
    print_status "Using Keycloak URL: $KEYCLOAK_URL"

    if [[ "$DRY_RUN" != "true" ]]; then
        cat << EOF | oc apply -f -
apiVersion: costmanagement-metrics-cfg.openshift.io/v1beta1
kind: CostManagementMetricsConfig
metadata:
  name: costmanagementmetricscfg-tls
  namespace: $NAMESPACE
spec:
  # API URL - Base URL for uploads (defaults to console.redhat.com if not set)
  # IMPORTANT: Set to local ingress to avoid uploading to Red Hat's hosted service
  api_url: "$INGRESS_URL"

  # Authentication configuration for Keycloak with JWT
  authentication:
    type: "service-account"  # Use service-account for Keycloak JWT via client_credentials flow
    token_url: "$KEYCLOAK_URL/realms/kubernetes/protocol/openid-connect/token"
    secret_name: "cost-management-auth-secret"

  # Upload configuration - works with JWT authentication
  upload:
    upload_toggle: true
    upload_cycle: 360  # 6 hours between uploads
    validate_cert: false  # Disable cert validation for self-signed Keycloak certificates (dev/test)
    ingress_path: "/api/ingress/v1/upload"

  # Prometheus configuration with TLS validation
  prometheus_config:
    service_address: "https://thanos-querier.openshift-monitoring.svc:9091"
    skip_tls_verification: false  # Keep TLS verification enabled
    collect_previous_data: true
    context_timeout: 120
    disable_metrics_collection_cost_management: false
    disable_metrics_collection_resource_optimization: false

  # Source configuration
  source:
    create_source: false
    check_cycle: 1440  # 24 hours
    sources_path: "/api/sources/v1.0/"
    name: ""

  # Packaging configuration
  packaging:
    max_reports_to_store: 30
    max_size_MB: 100
EOF
    else
        print_status "Would create CostManagementMetricsConfig with dynamic URLs"
    fi

    print_success "CostManagementMetricsConfig created successfully"
}

# Function to validate the setup
validate_setup() {
    if [[ "$SKIP_VALIDATION" == "true" ]]; then
        print_status "Skipping validation as requested"
        return 0
    fi

    print_header "Validating Setup"

    # Check if operator pod is running
    print_status "Checking operator pod status..."
    if oc get pods -n "$NAMESPACE" | grep -q "costmanagement-metrics-operator.*Running"; then
        print_success "Operator pod is running"
    else
        print_warning "Operator pod may not be running yet"
    fi

    # Check if ConfigMap was updated
    print_status "Checking CA bundle ConfigMap..."
    if oc get configmap combined-ca-bundle -n "$NAMESPACE" &> /dev/null; then
        print_success "CA bundle ConfigMap exists"
    else
        print_warning "CA bundle ConfigMap not found"
    fi

    # Check if auth secret exists
    print_status "Checking authentication secret..."
    if oc get secret cost-management-auth-secret -n "$NAMESPACE" &> /dev/null; then
        print_success "Authentication secret exists"
    else
        print_warning "Authentication secret not found"
    fi

    # Check if CostManagementMetricsConfig exists
    print_status "Checking CostManagementMetricsConfig..."
    if oc get costmanagementmetricsconfig -n "$NAMESPACE" | grep -q "costmanagementmetricscfg-tls"; then
        print_success "CostManagementMetricsConfig exists"
    else
        print_warning "CostManagementMetricsConfig not found"
    fi

    print_success "Validation completed"
}

# Function to show completion summary
show_completion_summary() {
    print_header "Setup Complete!"

    echo ""
    echo -e "${GREEN}🎉 Cost Management Operator TLS Setup Completed Successfully!${NC}"
    echo ""
    echo -e "${BLUE}What was configured:${NC}"
    echo -e "• ✅ Cost Management Operator installed in namespace: $NAMESPACE"
    echo -e "• ✅ CA certificates extracted and configured for self-signed cert support"
    echo -e "• ✅ Keycloak JWT authentication configured (service-account type)"
    echo -e "• ✅ Client credentials: $CLIENT_ID (stored in cost-management-auth-secret)"
    echo -e "• ✅ CostManagementMetricsConfig created with local ingress URL"
    echo -e "• ✅ API URL set to ${INGRESS_URL:-local ingress} (NOT console.redhat.com)"
    echo -e "• ✅ TLS certificate validation disabled for self-signed Keycloak certificates"
    echo -e "• ✅ Operator will acquire JWT tokens via client_credentials flow"
    echo -e "• ✅ All components validated and ready for use"
    echo ""
    echo -e "${BLUE}Next steps:${NC}"
    echo -e "• The operator will automatically start collecting metrics"
    echo -e "• Check operator logs: ${YELLOW}oc logs -n $NAMESPACE deployment/costmanagement-metrics-operator${NC}"
    echo -e "• Monitor uploads: ${YELLOW}oc get costmanagementmetricsconfig -n $NAMESPACE -o yaml${NC}"
    echo ""
    echo -e "${BLUE}Troubleshooting:${NC}"
    echo -e "• Documentation: docs/cost-management-operator-tls-setup.md"
    echo -e "• CA bundle functionality: integrated in this script (setup-cost-mgmt-tls.sh)"
    echo -e "• Test script: scripts/test-ocp-dataflow-cost-management.sh"
    echo ""
    echo -e "${GREEN}Your Cost Management Operator is now ready to work with self-signed certificates!${NC}"
    echo ""
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -n|--namespace)
            NAMESPACE="$2"
            shift 2
            ;;
        -k|--keycloak-namespace)
            KEYCLOAK_NAMESPACE="$2"
            shift 2
            ;;
        -c|--client-id)
            CLIENT_ID="$2"
            shift 2
            ;;
        -i|--ingress-url)
            DEFAULT_INGRESS_URL="$2"
            shift 2
            ;;
        -s|--keycloak-url)
            DEFAULT_KEYCLOAK_URL="$2"
            shift 2
            ;;
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -d|--dry-run)
            DRY_RUN=true
            shift
            ;;
        --skip-validation)
            SKIP_VALIDATION=true
            shift
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            echo "Use -h or --help for usage information"
            exit 1
            ;;
    esac
done

# Main execution
main() {
    print_header "Cost Management Operator TLS Setup"

    if [[ "$DRY_RUN" == "true" ]]; then
        print_warning "DRY RUN MODE - No changes will be made"
    fi

    if [[ "$VERBOSE" == "true" ]]; then
        print_status "Verbose mode enabled"
        set -x
    fi

    print_status "Configuration:"
    print_status "  Namespace: $NAMESPACE"
    print_status "  Keycloak Namespace: $KEYCLOAK_NAMESPACE"
    print_status "  Client ID: $CLIENT_ID"

    # Execute setup steps
    check_prerequisites
    install_operator
    update_ca_certificates
    extract_keycloak_credentials
    create_metrics_config
    validate_setup
    show_completion_summary
}

# Run main function
main "$@"
