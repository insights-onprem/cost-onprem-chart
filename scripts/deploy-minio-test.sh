#!/bin/bash

# Deploy MinIO for Testing in OpenShift Cluster
# This script deploys a standalone MinIO instance for testing the cost-onprem chart
# with MinIO instead of cluster object storage. This simulates the CI environment in an OCP cluster.

set -e  # Exit on any error

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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

# Configuration
NAMESPACE=${1:-"minio-test"}
ACTION=${2:-"deploy"}
MINIO_VERSION=${MINIO_VERSION:-"RELEASE.2025-07-23T15-54-02Z"}
STORAGE_CLASS=${STORAGE_CLASS:-""}  # Empty = use default
STORAGE_SIZE=${STORAGE_SIZE:-"10Gi"}

# Handle cleanup subcommand
if [ "$ACTION" = "cleanup" ]; then
    echo ""
    echo "════════════════════════════════════════════════════════════"
    echo "  Cleanup MinIO Resources"
    echo "════════════════════════════════════════════════════════════"
    echo ""
    echo "Namespace: $NAMESPACE"
    echo ""
    echo_info "Removing MinIO resources from namespace: $NAMESPACE"
    kubectl delete deployment minio -n "$NAMESPACE" --ignore-not-found
    kubectl delete svc minio -n "$NAMESPACE" --ignore-not-found
    kubectl delete svc minio-console -n "$NAMESPACE" --ignore-not-found
    kubectl delete pvc minio-pvc -n "$NAMESPACE" --ignore-not-found
    kubectl delete secret minio-credentials -n "$NAMESPACE" --ignore-not-found
    kubectl delete route minio-console -n "$NAMESPACE" --ignore-not-found 2>/dev/null || true
    echo_success "MinIO cleanup complete in namespace: $NAMESPACE"
    exit 0
fi

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  Deploy MinIO for Testing"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "Namespace:      $NAMESPACE"
echo "MinIO Version:  $MINIO_VERSION"
echo "Storage Size:   $STORAGE_SIZE"
echo "Storage Class:  ${STORAGE_CLASS:-default}"
echo ""

# Check prerequisites
if ! command -v kubectl &> /dev/null; then
    echo_error "kubectl not found. Please install kubectl."
    exit 1
fi

if ! command -v oc &> /dev/null; then
    echo_warning "oc not found. Using kubectl (some features may not work on OpenShift)"
fi

# Check cluster connectivity
if ! kubectl get nodes >/dev/null 2>&1; then
    echo_error "Cannot connect to cluster. Please check your kubectl configuration."
    exit 1
fi

# Detect platform
if kubectl get routes.route.openshift.io >/dev/null 2>&1; then
    PLATFORM="openshift"
    echo_success "Detected OpenShift platform"
else
    PLATFORM="kubernetes"
    echo_success "Detected Kubernetes platform"
fi

# Create namespace
echo_info "Creating namespace: $NAMESPACE"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
echo_success "Namespace ready"

# Generate MinIO credentials
echo_info "Generating MinIO credentials..."
MINIO_ACCESS_KEY="minioadmin"
MINIO_SECRET_KEY=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-32)

# Create MinIO credentials secret
echo_info "Creating MinIO credentials secret..."
kubectl create secret generic minio-credentials \
    --namespace="$NAMESPACE" \
    --from-literal=access-key="$MINIO_ACCESS_KEY" \
    --from-literal=secret-key="$MINIO_SECRET_KEY" \
    --dry-run=client -o yaml | kubectl apply -f -
echo_success "Credentials secret created"

# Build storage class parameter
STORAGE_CLASS_PARAM=""
if [ -n "$STORAGE_CLASS" ]; then
    STORAGE_CLASS_PARAM="storageClassName: $STORAGE_CLASS"
else
    echo_info "Using default storage class"
fi

# Deploy MinIO
echo_info "Deploying MinIO..."

cat <<EOF | kubectl apply -f -
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: minio-pvc
  namespace: $NAMESPACE
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: $STORAGE_SIZE
  ${STORAGE_CLASS_PARAM}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: minio
  namespace: $NAMESPACE
  labels:
    app: minio
