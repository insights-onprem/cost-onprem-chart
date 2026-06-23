#!/bin/bash

# Observability Stack Deployment Script for Cost On-Prem Performance Testing
# This script deploys Prometheus (via OpenShift user workload monitoring),
# postgres_exporter, and valkey-exporter for metrics collection.
#
# FLPATH-4061: Deploy observability stack for metrics collection
#
# The primary goal is to enable metrics collection for performance tests.
# Metrics are captured during test runs and exported to JSON/S3 for analysis.
# Grafana is optional and disabled by default (dashboards serve as metric reference).
#
# Prerequisites:
#   - OpenShift cluster with admin access
#   - oc CLI logged in
#   - cost-onprem chart deployed (for service discovery)
#
# Environment Variables:
#   LOG_LEVEL          - Control output verbosity (ERROR|WARN|INFO|DEBUG, default: WARN)
#   NAMESPACE          - Target namespace (default: cost-onprem)
#   GRAFANA_NAMESPACE  - Grafana namespace (default: grafana)
#   SKIP_UWM           - Skip user workload monitoring setup (default: false)
#   SKIP_EXPORTERS     - Skip exporter deployment (default: false)
#   SKIP_GRAFANA       - Skip Grafana deployment (default: true)
#   POSTGRES_HOST      - PostgreSQL host (default: auto-detect from namespace)
#   POSTGRES_PORT      - PostgreSQL port (default: 5432)
#   POSTGRES_USER      - PostgreSQL user for exporter (default: postgres)
#   POSTGRES_PASSWORD  - PostgreSQL password (required if deploying postgres_exporter)
#   VALKEY_HOST        - Valkey host (default: auto-detect from namespace)
#   VALKEY_PORT        - Valkey port (default: 6379)
#   RETENTION_DAYS     - Prometheus retention in days (default: 30)
#
# Examples:
#   # Deploy metrics collection infrastructure (no Grafana)
#   ./deploy-observability.sh
#
#   # Include Grafana for real-time visualization
#   SKIP_GRAFANA=false ./deploy-observability.sh
#
#   # Detailed output
#   LOG_LEVEL=INFO ./deploy-observability.sh

set -euo pipefail

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Logging configuration
LOG_LEVEL=${LOG_LEVEL:-WARN}

# Configuration
NAMESPACE=${NAMESPACE:-cost-onprem}
GRAFANA_NAMESPACE=${GRAFANA_NAMESPACE:-grafana}
SKIP_UWM=${SKIP_UWM:-false}
SKIP_EXPORTERS=${SKIP_EXPORTERS:-false}
SKIP_GRAFANA=${SKIP_GRAFANA:-true}  # Grafana is optional - dashboards are reference only
RETENTION_DAYS=${RETENTION_DAYS:-30}

# PostgreSQL settings (auto-detected if not set)
POSTGRES_HOST=${POSTGRES_HOST:-}
POSTGRES_PORT=${POSTGRES_PORT:-5432}
POSTGRES_USER=${POSTGRES_USER:-postgres}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-}

# Valkey settings (auto-detected if not set)
VALKEY_HOST=${VALKEY_HOST:-}
VALKEY_PORT=${VALKEY_PORT:-6379}

# Script directory for dashboard files
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Dashboard directories — JSON files planned for follow-up PR.
# deploy_grafana() handles missing directories gracefully (creates placeholder ConfigMap).
DASHBOARDS_DIR="${SCRIPT_DIR}/observability/dashboards/collected-metrics"
DASHBOARDS_PROMETHEUS_DIR="${SCRIPT_DIR}/observability/dashboards/prometheus"

# Logging functions
log_debug() {
    [[ "$LOG_LEVEL" == "DEBUG" ]] && echo -e "${BLUE}[DEBUG]${NC} $1"
    return 0
}

log_info() {
    [[ "$LOG_LEVEL" =~ ^(INFO|DEBUG)$ ]] && echo -e "${BLUE}[INFO]${NC} $1"
    return 0
}

log_success() {
    [[ "$LOG_LEVEL" =~ ^(WARN|INFO|DEBUG)$ ]] && echo -e "${GREEN}[SUCCESS]${NC} $1"
    return 0
}

log_warning() {
    [[ "$LOG_LEVEL" =~ ^(WARN|INFO|DEBUG)$ ]] && echo -e "${YELLOW}[WARNING]${NC} $1"
    return 0
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
    return 0
}

