#!/bin/bash

# AMQ Streams (Streams for Apache Kafka) Operator and Kafka Cluster Deployment Script
# This script automates the deployment of AMQ Streams operator via OLM and a KRaft-based
# Kafka cluster for the cost management on-premise platform on OpenShift.
#
# Uses separate controller and broker KafkaNodePool resources (production-recommended
# architecture) with persistent JBOD storage. ZooKeeper is not used — Kafka 4.1
# operates exclusively in KRaft mode.
#
# PREREQUISITE: This script should be run BEFORE install-helm-chart.sh
#
# Typical workflow:
#   1. ./deploy-kafka.sh            # Deploy Kafka infrastructure (this script)
#   2. ./install-helm-chart.sh     # Deploy cost management on-premise application
#
# Environment Variables:
#   LOG_LEVEL - Control output verbosity (ERROR|WARN|INFO|DEBUG, default: WARN)
#
# Examples:
#   # Default (clean output)
#   ./deploy-kafka.sh
#
#   # Detailed output
#   LOG_LEVEL=INFO ./deploy-kafka.sh

set -e  # Exit on any error

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging configuration
LOG_LEVEL=${LOG_LEVEL:-WARN}

# Configuration - AMQ Streams / Kafka settings
KAFKA_NAMESPACE=${KAFKA_NAMESPACE:-kafka}
KAFKA_CLUSTER_NAME=${KAFKA_CLUSTER_NAME:-cost-onprem-kafka}
KAFKA_VERSION=${KAFKA_VERSION:-4.1.0}
AMQ_STREAMS_CHANNEL=${AMQ_STREAMS_CHANNEL:-amq-streams-3.1.x}
KAFKA_ENVIRONMENT=${KAFKA_ENVIRONMENT:-dev}  # "dev" or "ocp"
STORAGE_CLASS=${STORAGE_CLASS:-}  # Auto-detect if empty

# Advanced options
AMQ_STREAMS_NAMESPACE=${AMQ_STREAMS_NAMESPACE:-}  # If set, use existing AMQ Streams operator
KAFKA_BOOTSTRAP_SERVERS=${KAFKA_BOOTSTRAP_SERVERS:-}  # If set, use external Kafka (skip deployment)

# Platform-specific configuration (auto-detected)
PLATFORM=""

# Logging functions with level-based filtering
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

# Backward compatibility aliases
echo_info() { log_info "$1"; }
echo_success() { log_success "$1"; }
echo_warning() { log_warning "$1"; }
echo_error() { log_error "$1"; }
echo_header() { log_header "$1"; }

# Function to verify OpenShift platform
detect_platform() {
    echo_info "Verifying OpenShift platform..."

    if kubectl get routes.route.openshift.io >/dev/null 2>&1; then
        echo_success "Verified OpenShift platform"
        PLATFORM="openshift"
        # Auto-detect default storage class if not provided
        if [ -z "$STORAGE_CLASS" ]; then
            STORAGE_CLASS=$(kubectl get storageclass -o jsonpath='{.items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class=="true")].metadata.name}' 2>/dev/null | awk '{print $1}')
            if [ -n "$STORAGE_CLASS" ]; then
                echo_info "Auto-detected default storage class: $STORAGE_CLASS"
            else
                echo_warning "No default storage class found. Storage class must be explicitly set."
            fi
        fi
        if [ "$KAFKA_ENVIRONMENT" = "dev" ]; then
            KAFKA_ENVIRONMENT="ocp"
        fi
    else
        echo_error "OpenShift platform not detected. This chart requires OpenShift."
        echo_error "Please ensure you are connected to an OpenShift cluster."
        exit 1
    fi
}

# Function to check prerequisites
check_prerequisites() {
    echo_header "CHECKING PREREQUISITES"

    if ! command -v kubectl >/dev/null 2>&1; then
        echo_error "kubectl command not found. Please install kubectl."
        exit 1
    fi
    echo_success "✓ kubectl is available"

    if ! kubectl get nodes >/dev/null 2>&1; then
        echo_error "Cannot connect to cluster. Please check your kubectl configuration."
        exit 1
    fi
    echo_success "✓ Connected to cluster"

    local current_context
    current_context=$(kubectl config current-context 2>/dev/null || echo "none")
    echo_info "Current kubectl context: $current_context"

    detect_platform

    if [ -n "$STORAGE_CLASS" ]; then
        if kubectl get storageclass "$STORAGE_CLASS" >/dev/null 2>&1; then
            echo_success "✓ Storage class '$STORAGE_CLASS' is available"
        else
            echo_warning "Storage class '$STORAGE_CLASS' not found. Available storage classes:"
            kubectl get storageclass --no-headers -o custom-columns=NAME:.metadata.name | sed 's/^/  - /' || true
        fi
    fi

    echo_success "Prerequisites check completed successfully"
}

