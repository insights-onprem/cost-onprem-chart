#!/bin/bash

# Performance Metrics Collection Script
# Captures Prometheus metrics snapshots during performance tests and exports to JSON/S3.
#
# FLPATH-4061: Metrics collection for performance testing
#
# This script queries Prometheus for key metrics and saves them to JSON files,
# optionally uploading to S3-compatible storage for historical analysis.
#
# Usage:
#   ./collect-metrics.sh                    # Snapshot all metrics to JSON
#   ./collect-metrics.sh --upload           # Snapshot and upload to S3
#   ./collect-metrics.sh --continuous 30    # Collect every 30 seconds
#
# Environment Variables:
#   NAMESPACE          - Target namespace (default: cost-onprem)
#   PROMETHEUS_URL     - Prometheus/Thanos URL (auto-detected if not set)
#   OUTPUT_DIR         - Output directory (default: ./metrics-snapshots)
#   S3_BUCKET            - S3 bucket for uploads (required for --upload)
#   S3_PREFIX            - S3 key prefix (default: cost-onprem-performance/)
#   S3_ENDPOINT          - S3 endpoint URL (for non-AWS S3-compatible storage)
#   S3_NO_VERIFY_SSL     - Skip TLS verification (default: true — MinIO uses untrusted certs)
#   S3_NO_SIGN_REQUEST   - Use anonymous access  (default: true — bucket is public, no creds)
#
# Note: The perf-results bucket (eco-bucket-perf-scale) is public/anonymous.
# No AWS credentials are needed. S3 variables are intentionally not defaulted
# to prevent accidental uploads during local development. Set S3_BUCKET
# explicitly to enable uploads.
#   PERF_PROFILE       - Performance profile name (default: baseline)
#   HELM_RELEASE_NAME  - Helm release name for version detection (default: cost-onprem)
#   TEST_RUN_ID        - Test run identifier (default: {chart_version}-{perf_profile}-{epoch})
#
# Examples:
#   # Single snapshot
#   ./collect-metrics.sh
#
#   # Continuous collection during test run
#   TEST_RUN_ID=perf-baseline-001 ./collect-metrics.sh --continuous 60
#
#   # Upload to S3-compatible storage (MinIO, Ceph, etc.)
#   S3_BUCKET=perf-metrics S3_ENDPOINT=https://minio.example.com ./collect-metrics.sh --upload

set -euo pipefail

# Cleanup background processes on exit
PORTFORWARD_PID=""
cleanup_on_exit() {
    if [[ -n "$PORTFORWARD_PID" ]]; then
        kill "$PORTFORWARD_PID" 2>/dev/null || true
    fi
}
trap cleanup_on_exit EXIT

# Configuration
NAMESPACE=${NAMESPACE:-cost-onprem}
PROMETHEUS_URL=${PROMETHEUS_URL:-}
OUTPUT_DIR=${OUTPUT_DIR:-./metrics-snapshots}
S3_BUCKET=${S3_BUCKET:-}
S3_PREFIX=${S3_PREFIX:-cost-onprem-performance/}
S3_ENDPOINT=${S3_ENDPOINT:-}
PERF_PROFILE=${PERF_PROFILE:-baseline}
HELM_RELEASE_NAME=${HELM_RELEASE_NAME:-cost-onprem}

# TEST_RUN_ID format: {chart_version}-{perf_profile}-{epoch_time}
# Will be auto-generated if not provided
TEST_RUN_ID=${TEST_RUN_ID:-}

# When METRICS_FLAT_OUTPUT=true, write directly to OUTPUT_DIR without adding TEST_RUN_ID subdirectory.
# Used when called from deploy-test-cost-onprem.sh which sets up the unified directory structure.
METRICS_FLAT_OUTPUT=${METRICS_FLAT_OUTPUT:-false}

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }

