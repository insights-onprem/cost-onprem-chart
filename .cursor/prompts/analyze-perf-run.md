# Analyze Performance Test Run

Analyze a performance test run to understand results, identify issues, and assess KPI compliance.

## Input

Provide one of:
1. **Local run directory**: e.g., `tests/perf-runs/0-2-20-rc5-baseline-1779843259`
2. **S3/MinIO URL**: e.g., `https://minio-s3-.../cost-onprem-performance/RUN_ID/`
3. **Session JSON file**: e.g., `session_20260527_005520.json`

## Quick Analysis Commands

```bash
# Set the run directory
RUN_DIR="tests/perf-runs/<run-id>"

# Summary from perf-summary.json
python3 -c "
import json
with open('$RUN_DIR/results/perf-summary.json') as f:
    d = json.load(f)
    r = d['run']
    print(f'''
Run: {r[\"run_id\"]}
Version: {r[\"chart_version\"]}
Profile: {r[\"profile\"]}
Duration: {r[\"duration_min\"]} min
Tests: {r[\"passed\"]}/{r[\"total_tests\"]} passed, {r[\"failed\"]} failed
KPI Violations: {r.get(\"kpi_violations\", 0)}
''')
"

# Quick test results
python3 -c "
import json
with open('$RUN_DIR/results/perf-summary.json') as f:
    for t in json.load(f)['tests']:
        status = '✅' if t['passed'] else '❌'
        print(f'{status} {t[\"test_name\"][:50]:50} {t[\"duration_s\"]:6.1f}s')
"
```

## Analysis Steps

### 1. Run Overview

Report these metrics from `perf-summary.json` or `session_*.json`:

| Metric | Description |
|--------|-------------|
| Run ID | Unique identifier |
| Chart Version | Helm chart version tested |
| Profile | baseline, small, medium, large |
| Duration | Total run time in minutes |
| Pass Rate | passed/total tests |
| KPI Violations | Count of KPI threshold breaches |

### 2. Test Results by Category

Group tests and identify patterns:

```bash
# API tests
grep -E "api_0" $RUN_DIR/results/perf-summary.json | python3 -c "
import json,sys
for line in sys.stdin:
    # parse and report API test results
"

# Ingestion tests (most important for baseline)
grep -E "ing_0" $RUN_DIR/results/perf-summary.json

# Scale tests
grep -E "scale_0" $RUN_DIR/results/perf-summary.json

# ROS tests
grep -E "ros_0" $RUN_DIR/results/perf-summary.json
```

### 3. KPI Assessment

Key Performance Indicators by test type:

#### API Tests
| Metric | Green | Yellow | Red |
|--------|-------|--------|-----|
| P95 Latency | < 2s | < 5s | ≥ 5s |
| Success Rate | > 95% | > 80% | ≤ 80% |

#### Ingestion Tests  
| Metric | Green | Yellow | Red |
|--------|-------|--------|-----|
| Processing Complete | Yes | - | No |
| Upload Throughput | > 1 MB/s | > 0.5 MB/s | ≤ 0.5 MB/s |

#### Scale Tests
| Metric | Green | Yellow | Red |
|--------|-------|--------|-----|
| Source Ramp P95 | < 2s | < 5s | ≥ 5s |
| Concurrent Query Success | > 95% | > 90% | ≤ 90% |

### 4. Resource Metrics (if available)

Check `metrics/` directory for Prometheus snapshots:

```bash
# List metric snapshots
ls $RUN_DIR/metrics/snapshot_*.json 2>/dev/null | wc -l

# Extract resource summary
python3 -c "
import json
from pathlib import Path
snapshots = sorted(Path('$RUN_DIR/metrics').glob('snapshot_*.json'))
cpu_vals, mem_vals = [], []
for s in snapshots:
    d = json.load(s.open())
    m = d.get('metrics', {})
    if cpu := m.get('pod_cpu_usage'):
        cpu_vals.append(cpu)
    if mem := m.get('pod_memory_usage_bytes'):
        mem_vals.append(mem / 1024 / 1024)
if cpu_vals:
    print(f'CPU: max={max(cpu_vals):.3f}, avg={sum(cpu_vals)/len(cpu_vals):.3f} cores')
if mem_vals:
    print(f'Memory: max={max(mem_vals):.1f}, avg={sum(mem_vals)/len(mem_vals):.1f} MB')
"
```

### 5. Failure Analysis

For any failed tests, investigate:

```bash
# List failures with error messages
python3 -c "
import json
with open('$RUN_DIR/results/perf-summary.json') as f:
    for t in json.load(f)['tests']:
        if not t['passed']:
            print(f'❌ {t[\"test_name\"]}')
            if err := t.get('error_message'):
                print(f'   Error: {err[:200]}')
            print()
"
```

Common failure patterns:
- **Processing timeout**: Ingestion didn't complete in time → check listener CPU, Celery queues
- **Success rate 0%**: API returned errors → check API logs, connection issues
- **KPI violation**: Metrics exceeded thresholds → performance regression or under-resourced

### 6. Comparison Template

When comparing runs (e.g., before/after a change):

```
                          Baseline      Current       Delta
Chart Version:            ___           ___           
Profile:                  ___           ___           
Duration (min):           ___           ___           ___ %
Tests Passed:             ___           ___           
Tests Failed:             ___           ___           
KPI Violations:           ___           ___           

API P95 Latency (ms):     ___           ___           ___ %
Ingestion Throughput:     ___           ___           ___ %
Listener CPU (max):       ___           ___           ___ %
Memory (max MB):          ___           ___           ___ %
```

### 7. Generate Visual Report

If you have the run locally, generate the HTML report:

```bash
python3 scripts/observability/generate-perf-run-report.py \
  --run-dir $RUN_DIR
# Opens: $RUN_DIR/reports/perf-run-report.html
```

### 8. Reviewer Checklist

For PR reviews with performance results:

- [ ] **Pass rate**: All baseline tests pass?
- [ ] **KPI compliance**: No red KPI violations?
- [ ] **Regression check**: No significant degradation from previous runs?
- [ ] **Resource usage**: CPU/memory within expected bounds?
- [ ] **Duration**: Run completed in expected time?
- [ ] **Error messages**: Any new error patterns?

### 9. Download from S3/MinIO

```bash
# From MinIO console URL
S3_BASE="https://minio-s3-ecosystem-qe-ai--pipeline.apps.gpc.ocp-hub.prod.psi.redhat.com"
BUCKET="eco-bucket-perf-scale"
RUN_ID="0-2-20-rc5-baseline-1779843259"

# Download perf-summary.json
curl -sO "${S3_BASE}/${BUCKET}/cost-onprem-performance/${RUN_ID}/results/perf-summary.json"

# Download full run
mkdir -p $RUN_ID
for path in results/perf-summary.json results/session_*.json reports/perf-run-report.html; do
  curl -s "${S3_BASE}/${BUCKET}/cost-onprem-performance/${RUN_ID}/${path}" \
    -o "${RUN_ID}/$(basename $path)" 2>/dev/null
done
```

## Quick One-Liner Analysis

```bash
# Fast summary of any perf-summary.json
python3 -c "
import json,sys
d = json.load(open(sys.argv[1]))
r = d['run']
fails = [t['test_name'] for t in d['tests'] if not t['passed']]
print(f'{r[\"run_id\"]}: {r[\"passed\"]}/{r[\"total_tests\"]} passed, {r[\"duration_min\"]}min')
if fails: print(f'  Failed: {fails[:5]}...' if len(fails)>5 else f'  Failed: {fails}')
" $RUN_DIR/results/perf-summary.json
```
