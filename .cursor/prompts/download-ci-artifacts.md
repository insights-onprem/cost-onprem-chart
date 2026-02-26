# Download CI Artifacts

Download test artifacts from OpenShift CI for debugging failed or passed test runs.

## Required Information

Please provide ONE of the following:

1. **Prow URL** (easiest - copy from GitHub CI check "Details" link):
   ```
   https://prow.ci.openshift.org/view/gs/test-platform-results/pr-logs/pull/insights-onprem_cost-onprem-chart/<PR>/<JOB>/<BUILD_ID>
   ```

2. **gcsweb URL** (from artifact browser):
   ```
   https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/insights-onprem_cost-onprem-chart/<PR>/<JOB>/<BUILD_ID>/
   ```

3. **PR number and Build ID**:
   - PR Number: e.g., `50`
   - Build ID: e.g., `2014360404288868352`

## CI Job Names

| Job Name | Description |
|----------|-------------|
| `pull-ci-insights-onprem-cost-onprem-chart-main-e2e` | **Main E2E test job** - runs pytest suite |
| `pull-ci-insights-onprem-cost-onprem-chart-main-lint` | Linting/validation only |

The E2E job runs these steps:
1. `ipi-install-rbac` - RBAC setup (~10s)
2. `insights-onprem-s4-deploy` - Deploy S4 for S3 (~40s)
3. `insights-onprem-cost-onprem-chart-e2e` - **Runs pytest** (~18m)

## Download Commands

### Using the Script (Recommended)

```bash
# From Prow URL (copy from GitHub "Details" link)
./scripts/download-ci-artifacts.sh --url "https://prow.ci.openshift.org/view/gs/test-platform-results/pr-logs/pull/insights-onprem_cost-onprem-chart/50/pull-ci-insights-onprem-cost-onprem-chart-main-e2e/2014360404288868352"

# From gcsweb URL
./scripts/download-ci-artifacts.sh --url "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/insights-onprem_cost-onprem-chart/50/pull-ci-insights-onprem-cost-onprem-chart-main-e2e/2014360404288868352/"

# From PR number and build ID
./scripts/download-ci-artifacts.sh 50 2014360404288868352
```

### Manual gcloud Commands

```bash
# Download all artifacts
gcloud storage cp -r \
  gs://test-platform-results/pr-logs/pull/insights-onprem_cost-onprem-chart/<PR>/pull-ci-insights-onprem-cost-onprem-chart-main-e2e/<BUILD_ID>/ \
  ./ci-artifacts/

# Download build log only
gcloud storage cp \
  gs://test-platform-results/pr-logs/pull/insights-onprem_cost-onprem-chart/<PR>/pull-ci-insights-onprem-cost-onprem-chart-main-e2e/<BUILD_ID>/build-log.txt \
  ./build-log.txt

# Download pytest output
gcloud storage cp \
  "gs://test-platform-results/pr-logs/pull/insights-onprem_cost-onprem-chart/<PR>/pull-ci-insights-onprem-cost-onprem-chart-main-e2e/<BUILD_ID>/artifacts/e2e/insights-onprem-cost-onprem-chart-e2e/build-log.txt" \
  ./pytest-output.txt
```

## View Logs Online (No Download)

```bash
# Prow Dashboard (with JUnit viewer)
https://prow.ci.openshift.org/view/gs/test-platform-results/pr-logs/pull/insights-onprem_cost-onprem-chart/<PR>/pull-ci-insights-onprem-cost-onprem-chart-main-e2e/<BUILD_ID>

# Build log (raw text)
https://storage.googleapis.com/test-platform-results/pr-logs/pull/insights-onprem_cost-onprem-chart/<PR>/pull-ci-insights-onprem-cost-onprem-chart-main-e2e/<BUILD_ID>/build-log.txt

# Browse all artifacts
https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/insights-onprem_cost-onprem-chart/<PR>/pull-ci-insights-onprem-cost-onprem-chart-main-e2e/<BUILD_ID>/
```

## Available Artifacts

| Path | Description |
|------|-------------|
| `build-log.txt` | Main CI operator log (job orchestration) |
| `finished.json` | Job completion status and result |
| `artifacts/e2e/insights-onprem-cost-onprem-chart-e2e/` | **Test step artifacts** |
| `artifacts/e2e/insights-onprem-cost-onprem-chart-e2e/build-log.txt` | **Pytest output** |
| `artifacts/junit_operator.xml` | JUnit test report |

## Finding the URL

1. Go to your PR on GitHub
2. Scroll to the CI checks section
3. Click "Details" on `pull-ci-insights-onprem-cost-onprem-chart-main-e2e`
4. Copy the Prow URL from your browser

## Prerequisites

- `gcloud` CLI installed: https://cloud.google.com/sdk/docs/install
- Authenticated: `gcloud auth login`
