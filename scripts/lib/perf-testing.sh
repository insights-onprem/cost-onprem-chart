#!/usr/bin/env bash
# scripts/lib/perf-testing.sh — Performance test orchestration and per-profile config
#
# Requires: log_info, log_success, log_warning, log_error, log_step,
#           log_verbose (from parent script)
# Requires: parse_cpu_to_millicores, calculate_max_listener_cpu,
#           validate_cpu_limit, set_listener_cpu (from lib/listener-cpu.sh)
# Requires: start_metrics_collection, stop_metrics_collection,
#           upload_perf_results_to_s3, generate_metadata_json (from lib/perf-observability.sh)
# Globals:  NAMESPACE, HELM_RELEASE_NAME, DRY_RUN, PERF_PROFILE, PERF_SUITE,
#           VERBOSE, LISTENER_CPU_LIMIT, CPU_BOOST_APPLIED,
#           ORIGINAL_LISTENER_CPU_LIMIT, MAX_LISTENER_CPU,
#           TEST_RUN_ID, PERF_OUTPUT_DIR, LOCAL_SCRIPTS_DIR,
#           USE_LOCAL_CHART, PROJECT_ROOT, SKIP_GRAFANA_LINKS,
#           GRAFANA_URL, GRAFANA_USER, GRAFANA_PASSWORD, GRAFANA_NAMESPACE

[[ -n "${_PERF_TESTING_SOURCED:-}" ]] && return 0
_PERF_TESTING_SOURCED=1

################################################################################
# Per-Profile Cluster Configuration
################################################################################