# Generate TEST_RUN_ID in format: {chart_version}-{perf_profile}-{epoch_time}
generate_test_run_id() {
    if [[ -n "$TEST_RUN_ID" ]]; then
        log_info "Using provided TEST_RUN_ID: $TEST_RUN_ID"
        return 0
    fi
    
    local chart_version="unknown"
    local epoch_time
    epoch_time=$(date +%s)
    
    # Try to get chart version from Helm release
    if command -v helm &>/dev/null; then
        local helm_chart
        helm_chart=$(helm list -n "$NAMESPACE" -o json 2>/dev/null | jq -r ".[0].chart // empty" 2>/dev/null)
        if [[ -n "$helm_chart" ]]; then
            # Extract version from chart name (e.g., "cost-onprem-0.2.20" -> "0.2.20")
            chart_version=$(echo "$helm_chart" | sed 's/.*-\([0-9][0-9.]*\)/\1/' | sed 's/[^0-9.]//g')
            if [[ -z "$chart_version" ]]; then
                chart_version="$helm_chart"
            fi
        fi
    fi
    
    # Fallback: try to get from deployment labels
    if [[ "$chart_version" == "unknown" ]] && command -v kubectl &>/dev/null; then
        chart_version=$(kubectl get deployment -n "$NAMESPACE" -l "app.kubernetes.io/name=cost-onprem" \
            -o jsonpath='{.items[0].metadata.labels.helm\.sh/chart}' 2>/dev/null | sed 's/cost-onprem-//' || echo "unknown")
    fi
    
    # Sanitize chart version (replace dots with dashes for S3 compatibility)
    chart_version=$(echo "$chart_version" | tr '.' '-')
    
    TEST_RUN_ID="${chart_version}-${PERF_PROFILE}-${epoch_time}"
    log_info "Generated TEST_RUN_ID: $TEST_RUN_ID"
}

# Get the metrics output directory
# In flat mode (when called from deploy-test-cost-onprem.sh), use OUTPUT_DIR directly
# In default mode (standalone), add TEST_RUN_ID subdirectory
get_metrics_output_dir() {
    if [[ "${METRICS_FLAT_OUTPUT}" == "true" ]]; then
        echo "${OUTPUT_DIR}"
    else
        echo "${OUTPUT_DIR}/${TEST_RUN_ID}"
    fi
}

# Auto-detect Prometheus URL
detect_prometheus_url() {
    if [[ -n "$PROMETHEUS_URL" ]]; then
        log_info "Using provided Prometheus URL: $PROMETHEUS_URL"
        return 0
    fi
    
    # Try Thanos Querier (OpenShift user workload monitoring)
    if oc get route thanos-querier -n openshift-monitoring &>/dev/null; then
        PROMETHEUS_URL="https://$(oc get route thanos-querier -n openshift-monitoring -o jsonpath='{.spec.host}')"
        log_info "Detected Thanos Querier: $PROMETHEUS_URL"
        return 0
    fi
    
    # Fallback to port-forward approach
    log_warning "No external Prometheus route found. Using port-forward."
    PROMETHEUS_URL="http://localhost:9091"
    
    # Start port-forward in background
    oc port-forward -n openshift-monitoring svc/thanos-querier 9091:9091 &>/dev/null &
    PORTFORWARD_PID=$!
    sleep 2
    
    # Verify connection
    if ! curl -s "$PROMETHEUS_URL/-/ready" &>/dev/null; then
        log_error "Cannot connect to Prometheus at $PROMETHEUS_URL"
        kill $PORTFORWARD_PID 2>/dev/null || true
        exit 1
    fi
}

# Get auth token for Prometheus
get_prometheus_token() {
    # For OpenShift, use service account token
    if [[ -f /var/run/secrets/kubernetes.io/serviceaccount/token ]]; then
        cat /var/run/secrets/kubernetes.io/serviceaccount/token
    else
        oc whoami -t 2>/dev/null || echo ""
    fi
}

# Query Prometheus and return JSON
query_prometheus() {
    local query="$1"
    local token
    token=$(get_prometheus_token)
    
    curl -s -k \
        ${token:+-H "Authorization: Bearer $token"} \
        --data-urlencode "query=$query" \
        "$PROMETHEUS_URL/api/v1/query" 2>/dev/null
}

# Query range for time-series data
query_prometheus_range() {
    local query="$1"
    local start="$2"
    local end="$3"
    local step="${4:-15s}"
    local token
    token=$(get_prometheus_token)
    
    curl -s -k \
        ${token:+-H "Authorization: Bearer $token"} \
        --data-urlencode "query=$query" \
        --data-urlencode "start=$start" \
        --data-urlencode "end=$end" \
        --data-urlencode "step=$step" \
        "$PROMETHEUS_URL/api/v1/query_range" 2>/dev/null
}