# Function to create namespace
create_namespace() {
    echo_header "CREATING NAMESPACE"

    if kubectl get namespace "$KAFKA_NAMESPACE" >/dev/null 2>&1; then
        echo_warning "Namespace '$KAFKA_NAMESPACE' already exists"
    else
        echo_info "Creating namespace: $KAFKA_NAMESPACE"
        kubectl create namespace "$KAFKA_NAMESPACE"
        echo_success "✓ Namespace '$KAFKA_NAMESPACE' created"
    fi
}

# Function to verify an existing AMQ Streams / Strimzi operator
verify_existing_operator() {
    local operator_namespace="$1"

    echo_info "Verifying existing AMQ Streams operator in namespace: $operator_namespace"

    if ! kubectl get pods -n "$operator_namespace" -l strimzi.io/kind=cluster-operator --no-headers 2>/dev/null | grep -q .; then
        echo_error "AMQ Streams operator not found in namespace: $operator_namespace"
        return 1
    fi

    echo_info "Checking AMQ Streams operator version compatibility..."
    local operator_pod
    operator_pod=$(kubectl get pods -n "$operator_namespace" -l strimzi.io/kind=cluster-operator -o jsonpath='{.items[0].metadata.name}')
    if [ -n "$operator_pod" ]; then
        local operator_image
        operator_image=$(kubectl get pod -n "$operator_namespace" "$operator_pod" -o jsonpath='{.spec.containers[0].image}')
        echo_info "Found operator image: $operator_image"

        if [[ "$operator_image" =~ :3\.1\. ]] || [[ "$operator_image" =~ :0\.48\. ]]; then
            echo_success "AMQ Streams operator version is compatible with Kafka $KAFKA_VERSION"
            return 0
        else
            echo_error "AMQ Streams operator version may not be compatible with Kafka $KAFKA_VERSION"
            echo_error "Found: $operator_image"
            echo_error "Required: AMQ Streams 3.1.x (Strimzi 0.48.x) for Kafka 4.1.0 support"
            return 1
        fi
    fi

    return 0
}

# Function to verify existing Kafka cluster
verify_existing_kafka() {
    local kafka_namespace="$1"

    echo_info "Verifying existing Kafka cluster in namespace: $kafka_namespace"

    if ! kubectl get kafka -n "$kafka_namespace" >/dev/null 2>&1; then
        echo_error "Kafka cluster not found in namespace: $kafka_namespace"
        return 1
    fi

    echo_info "Checking Kafka cluster version compatibility..."
    local kafka_cluster
    kafka_cluster=$(kubectl get kafka -n "$kafka_namespace" -o jsonpath='{.items[0].metadata.name}')
    if [ -n "$kafka_cluster" ]; then
        local kafka_version
        kafka_version=$(kubectl get kafka -n "$kafka_namespace" "$kafka_cluster" -o jsonpath='{.spec.kafka.version}')
        echo_info "Found Kafka cluster version: $kafka_version"

        if [[ "$kafka_version" =~ ^4\. ]]; then
            echo_success "Kafka cluster version is compatible: $kafka_version"
            return 0
        else
            echo_error "Kafka cluster version is not compatible"
            echo_error "Found: $kafka_version"
            echo_error "Required: 4.x (AMQ Streams 3.1 ships Kafka 4.1.0)"
            return 1
        fi
    fi

    return 0
}

