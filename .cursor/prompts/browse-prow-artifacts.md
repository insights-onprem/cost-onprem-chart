# Browse Prow CI Artifacts

Navigate and analyze test artifacts from OpenShift CI Prow jobs. Useful for debugging
failed tests, reviewing screenshots, traces, and understanding test behavior.

## Input

Provide a **gcsweb URL** like:
```
https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/insights-onprem_cost-onprem-chart/<PR>/<JOB>/<BUILD_ID>/artifacts/...
```

Or a **Prow URL** (will be converted):
```
https://prow.ci.openshift.org/view/gs/test-platform-results/pr-logs/pull/insights-onprem_cost-onprem-chart/<PR>/<JOB>/<BUILD_ID>
```

## URL Conversion

| From | To |
|------|-----|
| Prow URL | Replace `prow.ci.openshift.org/view/gs/` → `gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/` |
| gcsweb browsing | Append `/artifacts/e2e/insights-onprem-cost-onprem-chart-e2e/artifacts/` for test artifacts |

## Artifact Directory Structure

```
<BUILD_ID>/
├── build-log.txt                    # CI operator orchestration log
├── finished.json                    # Job result and timing
├── artifacts/
│   ├── junit_operator.xml           # Operator-level JUnit
│   └── e2e/
│       └── insights-onprem-cost-onprem-chart-e2e/
│           ├── build-log.txt        # Pytest output (test execution log)
│           └── artifacts/
│               ├── junit_chart.xml  # Chart tests JUnit report
│               ├── junit_iqe.xml    # IQE tests JUnit report (if run)
│               ├── iqe_output.log   # IQE test output (if run)
│               ├── screenshots/     # Failure screenshots
│               └── playwright/      # UI test artifacts (if enabled)
│                   ├── report.html  # pytest-html report
│                   ├── screenshots/ # All test screenshots
│                   ├── videos/      # Failed test recordings
│                   └── traces/      # Playwright traces
```

## Browsing Artifacts Online

### Direct gcsweb Links

Construct URLs by appending paths to the base:

```bash
# Base (replace <PR>, <JOB>, <BUILD_ID>)
BASE="https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/insights-onprem_cost-onprem-chart/<PR>/<JOB>/<BUILD_ID>"

# Test artifacts root
$BASE/artifacts/e2e/insights-onprem-cost-onprem-chart-e2e/artifacts/

# JUnit reports
$BASE/artifacts/e2e/insights-onprem-cost-onprem-chart-e2e/artifacts/junit_chart.xml

# Screenshots directory
$BASE/artifacts/e2e/insights-onprem-cost-onprem-chart-e2e/artifacts/screenshots/

# Playwright HTML report (if UI tests ran)
$BASE/artifacts/e2e/insights-onprem-cost-onprem-chart-e2e/artifacts/playwright/report.html
```

### Raw File Access

Replace `gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/` with `storage.googleapis.com/` for direct file download:

```bash
# Download build log directly
curl -O "https://storage.googleapis.com/test-platform-results/pr-logs/pull/insights-onprem_cost-onprem-chart/<PR>/<JOB>/<BUILD_ID>/build-log.txt"
```

## Downloading Artifacts

### Using the Script

```bash
# From gcsweb URL
./scripts/download-ci-artifacts.sh --url "<GCSWEB_URL>"

# From PR number and build ID
./scripts/download-ci-artifacts.sh <PR> <BUILD_ID>
```

### Using gcloud

```bash
# Download all artifacts
gcloud storage cp -r \
  "gs://test-platform-results/pr-logs/pull/insights-onprem_cost-onprem-chart/<PR>/<JOB>/<BUILD_ID>/" \
  ./ci-artifacts/

# Download just test artifacts
gcloud storage cp -r \
  "gs://test-platform-results/pr-logs/pull/insights-onprem_cost-onprem-chart/<PR>/<JOB>/<BUILD_ID>/artifacts/e2e/insights-onprem-cost-onprem-chart-e2e/artifacts/" \
  ./test-artifacts/
```

## Analyzing Downloaded Artifacts

### JUnit Reports

```bash
# Parse test results
grep -E "failures|errors|tests" test-artifacts/junit_chart.xml | head -5

# Find failed tests
grep -o 'name="[^"]*"' test-artifacts/junit_chart.xml | grep -i fail
```

### Playwright Traces

Playwright traces (`.zip` files) contain DOM snapshots, network logs, and action timelines.

```bash
# View locally (opens interactive viewer)
playwright show-trace test-artifacts/playwright/traces/<test_name>.zip

# Or upload to online viewer
# https://trace.playwright.dev
```

### Screenshots

Screenshots are captured for all UI tests. Failed test screenshots help identify the UI state at failure.

```bash
# List all screenshots
ls -la test-artifacts/playwright/screenshots/

# Open a specific screenshot
open test-artifacts/playwright/screenshots/<test_name>.png
```

### Videos

Videos are captured for failed UI tests only (to save storage).

```bash
# List video recordings
ls -la test-artifacts/playwright/videos/

# Play a video
open test-artifacts/playwright/videos/<test_name>.webm
```

### HTML Report

The pytest-html report includes embedded screenshots, videos, and trace links:

```bash
# Open the report
open test-artifacts/playwright/report.html
```

## Quick Analysis Commands

```bash
# After downloading artifacts to ./ci-artifacts/:

# 1. Check job result
cat ci-artifacts/*/finished.json | jq '.result, .timestamp'

# 2. Find failures in JUnit
grep -l "failures=\"[1-9]" ci-artifacts/*/artifacts/**/junit*.xml

# 3. List all screenshots
find ci-artifacts -name "*.png" -type f

# 4. List all traces
find ci-artifacts -name "*.zip" -path "*traces*" -type f

# 5. List all videos
find ci-artifacts -name "*.webm" -type f

# 6. View pytest output
less ci-artifacts/*/artifacts/e2e/insights-onprem-cost-onprem-chart-e2e/build-log.txt
```

## Common Patterns

### Finding Why a Test Failed

1. Check the pytest output in `build-log.txt` for the error message
2. Look at the screenshot in `screenshots/<test_name>.png`
3. If a video exists in `videos/<test_name>.webm`, review the recording
4. Open the trace in `traces/<test_name>.zip` with `playwright show-trace` for detailed timeline

### Understanding Test Timing

```bash
# From JUnit, extract test times
grep -oP 'time="[0-9.]+"' test-artifacts/junit_chart.xml | sort -t= -k2 -rn | head -10
```

### Comparing Two Runs

Download both artifact sets and diff the JUnit files or compare failure screenshots side-by-side.
