#!/bin/bash

# Kessel (SpiceDB + Relations API + Inventory API) Deployment Script for OpenShift
#
# Deploys the Kessel authorization stack required by Cost Management's ReBAC
# integration.  This script follows the same pattern as deploy-rhbk.sh and is
# called by deploy-test-cost-onprem.sh before the Helm chart install.
#
# Components deployed:
#   - PostgreSQL (dedicated instance for SpiceDB + Inventory)
#   - SpiceDB    (relationship store, gRPC on port 50051)
#   - Kessel Relations API (gRPC on port 9000, HTTP/REST on port 8000)
#   - Kessel Inventory API (HTTP on port 8000, gRPC on port 9000)
#
# This script owns the full Kessel infrastructure lifecycle, including:
#   - Schema provisioning: writes schema.zed to SpiceDB and creates the
#     kessel-schema ConfigMap consumed by the Relations API.
#   - Role seeding is handled separately by Koku's migration job
#     (kessel_seed_roles writes directly to SpiceDB).
#
# Environment Variables:
#   LOG_LEVEL             - Control output verbosity (ERROR|WARN|INFO|DEBUG, default: WARN)
#   KESSEL_NAMESPACE      - Namespace for Kessel components (default: kessel)
#   SPICEDB_PRESHARED_KEY - Pre-shared key for SpiceDB gRPC (default: auto-generated)
#   COST_MGMT_NAMESPACE   - Namespace where Cost Management runs (default: cost-onprem)
#   STORAGE_CLASS         - PVC storage class (default: auto-detect)
#
# Examples:
#   ./deploy-kessel.sh
#   LOG_LEVEL=INFO ./deploy-kessel.sh
#   KESSEL_NAMESPACE=my-kessel ./deploy-kessel.sh

set -eo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

LOG_LEVEL=${LOG_LEVEL:-WARN}