# Function to install AMQ Streams operator via OLM
install_amq_streams_operator() {
    echo_header "INSTALLING AMQ STREAMS OPERATOR"

    local target_namespace="$KAFKA_NAMESPACE"

    # Check if there's already a compatible operator we can reuse
    local existing_operator_ns
    existing_operator_ns=$(kubectl get pods -A -l strimzi.io/kind=cluster-operator -o jsonpath='{.items[0].metadata.namespace}' 2>/dev/null || echo "")

    if [ -n "$existing_operator_ns" ]; then
        echo_info "Found existing AMQ Streams operator in namespace: $existing_operator_ns"

        if verify_existing_operator "$existing_operator_ns" 2>/dev/null; then
            echo_success "Existing AMQ Streams operator is compatible, reusing it"
            target_namespace="$existing_operator_ns"
            KAFKA_NAMESPACE="$target_namespace"
            return 0
        else
            echo_error "Existing operator in namespace '$existing_operator_ns' is not compatible"
            echo_error "Required: AMQ Streams 3.1.x (Strimzi 0.48.x) for Kafka 4.1.0 support"
            echo_info "Run '$0 cleanup' to remove incompatible operator"
            exit 1
        fi
    fi

    echo_info "No existing AMQ Streams operator found, installing via OLM"

    # Check if there is already a Subscription for amq-streams in any namespace
    local existing_sub_ns
    existing_sub_ns=$(kubectl get subscriptions.operators.coreos.com -A -o jsonpath='{range .items[?(@.spec.name=="amq-streams")]}{.metadata.namespace}{end}' 2>/dev/null || echo "")
    if [ -n "$existing_sub_ns" ]; then
        echo_info "Found existing AMQ Streams subscription in namespace: $existing_sub_ns"
        echo_info "Waiting for operator to become ready..."
        target_namespace="$existing_sub_ns"
    else
        # Create OperatorGroup (required for OLM to install into the namespace)
        if ! kubectl get operatorgroup -n "$target_namespace" --no-headers 2>/dev/null | grep -q .; then
            echo_info "Creating OperatorGroup in namespace: $target_namespace"
            cat <<EOF | kubectl apply -f -
apiVersion: operators.coreos.com/v1
kind: OperatorGroup
metadata:
  name: amq-streams-operatorgroup
  namespace: $target_namespace
spec:
  targetNamespaces:
    - $target_namespace
EOF
            echo_success "✓ OperatorGroup created"
        else
            echo_info "OperatorGroup already exists in namespace: $target_namespace"
        fi

        # Create Subscription
        echo_info "Creating AMQ Streams subscription (channel: $AMQ_STREAMS_CHANNEL)..."
        cat <<EOF | kubectl apply -f -
apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
  name: amq-streams
  namespace: $target_namespace
spec:
  channel: $AMQ_STREAMS_CHANNEL
  installPlanApproval: Automatic
  name: amq-streams
  source: redhat-operators
  sourceNamespace: openshift-marketplace
EOF
        echo_success "✓ AMQ Streams subscription created"
    fi

    # Wait for CSV to reach Succeeded phase
    echo_info "Waiting for AMQ Streams operator to install..."
    local timeout=600
    local elapsed=0

    while [ $elapsed -lt $timeout ]; do
        local csv_name
        csv_name=$(kubectl get subscriptions.operators.coreos.com amq-streams -n "$target_namespace" -o jsonpath='{.status.installedCSV}' 2>/dev/null || echo "")

        if [ -n "$csv_name" ]; then
            local csv_phase
            csv_phase=$(kubectl get csv "$csv_name" -n "$target_namespace" -o jsonpath='{.status.phase}' 2>/dev/null || echo "")

            if [ "$csv_phase" = "Succeeded" ]; then
                echo_success "✓ AMQ Streams operator installed (CSV: $csv_name)"
                break
            fi
            echo_info "CSV '$csv_name' phase: $csv_phase (${elapsed}s elapsed)"
        else
            if [ $((elapsed % 30)) -eq 0 ]; then
                echo_info "Waiting for CSV to be created... (${elapsed}s elapsed)"
            fi
        fi

        sleep 10
        elapsed=$((elapsed + 10))
    done

    if [ $elapsed -ge $timeout ]; then
        echo_error "Timeout waiting for AMQ Streams operator to install"
        echo_error "Check: kubectl get subscriptions.operators.coreos.com amq-streams -n $target_namespace -o yaml"
        exit 1
    fi

    # Wait for operator pod to be ready
    echo_info "Waiting for operator pod to be ready..."
    timeout=300
    elapsed=0

    while [ $elapsed -lt $timeout ]; do
        if kubectl get pod -n "$target_namespace" -l strimzi.io/kind=cluster-operator --no-headers 2>/dev/null | grep -q .; then
            if kubectl wait --for=condition=ready pod -l strimzi.io/kind=cluster-operator -n "$target_namespace" --timeout=10s >/dev/null 2>&1; then
                echo_success "✓ AMQ Streams operator pod is ready"
                break
            fi
        fi

        if [ $((elapsed % 30)) -eq 0 ]; then
            echo_info "Still waiting for operator pod... (${elapsed}s elapsed)"
        fi

        sleep 5
        elapsed=$((elapsed + 5))
    done

    if [ $elapsed -ge $timeout ]; then
        echo_error "Timeout waiting for AMQ Streams operator pod to be ready"
        exit 1
    fi

    # Wait for CRDs to be established
    echo_info "Waiting for Kafka CRDs to be ready..."

    local required_crds=(
        "kafkas.kafka.strimzi.io"
        "kafkatopics.kafka.strimzi.io"
        "kafkanodepools.kafka.strimzi.io"
    )

    for crd in "${required_crds[@]}"; do
        local crd_timeout=120
        local crd_elapsed=0
        while ! kubectl get crd "$crd" >/dev/null 2>&1 && [ $crd_elapsed -lt $crd_timeout ]; do
            sleep 5
            crd_elapsed=$((crd_elapsed + 5))
        done

        if ! kubectl get crd "$crd" >/dev/null 2>&1; then
            echo_error "Timeout waiting for CRD: $crd"
            exit 1
        fi

        kubectl wait --for condition=established --timeout=60s "crd/$crd"
        echo_success "✓ CRD $crd is ready"
    done
}

