#!/bin/bash
set -euo pipefail

################################################################################
# Deploy BYOI (Bring Your Own Infrastructure) for testing external DB/Valkey
#
# This script deploys standalone PostgreSQL and Valkey instances to simulate
# an external infrastructure scenario for E2E testing of the BYOI configuration.
#
# Usage:
#   ./deploy-byoi-infra.sh [cleanup]
#
# Environment Variables:
#   BYOI_NAMESPACE    - Namespace for external services (default: byoi-infra)
#   COST_NAMESPACE    - Cost-onprem namespace for secrets (default: cost-onprem)
#
################################################################################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BYOI_NAMESPACE="${BYOI_NAMESPACE:-byoi-infra}"
COST_NAMESPACE="${COST_NAMESPACE:-cost-onprem}"

# Database credentials
POSTGRES_ADMIN_USER="postgres"
POSTGRES_ADMIN_PASSWORD="postgres123"
ROS_USER="ros_user"
ROS_PASSWORD="ros_password"
KRUIZE_USER="kruize_user"
KRUIZE_PASSWORD="kruize_password"
KOKU_USER="koku_user"
KOKU_PASSWORD="koku_password"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $*"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

cleanup() {
    log_info "Cleaning up BYOI infrastructure..."
    kubectl delete ns "$BYOI_NAMESPACE" --wait=false 2>/dev/null || true
    kubectl delete secret cost-onprem-db-credentials -n "$COST_NAMESPACE" 2>/dev/null || true
    log_success "Cleanup complete"
}

if [[ "${1:-}" == "cleanup" ]]; then
    cleanup
    exit 0
fi

log_info "Deploying BYOI infrastructure to namespace: $BYOI_NAMESPACE"

# Create namespace
kubectl create namespace "$BYOI_NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

# Create PostgreSQL init script
log_info "Creating PostgreSQL initialization ConfigMap..."
kubectl apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: postgresql-init
  namespace: $BYOI_NAMESPACE
data:
  init-databases.sh: |
    #!/bin/bash
    set -e
    
    echo "Creating users..."
    psql -v ON_ERROR_STOP=1 --username "\$POSTGRES_USER" --dbname postgres <<-EOSQL
        CREATE USER $ROS_USER WITH PASSWORD '$ROS_PASSWORD';
        CREATE USER $KRUIZE_USER WITH PASSWORD '$KRUIZE_PASSWORD';
        CREATE USER $KOKU_USER WITH PASSWORD '$KOKU_PASSWORD';
    EOSQL
    
    echo "Creating databases..."
    psql -v ON_ERROR_STOP=1 --username "\$POSTGRES_USER" --dbname postgres <<-EOSQL
        CREATE DATABASE costonprem_ros OWNER $ROS_USER;
        CREATE DATABASE costonprem_kruize OWNER $KRUIZE_USER;
        CREATE DATABASE costonprem_koku OWNER $KOKU_USER;
    EOSQL
    
    echo "Granting privileges..."
    psql -v ON_ERROR_STOP=1 --username "\$POSTGRES_USER" --dbname postgres <<-EOSQL
        GRANT ALL PRIVILEGES ON DATABASE costonprem_ros TO $ROS_USER;
        GRANT ALL PRIVILEGES ON DATABASE costonprem_kruize TO $KRUIZE_USER;
        GRANT ALL PRIVILEGES ON DATABASE costonprem_koku TO $KOKU_USER;
        ALTER USER $KOKU_USER CREATEDB CREATEROLE;
    EOSQL
    
    echo "Creating pg_stat_statements extension in koku database..."
    psql -v ON_ERROR_STOP=1 --username "\$POSTGRES_USER" --dbname costonprem_koku <<-EOSQL
        CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
    EOSQL
    
    echo "Database initialization complete!"
EOF

# Create PostgreSQL credentials secret in byoi-infra namespace
log_info "Creating PostgreSQL credentials secret..."
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: postgresql-credentials
  namespace: $BYOI_NAMESPACE
type: Opaque
stringData:
  postgres-user: "$POSTGRES_ADMIN_USER"
  postgres-password: "$POSTGRES_ADMIN_PASSWORD"
EOF

# Deploy PostgreSQL StatefulSet
log_info "Deploying PostgreSQL StatefulSet..."
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgresql
  namespace: $BYOI_NAMESPACE