# apply_perf_profile_config: Brings the live cluster into the correct state for
# the given PERF_PROFILE before tests run.  Called unconditionally at the start
# of run_performance_tests() — whether this is a fresh deploy or --skip-deploy.
#
# Two-phase approach:
#   Phase 1 — helm upgrade --reuse-values --set …
#     Applies all values.yaml-driven settings that differ from chart defaults:
#       · resource limits (cpu/memory) for kruize, ros-processor, listener
#       · ingress max upload size
#       · HAProxy route timeout (via annotation override)
#       · Envoy ingress route timeouts (via templated values)
#     This issues a single Helm release revision so changes are tracked.
#
#   Phase 2 — oc scale
#     Replica counts are set directly (idempotent, faster than helm upgrade).
#     Kruize is always kept at replicas=1 (scaling degrades throughput,
#     see PERF-FINDING-014).
#
# Profile matrix:
#   baseline/small : replicas=1, chart resource defaults
#   medium         : replicas=2; raised resources, 200MB upload, 180s timeouts
#   large          : replicas=3; raised resources, 500MB upload, 600s timeouts
#   xlarge         : replicas=3; higher worker CPU (1000m/2000m) for tag processing
apply_perf_profile_config() {
    local release="${HELM_RELEASE_NAME:-cost-onprem}"
    local namespace="${NAMESPACE:-cost-onprem}"

    local ros_processor_replicas=1
    local listener_replicas=1
    local ocp_worker_replicas=1
    local summary_worker_replicas=1

    local kruize_cpu_req="500m"   kruize_cpu_lim="1000m"
    local ros_mem_req="1Gi"       ros_mem_lim="1Gi"
    local listener_mem_req="300Mi" listener_mem_lim="600Mi"
    local ocp_worker_cpu_req="250m"  ocp_worker_cpu_lim="500m"
    local ocp_worker_mem_req="512Mi" ocp_worker_mem_lim="1Gi"
    local summary_worker_cpu_req="250m" summary_worker_cpu_lim="500m"
    local max_upload_size="104857600"   # 100MB chart default
    local max_upload_mem="33554432"    # 32MB chart default (in-memory buffer before spilling to disk)
    local app_mem_req="1Gi"  app_mem_lim="1Gi"   # resources.application (ingress pod + others)
    local haproxy_timeout="30s"
    local ingress_timeout="30s"   ingress_per_try_timeout="30s"

    case "${PERF_PROFILE}" in
        small)
            # Small customer profile (1 cluster, 15 nodes, up to 10 concurrent sources).
            # Default single-replica pipeline can't drain 5+ sources in time.
            listener_replicas=2
            ocp_worker_replicas=2
            summary_worker_replicas=2
            # Keep Kruize CPU request at default so it always schedules;
            # raise limit so it can burst under ROS load (PERF-FINDING-014).
            kruize_cpu_lim="2000m"
            ;;
        medium)
            ros_processor_replicas=2
            listener_replicas=2
            ocp_worker_replicas=2
            summary_worker_replicas=2
            kruize_cpu_lim="2000m"
            ros_mem_req="2Gi";          ros_mem_lim="4Gi"
            listener_mem_req="1Gi";     listener_mem_lim="2Gi"
            ocp_worker_cpu_req="250m";  ocp_worker_cpu_lim="1000m"
            ocp_worker_mem_req="512Mi"; ocp_worker_mem_lim="2Gi"
            summary_worker_cpu_req="250m"; summary_worker_cpu_lim="1000m"
            max_upload_size="209715200"   # 200MB
            max_upload_mem="67108864"     # 64MB — reduce disk spill for medium payloads
            app_mem_req="1Gi";          app_mem_lim="2Gi"   # PERF-FINDING-022: ingress OOM on large uploads
            haproxy_timeout="180s"
            ingress_timeout="180s";       ingress_per_try_timeout="180s"
            ;;
        large)
            ros_processor_replicas=3
            listener_replicas=3
            ocp_worker_replicas=3
            summary_worker_replicas=3
            kruize_cpu_lim="2000m"
            ros_mem_req="2Gi";          ros_mem_lim="4Gi"
            listener_mem_req="2Gi";     listener_mem_lim="4Gi"
            ocp_worker_cpu_req="500m";  ocp_worker_cpu_lim="1000m"
            ocp_worker_mem_req="1Gi";   ocp_worker_mem_lim="2Gi"
            summary_worker_cpu_req="500m"; summary_worker_cpu_lim="1000m"
            max_upload_size="524288000"   # 500MB
            max_upload_mem="134217728"    # 128MB — matches prior validated runs; larger values destabilize the pipeline
            app_mem_req="2Gi";          app_mem_lim="4Gi"   # PERF-FINDING-022: ingress OOM on large uploads
            haproxy_timeout="600s"
            ingress_timeout="600s";       ingress_per_try_timeout="300s"
            ;;
        xlarge)
            # 3.3x data volume vs large (23 clusters, 346 nodes, 6954 cores)
            # Same replica/resource config as large — cluster headroom is the constraint.
            # Worker CPU raised to 1000m/2000m to handle tag-based cost model processing.
            ros_processor_replicas=3
            listener_replicas=3
            ocp_worker_replicas=3
            summary_worker_replicas=3
            kruize_cpu_req="1000m";     kruize_cpu_lim="2000m"
            ros_mem_req="2Gi";          ros_mem_lim="4Gi"
            listener_mem_req="2Gi";     listener_mem_lim="4Gi"
            ocp_worker_cpu_req="1000m"; ocp_worker_cpu_lim="2000m"
            ocp_worker_mem_req="2Gi";   ocp_worker_mem_lim="4Gi"
            summary_worker_cpu_req="1000m"; summary_worker_cpu_lim="2000m"
            max_upload_size="524288000"   # 500MB
            max_upload_mem="134217728"    # 128MB
            app_mem_req="2Gi";          app_mem_lim="4Gi"
            haproxy_timeout="600s"
            ingress_timeout="600s";       ingress_per_try_timeout="300s"
            ;;
        baseline|*)
            ;;
    esac

    log_step "Applying per-profile cluster config for profile: ${PERF_PROFILE}"
    log_info "  replicas (processor/listener/ocp/summary) = ${ros_processor_replicas}/${listener_replicas}/${ocp_worker_replicas}/${summary_worker_replicas}"
    log_info "  kruize replicas                           = 1 (always)"
    log_info "  kruize cpu req/lim                        = ${kruize_cpu_req}/${kruize_cpu_lim}"
    log_info "  ros-processor memory req/lim              = ${ros_mem_req}/${ros_mem_lim}"
    log_info "  listener memory req/lim                   = ${listener_mem_req}/${listener_mem_lim}"
    log_info "  ocp worker cpu req/lim                    = ${ocp_worker_cpu_req}/${ocp_worker_cpu_lim}"
    log_info "  ocp worker memory req/lim                = ${ocp_worker_mem_req}/${ocp_worker_mem_lim}"
    log_info "  summary worker cpu req/lim               = ${summary_worker_cpu_req}/${summary_worker_cpu_lim}"
    log_info "  ingress max upload size                   = ${max_upload_size}"
    log_info "  ingress max upload memory                 = ${max_upload_mem}"
    log_info "  application memory req/lim (ingress pod)  = ${app_mem_req}/${app_mem_lim}"
    log_info "  haproxy/envoy ingress timeout             = ${haproxy_timeout}"

    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "DRY RUN: Would run helm upgrade --reuse-values --set <all overrides above>"
        log_info "DRY RUN: Would scale: processor=${ros_processor_replicas} listener=${listener_replicas} ocp=${ocp_worker_replicas} summary=${summary_worker_replicas} kruize=1"
        return 0
    fi

    # Phase 1: helm upgrade — apply resource/timeout/size overrides
    local chart_ref
    if [[ "${USE_LOCAL_CHART:-false}" == "true" ]]; then
        chart_ref="${PROJECT_ROOT}/cost-onprem"
    else
        chart_ref="cost-onprem-chart/cost-onprem"
    fi

    log_info "Applying resource/timeout overrides via helm upgrade..."
    if ! helm upgrade "${release}" "${chart_ref}" \
            --reuse-values \
            --no-hooks \
            --namespace "${namespace}" \
            --set "resources.kruize.requests.cpu=${kruize_cpu_req}" \
            --set "resources.kruize.limits.cpu=${kruize_cpu_lim}" \
            --set "resources.rosProcessor.requests.memory=${ros_mem_req}" \
            --set "resources.rosProcessor.limits.memory=${ros_mem_lim}" \
            --set "costManagement.listener.resources.requests.memory=${listener_mem_req}" \
            --set "costManagement.listener.resources.limits.memory=${listener_mem_lim}" \
            --set "costManagement.celery.workers.ocp.resources.requests.cpu=${ocp_worker_cpu_req}" \
            --set "costManagement.celery.workers.ocp.resources.limits.cpu=${ocp_worker_cpu_lim}" \
            --set "costManagement.celery.workers.ocp.resources.requests.memory=${ocp_worker_mem_req}" \
            --set "costManagement.celery.workers.ocp.resources.limits.memory=${ocp_worker_mem_lim}" \
            --set "costManagement.celery.workers.summary.resources.requests.cpu=${summary_worker_cpu_req}" \
            --set "costManagement.celery.workers.summary.resources.limits.cpu=${summary_worker_cpu_lim}" \
            --set "ingress.upload.maxUploadSize=${max_upload_size}" \
            --set "ingress.upload.maxMemory=${max_upload_mem}" \
            --set "resources.application.requests.memory=${app_mem_req}" \
            --set "resources.application.limits.memory=${app_mem_lim}" \
            --set "jwtAuth.envoy.ingressTimeout=${ingress_timeout}" \
            --set "jwtAuth.envoy.ingressPerTryTimeout=${ingress_per_try_timeout}" \
            --set "gatewayRoute.annotations.haproxy\\.router\\.openshift\\.io/timeout=${haproxy_timeout}" \
            --wait --timeout 5m 2>&1; then
        log_warning "helm upgrade for profile config failed — continuing with oc scale only; resource limits may not match profile"
    else
        log_success "Resource/timeout overrides applied"
    fi

    # Envoy reads its config at startup only — restart the gateway pod so the
    # ConfigMap changes (ingress timeout, per-try timeout) actually take effect.
    local gw_deploy="${release}-gateway"
    if oc rollout restart deployment "${gw_deploy}" -n "${namespace}" 2>/dev/null; then
        if oc rollout status deployment "${gw_deploy}" -n "${namespace}" --timeout=2m 2>/dev/null; then
            log_success "Gateway pod restarted (Envoy config reloaded)"
        else
            log_warning "Gateway rollout did not stabilize — Envoy may still use old timeouts"
        fi
    else
        log_warning "Could not restart gateway deployment — Envoy may still use old timeouts"
    fi

    # Phase 2: oc scale — replica counts (faster than helm upgrade)
    local scale_failed=false
    _scale_deploy() {
        local name="$1" replicas="$2"
        if oc scale deployment "${name}" --replicas="${replicas}" -n "${namespace}" 2>/dev/null; then
            log_info "  scaled ${name} → ${replicas}"
        else
            log_warning "  could not scale ${name} (may not exist yet)"
            scale_failed=true
        fi
    }

    _scale_deploy "${release}-ros-processor"          "${ros_processor_replicas}"
    _scale_deploy "${release}-koku-listener"          "${listener_replicas}"
    _scale_deploy "${release}-celery-worker-ocp"      "${ocp_worker_replicas}"
    _scale_deploy "${release}-celery-worker-summary"  "${summary_worker_replicas}"
    _scale_deploy "${release}-kruize"                 "1"

    if [[ "${scale_failed}" == "true" ]]; then
        log_warning "One or more deployments could not be scaled — verify cluster state before running tests"
        return 1
    fi

    log_info "Waiting for replica rollouts..."
    local rollout_ok=true
    for deploy in \
        "${release}-ros-processor" \
        "${release}-koku-listener" \
        "${release}-celery-worker-ocp" \
        "${release}-celery-worker-summary" \
        "${release}-kruize"; do
        if ! oc rollout status deployment "${deploy}" -n "${namespace}" --timeout=3m 2>/dev/null; then
            log_warning "  rollout timeout for ${deploy}"
            rollout_ok=false
        fi
    done
    [[ "${rollout_ok}" == "true" ]] && log_success "Rollouts complete" || log_warning "Some rollouts timed out"

    # Verify replica counts
    log_info "Verifying deployed replica counts..."
    local verified=true
    _verify_replicas() {
        local deploy_name="$1" expected="$2"
        local actual
        actual=$(oc get deployment "${deploy_name}" -n "${namespace}" \
                    -o jsonpath='{.spec.replicas}' 2>/dev/null)
        if [[ "${actual}" == "${expected}" ]]; then
            log_info "  ✓ ${deploy_name}: ${actual} replica(s)"
        else
            log_warning "  ✗ ${deploy_name}: expected ${expected}, got '${actual}'"
            verified=false
        fi
    }

    _verify_replicas "${release}-ros-processor"          "${ros_processor_replicas}"
    _verify_replicas "${release}-koku-listener"          "${listener_replicas}"
    _verify_replicas "${release}-celery-worker-ocp"      "${ocp_worker_replicas}"
    _verify_replicas "${release}-celery-worker-summary"  "${summary_worker_replicas}"
    _verify_replicas "${release}-kruize"                 "1"

    if [[ "${verified}" == "true" ]]; then
        log_success "All replica counts verified for ${PERF_PROFILE} profile"
    else
        log_warning "Some replica counts did not match — tests may not reflect expected scaling"
    fi

    # Verify Kruize pod actually has the expected CPU limit (catches scheduling
    # failures where the old ReplicaSet's pod keeps running with default resources)
    local actual_kruize_cpu_lim
    actual_kruize_cpu_lim=$(oc get pods -n "${namespace}" -l app.kubernetes.io/component=ros-optimization \
        --field-selector=status.phase=Running -o jsonpath='{.items[0].spec.containers[0].resources.limits.cpu}' 2>/dev/null)
    if [[ -n "${actual_kruize_cpu_lim}" ]]; then
        log_info "  Kruize running pod CPU limit: ${actual_kruize_cpu_lim} (expected: ${kruize_cpu_lim})"
        # Normalize both values to millicores for comparison (K8s returns "2" for "2000m")
        local actual_m expected_m
        if [[ "${actual_kruize_cpu_lim}" =~ ^[0-9]+$ ]]; then
            actual_m=$(( actual_kruize_cpu_lim * 1000 ))
        else
            actual_m="${actual_kruize_cpu_lim%m}"
        fi
        if [[ "${kruize_cpu_lim}" =~ ^[0-9]+$ ]]; then
            expected_m=$(( kruize_cpu_lim * 1000 ))
        else
            expected_m="${kruize_cpu_lim%m}"
        fi
        if [[ "${actual_m}" != "${expected_m}" ]]; then
            log_warning "  Kruize pod has stale CPU limit — cleaning up stuck ReplicaSet"
            # Delete any Pending Kruize pods (from failed scheduling)
            oc delete pods -n "${namespace}" -l app.kubernetes.io/component=ros-optimization \
                --field-selector=status.phase=Pending --grace-period=0 2>/dev/null || true
            # Scale stale ReplicaSets to 0
            for rs in $(oc get rs -n "${namespace}" -l app.kubernetes.io/component=ros-optimization \
                -o jsonpath='{range .items[?(@.status.readyReplicas==0)]}{.metadata.name}{"\n"}{end}' 2>/dev/null); do
                oc scale rs "${rs}" -n "${namespace}" --replicas=0 2>/dev/null || true
            done
            # Restart the deployment to pick up new resources
            oc rollout restart deployment "${release}-kruize" -n "${namespace}" 2>/dev/null || true
            if oc rollout status deployment "${release}-kruize" -n "${namespace}" --timeout=3m 2>/dev/null; then
                log_success "  Kruize restarted with correct CPU limit"
            else
                log_warning "  Kruize restart timed out — ROS tests may be slower than expected"
            fi
        fi
    fi
}