log_header() {
    [[ "$LOG_LEVEL" =~ ^(WARN|INFO|DEBUG)$ ]] && {
        echo ""
        echo -e "${BLUE}============================================${NC}"
        echo -e "${BLUE} $1${NC}"
        echo -e "${BLUE}============================================${NC}"
        echo ""
    }
    return 0
}

# Check prerequisites
check_prerequisites() {
    log_header "Checking Prerequisites"
    
    if ! command -v oc &> /dev/null; then
        log_error "oc CLI not found. Please install OpenShift CLI."
        exit 1
    fi
    
    if ! oc whoami &> /dev/null; then
        log_error "Not logged into OpenShift. Please run 'oc login' first."
        exit 1
    fi
    
    if ! oc get namespace "$NAMESPACE" &> /dev/null; then
        log_error "Namespace '$NAMESPACE' not found. Deploy cost-onprem first."
        exit 1
    fi
    
    log_success "Prerequisites check passed"
}

# Enable OpenShift user workload monitoring
enable_user_workload_monitoring() {
    if [[ "$SKIP_UWM" == "true" ]]; then
        log_info "Skipping user workload monitoring setup"
        return 0
    fi
    
    log_header "Enabling User Workload Monitoring"
    
    # Check if already enabled
    if oc get configmap cluster-monitoring-config -n openshift-monitoring &> /dev/null; then
        local uwm_enabled
        uwm_enabled=$(oc get configmap cluster-monitoring-config -n openshift-monitoring -o jsonpath='{.data.config\.yaml}' 2>/dev/null | grep -c "enableUserWorkload: true" || echo "0")
        if [[ "$uwm_enabled" -gt 0 ]]; then
            log_info "User workload monitoring already enabled"
            return 0
        fi
    fi
    
    log_info "Creating cluster-monitoring-config ConfigMap..."
    
    cat <<EOF | oc apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: cluster-monitoring-config
  namespace: openshift-monitoring
data:
  config.yaml: |
    enableUserWorkload: true
    prometheusK8s:
      retention: ${RETENTION_DAYS}d
      volumeClaimTemplate:
        spec:
          resources:
            requests:
              storage: 50Gi
EOF
    
    # Configure user workload monitoring retention
    log_info "Configuring user workload monitoring..."
    
    cat <<EOF | oc apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: user-workload-monitoring-config
  namespace: openshift-user-workload-monitoring
data:
  config.yaml: |
    prometheus:
      retention: ${RETENTION_DAYS}d
      volumeClaimTemplate:
        spec:
          resources:
            requests:
              storage: 20Gi
EOF
    
    # Wait for prometheus-user-workload to be ready
    log_info "Waiting for user workload Prometheus to be ready..."
    local max_wait=120
    local waited=0
    while [[ $waited -lt $max_wait ]]; do
        if oc get statefulset prometheus-user-workload -n openshift-user-workload-monitoring &> /dev/null; then
            local ready
            ready=$(oc get statefulset prometheus-user-workload -n openshift-user-workload-monitoring -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
            if [[ "$ready" -gt 0 ]]; then
                log_success "User workload monitoring enabled and ready"
                return 0
            fi
        fi
        sleep 5
        waited=$((waited + 5))
        log_debug "Waiting for Prometheus... ($waited/$max_wait seconds)"
    done
    
    log_warning "User workload Prometheus not ready after ${max_wait}s - may still be starting"
}

# Auto-detect PostgreSQL connection details
detect_postgres() {
    if [[ -n "$POSTGRES_HOST" ]]; then
        log_debug "Using provided PostgreSQL host: $POSTGRES_HOST"
        return 0
    fi
    
    log_info "Auto-detecting PostgreSQL connection..."
    
    # Look for PostgreSQL service in namespace
    local pg_svc
    pg_svc=$(oc get svc -n "$NAMESPACE" -l app.kubernetes.io/name=postgresql -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
    
    if [[ -z "$pg_svc" ]]; then
        # Try common service names (including Helm release patterns)
        for svc_name in cost-onprem-database postgresql postgres koku-db "${HELM_RELEASE_NAME:-cost-onprem}-database"; do
            if oc get svc "$svc_name" -n "$NAMESPACE" &>/dev/null; then
                pg_svc="$svc_name"
                break
            fi
        done
    fi
    
    if [[ -n "$pg_svc" ]]; then
        POSTGRES_HOST="${pg_svc}.${NAMESPACE}.svc.cluster.local"
        log_info "Detected PostgreSQL: $POSTGRES_HOST"
    else
        log_warning "Could not auto-detect PostgreSQL service"
    fi
}

# Auto-detect Valkey connection details
detect_valkey() {
    if [[ -n "$VALKEY_HOST" ]]; then
        log_debug "Using provided Valkey host: $VALKEY_HOST"
        return 0
    fi
    
    log_info "Auto-detecting Valkey connection..."
    
    # Look for Valkey/Redis service in namespace
    local valkey_svc
    valkey_svc=$(oc get svc -n "$NAMESPACE" -l app.kubernetes.io/name=valkey -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
    
    if [[ -z "$valkey_svc" ]]; then
        # Try common service names (including Helm release patterns)
        for svc_name in cost-onprem-valkey valkey redis valkey-master redis-master "${HELM_RELEASE_NAME:-cost-onprem}-valkey"; do
            if oc get svc "$svc_name" -n "$NAMESPACE" &>/dev/null; then
                valkey_svc="$svc_name"
                break
            fi
        done
    fi
    
    if [[ -n "$valkey_svc" ]]; then
        VALKEY_HOST="${valkey_svc}.${NAMESPACE}.svc.cluster.local"
        log_info "Detected Valkey: $VALKEY_HOST"
    else
        log_warning "Could not auto-detect Valkey service"
    fi
}

# Deploy postgres_exporter
deploy_postgres_exporter() {
    if [[ "$SKIP_EXPORTERS" == "true" ]]; then
        log_info "Skipping exporter deployment"
        return 0
    fi
    
    if [[ -z "$POSTGRES_HOST" ]]; then
        log_warning "PostgreSQL host not set, skipping postgres_exporter"
        return 0
    fi
    
    log_header "Deploying postgres_exporter"
    
    # Create secret for PostgreSQL credentials if password provided
    if [[ -n "$POSTGRES_PASSWORD" ]]; then
        log_info "Creating PostgreSQL credentials secret..."
        local data_source="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/postgres?sslmode=disable"
        
        oc create secret generic postgres-exporter-secret \
            --from-literal=DATA_SOURCE_NAME="$data_source" \
            -n "$NAMESPACE" \
            --dry-run=client -o yaml | oc apply -f -
    else
        log_warning "POSTGRES_PASSWORD not set - using existing secret or env vars"
    fi
    
    log_info "Deploying postgres_exporter..."
    
    cat <<EOF | oc apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres-exporter
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: postgres-exporter
    app.kubernetes.io/component: monitoring
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: postgres-exporter
  template:
    metadata:
      labels:
        app.kubernetes.io/name: postgres-exporter
        app.kubernetes.io/component: monitoring
    spec:
      containers:
        - name: postgres-exporter
          image: quay.io/prometheuscommunity/postgres-exporter:v0.15.0
          ports:
            - name: metrics
              containerPort: 9187
              protocol: TCP
          envFrom:
            - secretRef:
                name: postgres-exporter-secret
                optional: true
          env:
            - name: PG_EXPORTER_EXTEND_QUERY_PATH
              value: /etc/postgres_exporter/queries.yaml
            - name: PG_EXPORTER_AUTO_DISCOVER_DATABASES
              value: "true"
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 128Mi
          livenessProbe:
            httpGet:
              path: /healthz
              port: metrics
            initialDelaySeconds: 10
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /healthz
              port: metrics
            initialDelaySeconds: 5
            periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: postgres-exporter
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: postgres-exporter
    app.kubernetes.io/component: monitoring
spec:
  selector:
    app.kubernetes.io/name: postgres-exporter
  ports:
    - name: metrics
      port: 9187
      targetPort: metrics
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: postgres-exporter
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: postgres-exporter
    app.kubernetes.io/component: monitoring
spec:
  namespaceSelector:
    matchNames:
      - ${NAMESPACE}
  selector:
    matchLabels:
      app.kubernetes.io/name: postgres-exporter
  endpoints:
    - port: metrics
      interval: 15s
      path: /metrics
EOF
    
    log_success "postgres_exporter deployed"
}

# Deploy valkey-exporter (Redis-compatible)
deploy_valkey_exporter() {
    if [[ "$SKIP_EXPORTERS" == "true" ]]; then
        return 0
    fi
    
    if [[ -z "$VALKEY_HOST" ]]; then
        log_warning "Valkey host not set, skipping valkey-exporter"
        return 0
    fi
    
    log_header "Deploying valkey-exporter"
    
    log_info "Deploying valkey-exporter..."
    
    cat <<EOF | oc apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: valkey-exporter
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: valkey-exporter
    app.kubernetes.io/component: monitoring
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: valkey-exporter
  template:
    metadata:
      labels:
        app.kubernetes.io/name: valkey-exporter
        app.kubernetes.io/component: monitoring
    spec:
      containers:
        - name: valkey-exporter
          image: quay.io/oliver006/redis_exporter:v1.58.0
          args:
            - --redis.addr=redis://${VALKEY_HOST}:${VALKEY_PORT}
          ports:
            - name: metrics
              containerPort: 9121
              protocol: TCP
          resources:
            requests:
              cpu: 25m
              memory: 32Mi
            limits:
              cpu: 100m
              memory: 64Mi
          livenessProbe:
            httpGet:
              path: /health
              port: metrics
            initialDelaySeconds: 10
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /health
              port: metrics
            initialDelaySeconds: 5
            periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: valkey-exporter
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: valkey-exporter
    app.kubernetes.io/component: monitoring
spec:
  selector:
    app.kubernetes.io/name: valkey-exporter
  ports:
    - name: metrics
      port: 9121
      targetPort: metrics
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: valkey-exporter
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: valkey-exporter
    app.kubernetes.io/component: monitoring
spec:
  namespaceSelector:
    matchNames:
      - ${NAMESPACE}
  selector:
    matchLabels:
      app.kubernetes.io/name: valkey-exporter
  endpoints:
    - port: metrics
      interval: 15s
      path: /metrics
EOF
    
    log_success "valkey-exporter deployed"
}

# Deploy celery-exporter for Celery task metrics
deploy_celery_exporter() {
    if [[ "$SKIP_EXPORTERS" == "true" ]]; then
        log_info "Skipping exporter deployment"
        return 0
    fi
    
    if [[ -z "$VALKEY_HOST" ]]; then
        log_warning "Valkey host not set, skipping celery-exporter"
        return 0
    fi
    
    log_header "Deploying celery-exporter"
    
    local broker_url="redis://${VALKEY_HOST}:${VALKEY_PORT:-6379}/0"
    log_info "Using broker: ${broker_url}"
    
    cat <<EOF | oc apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: celery-exporter
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: celery-exporter
    app.kubernetes.io/component: monitoring
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: celery-exporter
  template:
    metadata:
      labels:
        app.kubernetes.io/name: celery-exporter
        app.kubernetes.io/component: monitoring
    spec:
      containers:
        - name: celery-exporter
          image: docker.io/danihodovic/celery-exporter:0.10.10
          args:
            - --broker-url=${broker_url}
            - --retry-interval=5
          ports:
            - name: metrics
              containerPort: 9808
              protocol: TCP
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 128Mi
          livenessProbe:
            httpGet:
              path: /health
              port: metrics
            initialDelaySeconds: 10
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /health
              port: metrics
            initialDelaySeconds: 5
            periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: celery-exporter
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: celery-exporter
    app.kubernetes.io/component: monitoring
spec:
  selector:
    app.kubernetes.io/name: celery-exporter
  ports:
    - name: metrics
      port: 9808
      targetPort: metrics
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: celery-exporter
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: celery-exporter
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: celery-exporter
  endpoints:
    - port: metrics
      interval: 15s
      path: /metrics
EOF
    
    log_success "celery-exporter deployed"
}

# Deploy Grafana via Operator
deploy_grafana() {
    if [[ "$SKIP_GRAFANA" == "true" ]]; then
        log_info "Skipping Grafana deployment"
        return 0
    fi
    
    log_header "Deploying Grafana"
    
    # Create namespace if needed
    if ! oc get namespace "$GRAFANA_NAMESPACE" &> /dev/null; then
        log_info "Creating namespace $GRAFANA_NAMESPACE..."
        oc create namespace "$GRAFANA_NAMESPACE"
    fi
    
    # Check if Grafana Operator is available
    log_info "Checking for Grafana Operator..."
    local grafana_op_available
    grafana_op_available=$(oc get packagemanifests -n openshift-marketplace grafana-operator 2>/dev/null | grep -c grafana-operator || echo "0")
    
    if [[ "$grafana_op_available" -gt 0 ]]; then
        log_info "Installing Grafana via Operator..."
        deploy_grafana_operator
    else
        log_info "Grafana Operator not found, deploying standalone..."
        deploy_grafana_standalone
    fi
}

# Deploy Grafana using Operator
deploy_grafana_operator() {
    # Create OperatorGroup if needed
    # Note: `oc get operatorgroup` returns exit 0 even with no resources, so check item count
    if [[ "$(oc get operatorgroup -n "$GRAFANA_NAMESPACE" --no-headers 2>/dev/null | wc -l)" -eq 0 ]]; then
        cat <<EOF | oc apply -f -
apiVersion: operators.coreos.com/v1
kind: OperatorGroup
metadata:
  name: grafana-operatorgroup
  namespace: ${GRAFANA_NAMESPACE}
spec:
  targetNamespaces:
    - ${GRAFANA_NAMESPACE}
EOF
    fi
    
    # Subscribe to Grafana Operator
    cat <<EOF | oc apply -f -
apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
  name: grafana-operator
  namespace: ${GRAFANA_NAMESPACE}
spec:
  channel: v5
  name: grafana-operator
  source: community-operators
  sourceNamespace: openshift-marketplace
EOF
    
    log_info "Waiting for Grafana Operator CSV to succeed..."
    local retries=60
    local i=0
    until oc get csv -n "${GRAFANA_NAMESPACE}" 2>/dev/null | grep -q "grafana-operator.*Succeeded"; do
        if [[ $i -ge $retries ]]; then
            log_error "Timed out waiting for Grafana Operator CSV to reach Succeeded phase"
            return 1
        fi
        sleep 10
        i=$((i + 1))
    done

    log_info "Waiting for Grafana CRD to be registered..."
    i=0
    until oc get crd grafanas.grafana.integreatly.org &>/dev/null; do
        if [[ $i -ge $retries ]]; then
            log_error "Timed out waiting for Grafana CRD"
            return 1
        fi
        sleep 5
        i=$((i + 1))
    done
    log_success "Grafana Operator ready"

    # Create Grafana instance
    cat <<EOF | oc apply -f -
apiVersion: grafana.integreatly.org/v1beta1
kind: Grafana
metadata:
  name: grafana
  namespace: ${GRAFANA_NAMESPACE}
  labels:
    dashboards: grafana
spec:
  config:
    auth:
      disable_login_form: "false"
    auth.anonymous:
      enabled: "true"
    security:
      admin_user: admin
      admin_password: admin
  route:
    spec:
      tls:
        termination: edge
EOF
    
    log_success "Grafana deployed via Operator"
}

# Deploy standalone Grafana (fallback)
deploy_grafana_standalone() {
    log_info "Deploying standalone Grafana..."
    
    cat <<EOF | oc apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: grafana
  namespace: ${GRAFANA_NAMESPACE}
  labels:
    app.kubernetes.io/name: grafana
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: grafana
  template:
    metadata:
      labels:
        app.kubernetes.io/name: grafana
    spec:
      securityContext:
        fsGroup: 472
        runAsNonRoot: true
        runAsUser: 472
      containers:
        - name: grafana
          image: docker.io/grafana/grafana:11.0.0
          ports:
            - name: http
              containerPort: 3000
          env:
            - name: GF_SECURITY_ADMIN_USER
              value: admin
            - name: GF_SECURITY_ADMIN_PASSWORD
              value: admin
            - name: GF_AUTH_ANONYMOUS_ENABLED
              value: "true"
            - name: GF_AUTH_ANONYMOUS_ORG_ROLE
              value: Viewer
            - name: GF_INSTALL_PLUGINS
              value: grafana-clock-panel,grafana-piechart-panel,yesoreyeram-infinity-datasource
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
          volumeMounts:
            - name: grafana-storage
              mountPath: /var/lib/grafana
            - name: grafana-datasources
              mountPath: /etc/grafana/provisioning/datasources
            - name: grafana-dashboards-config
              mountPath: /etc/grafana/provisioning/dashboards
            - name: grafana-dashboards
              mountPath: /var/lib/grafana/dashboards
          livenessProbe:
            httpGet:
              path: /api/health
              port: http
            initialDelaySeconds: 30
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /api/health
              port: http
            initialDelaySeconds: 10
            periodSeconds: 10
      volumes:
        - name: grafana-storage
          emptyDir: {}
        - name: grafana-datasources
          configMap:
            name: grafana-datasources
        - name: grafana-dashboards-config
          configMap:
            name: grafana-dashboards-config
        - name: grafana-dashboards
          configMap:
            name: grafana-dashboards
---
apiVersion: v1
kind: Service
metadata:
  name: grafana
  namespace: ${GRAFANA_NAMESPACE}
  labels:
    app.kubernetes.io/name: grafana
spec:
  selector:
    app.kubernetes.io/name: grafana
  ports:
    - name: http
      port: 3000
      targetPort: http
---
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: grafana
  namespace: ${GRAFANA_NAMESPACE}
spec:
  to:
    kind: Service
    name: grafana
  port:
    targetPort: http
  tls:
    termination: edge
EOF
    
    # Create datasources ConfigMap
    create_grafana_datasources
    
    # Create dashboards provisioning config
    create_grafana_dashboards_config
    
    # Create dashboards ConfigMap
    create_grafana_dashboards
    
    log_success "Grafana deployed (standalone)"
}

# Create Grafana datasources ConfigMap
create_grafana_datasources() {
    log_info "Creating Grafana datasources..."
    
    local thanos_url="https://thanos-querier.openshift-monitoring.svc.cluster.local:9091"

    # Create a service account for Grafana and bind cluster-monitoring-view
    log_info "Ensuring grafana-sa service account with cluster-monitoring-view..."
    oc create serviceaccount grafana-sa -n "${GRAFANA_NAMESPACE}" 2>/dev/null || true
    oc adm policy add-cluster-role-to-user cluster-monitoring-view \
        -z grafana-sa -n "${GRAFANA_NAMESPACE}" 2>/dev/null || true

    # Get a bearer token for the service account
    local prom_token=""
    prom_token=$(oc create token grafana-sa -n "${GRAFANA_NAMESPACE}" --duration=8760h 2>/dev/null || true)
    if [[ -z "${prom_token}" ]]; then
        # Fallback for older OCP: read from the SA secret
        local secret_name
        secret_name=$(oc get sa grafana-sa -n "${GRAFANA_NAMESPACE}" -o jsonpath='{.secrets[0].name}' 2>/dev/null || true)
        if [[ -n "${secret_name}" ]]; then
            prom_token=$(oc get secret "${secret_name}" -n "${GRAFANA_NAMESPACE}" -o jsonpath='{.data.token}' 2>/dev/null | base64 -d || true)
        fi
    fi

    if [[ -z "${prom_token}" ]]; then
        log_warning "Could not obtain Prometheus bearer token — Grafana datasource may not work"
    else
        log_success "Obtained Prometheus bearer token for grafana-sa (valid 1 year)"
    fi
    
    cat <<EOF | oc apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-datasources
  namespace: ${GRAFANA_NAMESPACE}
data:
  datasources.yaml: |
    apiVersion: 1
    datasources:
      - name: Prometheus
        type: prometheus
        access: proxy
        url: ${thanos_url}
        isDefault: true
        jsonData:
          httpHeaderName1: Authorization
          tlsSkipVerify: true
        secureJsonData:
          httpHeaderValue1: "Bearer ${prom_token}"
        editable: false
      - name: Prometheus-UWM
        type: prometheus
        access: proxy
        url: https://thanos-querier.openshift-monitoring.svc.cluster.local:9091
        jsonData:
          httpHeaderName1: Authorization
          tlsSkipVerify: true
        secureJsonData:
          httpHeaderValue1: "Bearer ${prom_token}"
        editable: false
      - name: MinIO (Infinity)
        type: yesoreyeram-infinity-datasource
        access: proxy
        isDefault: false
        jsonData:
          auth_method: "basicAuth"
          tlsSkipVerify: true
          allowedHosts:
            - "${S3_ENDPOINT:-https://minio-s3-ecosystem-qe-ai--pipeline.apps.gpc.ocp-hub.prod.psi.redhat.com}"
        secureJsonData:
          basicAuthPassword: "${AWS_SECRET_ACCESS_KEY:-}"
        basicAuth: true
        basicAuthUser: "${AWS_ACCESS_KEY_ID:-}"
        editable: true
EOF

    if [[ -n "${prom_token}" ]]; then
        # Restart Grafana to pick up the new datasource config
        oc rollout restart deployment/grafana -n "${GRAFANA_NAMESPACE}" 2>/dev/null || true
        log_info "Grafana restarted to pick up datasource with valid token"
    fi
}

# Create dashboards provisioning config
create_grafana_dashboards_config() {
    cat <<EOF | oc apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-dashboards-config
  namespace: ${GRAFANA_NAMESPACE}
data:
  dashboards.yaml: |
    apiVersion: 1
    providers:
      - name: 'cost-onprem'
        orgId: 1
        folder: 'Cost On-Prem'
        folderUid: 'cost-onprem'
        type: file
        disableDeletion: false
        updateIntervalSeconds: 30
        options:
          path: /var/lib/grafana/dashboards
EOF
}

# Create dashboards ConfigMap from JSON files
create_grafana_dashboards() {
    log_info "Creating Grafana dashboards ConfigMap..."
    
    if [[ -d "$DASHBOARDS_DIR" ]]; then
        # Create ConfigMap from dashboard files
        oc create configmap grafana-dashboards \
            --from-file="$DASHBOARDS_DIR" \
            -n "$GRAFANA_NAMESPACE" \
            --dry-run=client -o yaml | oc apply -f -
    else
        log_warning "Dashboards directory not found: $DASHBOARDS_DIR"
        log_info "Creating placeholder dashboards ConfigMap..."
        
        # Create empty ConfigMap (dashboards will be added later)
        cat <<EOF | oc apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-dashboards
  namespace: ${GRAFANA_NAMESPACE}
data: {}
EOF
    fi
}

# Print deployment summary
print_summary() {
    log_header "Deployment Summary"
    
    echo ""
    echo "Metrics Collection Infrastructure Ready"
    echo "========================================"
    echo ""
    echo "Components:"
    
    if [[ "$SKIP_UWM" != "true" ]]; then
        echo "  ✓ User Workload Monitoring (Prometheus)"
        echo "    - Retention: ${RETENTION_DAYS} days"
        echo "    - Namespace: openshift-user-workload-monitoring"
    fi
    
    if [[ "$SKIP_EXPORTERS" != "true" ]]; then
        if [[ -n "$POSTGRES_HOST" ]]; then
            echo "  ✓ postgres_exporter"
            echo "    - Target: ${POSTGRES_HOST}:${POSTGRES_PORT}"
        fi
        if [[ -n "$VALKEY_HOST" ]]; then
            echo "  ✓ valkey-exporter"
            echo "  ✓ celery-exporter"
            echo "    - Target: ${VALKEY_HOST}:${VALKEY_PORT}"
        fi
    fi
    
    if [[ "$SKIP_GRAFANA" != "true" ]]; then
        local grafana_route
        grafana_route=$(oc get route grafana -n "$GRAFANA_NAMESPACE" -o jsonpath='{.spec.host}' 2>/dev/null || echo "pending")
        echo "  ✓ Grafana (optional)"
        echo "    - URL: https://${grafana_route}"
        echo "    - Credentials: admin/admin"
    fi
    
    echo ""
    echo "Next steps:"
    echo "  1. Verify ServiceMonitors are scraping: oc get servicemonitors -n ${NAMESPACE}"
    echo "  2. Check Prometheus targets: OpenShift Console > Observe > Targets"
    echo "  3. Collect metrics during tests: ./scripts/observability/collect-metrics.sh"
    echo "  4. Upload results to S3: S3_BUCKET=mybucket ./scripts/observability/collect-metrics.sh --upload"
    echo ""
}

# Main function
main() {
    log_header "Cost On-Prem Observability Stack Deployment"
    
    check_prerequisites
    
    # Auto-detect services
    detect_postgres
    detect_valkey
    
    # Deploy components
    enable_user_workload_monitoring
    deploy_postgres_exporter
    deploy_valkey_exporter
    deploy_celery_exporter
    deploy_grafana
    
    print_summary
}

# Run main
main "$@"