spec:
  serviceName: postgresql
  replicas: 1
  selector:
    matchLabels:
      app: postgresql
  template:
    metadata:
      labels:
        app: postgresql
    spec:
      containers:
      - name: postgresql
        image: quay.io/insights-onprem/postgresql:16
        ports:
        - containerPort: 5432
        env:
        - name: POSTGRES_USER
          valueFrom:
            secretKeyRef:
              name: postgresql-credentials
              key: postgres-user
        - name: POSTGRES_PASSWORD
          valueFrom:
            secretKeyRef:
              name: postgresql-credentials
              key: postgres-password
        - name: PGDATA
          value: /var/lib/postgresql/data/pgdata
        volumeMounts:
        - name: data
          mountPath: /var/lib/postgresql/data
        - name: init-scripts
          mountPath: /docker-entrypoint-initdb.d
        resources:
          requests:
            memory: "256Mi"
            cpu: "100m"
          limits:
            memory: "1Gi"
            cpu: "500m"
      volumes:
      - name: init-scripts
        configMap:
          name: postgresql-init
          defaultMode: 0755
  volumeClaimTemplates:
  - metadata:
      name: data
    spec:
      accessModes: ["ReadWriteOnce"]
      resources:
        requests:
          storage: 10Gi
---
apiVersion: v1
kind: Service
metadata:
  name: postgresql
  namespace: $BYOI_NAMESPACE
spec:
  selector:
    app: postgresql
  ports:
  - port: 5432
    targetPort: 5432
  clusterIP: None
EOF

# Deploy Valkey
log_info "Deploying Valkey..."
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: valkey
  namespace: $BYOI_NAMESPACE
spec:
  replicas: 1
  selector:
    matchLabels:
      app: valkey
  template:
    metadata:
      labels:
        app: valkey
    spec:
      containers:
      - name: valkey
        image: docker.io/valkey/valkey:8.0
        args: ["--save", ""]
        ports:
        - containerPort: 6379
        volumeMounts:
        - name: data
          mountPath: /data
        resources:
          requests:
            memory: "64Mi"
            cpu: "50m"
          limits:
            memory: "256Mi"
            cpu: "200m"
      volumes:
      - name: data
        emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: valkey
  namespace: $BYOI_NAMESPACE
spec:
  selector:
    app: valkey
  ports:
  - port: 6379
    targetPort: 6379
EOF

# Wait for PostgreSQL to be ready
log_info "Waiting for PostgreSQL to be ready..."
kubectl rollout status statefulset/postgresql -n "$BYOI_NAMESPACE" --timeout=300s

# Wait for init scripts to complete (extra time for DB creation)
log_info "Waiting for database initialization..."
sleep 10

# Verify databases were created
log_info "Verifying database setup..."
kubectl exec -n "$BYOI_NAMESPACE" postgresql-0 -- psql -U postgres -c "\l" | grep -E "costonprem_ros|costonprem_kruize|costonprem_koku" || {
    log_error "Database initialization may have failed. Check pod logs:"
    log_error "  kubectl logs -n $BYOI_NAMESPACE postgresql-0"
    exit 1
}

# Wait for Valkey to be ready
log_info "Waiting for Valkey to be ready..."
kubectl rollout status deployment/valkey -n "$BYOI_NAMESPACE" --timeout=120s

# Create cost-onprem namespace if it doesn't exist
kubectl create namespace "$COST_NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

# Create the database credentials secret in the cost-onprem namespace
log_info "Creating database credentials secret in $COST_NAMESPACE namespace..."
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: cost-onprem-db-credentials
  namespace: $COST_NAMESPACE
type: Opaque
stringData:
  postgres-user: "$POSTGRES_ADMIN_USER"
  postgres-password: "$POSTGRES_ADMIN_PASSWORD"
  ros-user: "$ROS_USER"
  ros-password: "$ROS_PASSWORD"
  kruize-user: "$KRUIZE_USER"
  kruize-password: "$KRUIZE_PASSWORD"
  koku-user: "$KOKU_USER"
  koku-password: "$KOKU_PASSWORD"
EOF

log_success "BYOI infrastructure deployed successfully!"
echo ""
log_info "External services:"
echo "  PostgreSQL: postgresql.$BYOI_NAMESPACE.svc.cluster.local:5432"
echo "  Valkey:     valkey.$BYOI_NAMESPACE.svc.cluster.local:6379"
echo ""
log_info "Databases created:"
echo "  - costonprem_ros (owner: $ROS_USER)"
echo "  - costonprem_kruize (owner: $KRUIZE_USER)"  
echo "  - costonprem_koku (owner: $KOKU_USER)"
echo ""
log_info "Credentials secret created in $COST_NAMESPACE namespace"
echo ""
log_info "To deploy cost-onprem with BYOI:"
echo "  OPENSHIFT_VALUES_FILE=docs/examples/byoi-values.yaml ./scripts/deploy-test-cost-onprem.sh"