################################################################################
# Performance Test Execution (FLPATH-4036)
################################################################################

run_performance_tests() {
    log_step "Running performance tests (FLPATH-4036)"

    local pytest_script="${LOCAL_SCRIPTS_DIR}/run-pytest.sh"
    if [[ ! -x "${pytest_script}" ]]; then
        log_error "Pytest runner not found at: ${pytest_script}"
        return 1
    fi

    log_info "Running performance tests with profile: ${PERF_PROFILE}"

    export PERF_PROFILE="${PERF_PROFILE}"

    # Apply profile-specific replica scaling before tests run.
    apply_perf_profile_config

    # Listener CPU boost — always applied for perf tests unless explicitly disabled.
    # The listener is the principal processing bottleneck: at the chart default (300m)
    # it throttles every ingestion test, producing results that measure the CPU cap
    # rather than actual pipeline throughput.
    local perf_listener_cpu="${LISTENER_CPU_LIMIT:-max}"
    if [[ "${perf_listener_cpu}" != "none" ]] && [[ "${CPU_BOOST_APPLIED:-false}" != "true" ]]; then
        local effective_cpu_limit="${perf_listener_cpu}"
        if [[ "${perf_listener_cpu}" == "max" ]]; then
            calculate_max_listener_cpu
            effective_cpu_limit="${MAX_LISTENER_CPU}m"
            log_info "Listener CPU boost: calculated max = ${effective_cpu_limit}"
        fi
        if validate_cpu_limit "${effective_cpu_limit}"; then
            log_step "Boosting listener CPU to ${effective_cpu_limit} for performance tests"
            if set_listener_cpu "${effective_cpu_limit}"; then
                CPU_BOOST_APPLIED=true
                log_success "Listener CPU boosted to ${effective_cpu_limit} (was ${ORIGINAL_LISTENER_CPU_LIMIT})"
            else
                log_warning "Could not boost listener CPU — results may reflect the 300m throttle"
            fi
        fi
    elif [[ "${CPU_BOOST_APPLIED:-false}" == "true" ]]; then
        log_info "Listener CPU already boosted by run_tests() — skipping duplicate boost"
    else
        log_warning "Listener CPU boost disabled (--listener-cpu none) — results will reflect chart defaults"
    fi

    start_metrics_collection

    local perf_args=()
    if [[ "${PERF_SUITE}" == "all" ]]; then
        perf_args+=("--performance")
    else
        IFS=',' read -ra suites <<< "${PERF_SUITE}"
        for suite in "${suites[@]}"; do
            case "${suite}" in
                api)       perf_args+=("--perf-api") ;;
                ros)       perf_args+=("--perf-ros") ;;
                ingestion) perf_args+=("--perf-ingestion") ;;
                scale)     perf_args+=("--perf-scale") ;;
                soak)      perf_args+=("--perf-soak") ;;
            esac
        done
    fi
    if [[ "${VERBOSE}" == "true" ]]; then
        perf_args+=("-v")
    fi
    perf_args+=("-s")

    local output_info="tests/reports/performance/"
    if [[ -n "${TEST_RUN_ID:-}" ]]; then
        output_info="${PERF_OUTPUT_DIR}/${TEST_RUN_ID}/"
    fi

    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "DRY RUN: Would execute: ${pytest_script} ${perf_args[*]}"
        stop_metrics_collection
        return 0
    fi

    log_info "Performance test command: ${pytest_script} ${perf_args[*]}"
    log_info "Output directory: ${output_info}"

    local test_result=0
    if ! "${pytest_script}" "${perf_args[@]}"; then
        log_error "Performance tests failed"
        log_info "Check ${output_info} for detailed results"
        test_result=1
    else
        log_success "Performance tests completed"
        log_info "Results: ${output_info}"
    fi

    stop_metrics_collection

    # Generate reports BEFORE uploading so they're included in the S3 upload
    if [[ -n "${TEST_RUN_ID:-}" ]]; then
        local run_dir="${PERF_OUTPUT_DIR}/${TEST_RUN_ID}"
        local scripts_dir="$(dirname "${BASH_SOURCE[0]}")/../observability"

        generate_metadata_json

        local run_report_script="${scripts_dir}/generate-perf-run-report.py"
        local _report_python=""
        local _venv_py="$(dirname "${BASH_SOURCE[0]}")/../../tests/.venv/bin/python"
        if [[ -x "${_venv_py}" ]]; then
            _report_python="${_venv_py}"
        elif command -v python3 &>/dev/null; then
            _report_python="python3"
        fi
        if [[ -f "${run_report_script}" ]] && [[ -n "${_report_python}" ]]; then
            log_info "Generating visual run report..."
            if "${_report_python}" "${run_report_script}" --run-dir "${run_dir}"; then
                log_success "Visual report: ${run_dir}/reports/perf-run-report.html"
            else
                log_warning "Could not generate visual run report (non-fatal)"
            fi
        fi

        if [[ "${SKIP_GRAFANA_LINKS}" != "true" ]]; then
            local grafana_script="${scripts_dir}/push-grafana-snapshot.py"
            if [[ -f "${grafana_script}" ]] && command -v python3 &>/dev/null; then
                log_info "Linking Grafana dashboard (if cluster is up)..."
                if python3 "${grafana_script}" \
                    --run-dir "${run_dir}" \
                    ${GRAFANA_URL:+--grafana-url "${GRAFANA_URL}"} \
                    ${GRAFANA_USER:+--grafana-user "${GRAFANA_USER}"} \
                    ${GRAFANA_PASSWORD:+--grafana-pass "${GRAFANA_PASSWORD}"} \
                    --namespace "${GRAFANA_NAMESPACE:-grafana}" 2>/dev/null; then
                    local links_file="${run_dir}/reports/grafana-links.json"
                    if [[ -f "${links_file}" ]]; then
                        local snap_url
                        snap_url=$(python3 -c "import json; d=json.load(open('${links_file}')); print(d.get('snapshot_url',''))" 2>/dev/null)
                        [[ -n "${snap_url}" ]] && log_success "Grafana snapshot: ${snap_url}"
                    fi
                fi
            fi
        else
            log_info "Skipping Grafana links (SKIP_GRAFANA_LINKS=true)"
        fi
    fi

    # Create tarball of the full run directory for easy sharing
    if [[ -n "${TEST_RUN_ID:-}" ]]; then
        local run_dir="${PERF_OUTPUT_DIR}/${TEST_RUN_ID}"
        local tarball="${PERF_OUTPUT_DIR}/${TEST_RUN_ID}.tar.gz"
        log_info "Creating run archive..."
        if tar -czf "${tarball}" -C "${PERF_OUTPUT_DIR}" "${TEST_RUN_ID}" 2>/dev/null; then
            local tar_size
            tar_size=$(du -h "${tarball}" | cut -f1)
            log_success "Archive: ${tarball} (${tar_size})"
        else
            log_warning "Could not create tarball (non-fatal)"
        fi
    fi

    upload_perf_results_to_s3

    return ${test_result}
}

