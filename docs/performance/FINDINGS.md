# Performance Testing Findings

Issues and sizing requirements discovered during performance testing
(FLPATH-4036, COST-7567). This document is a point-in-time record of what
was found and how it was addressed. Finding statuses are intentionally fixed
at the time of discovery — Jira tickets track ongoing resolution.

---

## Product Issues

### PERF-FINDING-001: Gateway Timeout Too Low for Large File Uploads

**Status**: Code Review  
**Severity**: Critical  
**Jira**: [FLPATH-4091](https://redhat.atlassian.net/browse/FLPATH-4091)

**Problem**:
The Envoy gateway and HAProxy route had 30s timeouts for the `/api/ingress/` route. Large file uploads (30+ days of data, ~48MB+) take 25-60+ seconds to transfer, causing HTTP 408/504 timeout errors even though ingress successfully processes the upload.

**Impact**:
- Customers with large clusters cannot upload more than ~2 weeks of data at once
- HTTP 504 errors returned to clients despite successful server-side processing

**Proposed Fix**:
1. HAProxy route timeout: 30s → 180s (`values.yaml`)
2. Envoy route timeout: 30s → 180s, per_try_timeout: 10s → 60s (`configmap-envoy.yaml`)

**Validation**: `ing_002[30-days]` passes with fix (68MB upload at 5.23 MB/s).
Applied automatically by `apply_perf_profile_config()` for medium+ profiles.

---

### PERF-FINDING-006: Kruize Pod Restarts Under Load — CPU Throttling

**Status**: Code Review  
**Severity**: Medium  
**Jira**: [FLPATH-4302](https://redhat.atlassian.net/browse/FLPATH-4302)

**Problem**:
Kruize pod restarts during moderate load (50 concurrent experiments) due to CPU throttling at the 1 core limit. Peak memory was only 786 MB (38% of 2Gi limit), ruling out OOM. CPU throttling causes slow liveness probe responses, triggering restarts.

**Proposed Fix**:
```yaml
resources:
  kruize:
    requests:
      cpu: "1000m"    # Was 500m
    limits:
      cpu: "2000m"    # Was 1000m
```

**Validation**: 0 restarts at 2 cores with the same workload.
Applied automatically by `apply_perf_profile_config()` for medium+ profiles.

---

### PERF-FINDING-011: Envoy `request_timeout` Kills Large Uploads

**Status**: Fixed in chart  
**Severity**: High

**Problem**: The Envoy connection manager had `request_timeout: 60s`, which caps total time to receive the full request body from the client. For payloads >~40 MB over WAN, body transfer exceeds 60s and Envoy returns 408 before the request reaches the ingress pod.

**Fix**: `request_timeout: 0s` (disabled; per-route timeouts govern each path). `stream_idle_timeout: 300s` remains as protection against hung connections.

---

### PERF-FINDING-013: ROS Processor FK Errors Block Kafka Queue

**Status**: Mitigated; upstream fix needed  
**Severity**: High  
**Jira**: [FLPATH-4428](https://redhat.atlassian.net/browse/FLPATH-4428) — related to [COST-7722](https://redhat.atlassian.net/browse/COST-7722) (ros-ocp-backend performance optimization)

**Problem**:
The ros-processor (Go) uses `kafkaAutoCommit: true` and does not advance the offset on FK errors. When a source is deleted while ROS events are still in Kafka, those events become permanent poison pills that block all subsequent events on that partition.

**Impact**:
Not a production issue today because sources are long-lived. However, it represents a resilience gap for any source lifecycle operation (bulk source removal, disaster recovery re-registration).

**Upstream fix needed**: Dead-letter handling — after N consecutive FK failures on the same event, commit the offset and log a warning rather than retrying forever. The ros-ocp-backend codebase (`ros-ocp-backend/processor/`) handles Kafka consumption in a single-threaded loop with `kafkaAutoCommit: true`. The consumer calls `commitSync()` only after successful DB writes. When an FK constraint violation occurs (e.g., referencing a deleted source), the offset is never advanced, and the same message is re-consumed indefinitely. A dead-letter topic or configurable retry limit (e.g., `MAX_FK_RETRIES=3`) would allow the consumer to skip poison-pill messages and continue processing the partition.

---

## Sizing Requirements

### PERF-FINDING-002: Listener CPU is the Primary Ingestion Bottleneck

**Status**: Documented — sizing recommendation  
**Severity**: High  
**Related Jira**: [COST-7618](https://redhat.atlassian.net/browse/COST-7618) (Sizing guide), [COST-6993](https://redhat.atlassian.net/browse/COST-6993) (Listener autoscaling)

**Problem**:
The chart default CPU limit (150m request / 300m limit) throttles all ingestion workloads. At 300m, the listener runs at 157% CPU (throttled) during medium-profile ingestion.

**Evidence**:

| Component | Peak CPU | CPU Limit | Utilization |
|-----------|---------|-----------|-------------|
| Listener | 471m | 300m | 157% (throttled) |
| Kruize | 185m | 2000m | 9% |
| Celery Workers | 368m | varies | low |
| Postgres | 327m | - | moderate |

**Impact**: ~40-50% slower ingestion compared to uncapped CPU.

**Recommendation**: Production deployments handling burst ingestion or large file uploads should raise the listener CPU limit to at least 1000m.

---

### PERF-FINDING-003: Pipeline Serialization Limits Concurrent Source Processing

**Status**: Documented — scaling recommendation  
**Severity**: Medium  
**Related Jira**: [COST-7598](https://redhat.atlassian.net/browse/COST-7598) (Pipeline analysis), [COST-6163](https://redhat.atlassian.net/browse/COST-6163) (Worker autoscaling)

**Problem**:
The default single-replica listener/worker configuration cannot drain concurrent source uploads within expected processing windows. Sources queue in Kafka and process serially.

**Recommended Configuration**:

| Concurrent Sources | listener | ocp-worker | summary-worker |
|--------------------|----------|------------|----------------|
| 1–3 | 1 | 1 | 1 |
| 4–10 | 2 | 2 | 2 |
| 10+ | 3 | 3 | 3 |

---

### PERF-FINDING-004: Kruize Experiment Creation Rate — CPU Was the Bottleneck

**Status**: Documented — throughput baseline updated  
**Severity**: Low (informational)  
**Related Jira**: [COST-7722](https://redhat.atlassian.net/browse/COST-7722) (ros-ocp-backend performance optimization)

**Problem** (original assessment):
Kruize created experiments at ~8 per minute at the default 500m/1000m CPU allocation, which was attributed to Hibernate connection pool limits (`c3p0maxsize=5`).

**Updated assessment**:
With the FLPATH-4302 CPU increase (1000m/2000m), Kruize throughput jumped to **31 exp/min** — a 4x improvement. This confirms the bottleneck was **CPU throttling**, not the connection pool. The original 1000m CPU limit caused liveness probe slowdowns that gated experiment creation throughput.

**Evidence**:

| Run | Profile | CPU req/lim | Experiments | Rate/min | Peak Memory | Restarts |
|-----|---------|-------------|-------------|----------|-------------|----------|
| 1781193941 | medium | 500m/1000m | 160/160 | 8.6 | 723 MB | 0 |
| 1782335628 | xlarge | 1000m/2000m | 600/600 | 31.0 | 415 MB | 0 |

**Recommendation**: Keep Kruize at 1 replica. The CPU increase from FLPATH-4302 is the primary lever for throughput — connection pool tuning is secondary.

---

### Resource Sizing — Under-Provisioned Defaults

| Component | Chart Default | Recommended | Evidence |
|-----------|-------------|-------------|----------|
| Kruize CPU | 500m/1000m | 1000m/2000m | FINDING-006: probe failures at 1 core |
| ROS processor memory | 1Gi/1Gi | 2Gi/4Gi | OOMKill during experiment processing |
| Listener memory | 300Mi/600Mi | 1Gi/2Gi (large: 2Gi/4Gi) | OOMKill during archive extraction |
| Ingress max upload | 100MB | 200MB (large: 500MB) | Hard limit; needs explicit change for large files |
| Ingress upload memory | 32MB | 64MB (large: 128MB) | FINDING-021: disk spill adds latency |
| Gateway timeouts | 30s | 180s (large: 600s) | FINDING-001, -020: 504 on medium/large uploads |
| Ingress pod memory | 1Gi/1Gi | 1Gi/2Gi (large: 2Gi/4Gi) | FINDING-022: OOM on large/concurrent uploads |
| OCP worker CPU | 250m/500m | 500m/1000m (large) | FINDING-025: 15% faster processing |
| Summary worker CPU | 250m/500m | 500m/1000m (large) | FINDING-025: 15% faster processing |
| OCP worker memory | 512Mi/1Gi | 1Gi/2Gi (large) | FINDING-025: headroom for large data sets |

---

### PERF-FINDING-020: Envoy Gateway ConfigMap Not Reloaded on Helm Upgrade

**Status**: Mitigated in perf scripts; chart fix needed  
**Severity**: Critical  
**Jira**: [FLPATH-4429](https://redhat.atlassian.net/browse/FLPATH-4429)

**Problem**:
Envoy reads its config file at startup only — it does not watch for changes. When `helm upgrade` modifies the gateway ConfigMap (e.g. increasing `ingressTimeout` from 30s to 600s), the running gateway pod continues using the old values. This rendered all timeout overrides from `apply_perf_profile_config()` ineffective, causing HTTP 504 failures on medium/large profile uploads despite correct values in the ConfigMap.

**Evidence**: Run `#37` — Envoy ConfigMap showed 600s timeouts, but uploads failed at ~30s (the old default). All ING-001[medium/large], ING-002, ING-003[10], ING-004 tests failed with instant 504/500 errors.

**Mitigation**: `perf-testing.sh` now explicitly runs `oc rollout restart` on the gateway deployment after helm upgrade.

**Recommended chart fix**: Add a `checksum/envoy-config` pod template annotation to the gateway deployment so any ConfigMap change triggers an automatic rolling restart during `helm upgrade`. This is the [standard Helm pattern](https://helm.sh/docs/howto/charts_tips_and_tricks/#automatically-roll-deployments) for static-config applications like Envoy.

**Why this matters for customers**: Any customer who adjusts gateway timeouts, route prefixes, or TLS settings via `helm upgrade` will silently run on stale Envoy config until the gateway pod is manually restarted. This is a silent misconfiguration that is difficult to diagnose — the ConfigMap shows the correct values, but the running process uses the old ones.

---

### PERF-FINDING-021: Ingress In-Memory Buffer Too Small for Large Uploads

**Status**: Fixed in perf profiles  
**Severity**: Medium

**Problem**:
The `INGRESS_MAXUPLOADMEM` default is 32 MB. For uploads >32 MB, insights-ingress-go spills to disk, adding latency. Combined with tight per-try timeouts, this can push uploads past the retry window.

**Fix**: `apply_perf_profile_config()` now sets `ingress.upload.maxMemory` per profile:
- medium: 64 MB
- large: 128 MB

---

### PERF-FINDING-022: Ingress Pod Memory Insufficient for Large/Concurrent Uploads

**Status**: Mitigated in perf profiles; chart fix needed  
**Severity**: High  
**Jira**: [FLPATH-4430](https://redhat.atlassian.net/browse/FLPATH-4430)

**Problem**:
The ingress pod (`insights-ingress-go`) uses the shared `resources.application` block (1Gi memory limit). Processing large uploads requires multipart parsing, tar extraction, and S3 staging — all memory-intensive. Uploads >100 MB or 10+ concurrent uploads cause HTTP 500 errors from the ingress pod due to memory exhaustion.

**Evidence** (Run `#38`, large profile):
- ING-004[100] (101 MB) passed but ING-004[50] failed immediately after — residual memory pressure
- ING-001[large] (~200+ MB package) — HTTP 500 on all attempts
- ING-003[10] (10 concurrent uploads) — all 10 uploads returned HTTP 500

Uploads up to 138 MB succeed individually (ING-002[90-days] = 138.74 MB at 2.44 MB/s).

**Mitigation**: `apply_perf_profile_config()` now overrides `resources.application` per profile:
- medium: 1Gi/2Gi
- large: 2Gi/4Gi

**Recommended chart fix**: Give ingress its own resource block in `values.yaml` (separate from the shared `resources.application`) so it can be sized independently for large file handling without affecting other services.

**Why this matters**: The `resources.application` block is shared across 8+ deployments (ingress, koku-api, koku-worker, koku-listener, koku-clowder, masu, etc.). Raising memory limits for ingress also raises them for every other service using the shared block, wasting cluster resources. Ingress has fundamentally different memory characteristics — it buffers entire upload payloads in memory during multipart parsing and S3 staging, whereas most other services have modest steady-state memory needs. An independent `resources.ingress` block in `values.yaml` would allow customers to size ingress for their expected upload volume without over-provisioning the rest of the stack.

---

### PERF-FINDING-024: Ingress Single-Part S3 Upload Fails for Large Payloads

**Status**: Product limitation; upstream enhancement needed  
**Severity**: Medium  
**Jira**: [FLPATH-4431](https://redhat.atlassian.net/browse/FLPATH-4431)

**Problem**:
`insights-ingress-go` uses the minio-go `PutObject()` API for S3 staging, which performs a single-part upload. Payloads exceeding ~150 MB consistently fail with HTTP 500 against NooBaa/Ceph RGW backends. The error originates in the S3 staging step, not in multipart form parsing or pod memory limits.

**Evidence**:
- ING-002[90-days] (138 MB) passes reliably across runs #40, #41
- ING-001[large] (~200+ MB) fails with HTTP 500 on every attempt (runs #40, #41, #42)
- ING-003[10] (10 concurrent small uploads) also fails — likely S3 connection pool exhaustion under concurrent staging

**What was tried**:
- Pod memory: `resources.application` increased to 2Gi/4Gi — no effect on the 500s (pod uses only ~49 Mi at idle, 0 restarts, 0 OOM events)
- `INGRESS_MAXUPLOADMEM`: Increased from 128 MB to 512 MB in run #42 — **made things worse**. Go's `ParseMultipartForm(512MB)` pre-allocates heap per request, and the oversized allocation destabilized the pipeline (ING-002[30-days], previously reliable, failed with "manifest not yet visible" after 1500s). Reverted to 128 MB.
- Node headroom: Worker nodes at 53-67% memory requests, no evictions — cluster resources are not the constraint

**Recommended upstream enhancement**: `insights-ingress-go` should use multipart S3 uploads (e.g., minio-go `PutObject` with `PartSize` option or the AWS SDK S3 upload manager) for payloads exceeding a configurable threshold. This is the standard pattern for large object uploads to S3-compatible backends.

**Workaround**: Customers with large clusters generating >150 MB upload packages should split data into multiple smaller uploads (e.g., by time range or namespace).

**Upstream context**: The `insights-ingress-go` service ([project-koku/insights-ingress-go](https://github.com/project-koku/insights-ingress-go)) uses `minio.Client.PutObject()` with default options, which performs a single-part upload for small objects and only switches to multipart for objects exceeding `minPartSize` (default 16 MiB in minio-go). However, the S3 backend (NooBaa/Ceph RGW) rejects single-part PUTs exceeding ~150 MB. Setting an explicit `PartSize` option (e.g., 64 MiB) on the `PutObjectOptions` would force multipart uploads for payloads above that threshold, matching the standard pattern for large object uploads to S3-compatible backends.

---

### PERF-FINDING-025: OCP/Summary Worker CPU Throttling Slows Data Processing

**Status**: Mitigated in perf profiles  
**Severity**: Medium  
**Related Jira**: [COST-7598](https://redhat.atlassian.net/browse/COST-7598) (Pipeline analysis), [COST-7618](https://redhat.atlassian.net/browse/COST-7618) (Sizing guide), [COST-7599](https://redhat.atlassian.net/browse/COST-7599) (Validate tuned configuration)

**Problem**:
The chart default CPU limits for OCP and summary celery workers (250m request / 500m limit) throttle data processing throughput. When the listener ingests data faster than workers can process it, the pipeline backs up.

**Evidence** (Run #40 → #41, large profile):
- Worker CPU boosted from 250m/500m to 500m/1000m (request/limit)
- Worker memory boosted from 512Mi/1Gi to 1Gi/2Gi
- Total run time: 112 min → 97.6 min (**15% faster**)
- KPI violations: 1 → 0

**Fix**: `apply_perf_profile_config()` now overrides worker resources per profile:
- medium: 250m/1000m CPU, 512Mi/2Gi memory
- large: 500m/1000m CPU, 1Gi/2Gi memory

**Recommendation**: Production deployments processing large or frequent uploads should increase OCP and summary worker CPU limits to at least 1000m.

---

## Backend Bottleneck Analysis (COST-7605)

### PERF-FINDING-026: Valkey Evictions Do Not Cause Chord Failures at Medium Workload

**Status**: Documented — further testing at large profile recommended  
**Severity**: Low (informational)  
**Related Jira**: [COST-7605](https://redhat.atlassian.net/browse/COST-7605) (Backend bottleneck analysis)  
**Test**: PERF-VK-001 (`test_valkey_eviction.py`)

**Question**:
Does Valkey key eviction under memory pressure cause Celery chord failures during ingestion? Chords store intermediate results in Valkey DB1 (`celery-task-meta-*` keys). If in-flight chord members are evicted, the chord callback never fires and processing stalls.

**Method**:
Constrained Valkey `maxmemory` via runtime `CONFIG SET` (no pod restart) and ran single-source medium-profile ingestion while monitoring evictions, task failures, and chord errors via a background `ValkeyMonitor` thread.

| Variant | maxmemory | Evictions | Mem % | Processing | Task Failures | Chord Errors |
|---------|-----------|-----------|-------|------------|---------------|--------------|
| 512Mi-default | 536 MB | 0 | 0% | Complete (18s) | 0 | 0 |
| 2Mi-tight | 2.0 MB | 0 (stale keys already purged) | 102% | Complete (18s) | 0 | 0 |
| baseline+10K | 2.0 MB (baseline + 10KB) | **7 (0.1/s)** | 101% | Complete (18s) | 0 | 0 |

**Findings**:
1. **Evictions were triggered** at the `baseline+10K` level — maxmemory set to just 10KB above current used_memory. All 120 stale `celery-task-meta-*` keys were evicted by the `allkeys-lru` policy.
2. **No chord failures occurred** despite active evictions. The LRU policy evicts stale results (completed tasks) before in-flight chord members, protecting active processing.
3. **Medium workload is too light to stress Valkey**. A single source generates ~170 task results using ~49KB total (~288 bytes/key). Even at 2MB maxmemory, there is ample headroom for in-flight tasks.
4. **Baseline Valkey memory usage is ~1.8MB** (process overhead), independent of workload. The 512Mi default provides >250x headroom.

**Implications for sizing**:
- The default `valkey.maxMemory: 512MB` is dramatically oversized for single-source deployments. Even 8MB would be sufficient.
- Chord failure risk is theoretical at medium scale. It would require enough concurrent chords to fill Valkey memory with in-flight members — likely 50+ simultaneous sources at large profile.
- The `allkeys-lru` policy (chart default) is the correct choice. A policy like `noeviction` would cause Celery write failures (MISCONF errors) instead, which is worse.

**Next steps**:
- Re-run at large/xlarge profile with 10+ concurrent sources to determine whether chord members can be evicted while still in use.
- Consider adding a `volatile-lru` policy with explicit TTL on task results as a defense-in-depth measure (currently tracked by `CELERY_RESULT_EXPIRES=28800s`).

---

## Environment Issues

### PERF-FINDING-010: ODF Default Resources Exhaust Cluster Memory

**Status**: Mitigated  
**Severity**: Critical  
**Category**: Environment (not product)

**Problem**:
ODF 4.20 applies full upstream default resource requests. On a 3-worker cluster, ODF consumed ~61.5Gi (68%) in memory requests, leaving insufficient headroom for cost-onprem + Kafka.

**Remediation**: Patch `StorageCluster` and NooBaa resources to reduced targets. See auto-toolbox `odf_reduced_resources` flag.

---

### PERF-FINDING-019: Ceph OSD False-Full Cascade Under Load

**Status**: Documented  
**Severity**: Critical  
**Category**: Environment (not product)

**Problem**:
During medium-profile tests, Ceph reported `OSD_FULL` despite disks at 25-29% actual usage. This cascaded to all CephFS-backed pods (database, Kafka) via SELinux relabel failures.

**Recovery**: Restart OSD pods to clear false-full state, then restart affected workloads.

**Production Implication**: Clusters running cost-onprem at high utilization with CephFS may experience this failure mode. The false-full condition cascades to all CephFS-backed workloads.

---

## Performance Baselines

Validated throughput, processing times, API latencies, and concurrent upload
results are maintained in the [Sizing Guide](sizing-guide.md#performance-baselines-validated).

---

## Jira Tracking

| Finding | Summary | Jira | Status |
|---------|---------|------|--------|
| FINDING-001 | HAProxy + Envoy timeouts 30s → 180s | [FLPATH-4091](https://redhat.atlassian.net/browse/FLPATH-4091) | Code Review |
| FINDING-002 | Listener CPU ≥1000m for burst ingestion | [COST-7618](https://redhat.atlassian.net/browse/COST-7618), [COST-6993](https://redhat.atlassian.net/browse/COST-6993) | Documented |
| FINDING-003 | Scale replicas for concurrent source count | [COST-7598](https://redhat.atlassian.net/browse/COST-7598), [COST-6163](https://redhat.atlassian.net/browse/COST-6163) | Documented |
| FINDING-004 | Kruize at 1 replica; CPU is throughput lever | [COST-7722](https://redhat.atlassian.net/browse/COST-7722) | Documented |
| FINDING-006 | Kruize CPU 500m/1000m → 1000m/2000m | [FLPATH-4302](https://redhat.atlassian.net/browse/FLPATH-4302) | Code Review |
| FINDING-011 | Envoy `request_timeout` removed | — | Fixed in chart |
| FINDING-013 | ROS processor dead-letter handling | [FLPATH-4428](https://redhat.atlassian.net/browse/FLPATH-4428) | Mitigated |
| FINDING-020 | Gateway ConfigMap checksum annotation | [FLPATH-4429](https://redhat.atlassian.net/browse/FLPATH-4429) | Mitigated |
| FINDING-021 | Ingress upload memory per profile | — | Fixed in perf profiles |
| FINDING-022 | Ingress dedicated resource block | [FLPATH-4430](https://redhat.atlassian.net/browse/FLPATH-4430) | Mitigated |
| FINDING-024 | Ingress multipart S3 upload | [FLPATH-4431](https://redhat.atlassian.net/browse/FLPATH-4431) | Product limitation |
| FINDING-025 | Worker CPU/memory sizing | [COST-7598](https://redhat.atlassian.net/browse/COST-7598), [COST-7618](https://redhat.atlassian.net/browse/COST-7618) | Fixed in perf profiles |
| FINDING-026 | Valkey evictions do not cause chord failures (medium) | [COST-7605](https://redhat.atlassian.net/browse/COST-7605) | Documented |

**Parent epic**: [COST-7567](https://redhat.atlassian.net/browse/COST-7567) (CoP Performance Tuning & Hardware Sizing Guidelines)

---

_Last Updated: 2026-07-09_