# Function to deploy KRaft-based Kafka cluster with separate controller and broker node pools
deploy_kafka_cluster() {
    echo_header "DEPLOYING KAFKA CLUSTER (KRaft)"

    # Check if Kafka cluster already exists
    if kubectl get kafka "$KAFKA_CLUSTER_NAME" -n "$KAFKA_NAMESPACE" >/dev/null 2>&1; then
        echo_warning "Kafka cluster '$KAFKA_CLUSTER_NAME' already exists in namespace '$KAFKA_NAMESPACE'"
        return 0
    fi

    # Set environment-specific configuration
    local broker_replicas=1
    local broker_storage_size="10Gi"
    local controller_replicas=1
    local controller_storage_size="5Gi"
    local storage_class=""
    local tls_enabled="false"

    case "$KAFKA_ENVIRONMENT" in
        "ocp"|"openshift")
            broker_replicas=3
            broker_storage_size="100Gi"
            controller_replicas=3
            controller_storage_size="20Gi"
            storage_class="$STORAGE_CLASS"
            tls_enabled="true"
            ;;
        "dev"|"development")
            ;;
    esac

    echo_info "Creating KRaft Kafka cluster with configuration:"
    echo_info "  Name: $KAFKA_CLUSTER_NAME"
    echo_info "  Kafka Version: $KAFKA_VERSION"
    echo_info "  Controller replicas: $controller_replicas"
    echo_info "  Controller storage: $controller_storage_size"
    echo_info "  Broker replicas: $broker_replicas"
    echo_info "  Broker storage: $broker_storage_size"
    if [ -n "$storage_class" ]; then
        echo_info "  Storage class: $storage_class"
    fi
    echo_info "  TLS enabled: $tls_enabled"

    # Build storage class snippet for YAML
    local storage_class_yaml=""
    if [ -n "$storage_class" ]; then
        storage_class_yaml="
        class: $storage_class"
    fi

    # --- Controller KafkaNodePool ---
    echo_info "Creating controller node pool..."
    cat <<EOF | kubectl apply -f -
apiVersion: kafka.strimzi.io/v1beta2
kind: KafkaNodePool
metadata:
  name: controller
  namespace: $KAFKA_NAMESPACE
  labels:
    strimzi.io/cluster: $KAFKA_CLUSTER_NAME
spec:
  replicas: $controller_replicas
  roles:
    - controller
  storage:
    type: jbod
    volumes:
      - id: 0
        type: persistent-claim
        size: $controller_storage_size
        deleteClaim: false${storage_class_yaml}
EOF
    echo_success "✓ Controller node pool created"

    # --- Broker KafkaNodePool ---
    echo_info "Creating broker node pool..."
    cat <<EOF | kubectl apply -f -
apiVersion: kafka.strimzi.io/v1beta2
kind: KafkaNodePool
metadata:
  name: broker
  namespace: $KAFKA_NAMESPACE
  labels:
    strimzi.io/cluster: $KAFKA_CLUSTER_NAME
spec:
  replicas: $broker_replicas
  roles:
    - broker
  storage:
    type: jbod
    volumes:
      - id: 0
        type: persistent-claim
        size: $broker_storage_size
        deleteClaim: false${storage_class_yaml}
EOF
    echo_success "✓ Broker node pool created"

    # --- Kafka resource ---
    echo_info "Creating Kafka cluster resource..."

    local listeners_yaml="
    listeners:
      - name: plain
        port: 9092
        type: internal
        tls: false"

    if [ "$tls_enabled" = "true" ]; then
        listeners_yaml="$listeners_yaml
      - name: tls
        port: 9093
        type: internal
        tls: true"
    fi

    local min_isr=$((broker_replicas / 2 + 1))
    if [ "$min_isr" -lt 1 ]; then
        min_isr=1
    fi

    cat <<EOF | kubectl apply -f -
apiVersion: kafka.strimzi.io/v1beta2
kind: Kafka
metadata:
  name: $KAFKA_CLUSTER_NAME
  namespace: $KAFKA_NAMESPACE
spec:
  kafka:
    version: $KAFKA_VERSION${listeners_yaml}
    config:
      auto.create.topics.enable: "true"
      default.replication.factor: "$broker_replicas"
      log.retention.hours: "168"
      log.segment.bytes: "1073741824"
      min.insync.replicas: "$min_isr"
      offsets.topic.replication.factor: "$broker_replicas"
      transaction.state.log.min.isr: "$min_isr"
      transaction.state.log.replication.factor: "$broker_replicas"
  entityOperator:
    topicOperator: {}
    userOperator: {}
EOF
    echo_success "✓ Kafka cluster resource created"

    # Wait for Kafka cluster to be ready
    echo_info "Waiting for Kafka cluster to be ready (this may take several minutes)..."
    local timeout=600
    local elapsed=0

    while [ $elapsed -lt $timeout ]; do
        local status
        status=$(kubectl get kafka "$KAFKA_CLUSTER_NAME" -n "$KAFKA_NAMESPACE" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "False")
        if [ "$status" = "True" ]; then
            echo_success "✓ Kafka cluster is ready"
            return 0
        fi

        if [ $((elapsed % 60)) -eq 0 ]; then
            echo_info "Still waiting for Kafka cluster... (${elapsed}s elapsed)"
        fi

        sleep 10
        elapsed=$((elapsed + 10))
    done

    if [ $elapsed -ge $timeout ]; then
        echo_error "Timeout waiting for Kafka cluster to be ready"
        echo_error "Check: kubectl get kafka $KAFKA_CLUSTER_NAME -n $KAFKA_NAMESPACE -o yaml"
        echo_error "Check: kubectl get kafkanodepool -n $KAFKA_NAMESPACE"
        exit 1
    fi
}