spec:
  replicas: 1
  selector:
    matchLabels:
      app: minio
  template:
    metadata:
      labels:
        app: minio
    spec:
      containers:
        - name: minio
          image: quay.io/minio/minio:$MINIO_VERSION
          args:
            - server
            - /data
            - --console-address
            - ":9001"
          env:
            - name: MINIO_ROOT_USER
              valueFrom:
                secretKeyRef:
                  name: minio-credentials
                  key: access-key
            - name: MINIO_ROOT_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: minio-credentials
                  key: secret-key
          ports:
            - containerPort: 9000
              name: api
            - containerPort: 9001
              name: console
          volumeMounts:
            - name: data
              mountPath: /data
          livenessProbe:
            httpGet:
              path: /minio/health/live
              port: 9000
            initialDelaySeconds: 30
            periodSeconds: 10
            timeoutSeconds: 5
          readinessProbe:
            httpGet:
              path: /minio/health/ready
              port: 9000
            initialDelaySeconds: 10
            periodSeconds: 5
            timeoutSeconds: 3
          resources:
            requests:
              memory: "512Mi"
              cpu: "250m"
            limits:
              memory: "1Gi"
              cpu: "500m"
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: minio-pvc
---
apiVersion: v1
kind: Service
metadata:
  name: minio
  namespace: $NAMESPACE
  labels:
    app: minio
spec:
  type: ClusterIP
  ports:
    - port: 80
      targetPort: 9000
      name: api
    - port: 9001
      targetPort: 9001
      name: console
  selector:
    app: minio
EOF

if [ $? -ne 0 ]; then
    echo_error "Failed to deploy MinIO"
    exit 1
fi

echo_success "MinIO resources created"

# Create OpenShift Route for console (if on OpenShift)
if [ "$PLATFORM" = "openshift" ]; then
    echo_info "Creating OpenShift Route for MinIO console..."

    cat <<EOF | kubectl apply -f -
---
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: minio-console
  namespace: $NAMESPACE
  labels:
    app: minio
spec:
  to:
    kind: Service
    name: minio
  port:
    targetPort: console
  tls:
    termination: edge
    insecureEdgeTerminationPolicy: Redirect
EOF

    echo_success "Route created"
fi

# Wait for MinIO to be ready
echo_info "Waiting for MinIO to be ready..."
if kubectl wait --for=condition=ready pod \
    -l app=minio \
    -n "$NAMESPACE" \
    --timeout=300s; then
    echo_success "MinIO is ready"
else
    echo_error "MinIO failed to become ready within 5 minutes"
    echo_info "Check pod status: kubectl get pods -n $NAMESPACE"
    echo_info "Check pod logs: kubectl logs -n $NAMESPACE deployment/minio"
    exit 1
fi

# Get connection details
echo ""
echo "════════════════════════════════════════════════════════════"
echo "  MinIO Deployment Complete"
echo "════════════════════════════════════════════════════════════"
echo ""

# Internal endpoint
INTERNAL_ENDPOINT="http://minio.$NAMESPACE.svc.cluster.local"
echo_success "✓ MinIO API Endpoint (internal): $INTERNAL_ENDPOINT"

# External console URL (OpenShift only)
if [ "$PLATFORM" = "openshift" ]; then
    CONSOLE_URL=$(kubectl get route minio-console -n "$NAMESPACE" -o jsonpath='{.spec.host}' 2>/dev/null || echo "")
    if [ -n "$CONSOLE_URL" ]; then
        echo_success "✓ MinIO Console (external): https://$CONSOLE_URL"
    fi
fi

# Credentials
echo ""
echo_info "Credentials:"
echo "  Access Key: $MINIO_ACCESS_KEY"
echo "  Secret Key: $MINIO_SECRET_KEY"
echo ""
echo_info "To retrieve credentials later:"
echo "  kubectl get secret minio-credentials -n $NAMESPACE -o jsonpath='{.data.access-key}' | base64 -d"
echo "  kubectl get secret minio-credentials -n $NAMESPACE -o jsonpath='{.data.secret-key}' | base64 -d"
echo ""

# Usage instructions
echo "════════════════════════════════════════════════════════════"
echo "  Usage Instructions"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "To deploy cost-onprem with this MinIO instance:"
echo ""
echo "  MINIO_ENDPOINT=\"$INTERNAL_ENDPOINT\" \\"
echo "    ./scripts/install-helm-chart.sh"
echo ""
echo "Or set the environment variable first:"
echo ""
echo "  export MINIO_ENDPOINT=\"$INTERNAL_ENDPOINT\""
echo "  ./scripts/install-helm-chart.sh"
echo ""
echo "To delete this MinIO deployment:"
echo "  kubectl delete namespace $NAMESPACE"
echo ""
