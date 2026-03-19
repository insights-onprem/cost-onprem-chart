#!/bin/bash
# Script to force the Cost Management Metrics Operator to package and upload metrics immediately
# This bypasses the 6-hour packaging/upload cycle timers
#
# Usage: ./force-operator-package-upload.sh [--enable-monitoring] [--help]

set -euo pipefail

NAMESPACE="costmanagement-metrics-operator"
CONFIG_NAME="costmanagementmetricscfg-tls"
MONITORING_NAMESPACE="openshift-user-workload-monitoring"
MONITORING_CONFIG_NAMESPACE="openshift-monitoring"

# Flag parsing
ENABLE_MONITORING=false

show_help() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Force the Cost Management Metrics Operator to package and upload metrics immediately."
    echo "This bypasses the 6-hour packaging/upload cycle timers."
    echo ""
    echo "Options:"
    echo "  --enable-monitoring    Enable User Workload Monitoring if not already enabled"
    echo "  --help                 Show this help message"
    echo ""
    echo "Prerequisites:"
    echo "  - OpenShift cluster with Cost Management Metrics Operator installed"
    echo "  - User Workload Monitoring enabled (use --enable-monitoring to enable)"
    echo "  - costmanagementmetricsconfig resource configured"
    echo ""
    echo "Examples:"
    echo "  $0                        # Check monitoring status and force upload"
    echo "  $0 --enable-monitoring    # Enable monitoring if needed, then force upload"
    exit 0
}

# Parse arguments
for arg in "$@"; do
    case $arg in
        --enable-monitoring)
            ENABLE_MONITORING=true
            shift
            ;;
        --help|-h)
            show_help
            ;;
        *)
            echo "Unknown option: $arg"
            echo "Use --help for usage information."
            exit 1
            ;;
    esac
done

# Function to check and optionally enable User Workload Monitoring
check_user_workload_monitoring() {
    echo "🔍 Checking User Workload Monitoring status..."

    # Check if prometheus-user-workload pods exist
    POD_COUNT=$(kubectl get pods -n "$MONITORING_NAMESPACE" -l app.kubernetes.io/name=prometheus --no-headers 2>/dev/null | grep "prometheus-user-workload" | wc -l || echo "0")
    POD_COUNT=$(echo "$POD_COUNT" | tr -d ' \n')

    if [[ "$POD_COUNT" -gt 0 ]]; then
        echo "   ✅ User Workload Monitoring is enabled ($POD_COUNT prometheus-user-workload pod(s) running)"
        echo ""
        return 0
    fi

    echo "   ⚠️  User Workload Monitoring is NOT enabled"
    echo ""

    if [[ "$ENABLE_MONITORING" == "true" ]]; then
        echo "📦 Enabling User Workload Monitoring..."

        # Create cluster-monitoring-config ConfigMap
        cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: cluster-monitoring-config
  namespace: $MONITORING_CONFIG_NAMESPACE
data:
  config.yaml: |
    enableUserWorkload: true
EOF

        echo "   ✅ Created cluster-monitoring-config ConfigMap"
        echo ""
        echo "⏳ Waiting for prometheus-user-workload pods to start (up to 120 seconds)..."

        # Wait for pods to be ready
        local wait_time=0
        local max_wait=120
        while [[ $wait_time -lt $max_wait ]]; do
            POD_COUNT=$(kubectl get pods -n "$MONITORING_NAMESPACE" -l app.kubernetes.io/name=prometheus --no-headers 2>/dev/null | grep "prometheus-user-workload" | wc -l || echo "0")
            POD_COUNT=$(echo "$POD_COUNT" | tr -d ' \n')
            if [[ "$POD_COUNT" -gt 0 ]]; then
                # Check if pods are ready
                READY_COUNT=$(kubectl get pods -n "$MONITORING_NAMESPACE" -l app.kubernetes.io/name=prometheus --no-headers 2>/dev/null | grep "prometheus-user-workload" | grep "Running" | wc -l || echo "0")
                READY_COUNT=$(echo "$READY_COUNT" | tr -d ' \n')
                if [[ "$READY_COUNT" -gt 0 ]]; then
                    echo "   ✅ User Workload Monitoring is now enabled ($READY_COUNT pod(s) running)"
                    echo ""
                    return 0
                fi
            fi
            sleep 5
            wait_time=$((wait_time + 5))
            echo "   Waiting... ($wait_time/$max_wait seconds)"
        done

        echo "   ⚠️  Pods did not become ready within $max_wait seconds"
        echo "   You can check status with: kubectl get pods -n $MONITORING_NAMESPACE"
        echo ""
        return 1
    else
        echo "   User Workload Monitoring is required for ROS metrics collection."
        echo "   Without it, ServiceMonitors are created but no metrics are scraped."
        echo ""
        echo "   To enable, either:"
        echo "     1. Re-run this script with --enable-monitoring flag:"
        echo "        $0 --enable-monitoring"
        echo ""
        echo "     2. Or manually apply:"
        echo "        cat <<EOF | kubectl apply -f -"
        echo "        apiVersion: v1"
        echo "        kind: ConfigMap"
        echo "        metadata:"
        echo "          name: cluster-monitoring-config"
        echo "          namespace: $MONITORING_CONFIG_NAMESPACE"
        echo "        data:"
        echo "          config.yaml: |"
        echo "            enableUserWorkload: true"
        echo "        EOF"
        echo ""
        echo "   See docs/operations/installation.md for more details."
        echo ""
        echo "   Continuing anyway, but metrics may not be available..."
        echo ""
        return 0  # Don't fail, just warn
    fi
}