generate_metadata_json() {
    local metadata_file="${PERF_OUTPUT_DIR}/${TEST_RUN_ID}/metadata.json"

    log_info "Generating metadata.json..."

    local ocp_version="unknown"
    local node_count=0
    local storage_type="unknown"

    if command -v oc &>/dev/null; then
        ocp_version=$(oc get clusterversion version -o jsonpath='{.status.desired.version}' 2>/dev/null || echo "unknown")
        node_count=$(oc get nodes --no-headers 2>/dev/null | wc -l | tr -d ' ' || echo "0")

        if oc get storagecluster -n openshift-storage &>/dev/null; then
            storage_type="ODF"
        elif oc get storageclass s4-storage &>/dev/null; then
            storage_type="S4"
        fi
    fi

    local chart_version="unknown"
    if command -v helm &>/dev/null; then
        chart_version=$(helm list -n "${NAMESPACE}" -o json 2>/dev/null | jq -r ".[0].app_version // .[0].chart // \"unknown\"" 2>/dev/null || echo "unknown")
    fi

    local metrics_count=$(find "${PERF_OUTPUT_DIR}/${TEST_RUN_ID}/metrics" -name "*.json" 2>/dev/null | wc -l | tr -d ' ')
    local results_count=$(find "${PERF_OUTPUT_DIR}/${TEST_RUN_ID}/results" -name "*.json" 2>/dev/null | wc -l | tr -d ' ')
    local reports_count=$(find "${PERF_OUTPUT_DIR}/${TEST_RUN_ID}/reports" -type f 2>/dev/null | wc -l | tr -d ' ')

    cat > "${metadata_file}" <<EOF
{
  "test_run_id": "${TEST_RUN_ID}",
  "chart_version": "${chart_version}",
  "perf_profile": "${PERF_PROFILE}",
  "perf_suite": "${PERF_SUITE}",
  "listener_cpu_limit": "${LISTENER_CPU:-default}",
  "namespace": "${NAMESPACE}",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "cluster_info": {
    "ocp_version": "${ocp_version}",
    "node_count": ${node_count},
    "storage_type": "${storage_type}",
    "platform": "${CLUSTER_PLATFORM:-unknown}"
  },
  "file_counts": {
    "metrics": ${metrics_count},
    "results": ${results_count},
    "reports": ${reports_count}
  }
}
EOF

    log_success "Generated: ${metadata_file}"
}