# Define metrics to collect
# Metric names and their PromQL queries — parallel arrays for bash 3.2 compatibility.
# (bash 3.2 is the default on macOS; declare -A requires bash 4+)
METRIC_NAMES=(
    # API metrics
    api_request_rate
    api_latency_p50
    api_latency_p95
    api_latency_p99
    api_error_rate
    # Celery/Processing metrics
    # Note: celery_queue_length is unreliable with Redis/Valkey due to worker
    # prefetch (tasks are pulled from broker immediately, so LLEN is always 0).
    # Instead we track active tasks, throughput, and task runtime.
    celery_tasks_active
    celery_task_rate
    celery_task_success_rate
    celery_task_failure_rate
    celery_task_duration_p95
    celery_workers_up
    celery_active_process_count
    # Database metrics
    pg_connections_active
    pg_connections_max
    pg_cache_hit_rate
    pg_database_size_bytes
    pg_locks_exclusive
    # Valkey/Redis metrics
    valkey_memory_used_bytes
    valkey_connected_clients
    valkey_commands_per_sec
    valkey_hit_rate
    valkey_evictions
    # ROS/Kruize metrics
    kruize_heap_used_bytes
    kruize_experiments_total
    # Pod resource metrics (aggregated)
    pod_cpu_usage
    pod_memory_usage_bytes
    # Per-component resource metrics
    listener_cpu_cores
    listener_memory_mb
    celery_worker_cpu_cores
    celery_worker_memory_mb
    postgres_cpu_cores
    postgres_memory_mb
    # Ingress metrics
    ingress_upload_rate
    kafka_consumer_lag
)
METRIC_QUERIES=(
    "sum(rate(http_requests_total{namespace=\"${NAMESPACE}\", job=~\".*koku.*\"}[5m]))"
    "histogram_quantile(0.50, sum(rate(http_request_duration_seconds_bucket{namespace=\"${NAMESPACE}\", job=~\".*koku.*\"}[5m])) by (le))"
    "histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{namespace=\"${NAMESPACE}\", job=~\".*koku.*\"}[5m])) by (le))"
    "histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{namespace=\"${NAMESPACE}\", job=~\".*koku.*\"}[5m])) by (le))"
    "sum(rate(http_requests_total{namespace=\"${NAMESPACE}\", job=~\".*koku.*\", status=~\"5..\"}[5m]))"
    "sum(celery_worker_tasks_active{namespace=\"${NAMESPACE}\"})"
    "sum(rate(celery_task_received_total{namespace=\"${NAMESPACE}\"}[5m]))"
    "sum(rate(celery_task_succeeded_total{namespace=\"${NAMESPACE}\"}[5m]))"
    "sum(rate(celery_task_failed_total{namespace=\"${NAMESPACE}\"}[5m]))"
    "histogram_quantile(0.95, sum(rate(celery_task_runtime_bucket{namespace=\"${NAMESPACE}\"}[5m])) by (le))"
    "sum(celery_worker_up{namespace=\"${NAMESPACE}\"})"
    "sum(celery_active_process_count{namespace=\"${NAMESPACE}\"})"
    "sum(pg_stat_activity_count{namespace=\"${NAMESPACE}\"})"
    "max(pg_settings_max_connections{namespace=\"${NAMESPACE}\"})"
    "pg_stat_database_blks_hit{namespace=\"${NAMESPACE}\", datname=\"costonprem_koku\"} / (pg_stat_database_blks_hit{namespace=\"${NAMESPACE}\", datname=\"costonprem_koku\"} + pg_stat_database_blks_read{namespace=\"${NAMESPACE}\", datname=\"costonprem_koku\"} + 0.0001) * 100"
    "pg_database_size_bytes{namespace=\"${NAMESPACE}\", datname=\"costonprem_koku\"}"
    "sum(pg_locks_count{namespace=\"${NAMESPACE}\"})"
    "redis_memory_used_bytes{namespace=\"${NAMESPACE}\"}"
    "redis_connected_clients{namespace=\"${NAMESPACE}\"}"
    "rate(redis_commands_processed_total{namespace=\"${NAMESPACE}\"}[5m])"
    "redis_keyspace_hits_total{namespace=\"${NAMESPACE}\"} / (redis_keyspace_hits_total{namespace=\"${NAMESPACE}\"} + redis_keyspace_misses_total{namespace=\"${NAMESPACE}\"} + 0.0001) * 100"
    "increase(redis_evicted_keys_total{namespace=\"${NAMESPACE}\"}[5m])"
    "jvm_memory_used_bytes{namespace=\"${NAMESPACE}\", pod=~\".*kruize.*\", area=\"heap\"}"
    "kruize_experiments_total{namespace=\"${NAMESPACE}\"}"
    "sum(rate(container_cpu_usage_seconds_total{namespace=\"${NAMESPACE}\", container!=\"\", container!=\"POD\"}[5m])) by (pod)"
    "sum(container_memory_working_set_bytes{namespace=\"${NAMESPACE}\", container!=\"\", container!=\"POD\"}) by (pod)"
    "sum(rate(container_cpu_usage_seconds_total{namespace=\"${NAMESPACE}\", pod=~\".*listener.*|.*ingress.*|.*koku-api.*\", container!=\"\", container!=\"POD\"}[5m]))"
    "sum(container_memory_working_set_bytes{namespace=\"${NAMESPACE}\", pod=~\".*listener.*|.*ingress.*|.*koku-api.*\", container!=\"\", container!=\"POD\"}) / 1024 / 1024"
    "sum(rate(container_cpu_usage_seconds_total{namespace=\"${NAMESPACE}\", pod=~\".*worker.*|.*clowder-worker.*|.*celery.*\", container!=\"\", container!=\"POD\"}[5m]))"
    "sum(container_memory_working_set_bytes{namespace=\"${NAMESPACE}\", pod=~\".*worker.*|.*clowder-worker.*|.*celery.*\", container!=\"\", container!=\"POD\"}) / 1024 / 1024"
    "sum(rate(container_cpu_usage_seconds_total{namespace=\"${NAMESPACE}\", pod=~\".*postgres.*|.*database.*|.*db.*\", container!=\"\", container!=\"POD\"}[5m]))"
    "sum(container_memory_working_set_bytes{namespace=\"${NAMESPACE}\", pod=~\".*postgres.*|.*database.*|.*db.*\", container!=\"\", container!=\"POD\"}) / 1024 / 1024"
    "sum(rate(ingress_uploads_total{namespace=\"${NAMESPACE}\"}[5m]))"
    "sum(kafka_consumer_records_lag{namespace=\"${NAMESPACE}\"})"
)