echo "🚀 Force Packaging & Upload to Ingress"
echo "========================================"
echo ""

# Check User Workload Monitoring status
check_user_workload_monitoring

# Check if metrics were collected recently
echo "📊 Checking last metrics collection time..."
LAST_QUERY=$(kubectl get costmanagementmetricsconfig -n "$NAMESPACE" "$CONFIG_NAME" \
  -o jsonpath='{.status.prometheus.last_query_success_time}')
echo "   Last collection: $LAST_QUERY"
echo ""

# Step 1: Manipulate packaging timestamp to bypass 360-minute timer
echo "⏰ Step 1: Resetting packaging timestamp..."
kubectl patch costmanagementmetricsconfig -n "$NAMESPACE" "$CONFIG_NAME" \
  --type='json' \
  -p='[{"op": "replace", "path": "/status/packaging/last_successful_packaging_time", "value": "2020-01-01T00:00:00Z"}]' \
  --subresource=status
echo "   ✅ Timestamp reset to 2020-01-01 (forces repackaging)"
echo ""

# Step 2: Trigger reconciliation with force-collection annotation
echo "🔄 Step 2: Triggering operator reconciliation..."
kubectl annotate -n "$NAMESPACE" costmanagementmetricsconfig "$CONFIG_NAME" \
  clusterconfig.openshift.io/force-collection="$(date +%s)" --overwrite
echo "   ✅ Reconciliation triggered"
echo ""

# Wait for operator to process
echo "⏳ Waiting 60 seconds for operator to package and upload..."
sleep 60
echo ""

# Check upload status
echo "📋 Checking upload status..."
UPLOAD_STATUS=$(kubectl get costmanagementmetricsconfig -n "$NAMESPACE" "$CONFIG_NAME" \
  -o jsonpath='{.status.upload.last_upload_status}' 2>/dev/null || echo "Unknown")
LAST_UPLOAD_TIME=$(kubectl get costmanagementmetricsconfig -n "$NAMESPACE" "$CONFIG_NAME" \
  -o jsonpath='{.status.upload.last_successful_upload_time}' 2>/dev/null || echo "Unknown")

echo "   Last upload status: $UPLOAD_STATUS"
echo "   Last successful upload: $LAST_UPLOAD_TIME"
echo ""

if [[ "$UPLOAD_STATUS" == *"202"* ]]; then
  echo "✅ SUCCESS! Operator successfully packaged and uploaded metrics."
  echo ""
  echo "Next steps:"
  echo "  1. Check ingress logs: kubectl logs -n cost-onprem -l app.kubernetes.io/component=ingress -c ingress --tail=50"
  echo "  2. Check processor logs: kubectl logs -n cost-onprem -l app.kubernetes.io/component=ros-processor --tail=50"
  echo "  3. Check Kruize logs: kubectl logs -n cost-onprem -l app.kubernetes.io/component=ros-optimization --tail=50"
else
  echo "⚠️  Upload may have failed or is still in progress."
  echo ""
  echo "Check operator logs for details:"
  echo "  kubectl logs -n $NAMESPACE -l app.kubernetes.io/name=costmanagement-metrics-operator --tail=100"
fi
echo ""
echo "════════════════════════════════════════════════════════"
