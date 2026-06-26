# Observability Stack

Metrics collection infrastructure for Cost On-Prem performance testing.

**Jira:** [FLPATH-4061](https://redhat.atlassian.net/browse/FLPATH-4061)

## Purpose

Collect performance metrics during test runs, export to JSON and self-contained HTML reports, and publish to S3-compatible storage for historical analysis.

## Quick Start

```bash
# 1. Deploy metrics collection infrastructure
./deploy-observability.sh

# 2. Run performance tests while collecting metrics
TEST_RUN_ID=baseline-v0.2.20 ./collect-metrics.sh --continuous 30

# 3. Upload results to S3 (Red Hat Ecosystem QE MinIO)
# S3_BUCKET is intentionally not defaulted to prevent accidental uploads
S3_ENDPOINT="https://minio-s3-ecosystem-qe-ai--pipeline.apps.gpc.ocp-hub.prod.psi.redhat.com" \
S3_BUCKET="eco-bucket-perf-scale" \
./collect-metrics.sh --upload
```

## Scripts

| Script | Description |
|--------|-------------|
| `deploy-observability.sh` | Deploy Prometheus exporters for PostgreSQL/Valkey |
| `collect-metrics.sh` | Capture metrics snapshots and export to JSON/S3 |
| `generate-perf-summary.py` | Generate flat `perf-summary.json` summary from test results |
| `generate-perf-run-report.py` | Generate visual HTML snapshot for a **single** perf run |
| `generate-perf-matrix-report.py` | Generate HTML matrix report across **all** runs in `perf-runs/` |

### Single-Run Visual Report

Generates a self-contained HTML snapshot of one perf run with:
- **KPI cards** — pass/fail count, total duration, avg upload throughput
- **Test duration timeline** — horizontal bar chart per test, colored pass/fail
- **API latency charts** — p50/p95/p99 ms per endpoint and iteration count
- **Upload throughput** — MB/s per ingestion test
- **Processing time** — minutes per ingestion test
- **Concurrent upload scaling** — throughput vs. source count
- **Time-series charts** — listener CPU, Celery queue depth (when metrics snapshots available)
- **Full results table** — every test, status, duration, key metric, error message

```bash
# Generate for a specific run
python3 scripts/observability/generate-perf-run-report.py \
  --run-dir tests/perf-runs/0-2-20-rc5-baseline-1779304523

# Custom output path
python3 scripts/observability/generate-perf-run-report.py \
  --run-dir tests/perf-runs/<run-id> \
  --output /tmp/my-report.html
```

The report is written to `<run-dir>/reports/perf-run-report.html` by default and is
automatically generated at the end of each `deploy-test-cost-onprem.sh --perf-only` run.

### Cross-Run Matrix Report

Generates a self-contained HTML page showing the
[listener CPU × load profile matrix](../../docs/performance/TEST-MATRIX.md#listener-cpu-sizing-scenarios)
with inline results, pass/fail badges, ingestion metrics, and links to full reports:

```bash
# Generate from default perf-runs/ directory
python3 scripts/observability/generate-perf-matrix-report.py

# Custom paths
python3 scripts/observability/generate-perf-matrix-report.py \
  --runs-dir ./perf-runs \
  --output perf-matrix-report.html
```

Open `perf-matrix-report.html` in a browser. Each cell in the matrix represents
one or more completed runs at that CPU config × profile combination. Empty cells
indicate scenarios not yet executed. The **report** link in each cell opens the
single-run visual report (above).

## Components

| Component | Description |
|-----------|-------------|
| **User Workload Monitoring** | OpenShift's built-in Prometheus with 30-day retention |
| **postgres_exporter** | PostgreSQL metrics (connections, queries, locks) |
| **valkey-exporter** | Valkey/Redis metrics (memory, clients, operations) |
| **collect-metrics.sh** | Query Prometheus and export to JSON/S3 |

## S3 Upload

When `S3_BUCKET` is set, `deploy-test-cost-onprem.sh --upload-metrics` publishes results:

```
s3://<bucket>/cost-onprem-performance/<run-id>/
  results/
    session_*.json          # raw test results
    perf-summary.json       # flat summary (run meta, tests[], api[], ingestion[])
  metrics/
    snapshot_*.json         # Prometheus snapshots (if collected)
  reports/
    perf-run-report.html    # self-contained HTML report
    report.html             # pytest-html
    junit.xml

s3://<bucket>/cost-onprem-performance/index.json   # listing of all runs
```

Generate `perf-summary.json` for an existing run:

```bash
python3 scripts/observability/generate-perf-summary.py \
  --run-dir tests/perf-runs/<run-id>

# Also update the S3 index
S3_ENDPOINT=https://minio-s3-... \
S3_BUCKET=eco-bucket-perf-scale \
python3 scripts/observability/generate-perf-summary.py \
  --run-dir tests/perf-runs/<run-id> \
  --update-index
```

## Metrics Output

```bash
tests/perf-runs/<run-id>/
├── metadata.json                # Run metadata (chart version, profile, cluster info)
├── results/
│   ├── session_<timestamp>.json # All test results
│   └── test_*.json              # Per-test detail
├── metrics/
│   ├── snapshot_<timestamp>.json
│   └── ...
└── reports/
    ├── perf-run-report.html     # Visual HTML snapshot (Chart.js)
    ├── report.html              # pytest-html full report
    └── junit.xml
```

## Documentation

See [docs/performance/OBSERVABILITY.md](../../docs/performance/OBSERVABILITY.md) for:

- Full metrics reference
- S3 upload configuration
- PromQL query examples