# Collect all metrics and save to JSON
collect_metrics_snapshot() {
    local timestamp
    timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    local metrics_dir
    metrics_dir=$(get_metrics_output_dir)
    local snapshot_file="${metrics_dir}/snapshot_$(date +%Y%m%d_%H%M%S).json"
    
    mkdir -p "$(dirname "$snapshot_file")"
    
    log_info "Collecting metrics snapshot..."
    
    # Build JSON object
    local json_output='{'
    json_output+='"timestamp": "'$timestamp'",'
    json_output+='"test_run_id": "'$TEST_RUN_ID'",'
    json_output+='"namespace": "'$NAMESPACE'",'
    json_output+='"metrics": {'
    
    local first=true
    local i
    for i in "${!METRIC_NAMES[@]}"; do
        local metric_name="${METRIC_NAMES[$i]}"
        local query="${METRIC_QUERIES[$i]}"
        local result
        result=$(query_prometheus "$query")
        
        if [[ "$first" == "true" ]]; then
            first=false
        else
            json_output+=','
        fi
        
        # Extract value from Prometheus response
        local value
        value=$(echo "$result" | jq -r '.data.result[0].value[1] // .data.result // "null"' 2>/dev/null || echo "null")
        
        json_output+='"'$metric_name'": '$value
    done
    
    json_output+='}}'
    
    # Pretty-print and save
    echo "$json_output" | jq '.' > "$snapshot_file"
    
    log_success "Snapshot saved: $snapshot_file"
    echo "$snapshot_file"
}