# Function to create Kafka topics
create_kafka_topics() {
    echo_header "CREATING KAFKA TOPICS"

    local replication_factor=1
    if [ "$KAFKA_ENVIRONMENT" = "ocp" ] || [ "$KAFKA_ENVIRONMENT" = "openshift" ]; then
        replication_factor=3
    fi

    echo_info "Creating Kafka topics with replication factor: $replication_factor"

    local required_topics=(
        "hccm.ros.events:3:$replication_factor"
        "platform.sources.event-stream:3:$replication_factor"
        "rosocp.kruize.recommendations:3:$replication_factor"
        "platform.upload.announce:3:$replication_factor"
        "platform.payload-status:3:$replication_factor"
    )

    for topic_config in "${required_topics[@]}"; do
        IFS=':' read -r topic_name partitions rf <<< "$topic_config"

        if kubectl get kafkatopic "$topic_name" -n "$KAFKA_NAMESPACE" >/dev/null 2>&1; then
            echo_info "Topic '$topic_name' already exists, skipping"
            continue
        fi

        echo_info "Creating topic: $topic_name (partitions: $partitions, replication: $rf)"

        cat <<EOF | kubectl apply -f -
apiVersion: kafka.strimzi.io/v1beta2
kind: KafkaTopic
metadata:
  name: $topic_name
  namespace: $KAFKA_NAMESPACE
  labels:
    strimzi.io/cluster: $KAFKA_CLUSTER_NAME
spec:
  partitions: $partitions
  replicas: $rf
  config:
    retention.ms: "604800000"
    segment.ms: "86400000"
EOF

        if [ $? -eq 0 ]; then
            echo_success "✓ Topic '$topic_name' created"
        else
            echo_warning "Failed to create topic '$topic_name'"
        fi
    done

    echo_success "Kafka topics creation completed"
}

# Function to validate deployment
validate_deployment() {
    echo_header "VALIDATING DEPLOYMENT"

    local validation_errors=0

    # Check namespace
    if kubectl get namespace "$KAFKA_NAMESPACE" >/dev/null 2>&1; then
        echo_success "✓ Namespace '$KAFKA_NAMESPACE' exists"
    else
        echo_error "✗ Namespace '$KAFKA_NAMESPACE' not found"
        validation_errors=$((validation_errors + 1))
    fi

    # Check AMQ Streams operator
    if kubectl get pods -n "$KAFKA_NAMESPACE" -l strimzi.io/kind=cluster-operator --no-headers 2>/dev/null | grep -q .; then
        local ready
        ready=$(kubectl get pods -n "$KAFKA_NAMESPACE" -l strimzi.io/kind=cluster-operator -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "False")
        if [ "$ready" = "True" ]; then
            echo_success "✓ AMQ Streams operator is running"
        else
            echo_error "✗ AMQ Streams operator is not ready"
            validation_errors=$((validation_errors + 1))
        fi
    else
        echo_error "✗ AMQ Streams operator not found"
        validation_errors=$((validation_errors + 1))
    fi

    # Check Kafka cluster
    if kubectl get kafka "$KAFKA_CLUSTER_NAME" -n "$KAFKA_NAMESPACE" >/dev/null 2>&1; then
        local status
        status=$(kubectl get kafka "$KAFKA_CLUSTER_NAME" -n "$KAFKA_NAMESPACE" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "False")
        if [ "$status" = "True" ]; then
            echo_success "✓ Kafka cluster '$KAFKA_CLUSTER_NAME' is ready"
        else
            echo_error "✗ Kafka cluster '$KAFKA_CLUSTER_NAME' is not ready"
            validation_errors=$((validation_errors + 1))
        fi
    else
        echo_error "✗ Kafka cluster '$KAFKA_CLUSTER_NAME' not found"
        validation_errors=$((validation_errors + 1))
    fi

    # Check KafkaNodePool resources
    echo_info "Checking KafkaNodePool resources..."
    local pool_count
    pool_count=$(kubectl get kafkanodepool -n "$KAFKA_NAMESPACE" --no-headers 2>/dev/null | wc -l || echo "0")
    pool_count=$(echo "$pool_count" | tr -d ' ')
    if [ "$pool_count" -gt 0 ]; then
        echo_success "✓ Found $pool_count KafkaNodePool(s)"
        kubectl get kafkanodepool -n "$KAFKA_NAMESPACE" -o custom-columns=NAME:.metadata.name,ROLES:.spec.roles,REPLICAS:.spec.replicas 2>/dev/null || true
    else
        echo_warning "⚠ No KafkaNodePool resources found"
    fi

    # Check topics
    echo_info "Checking Kafka topics..."
    local topic_count
    topic_count=$(kubectl get kafkatopic -n "$KAFKA_NAMESPACE" --no-headers 2>/dev/null | wc -l || echo "0")
    topic_count=$(echo "$topic_count" | tr -d ' ')
    if [ "$topic_count" -gt 0 ]; then
        echo_success "✓ Found $topic_count Kafka topic(s)"
        kubectl get kafkatopic -n "$KAFKA_NAMESPACE" -o custom-columns=NAME:.metadata.name,READY:.status.conditions[0].status 2>/dev/null || true
    else
        echo_warning "⚠ No Kafka topics found (may be created later)"
    fi

    if [ $validation_errors -eq 0 ]; then
        echo_success "All validation checks passed!"
        return 0
    else
        echo_error "$validation_errors validation error(s) found"
        return 1
    fi
}