NAMESPACE=${KESSEL_NAMESPACE:-kessel}
SPICEDB_PRESHARED_KEY=${SPICEDB_PRESHARED_KEY:-$(openssl rand -hex 16)}
COST_MGMT_NAMESPACE=${COST_MGMT_NAMESPACE:-cost-onprem}
STORAGE_CLASS=${STORAGE_CLASS:-}
KEYCLOAK_NAMESPACE=${KEYCLOAK_NAMESPACE:-keycloak}
KEYCLOAK_REALM=${KEYCLOAK_REALM:-kubernetes}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log_debug()   { [[ "$LOG_LEVEL" == "DEBUG" ]] && echo -e "${BLUE}[DEBUG]${NC} $1"; return 0; }
log_info()    { [[ "$LOG_LEVEL" =~ ^(INFO|DEBUG)$ ]] && echo -e "${BLUE}[INFO]${NC} $1"; return 0; }
log_success() { [[ "$LOG_LEVEL" =~ ^(WARN|INFO|DEBUG)$ ]] && echo -e "${GREEN}[SUCCESS]${NC} $1"; return 0; }
log_warning() { [[ "$LOG_LEVEL" =~ ^(WARN|INFO|DEBUG)$ ]] && echo -e "${YELLOW}[WARNING]${NC} $1"; return 0; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1" >&2; return 0; }
log_header()  {
    [[ "$LOG_LEVEL" =~ ^(WARN|INFO|DEBUG)$ ]] && {
        echo ""
        echo -e "${BLUE}============================================${NC}"
        echo -e "${BLUE} $1${NC}"
        echo -e "${BLUE}============================================${NC}"
        echo ""
    }
    return 0
}

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
check_prerequisites() {
    log_header "CHECKING PREREQUISITES"

    if ! command -v oc >/dev/null 2>&1; then
        log_error "oc command not found. Please install OpenShift CLI."
        exit 1
    fi
    log_success "✓ OpenShift CLI (oc) is available"

    if ! kubectl cluster-info >/dev/null 2>&1; then
        log_error "Not logged into an OpenShift/Kubernetes cluster."
        exit 1
    fi
    log_success "✓ Logged into cluster"

    if [[ -z "$STORAGE_CLASS" ]]; then
        STORAGE_CLASS=$(oc get storageclass -o jsonpath='{.items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class=="true")].metadata.name}' 2>/dev/null || true)
        if [[ -z "$STORAGE_CLASS" ]]; then
            STORAGE_CLASS=$(oc get storageclass -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
        fi
        if [[ -z "$STORAGE_CLASS" ]]; then
            log_error "No StorageClass found. Set STORAGE_CLASS env var."
            exit 1
        fi
        log_info "Auto-detected StorageClass: $STORAGE_CLASS"
    fi
    log_success "✓ StorageClass: $STORAGE_CLASS"
}

# ---------------------------------------------------------------------------
# Namespace
# ---------------------------------------------------------------------------
create_namespace() {
    log_header "CREATING NAMESPACE"

    if oc get namespace "$NAMESPACE" >/dev/null 2>&1; then
        log_warning "Namespace '$NAMESPACE' already exists"
    else
        log_info "Creating namespace: $NAMESPACE"
        oc create namespace "$NAMESPACE"
        log_success "✓ Namespace '$NAMESPACE' created"
    fi

    oc label namespace "$NAMESPACE" app=kessel --overwrite=true
    log_success "✓ Namespace labeled"
}

# ---------------------------------------------------------------------------
# Wait helper
# ---------------------------------------------------------------------------
wait_for_rollout() {
    local resource="$1"
    local timeout="${2:-300}"

    log_info "Waiting for $resource to be ready (timeout: ${timeout}s)..."
    if oc rollout status "$resource" -n "$NAMESPACE" --timeout="${timeout}s" 2>/dev/null; then
        log_success "✓ $resource is ready"
    else
        log_error "Timeout waiting for $resource"
        exit 1
    fi
}

wait_for_job() {
    local job_name="$1"
    local timeout="${2:-120}"
    local elapsed=0

    log_info "Waiting for job/$job_name to complete (timeout: ${timeout}s)..."
    while [ $elapsed -lt "$timeout" ]; do
        local status
        status=$(oc get job "$job_name" -n "$NAMESPACE" -o jsonpath='{.status.succeeded}' 2>/dev/null || echo "")
        if [[ "$status" == "1" ]]; then
            log_success "✓ job/$job_name completed"
            return 0
        fi

        local failed
        failed=$(oc get job "$job_name" -n "$NAMESPACE" -o jsonpath='{.status.failed}' 2>/dev/null || echo "")
        if [[ -n "$failed" ]] && [[ "$failed" -ge 3 ]]; then
            log_error "job/$job_name failed ($failed attempts)"
            oc logs "job/$job_name" -n "$NAMESPACE" --tail=20 2>/dev/null || true
            exit 1
        fi

        sleep 5
        elapsed=$((elapsed + 5))
    done

    log_error "Timeout waiting for job/$job_name"
    exit 1
}

# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------
deploy_postgresql() {
    log_header "DEPLOYING POSTGRESQL FOR KESSEL"

    if ! oc get secret kessel-db-secret -n "$NAMESPACE" >/dev/null 2>&1; then
        log_info "Creating database credentials secret..."
        cat <<EOF | oc apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: kessel-db-secret
  namespace: $NAMESPACE
type: Opaque
stringData:
  username: kessel
  password: $(openssl rand -hex 12)
  database: spicedb
EOF
        log_success "✓ Database credentials secret created"
    else
        log_warning "Database credentials secret already exists"
    fi

    if ! oc get service kessel-db -n "$NAMESPACE" >/dev/null 2>&1; then
        log_info "Creating PostgreSQL Service..."
        cat <<EOF | oc apply -f -
apiVersion: v1
kind: Service
metadata:
  name: kessel-db
  namespace: $NAMESPACE
  labels:
    app: kessel-db
spec:
  ports:
    - name: postgres
      port: 5432
      targetPort: 5432
  selector:
    app: kessel-db
  clusterIP: None
EOF
        log_success "✓ PostgreSQL Service created"
    fi

    if oc get statefulset kessel-db -n "$NAMESPACE" >/dev/null 2>&1; then
        log_warning "PostgreSQL StatefulSet already exists"
    else
        log_info "Creating PostgreSQL StatefulSet..."
        local DB_USER DB_PASS DB_NAME
        DB_USER=$(oc get secret kessel-db-secret -n "$NAMESPACE" -o jsonpath='{.data.username}' | base64 -d)
        DB_PASS=$(oc get secret kessel-db-secret -n "$NAMESPACE" -o jsonpath='{.data.password}' | base64 -d)
        DB_NAME=$(oc get secret kessel-db-secret -n "$NAMESPACE" -o jsonpath='{.data.database}' | base64 -d)

        cat <<EOF | oc apply -f -
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: kessel-db
  namespace: $NAMESPACE
  labels:
    app: kessel-db
spec:
  serviceName: kessel-db
  replicas: 1
  selector:
    matchLabels:
      app: kessel-db
  template:
    metadata:
      labels:
        app: kessel-db
    spec:
      containers:
        - name: postgres
          image: docker.io/library/postgres:16
          ports:
            - containerPort: 5432
          env:
            - name: POSTGRES_USER
              value: "$DB_USER"
            - name: POSTGRES_PASSWORD
              value: "$DB_PASS"
            - name: POSTGRES_DB
              value: "$DB_NAME"
            - name: PGDATA
              value: /var/lib/postgresql/data/pgdata
          volumeMounts:
            - name: postgres-storage
              mountPath: /var/lib/postgresql/data
          readinessProbe:
            exec:
              command: ["pg_isready", "-U", "$DB_USER"]
            initialDelaySeconds: 5
            periodSeconds: 5
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
  volumeClaimTemplates:
    - metadata:
        name: postgres-storage
      spec:
        accessModes: ["ReadWriteOnce"]
        storageClassName: $STORAGE_CLASS
        resources:
          requests:
            storage: 1Gi
EOF
        log_success "✓ PostgreSQL StatefulSet created"
    fi

    wait_for_rollout "statefulset/kessel-db"
}

# ---------------------------------------------------------------------------
# SpiceDB
# ---------------------------------------------------------------------------
deploy_spicedb() {
    log_header "DEPLOYING SPICEDB"

    if ! oc get secret spicedb-config -n "$NAMESPACE" >/dev/null 2>&1; then
        log_info "Creating SpiceDB config secret..."
        local DB_USER DB_PASS DB_NAME
        DB_USER=$(oc get secret kessel-db-secret -n "$NAMESPACE" -o jsonpath='{.data.username}' | base64 -d)
        DB_PASS=$(oc get secret kessel-db-secret -n "$NAMESPACE" -o jsonpath='{.data.password}' | base64 -d)
        DB_NAME=$(oc get secret kessel-db-secret -n "$NAMESPACE" -o jsonpath='{.data.database}' | base64 -d)

        cat <<EOF | oc apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: spicedb-config
  namespace: $NAMESPACE
type: Opaque
stringData:
  preshared-key: "$SPICEDB_PRESHARED_KEY"
  datastore-uri: "postgres://${DB_USER}:${DB_PASS}@kessel-db:5432/${DB_NAME}?sslmode=disable"
EOF
        log_success "✓ SpiceDB config secret created"
    else
        log_warning "SpiceDB config secret already exists"
    fi

    # Run migration job
    oc delete job spicedb-migrate -n "$NAMESPACE" 2>/dev/null || true
    log_info "Running SpiceDB migration..."
    cat <<EOF | oc apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: spicedb-migrate
  namespace: $NAMESPACE
spec:
  backoffLimit: 3
  template:
    spec:
      restartPolicy: OnFailure
      containers:
        - name: migrate
          image: docker.io/authzed/spicedb:latest
          command: ["spicedb", "migrate", "head"]
          env:
            - name: SPICEDB_DATASTORE_ENGINE
              value: postgres
            - name: SPICEDB_DATASTORE_CONN_URI
              valueFrom:
                secretKeyRef:
                  name: spicedb-config
                  key: datastore-uri
EOF
    wait_for_job "spicedb-migrate"

    if ! oc get service spicedb -n "$NAMESPACE" >/dev/null 2>&1; then
        log_info "Creating SpiceDB Service..."
        cat <<EOF | oc apply -f -
apiVersion: v1
kind: Service
metadata:
  name: spicedb
  namespace: $NAMESPACE
  labels:
    app: spicedb
spec:
  ports:
    - name: grpc
      port: 50051
      targetPort: 50051
  selector:
    app: spicedb
EOF
        log_success "✓ SpiceDB Service created"
    fi

    if oc get deployment spicedb -n "$NAMESPACE" >/dev/null 2>&1; then
        log_warning "SpiceDB Deployment already exists"
    else
        log_info "Creating SpiceDB Deployment..."
        cat <<EOF | oc apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: spicedb
  namespace: $NAMESPACE
  labels:
    app: spicedb
spec:
  replicas: 1
  selector:
    matchLabels:
      app: spicedb
  template:
    metadata:
      labels:
        app: spicedb
    spec:
      containers:
        - name: spicedb
          image: docker.io/authzed/spicedb:latest
          command: ["spicedb", "serve"]
          ports:
            - name: grpc
              containerPort: 50051
            - name: http
              containerPort: 8443
          env:
            - name: SPICEDB_GRPC_PRESHARED_KEY
              valueFrom:
                secretKeyRef:
                  name: spicedb-config
                  key: preshared-key
            - name: SPICEDB_DATASTORE_ENGINE
              value: postgres
            - name: SPICEDB_DATASTORE_CONN_URI
              valueFrom:
                secretKeyRef:
                  name: spicedb-config
                  key: datastore-uri
            - name: SPICEDB_HTTP_ENABLED
              value: "true"
            - name: SPICEDB_DATASTORE_REVISION_QUANTIZATION_INTERVAL
              value: "0s"
          readinessProbe:
            exec:
              command: ["grpc_health_probe", "-addr=:50051"]
            initialDelaySeconds: 5
            periodSeconds: 10
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
EOF
        log_success "✓ SpiceDB Deployment created"
    fi

    wait_for_rollout "deployment/spicedb"
}

# ---------------------------------------------------------------------------
# Kessel Relations API
# ---------------------------------------------------------------------------
deploy_relations_api() {
    log_header "DEPLOYING KESSEL RELATIONS API"

    if ! oc get service kessel-relations -n "$NAMESPACE" >/dev/null 2>&1; then
        log_info "Creating Relations API Service..."
        cat <<EOF | oc apply -f -
apiVersion: v1
kind: Service
metadata:
  name: kessel-relations
  namespace: $NAMESPACE
  labels:
    app: kessel-relations
spec:
  ports:
    - name: grpc
      port: 9000
      targetPort: 9000
    - name: http
      port: 8000
      targetPort: 8000
  selector:
    app: kessel-relations
EOF
        log_success "✓ Relations API Service created"
    fi

    if oc get deployment kessel-relations -n "$NAMESPACE" >/dev/null 2>&1; then
        log_warning "Relations API Deployment already exists -- updating..."
        oc delete deployment kessel-relations -n "$NAMESPACE"
    fi

    log_info "Creating Relations API Deployment..."
    cat <<EOF | oc apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kessel-relations
  namespace: $NAMESPACE
  labels:
    app: kessel-relations
spec:
  replicas: 1
  selector:
    matchLabels:
      app: kessel-relations
  template:
    metadata:
      labels:
        app: kessel-relations
    spec:
      containers:
        - name: relations-api
          image: quay.io/cloudservices/kessel-relations:latest
          ports:
            - name: grpc
              containerPort: 9000
            - name: http
              containerPort: 8000
          env:
            - name: SPICEDB_PRESHARED
              valueFrom:
                secretKeyRef:
                  name: spicedb-config
                  key: preshared-key
            - name: SPICEDB_ENDPOINT
              value: "spicedb:50051"
            - name: SPICEDB_SCHEMA_FILE
              value: "/etc/schema/schema.zed"
            - name: ENABLEAUTH
              value: "true"
            - name: JWKSURL
              value: "http://keycloak-service.${KEYCLOAK_NAMESPACE}.svc.cluster.local:8080/realms/${KEYCLOAK_REALM}/protocol/openid-connect/certs"
          volumeMounts:
            - name: schema
              mountPath: /etc/schema
              readOnly: true
          readinessProbe:
            tcpSocket:
              port: 9000
            initialDelaySeconds: 5
            periodSeconds: 10
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: 250m
              memory: 256Mi
      volumes:
        - name: schema
          configMap:
            name: kessel-schema
            optional: true
EOF
    log_success "✓ Relations API Deployment created"

    wait_for_rollout "deployment/kessel-relations"
}

# ---------------------------------------------------------------------------
# Kessel Inventory API
# ---------------------------------------------------------------------------
deploy_inventory_api() {
    log_header "DEPLOYING KESSEL INVENTORY API"

    local DB_USER DB_PASS DB_NAME
    DB_USER=$(oc get secret kessel-db-secret -n "$NAMESPACE" -o jsonpath='{.data.username}' | base64 -d)
    DB_PASS=$(oc get secret kessel-db-secret -n "$NAMESPACE" -o jsonpath='{.data.password}' | base64 -d)
    DB_NAME=$(oc get secret kessel-db-secret -n "$NAMESPACE" -o jsonpath='{.data.database}' | base64 -d)

    # Create resource-schemas ConfigMap from the local directory.
    # The Inventory API needs these to validate ReportResource payloads.
    local RESOURCE_SCHEMAS_DIR="$SCRIPT_DIR/kessel/resource-schemas"
    if [ -d "$RESOURCE_SCHEMAS_DIR" ]; then
        log_info "Creating resource-schemas ConfigMap from $RESOURCE_SCHEMAS_DIR..."
        local cm_args=()
        while IFS= read -r -d '' f; do
            local rel="${f#"$RESOURCE_SCHEMAS_DIR/"}"
            local key="${rel//\//__}"
            cm_args+=(--from-file="$key=$f")
        done < <(find "$RESOURCE_SCHEMAS_DIR" -type f -print0)
        oc create configmap kessel-resource-schemas "${cm_args[@]}" \
            -n "$NAMESPACE" --dry-run=client -o yaml | oc apply -f -
        log_success "✓ resource-schemas ConfigMap created/updated"
    else
        log_warning "resource-schemas directory not found at $RESOURCE_SCHEMAS_DIR"
    fi

    local KC_ROUTE
    KC_ROUTE=$(oc get route keycloak -n "$KEYCLOAK_NAMESPACE" -o jsonpath='{.spec.host}' 2>/dev/null || true)
    local KEYCLOAK_ISSUER
    if [ -n "$KC_ROUTE" ]; then
        KEYCLOAK_ISSUER="https://${KC_ROUTE}/realms/${KEYCLOAK_REALM}"
    else
        KEYCLOAK_ISSUER="http://keycloak-service.${KEYCLOAK_NAMESPACE}.svc.cluster.local:8080/realms/${KEYCLOAK_REALM}"
    fi
    log_info "Inventory OIDC issuer: $KEYCLOAK_ISSUER"

    local AUTHN_BLOCK
    AUTHN_BLOCK=$(cat <<'AUTHNEOF'
    authn:
      authenticator:
        type: first_match
        chain:
          - type: oidc
            config:
              authn-server-url: KEYCLOAK_ISSUER_PLACEHOLDER
              skip-client-id-check: true
              insecure-client: true
AUTHNEOF
)
    AUTHN_BLOCK="${AUTHN_BLOCK//KEYCLOAK_ISSUER_PLACEHOLDER/$KEYCLOAK_ISSUER}"

    local AUTHZ_SA_LINES=""
    local inv_client_id inv_client_secret
    inv_client_id=$(oc get secret kessel-inventory-client -n "$NAMESPACE" \
        -o jsonpath='{.data.client-id}' 2>/dev/null | base64 -d 2>/dev/null || true)
    inv_client_secret=$(oc get secret kessel-inventory-client -n "$NAMESPACE" \
        -o jsonpath='{.data.client-secret}' 2>/dev/null | base64 -d 2>/dev/null || true)

    if [ -z "$inv_client_id" ] || [ -z "$inv_client_secret" ]; then
        log_info "Mirroring inventory client secret from keycloak namespace..."
        inv_client_id=$(oc get secret keycloak-client-secret-cost-management-inventory \
            -n "$KEYCLOAK_NAMESPACE" -o jsonpath='{.data.CLIENT_ID}' 2>/dev/null | base64 -d 2>/dev/null || true)
        inv_client_secret=$(oc get secret keycloak-client-secret-cost-management-inventory \
            -n "$KEYCLOAK_NAMESPACE" -o jsonpath='{.data.CLIENT_SECRET}' 2>/dev/null | base64 -d 2>/dev/null || true)
        if [ -n "$inv_client_id" ] && [ -n "$inv_client_secret" ]; then
            oc create secret generic kessel-inventory-client \
                --from-literal=client-id="$inv_client_id" \
                --from-literal=client-secret="$inv_client_secret" \
                -n "$NAMESPACE" 2>/dev/null || true
            log_success "✓ Created kessel-inventory-client secret in $NAMESPACE"
        fi
    fi

    if [ -n "$inv_client_id" ] && [ -n "$inv_client_secret" ]; then
        local KEYCLOAK_TOKEN_URL="http://keycloak-service.${KEYCLOAK_NAMESPACE}.svc.cluster.local:8080/realms/${KEYCLOAK_REALM}/protocol/openid-connect/token"
        AUTHZ_SA_LINES="        sa-client-id: ${inv_client_id}
        sa-client-secret: ${inv_client_secret}
        sso-token-endpoint: ${KEYCLOAK_TOKEN_URL}"
    else
        log_error "Inventory client secret not found in $NAMESPACE or $KEYCLOAK_NAMESPACE"
        log_error "  Run deploy-rhbk.sh first to create Keycloak clients"
        exit 1
    fi

    log_info "Creating Inventory config ConfigMap (inline)..."
    cat <<CFGEOF | oc apply -n "$NAMESPACE" -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: kessel-inventory-config
data:
  inventory-config.yaml: |
    server:
      http:
        address: 0.0.0.0:8000
      grpc:
        address: 0.0.0.0:9000
        certfile: /etc/tls/grpc/tls.crt
        keyfile: /etc/tls/grpc/tls.key
${AUTHN_BLOCK}
    authz:
      impl: kessel
      kessel:
        url: kessel-relations:9000
        insecure-client: true
        enable-oidc-auth: true
${AUTHZ_SA_LINES}
    eventing:
      eventer: stdout
    consumer:
      enabled: false
    storage:
      database: postgres
      postgres:
        host: "kessel-db"
        port: "5432"
        user: "${DB_USER}"
        password: "${DB_PASS}"
        dbname: "${DB_NAME}"
    schema:
      schemas: in-memory
      in-memory:
        Type: dir
        Path: /resource-schemas
    log:
      level: "info"
      livez: true
      readyz: true
CFGEOF
    log_success "✓ Inventory config ConfigMap created/updated"

    if ! oc get service kessel-inventory -n "$NAMESPACE" >/dev/null 2>&1; then
        log_info "Creating Inventory API Service..."
        cat <<EOF | oc apply -f -
apiVersion: v1
kind: Service
metadata:
  name: kessel-inventory
  namespace: $NAMESPACE
  labels:
    app: kessel-inventory
  annotations:
    service.beta.openshift.io/serving-cert-secret-name: kessel-inventory-tls
spec:
  ports:
    - name: http
      port: 8000
      targetPort: 8000
    - name: grpc
      port: 9000
      targetPort: 9000
  selector:
    app: kessel-inventory
EOF
        log_success "✓ Inventory API Service created"
    fi

    # Run Inventory API database migration (creates reporter_resources table, etc.)
    oc delete job kessel-inventory-migrate -n "$NAMESPACE" --ignore-not-found 2>/dev/null
    log_info "Running Inventory API database migration..."
    cat <<EOF | oc apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: kessel-inventory-migrate
  namespace: $NAMESPACE
spec:
  backoffLimit: 3
  template:
    spec:
      restartPolicy: OnFailure
      containers:
        - name: migrate
          image: quay.io/cloudservices/kessel-inventory:latest
          command: ["inventory-api", "migrate"]
          env:
            - name: INVENTORY_API_CONFIG
              value: /inventory-config.yaml
          volumeMounts:
            - name: config
              mountPath: /inventory-config.yaml
              subPath: inventory-config.yaml
              readOnly: true
      volumes:
        - name: config
          configMap:
            name: kessel-inventory-config
EOF
    wait_for_job "kessel-inventory-migrate"

    if oc get deployment kessel-inventory -n "$NAMESPACE" >/dev/null 2>&1; then
        log_warning "Inventory API Deployment already exists -- updating..."
        oc delete deployment kessel-inventory -n "$NAMESPACE"
    fi

    log_info "Creating Inventory API Deployment..."
    cat <<EOF | oc apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kessel-inventory
  namespace: $NAMESPACE
  labels:
    app: kessel-inventory
spec:
  replicas: 1
  selector:
    matchLabels:
      app: kessel-inventory
  template:
    metadata:
      labels:
        app: kessel-inventory
    spec:
      containers:
        - name: inventory-api
          image: quay.io/cloudservices/kessel-inventory:latest
          command: ["inventory-api", "serve"]
          ports:
            - name: http
              containerPort: 8000
            - name: grpc
              containerPort: 9000
          env:
            - name: INVENTORY_API_CONFIG
              value: /inventory-config.yaml
          volumeMounts:
            - name: config
              mountPath: /inventory-config.yaml
              subPath: inventory-config.yaml
              readOnly: true
            - name: resource-schemas
              mountPath: /resource-schemas
              readOnly: true
            - name: grpc-tls
              mountPath: /etc/tls/grpc
              readOnly: true
          readinessProbe:
            tcpSocket:
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: 250m
              memory: 256Mi
      initContainers:
        - name: unpack-schemas
          image: docker.io/library/busybox:latest
          command: ["sh", "-c"]
          args:
            - |
              cd /flat
              for f in *; do
                dir="/out/\$(echo "\$f" | sed 's/__/\\//g' | xargs dirname)"
                mkdir -p "\$dir"
                cp "/flat/\$f" "/out/\$(echo "\$f" | sed 's/__/\\//g')"
              done
          volumeMounts:
            - name: resource-schemas-flat
              mountPath: /flat
              readOnly: true
            - name: resource-schemas
              mountPath: /out
      volumes:
        - name: config
          configMap:
            name: kessel-inventory-config
        - name: resource-schemas-flat
          configMap:
            name: kessel-resource-schemas
        - name: resource-schemas
          emptyDir: {}
        - name: grpc-tls
          secret:
            secretName: kessel-inventory-tls
EOF
    log_success "✓ Inventory API Deployment created"

    wait_for_rollout "deployment/kessel-inventory"
}

# ---------------------------------------------------------------------------
# Schema ConfigMap + cross-namespace RBAC for the Koku migration job
# ---------------------------------------------------------------------------
provision_schema() {
    log_header "PROVISIONING SCHEMA TO SPICEDB AND CONFIGMAP"

    local SCHEMA_FILE="$SCRIPT_DIR/kessel/schema.zed"
    if [ ! -f "$SCHEMA_FILE" ]; then
        log_error "Schema file not found: $SCHEMA_FILE"
        exit 1
    fi
    log_info "Using schema from $SCHEMA_FILE"

    # Create/update kessel-schema ConfigMap with actual schema content.
    # The Relations API mounts this at /etc/schema/schema.zed.
    log_info "Creating/updating kessel-schema ConfigMap..."
    oc create configmap kessel-schema \
        --from-file=schema.zed="$SCHEMA_FILE" \
        -n "$NAMESPACE" \
        --dry-run=client -o yaml | oc apply -f -
    log_success "✓ kessel-schema ConfigMap created/updated"

    # Write schema to SpiceDB using a containerized zed Job.
    oc delete job spicedb-schema-init -n "$NAMESPACE" --ignore-not-found 2>/dev/null
    log_info "Writing schema to SpiceDB via zed Job..."

    local JOB_YAML
    JOB_YAML=$(cat <<'JOBEOF'
apiVersion: batch/v1
kind: Job
metadata:
  name: spicedb-schema-init
spec:
  backoffLimit: 3
  template:
    spec:
      restartPolicy: OnFailure
      containers:
        - name: zed
          image: ghcr.io/authzed/zed:v0.35.0
          command: ["zed"]
          args:
            - schema
            - write
            - --endpoint=spicedb:50051
            - "--token=$(SPICEDB_TOKEN)"
            - --insecure
            - /etc/schema/schema.zed
          env:
            - name: SPICEDB_TOKEN
              valueFrom:
                secretKeyRef:
                  name: spicedb-config
                  key: preshared-key
          volumeMounts:
            - name: schema
              mountPath: /etc/schema
              readOnly: true
      volumes:
        - name: schema
          configMap:
            name: kessel-schema
JOBEOF
    )

    echo "$JOB_YAML" | oc apply -n "$NAMESPACE" -f -
    wait_for_job "spicedb-schema-init"
}

# ---------------------------------------------------------------------------
# Create secret for Cost Management namespace
# ---------------------------------------------------------------------------
create_cost_mgmt_secret() {
    log_header "CREATING KESSEL SECRET FOR COST MANAGEMENT"

    # The kessel-config secret is normally created by install-helm-chart.sh
    # during the pre-Helm secret creation phase. This function is a convenience
    # fallback for manual deployments or when re-running deploy-kessel.sh after
    # the cost-onprem namespace already exists.

    if ! oc get namespace "$COST_MGMT_NAMESPACE" >/dev/null 2>&1; then
        log_warning "Namespace '$COST_MGMT_NAMESPACE' does not exist yet."
        log_info "The kessel-config secret will be created by install-helm-chart.sh"
        log_info "during the Helm installation phase."
        return 0
    fi

    local SPICEDB_KEY
    SPICEDB_KEY=$(oc get secret spicedb-config -n "$NAMESPACE" -o jsonpath='{.data.preshared-key}' | base64 -d)

    if [ -z "$SPICEDB_KEY" ]; then
        log_error "Failed to retrieve SpiceDB preshared key from secret spicedb-config in $NAMESPACE"
        return 1
    fi

    if ! oc get secret kessel-config -n "$COST_MGMT_NAMESPACE" >/dev/null 2>&1; then
        log_info "Creating kessel-config secret in $COST_MGMT_NAMESPACE..."
        oc create secret generic kessel-config \
            --from-literal=spicedb-preshared-key="$SPICEDB_KEY" \
            --namespace="$COST_MGMT_NAMESPACE"
        log_success "✓ kessel-config secret created in $COST_MGMT_NAMESPACE"
    else
        log_warning "kessel-config secret already exists in $COST_MGMT_NAMESPACE"
    fi
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print_summary() {
    log_header "KESSEL DEPLOYMENT SUMMARY"

    echo ""
    echo "  Namespace:         $NAMESPACE"
    echo "  SpiceDB:           spicedb.${NAMESPACE}.svc.cluster.local:50051"
    echo "  Relations API:     kessel-relations.${NAMESPACE}.svc.cluster.local:9000 (gRPC), :8000 (HTTP/REST)"
    echo "  Inventory API:     kessel-inventory.${NAMESPACE}.svc.cluster.local:8000 (HTTP)"
    echo "                     kessel-inventory.${NAMESPACE}.svc.cluster.local:9000 (gRPC)"
    echo ""
    echo "  Schema is provisioned by this script (ConfigMap + SpiceDB)."
    echo "  Role seeding is handled by Koku's migration job (kessel_seed_roles)."
    echo ""
    echo "  install-helm-chart.sh will auto-detect Kessel and set Helm values."
    echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    echo ""
    echo "==========================================="
    echo "  Kessel Deployment for Cost Management"
    echo "==========================================="
    echo ""

    check_prerequisites
    create_namespace
    deploy_postgresql
    deploy_spicedb
    provision_schema
    deploy_relations_api
    deploy_inventory_api
    create_cost_mgmt_secret
    print_summary

    log_success "Kessel deployment complete!"
}

main "$@"