# Collect metrics over a time range
collect_metrics_range() {
    local start_time="$1"
    local end_time="$2"
    local step="${3:-15s}"
    local metrics_dir
    metrics_dir=$(get_metrics_output_dir)
    local range_file="${metrics_dir}/range_${start_time}_${end_time}.json"
    
    mkdir -p "$(dirname "$range_file")"
    
    log_info "Collecting metrics range: $start_time to $end_time"
    
    local json_output='{'
    json_output+='"start_time": "'$start_time'",'
    json_output+='"end_time": "'$end_time'",'
    json_output+='"step": "'$step'",'
    json_output+='"test_run_id": "'$TEST_RUN_ID'",'
    json_output+='"namespace": "'$NAMESPACE'",'
    json_output+='"metrics": {'
    
    local first=true
    local i
    for i in "${!METRIC_NAMES[@]}"; do
        local metric_name="${METRIC_NAMES[$i]}"
        local query="${METRIC_QUERIES[$i]}"
        local result
        result=$(query_prometheus_range "$query" "$start_time" "$end_time" "$step")
        
        if [[ "$first" == "true" ]]; then
            first=false
        else
            json_output+=','
        fi
        
        # Include full time-series data
        local values
        values=$(echo "$result" | jq '.data.result' 2>/dev/null || echo "null")
        
        json_output+='"'$metric_name'": '$values
    done
    
    json_output+='}}'
    
    echo "$json_output" | jq '.' > "$range_file"
    
    log_success "Range data saved: $range_file"
    echo "$range_file"
}

# Upload to S3-compatible storage
upload_to_s3() {
    local file="$1"
    local s3_key="${S3_PREFIX}${TEST_RUN_ID}/$(basename "$file")"
    
    if [[ -z "$S3_BUCKET" ]]; then
        log_error "S3_BUCKET not set. Cannot upload."
        return 1
    fi
    
    log_info "Uploading to s3://${S3_BUCKET}/${s3_key}..."
    
    local endpoint_arg=""
    if [[ -n "$S3_ENDPOINT" ]]; then
        endpoint_arg="--endpoint-url $S3_ENDPOINT"
    fi
    
    if command -v aws &>/dev/null; then
        # AWS CLI uses AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY env vars automatically
        aws s3 cp "$file" "s3://${S3_BUCKET}/${s3_key}" $endpoint_arg --no-progress
    elif command -v mc &>/dev/null; then
        # Configure MinIO client alias if credentials provided
        if [[ -n "${AWS_ACCESS_KEY_ID:-}" ]] && [[ -n "${AWS_SECRET_ACCESS_KEY:-}" ]]; then
            mc alias set s3upload "${S3_ENDPOINT:-https://s3.amazonaws.com}" \
                "$AWS_ACCESS_KEY_ID" "$AWS_SECRET_ACCESS_KEY" --api S3v4 &>/dev/null
            mc cp "$file" "s3upload/${S3_BUCKET}/${s3_key}"
        else
            mc cp "$file" "s3/${S3_BUCKET}/${s3_key}"
        fi
    elif command -v s3cmd &>/dev/null; then
        # s3cmd fallback
        local s3cmd_args=()
        [[ -n "$S3_ENDPOINT" ]] && s3cmd_args+=("--host=$S3_ENDPOINT" "--host-bucket=$S3_ENDPOINT/${S3_BUCKET}")
        [[ -n "${AWS_ACCESS_KEY_ID:-}" ]] && s3cmd_args+=("--access_key=$AWS_ACCESS_KEY_ID")
        [[ -n "${AWS_SECRET_ACCESS_KEY:-}" ]] && s3cmd_args+=("--secret_key=$AWS_SECRET_ACCESS_KEY")
        s3cmd put "$file" "s3://${S3_BUCKET}/${s3_key}" "${s3cmd_args[@]}"
    else
        log_error "No S3 client found (aws, mc, or s3cmd). Install one of them."
        return 1
    fi
    
    log_success "Uploaded: s3://${S3_BUCKET}/${s3_key}"
}