# Function to display deployment summary
display_summary() {
    echo_header "DEPLOYMENT SUMMARY"

    echo_info "AMQ Streams / Kafka Deployment Information:"
    echo_info "  Platform: $PLATFORM"
    echo_info "  Namespace: $KAFKA_NAMESPACE"
    echo_info "  Kafka Cluster: $KAFKA_CLUSTER_NAME"
    echo_info "  Kafka Version: $KAFKA_VERSION"
    echo_info "  AMQ Streams Channel: $AMQ_STREAMS_CHANNEL"
    echo_info "  Mode: KRaft (no ZooKeeper)"
    echo ""

    local kafka_bootstrap_servers=""

    local bootstrap_address
    bootstrap_address=$(kubectl get kafka "$KAFKA_CLUSTER_NAME" -n "$KAFKA_NAMESPACE" -o jsonpath='{.status.listeners[?(@.name=="plain")].bootstrapServers}' 2>/dev/null || echo "")

    if [ -n "$bootstrap_address" ]; then
        kafka_bootstrap_servers="$bootstrap_address"
        echo_info "Kafka Connection Information:"
        echo_info "  Bootstrap Servers: $kafka_bootstrap_servers"
        echo_info "  Source: Kafka cluster status"
    else
        kafka_bootstrap_servers="${KAFKA_CLUSTER_NAME}-kafka-bootstrap.${KAFKA_NAMESPACE}:9092"
        echo_info "Kafka Connection Information:"
        echo_info "  Bootstrap Servers: $kafka_bootstrap_servers"
        echo_info "  Source: Service name (auto-generated)"
        echo_warning "Could not read from Kafka status, using service name fallback"
    fi
    echo ""

    echo "export KAFKA_BOOTSTRAP_SERVERS=\"$kafka_bootstrap_servers\"" > /tmp/kafka-bootstrap-servers.env
    echo_success "✓ Kafka bootstrap servers exported to: /tmp/kafka-bootstrap-servers.env"
    echo ""

    echo_info "Verification Commands:"
    echo_info "  kubectl get kafka -n $KAFKA_NAMESPACE"
    echo_info "  kubectl get kafkanodepool -n $KAFKA_NAMESPACE"
    echo_info "  kubectl get kafkatopic -n $KAFKA_NAMESPACE"
    echo ""

    echo_info "Troubleshooting:"
    echo_info "  Kafka logs: kubectl logs -n $KAFKA_NAMESPACE -l strimzi.io/name=${KAFKA_CLUSTER_NAME}-broker"
    echo_info "  Controller logs: kubectl logs -n $KAFKA_NAMESPACE -l strimzi.io/name=${KAFKA_CLUSTER_NAME}-controller"
    echo_info "  Operator logs: kubectl logs -n $KAFKA_NAMESPACE -l strimzi.io/kind=cluster-operator"
    echo ""

    echo_success "Kafka infrastructure deployment completed successfully!"
    echo ""
    echo_info "Next Steps:"
    echo_info "  1. (Optional) Verify Kafka cluster: kubectl get kafka $KAFKA_CLUSTER_NAME -n $KAFKA_NAMESPACE"
    echo_info "  2. Deploy Cost Management On-Premise application: ./install-helm-chart.sh"
    echo ""
}

