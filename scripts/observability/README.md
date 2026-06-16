# Observability Stack

Metrics collection infrastructure for Cost On-Prem performance testing.

**Jira:** [FLPATH-4061](https://redhat.atlassian.net/browse/FLPATH-4061)

## Purpose

Collect performance metrics during test runs, export to JSON, and publish to S3-compatible storage for historical analysis. The Grafana dashboards serve as a **reference for which metrics to collect**, not as the primary visualization tool.

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
| `generate-perf-summary.py` | Generate flat `perf-summary.json` for Grafana Infinity queries |
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

## Dashboards (Reference)

The dashboard JSON files define which metrics to collect and can be imported into any Grafana instance for real-time or historical visualization.

```
dashboards/
├── overview.json        # High-level health and KPIs
├── ingress.json         # Upload latency, Kafka metrics
├── processing.json      # Celery queues, task performance
├── database.json        # PostgreSQL connections, queries, locks
├── ros.json             # Kruize and ROS recommendation metrics
└── infrastructure.json  # Node resources, Valkey cache
```

## Grafana Integration

### Automatic (after a perf run)

`deploy-test-cost-onprem.sh --perf-only` automatically runs `push-grafana-snapshot.py` at
the end of every run. If Grafana is reachable it will:

1. **Import** the `dashboards/collected-metrics/*.json` files into Grafana (or `dashboards/prometheus/*.json` for legacy operational dashboards)
2. **Create a permanent snapshot** of the run (static, no Prometheus required after creation)
3. **Generate a live link** scoped to the run's exact time window
4. **Patch** `reports/perf-run-report.html` with both links
5. Write `reports/grafana-links.json` for CI consumption

```bash
# Run it manually against an existing result directory
python3 scripts/observability/push-grafana-snapshot.py \
  --run-dir tests/perf-runs/<run-id>

# With an explicit Grafana URL
GRAFANA_URL=https://grafana.example.com \
GRAFANA_PASSWORD=mysecret \
python3 scripts/observability/push-grafana-snapshot.py \
  --run-dir tests/perf-runs/<run-id>

# Dry-run: preview what would happen without writing anything
python3 scripts/observability/push-grafana-snapshot.py \
  --run-dir tests/perf-runs/<run-id> --dry-run
```

Environment variables: `GRAFANA_URL`, `GRAFANA_USER` (default: `admin`),
`GRAFANA_PASSWORD` (default: `admin`), `GRAFANA_NAMESPACE` (default: `grafana`).

### Deploying Grafana on the Test Cluster

```bash
# Deploy exporters + Grafana on the same cluster as cost-onprem
SKIP_GRAFANA=false ./scripts/deploy-observability.sh

# The Grafana route is printed at the end — or query it later:
oc get route grafana -n grafana -o jsonpath='{.spec.host}'
```

Grafana is pre-configured with:
- **Prometheus datasource** → Thanos Querier (OpenShift UWM, 30-day retention)
- **Anonymous read access** so CI jobs can create snapshots without credentials
- **Admin password**: `admin` (change in production with `GF_SECURITY_ADMIN_PASSWORD`)

### Persistent Grafana Instance (Shared / Production)

For a shared team Grafana that outlives individual test clusters:

#### Option A — Grafana Cloud (easiest)

1. Create a free [Grafana Cloud](https://grafana.com/auth/sign-up/create-user) org
2. Note your stack URL (`https://yourstack.grafana.net`) and an API key
3. Set `GRAFANA_URL` and `GRAFANA_PASSWORD` (API key) before running perf tests:

   ```bash
   export GRAFANA_URL=https://yourstack.grafana.net
   export GRAFANA_USER=<your-grafana-cloud-user>
   export GRAFANA_PASSWORD=<grafana-cloud-api-key>
   ```

4. Snapshots will be pushed to your cloud org and accessible to any team member

> **Note**: Live dashboard links require a Prometheus datasource pointed at the cluster's
> Thanos Querier. Snapshots are fully self-contained and work without a datasource.

#### Option B — OpenShift Deployment (team-shared namespace)

Deploy Grafana once in a long-lived namespace and point all perf runs at it:

```bash
# Deploy to a shared namespace on your hub cluster
GRAFANA_NAMESPACE=shared-grafana \
SKIP_GRAFANA=false \
./scripts/deploy-observability.sh

# Export the URL for all subsequent runs
export GRAFANA_URL=$(oc get route grafana -n shared-grafana -o jsonpath='https://{.spec.host}')
```

To retain data across cluster rebuilds:
- Enable Grafana's SQLite persistence with a PVC (add `persistence.enabled: true` in standalone deployment)
- Snapshots are stored in Grafana's database — they survive pod restarts automatically

#### Option C — Existing Grafana (import dashboards manually)

1. Log in to your Grafana instance → **Dashboards → Import**
2. Upload each file from `scripts/observability/dashboards/`
3. Set the Prometheus datasource to your cluster's Thanos Querier URL:
   `https://thanos-querier.openshift-monitoring.svc.cluster.local:9091`
4. Point `GRAFANA_URL` at the instance when running perf tests

### What the Grafana Snapshot Contains

Each snapshot is a static HTML representation pushed to Grafana's snapshot store. It includes:

| Panel | Data source |
|-------|-------------|
| KPI stats (pass/fail, duration, throughput) | JSON result files |
| Test duration bar chart | JSON timings |
| Full results table (all 24+ tests) | JSON result files |
| Live time-series (CPU, queue depth) | Prometheus / Thanos (live dashboards only) |

Snapshots are **permanent by default** (no expiry). To delete: use the `deleteUrl` printed
during creation, or manage via **Grafana → Snapshots** in the UI.

## MinIO → Grafana: Historical Runs Dashboard

This is the fully decoupled data path — no live cluster required.

```
Test Run
  └── results/ + metrics/ + reports/
        │
        ├── generate-perf-summary.py    (runs automatically on S3 upload)
        │      └── perf-summary.json   (flat JSON: run meta, tests[], api[], ingestion[])
        │
        └── upload to MinIO
               └── s3://<bucket>/<prefix>/<run-id>/results/perf-summary.json
                   s3://<bucket>/<prefix>/index.json  (listing all runs)

Persistent Grafana (any instance, any location)
  └── Infinity datasource  →  S3/MinIO HTTP
        └── dashboards/collected-metrics/perf-history.json
              ├── Run selector (from index.json)
              ├── KPI stat panels
              ├── All test results table
              ├── Test duration bar chart
              ├── API latency table
              └── Ingestion performance table
```

### Setup

**1. Deploy Grafana with Infinity plugin** (once per shared instance):

```bash
# Infinity is now included in the standalone deployment
SKIP_GRAFANA=false \
AWS_ACCESS_KEY_ID=<minio-key> \
AWS_SECRET_ACCESS_KEY=<minio-secret> \
./scripts/deploy-observability.sh
```

The Infinity datasource is provisioned automatically with MinIO credentials.
The `perf-history.json` dashboard is loaded from the dashboards ConfigMap.

**2. Import manually** (existing Grafana):

```
Grafana → Administration → Plugins → search "Infinity" → Install
Grafana → Connections → Add datasource → Infinity
  → Set allowed hosts: https://minio-s3-...
  → Set auth: Basic Auth with MinIO access key/secret
Grafana → Dashboards → Import → upload dashboards/collected-metrics/perf-history.json
```

**3. What gets uploaded automatically:**

Every time `deploy-test-cost-onprem.sh --upload-metrics` runs a perf test with `S3_BUCKET` set:

```
s3://<bucket>/cost-onprem-performance/<run-id>/
  results/
    session_*.json          # raw test results
    perf-summary.json       # flat summary for Infinity (NEW)
  metrics/
    snapshot_*.json         # Prometheus snapshots (if collected)
  reports/
    perf-run-report.html    # self-contained Chart.js report
    report.html             # pytest-html
    grafana-links.json      # snapshot + live URLs

s3://<bucket>/cost-onprem-performance/index.json   # run listing (NEW)
```

**4. Generate `perf-summary.json` for an existing run:**

```bash
# For a run already on disk (no upload needed)
python3 scripts/observability/generate-perf-summary.py \
  --run-dir tests/perf-runs/<run-id>

# Also update the bucket index (requires S3_BUCKET etc.)
S3_ENDPOINT=https://minio-s3-... \
S3_BUCKET=eco-bucket-perf-scale \
python3 scripts/observability/generate-perf-summary.py \
  --run-dir tests/perf-runs/<run-id> \
  --update-index
```

### Dashboard: `dashboards/collected-metrics/perf-history.json`

| Panel | Query |
|-------|-------|
| Run selector | `index.json` → list of all run IDs |
| KPI cards | `perf-summary.json` → `run.passed`, `run.failed`, `run.duration_min` |
| All tests table | `perf-summary.json` → `tests[]` flat array |
| Duration chart | `perf-summary.json` → `tests[].duration_s` |
| API latency | `perf-summary.json` → `api[]` with p50/p95/p99 per endpoint |
| Ingestion perf | `perf-summary.json` → `ingestion[]` with upload speed and processing time |

All panels use [UQL](https://yesoreyeram.github.io/infinity-datasource/docs/uql/) to
navigate the JSON structure without needing a database.

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
    ├── grafana-links.json       # Grafana snapshot + live URLs (if linked)
    └── junit.xml
```

## Documentation

See [docs/performance/OBSERVABILITY.md](../../docs/performance/OBSERVABILITY.md) for:

- Full metrics reference
- S3 upload configuration
- PromQL query examples