# Upload all files in test run directory
upload_test_run() {
    local test_dir
    test_dir=$(get_metrics_output_dir)
    
    if [[ ! -d "$test_dir" ]]; then
        log_error "Test run directory not found: $test_dir"
        return 1
    fi
    
    log_info "Uploading test run $TEST_RUN_ID to S3..."
    
    for file in "$test_dir"/*.json; do
        if [[ -f "$file" ]]; then
            upload_to_s3 "$file"
        fi
    done
    
    # Create and upload summary
    create_test_summary
    upload_to_s3 "${test_dir}/summary.json"
}

# Create test run summary
create_test_summary() {
    local test_dir
    test_dir=$(get_metrics_output_dir)
    local summary_file="${test_dir}/summary.json"
    
    local snapshot_count
    snapshot_count=$(find "$test_dir" -name "snapshot_*.json" | wc -l)
    
    local first_snapshot
    first_snapshot=$(find "$test_dir" -name "snapshot_*.json" | sort | head -1)
    
    local last_snapshot
    last_snapshot=$(find "$test_dir" -name "snapshot_*.json" | sort | tail -1)
    
    local start_time=""
    local end_time=""
    
    if [[ -f "$first_snapshot" ]]; then
        start_time=$(jq -r '.timestamp' "$first_snapshot")
    fi
    if [[ -f "$last_snapshot" ]]; then
        end_time=$(jq -r '.timestamp' "$last_snapshot")
    fi
    
    cat > "$summary_file" <<EOF
{
  "test_run_id": "${TEST_RUN_ID}",
  "namespace": "${NAMESPACE}",
  "start_time": "${start_time}",
  "end_time": "${end_time}",
  "snapshot_count": ${snapshot_count},
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "files": $(find "$test_dir" -name "*.json" -exec basename {} \; | jq -R -s -c 'split("\n") | map(select(length > 0))')
}
EOF
    
    log_success "Summary created: $summary_file"
}

# Continuous collection mode
continuous_collection() {
    local interval="$1"
    local max_duration="${2:-14400}"
    local start_time
    start_time=$(date +%s)

    log_info "Starting continuous collection (interval: ${interval}s, max duration: ${max_duration}s)"
    log_info "Test run ID: $TEST_RUN_ID"
    log_info "Press Ctrl+C to stop and upload results"

    trap 'log_info "Stopping collection..."; create_test_summary; exit 0' INT TERM

    while true; do
        local elapsed=$(( $(date +%s) - start_time ))
        if [[ ${elapsed} -ge ${max_duration} ]]; then
            log_warning "Max duration reached (${max_duration}s), stopping collection"
            create_test_summary
            return 0
        fi
        collect_metrics_snapshot
        sleep "$interval"
    done
}

# Main
main() {
    local mode="snapshot"
    local interval=30
    local max_duration=14400
    local upload=false
    
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --continuous)
                mode="continuous"
                interval="${2:-30}"
                shift 2
                ;;
            --max-duration)
                max_duration="${2:?--max-duration requires a value in seconds}"
                shift 2
                ;;
            --range)
                mode="range"
                shift
                ;;
            --upload)
                upload=true
                shift
                ;;
            --start)
                START_TIME="$2"
                shift 2
                ;;
            --end)
                END_TIME="$2"
                shift 2
                ;;
            --help|-h)
                echo "Usage: $0 [OPTIONS]"
                echo ""
                echo "Options:"
                echo "  --continuous [INTERVAL]  Continuous collection mode (default: 30s)"
                echo "  --max-duration SECONDS   Max duration for continuous mode (default: 14400 = 4h)"
                echo "  --range                  Collect range data (requires --start, --end)"
                echo "  --start TIME             Start time for range query (RFC3339)"
                echo "  --end TIME               End time for range query (RFC3339)"
                echo "  --upload                 Upload results to S3 after collection"
                echo "  --help                   Show this help"
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                exit 1
                ;;
        esac
    done
    
    # Setup
    detect_prometheus_url
    generate_test_run_id
    
    local metrics_dir
    metrics_dir=$(get_metrics_output_dir)
    mkdir -p "$metrics_dir"
    
    log_info "Test run configuration:"
    log_info "  TEST_RUN_ID: $TEST_RUN_ID"
    log_info "  NAMESPACE: $NAMESPACE"
    log_info "  PERF_PROFILE: $PERF_PROFILE"
    log_info "  OUTPUT_DIR: $metrics_dir/"
    [[ -n "$S3_BUCKET" ]] && log_info "  S3 Target: s3://$S3_BUCKET/$S3_PREFIX$TEST_RUN_ID/"
    
    # Execute based on mode
    case "$mode" in
        snapshot)
            collect_metrics_snapshot
            if [[ "$upload" == "true" ]]; then
                create_test_summary
                upload_test_run
            fi
            ;;
        continuous)
            continuous_collection "$interval" "$max_duration"
            if [[ "$upload" == "true" ]]; then
                upload_test_run
            fi
            ;;
        range)
            if [[ -z "$START_TIME" || -z "$END_TIME" ]]; then
                log_error "Range mode requires --start and --end times"
                exit 1
            fi
            collect_metrics_range "$START_TIME" "$END_TIME"
            if [[ "$upload" == "true" ]]; then
                create_test_summary
                upload_test_run
            fi
            ;;
    esac
    
    # Port-forward cleanup handled by EXIT trap
}

main "$@"