# Function to clean up deployment
cleanup_deployment() {
    echo_header "CLEANING UP DEPLOYMENT"
    echo_info "Removing AMQ Streams and Kafka resources..."

    # Remove Kafka resources (with timeout to prevent hanging)
    kubectl delete kafka --all -n "$KAFKA_NAMESPACE" --timeout 60s 2>/dev/null || \
        kubectl patch kafka --all -n "$KAFKA_NAMESPACE" -p '{"metadata":{"finalizers":[]}}' --type=merge 2>/dev/null || true
    kubectl delete kafkanodepool --all -n "$KAFKA_NAMESPACE" --timeout 30s 2>/dev/null || \
        kubectl patch kafkanodepool --all -n "$KAFKA_NAMESPACE" -p '{"metadata":{"finalizers":[]}}' --type=merge 2>/dev/null || true
    kubectl delete kafkatopic --all -n "$KAFKA_NAMESPACE" --timeout 30s 2>/dev/null || \
        kubectl patch kafkatopic --all -n "$KAFKA_NAMESPACE" -p '{"metadata":{"finalizers":[]}}' --type=merge 2>/dev/null || true
    kubectl delete kafkauser --all -n "$KAFKA_NAMESPACE" --timeout 30s 2>/dev/null || \
        kubectl patch kafkauser --all -n "$KAFKA_NAMESPACE" -p '{"metadata":{"finalizers":[]}}' --type=merge 2>/dev/null || true

    # Remove OLM resources (Subscription, CSV, OperatorGroup)
    local csv_name
    csv_name=$(kubectl get subscriptions.operators.coreos.com amq-streams -n "$KAFKA_NAMESPACE" -o jsonpath='{.status.installedCSV}' 2>/dev/null || echo "")

    kubectl delete subscriptions.operators.coreos.com amq-streams -n "$KAFKA_NAMESPACE" --timeout 30s 2>/dev/null || true

    if [ -n "$csv_name" ]; then
        kubectl delete csv "$csv_name" -n "$KAFKA_NAMESPACE" --timeout 60s 2>/dev/null || true
    fi

    kubectl delete operatorgroup amq-streams-operatorgroup -n "$KAFKA_NAMESPACE" --timeout 30s 2>/dev/null || true

    # Also clean up any legacy Helm-based Strimzi installations
    kubectl get pods -A -l strimzi.io/kind=cluster-operator --no-headers 2>/dev/null | while read -r namespace pod_name rest; do
        kubectl delete deployment -n "$namespace" -l strimzi.io/kind=cluster-operator --timeout 30s 2>/dev/null || true
        helm uninstall strimzi-kafka-operator -n "$namespace" --timeout 2m0s 2>/dev/null || true
        helm uninstall strimzi-cluster-operator -n "$namespace" --timeout 2m0s 2>/dev/null || true
    done

    # Remove RBAC resources created by the operator
    kubectl delete clusterrolebinding strimzi-cluster-operator 2>/dev/null || true
    kubectl delete clusterrole strimzi-cluster-operator-global 2>/dev/null || true
    kubectl delete clusterrole strimzi-cluster-operator-leader-election 2>/dev/null || true
    kubectl delete clusterrole strimzi-cluster-operator-namespaced 2>/dev/null || true
    kubectl delete clusterrole strimzi-cluster-operator-watched 2>/dev/null || true
    kubectl delete clusterrole strimzi-entity-operator 2>/dev/null || true
    kubectl delete clusterrole strimzi-kafka-broker 2>/dev/null || true
    kubectl delete clusterrole strimzi-kafka-client 2>/dev/null || true
    kubectl delete clusterrolebinding strimzi-cluster-operator-kafka-broker-delegation 2>/dev/null || true
    kubectl delete clusterrolebinding strimzi-cluster-operator-kafka-client-delegation 2>/dev/null || true

    # Remove CRDs (with timeout to prevent hanging)
    kubectl delete crd kafkas.kafka.strimzi.io --timeout 30s 2>/dev/null || true
    kubectl delete crd kafkatopics.kafka.strimzi.io --timeout 30s 2>/dev/null || true
    kubectl delete crd kafkausers.kafka.strimzi.io --timeout 30s 2>/dev/null || true
    kubectl delete crd kafkanodepools.kafka.strimzi.io --timeout 30s 2>/dev/null || true
    kubectl delete crd kafkaconnects.kafka.strimzi.io --timeout 30s 2>/dev/null || true
    kubectl delete crd kafkaconnectors.kafka.strimzi.io --timeout 30s 2>/dev/null || true
    kubectl delete crd kafkamirrormakers.kafka.strimzi.io --timeout 30s 2>/dev/null || true
    kubectl delete crd kafkamirrormaker2s.kafka.strimzi.io --timeout 30s 2>/dev/null || true
    kubectl delete crd kafkabridges.kafka.strimzi.io --timeout 30s 2>/dev/null || true
    kubectl delete crd kafkarebalances.kafka.strimzi.io --timeout 30s 2>/dev/null || true
    kubectl delete crd strimzipodsets.core.strimzi.io --timeout 30s 2>/dev/null || true

    # Remove namespace
    kubectl delete namespace "$KAFKA_NAMESPACE" --timeout 60s 2>/dev/null || true

    echo_info "Waiting for namespace to be fully deleted..."
    local timeout=120
    local elapsed=0
    while [ $elapsed -lt $timeout ]; do
        if ! kubectl get namespace "$KAFKA_NAMESPACE" >/dev/null 2>&1; then
            echo_success "✓ Namespace '$KAFKA_NAMESPACE' fully deleted"
            break
        fi
        if [ $((elapsed % 10)) -eq 0 ]; then
            echo_info "Still waiting for namespace deletion... (${elapsed}s elapsed)"
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done

    if [ $elapsed -ge $timeout ]; then
        echo_warning "Timeout waiting for namespace deletion. Namespace may still be terminating."
        echo_info "You may need to manually remove finalizers if the namespace remains stuck."
    fi

    echo_success "Cleanup completed"
}

# Main execution function
main() {
    echo_header "AMQ STREAMS / KAFKA DEPLOYMENT SCRIPT"

    # Handle existing Kafka on cluster (bootstrap servers provided by user)
    if [ -n "$KAFKA_BOOTSTRAP_SERVERS" ]; then
        echo_info "Using existing Kafka cluster (provided bootstrap servers)"
        echo_info ""
        echo_info "Configuration:"
        echo_info "  Bootstrap Servers: $KAFKA_BOOTSTRAP_SERVERS"
        echo_info "  Deployment: Skipped (using external Kafka)"
        echo ""

        echo "export KAFKA_BOOTSTRAP_SERVERS=\"$KAFKA_BOOTSTRAP_SERVERS\"" > /tmp/kafka-bootstrap-servers.env
        echo_success "✓ Kafka bootstrap servers exported to /tmp/kafka-bootstrap-servers.env"
        echo ""
        echo_success "Kafka configuration completed successfully!"
        exit 0
    fi

    echo_info "This script will deploy AMQ Streams operator and Kafka cluster (KRaft mode)"
    echo_info "Deployment Configuration:"
    echo_info "  Namespace: $KAFKA_NAMESPACE"
    echo_info "  Cluster Name: $KAFKA_CLUSTER_NAME"
    echo_info "  Kafka Version: $KAFKA_VERSION"
    echo_info "  AMQ Streams Channel: $AMQ_STREAMS_CHANNEL"
    echo_info "  Environment: $KAFKA_ENVIRONMENT"
    if [ -n "$STORAGE_CLASS" ]; then
        echo_info "  Storage Class: $STORAGE_CLASS"
    fi
    if [ -n "$AMQ_STREAMS_NAMESPACE" ]; then
        echo_info "  Existing Operator: $AMQ_STREAMS_NAMESPACE"
    fi
    echo ""

    check_prerequisites

    if [ -n "$AMQ_STREAMS_NAMESPACE" ]; then
        echo_info "Using existing AMQ Streams operator in namespace: $AMQ_STREAMS_NAMESPACE"
        if ! verify_existing_operator "$AMQ_STREAMS_NAMESPACE"; then
            exit 1
        fi
        if ! verify_existing_kafka "$AMQ_STREAMS_NAMESPACE"; then
            exit 1
        fi
        KAFKA_NAMESPACE="$AMQ_STREAMS_NAMESPACE"
    else
        create_namespace
        install_amq_streams_operator
        deploy_kafka_cluster
        create_kafka_topics
    fi

    if validate_deployment; then
        display_summary
    else
        echo_error "Deployment validation failed. Check the logs above for details."
        exit 1
    fi
}

# Handle script arguments
case "${1:-}" in
    "cleanup"|"clean")
        detect_platform
        cleanup_deployment
        exit 0
        ;;
    "validate"|"check")
        detect_platform
        validate_deployment
        exit $?
        ;;
    "help"|"-h"|"--help")
        echo "Usage: $0 [command]"
        echo ""
        echo "Commands:"
        echo "  (no command)     Deploy AMQ Streams operator and Kafka cluster (KRaft mode)"
        echo "  validate         Validate existing deployment"
        echo "  cleanup          Remove all AMQ Streams and Kafka resources"
        echo "  help             Show this help message"
        echo ""
        echo "Environment Variables:"
        echo "  KAFKA_BOOTSTRAP_SERVERS  Bootstrap servers for existing Kafka on cluster (skips deployment)"
        echo "  KAFKA_NAMESPACE          Target namespace (default: kafka)"
        echo "  KAFKA_CLUSTER_NAME       Kafka cluster name (default: cost-onprem-kafka)"
        echo "  KAFKA_VERSION            Kafka version (default: 4.1.0)"
        echo "  AMQ_STREAMS_CHANNEL      OLM subscription channel (default: amq-streams-3.1.x)"
        echo "  KAFKA_ENVIRONMENT        Environment type: dev or ocp (default: dev)"
        echo "  STORAGE_CLASS            Storage class name (auto-detected if empty)"
        echo "  AMQ_STREAMS_NAMESPACE    Use existing AMQ Streams operator in this namespace"
        echo ""
        echo "Examples:"
        echo "  # Deploy with default settings"
        echo "  $0"
        echo ""
        echo "  # Deploy for OpenShift with custom storage"
        echo "  KAFKA_ENVIRONMENT=ocp STORAGE_CLASS=gp2 $0"
        echo ""
        echo "  # Use existing AMQ Streams operator"
        echo "  AMQ_STREAMS_NAMESPACE=existing-operator $0"
        echo ""
        echo "  # Use existing Kafka on cluster (no deployment)"
        echo "  KAFKA_BOOTSTRAP_SERVERS=my-kafka-bootstrap.my-namespace:9092 $0"
        echo ""
        echo "  # Validate existing deployment"
        echo "  $0 validate"
        echo ""
        echo "  # Clean up deployment"
        echo "  $0 cleanup"
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
