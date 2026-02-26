# Claude Project Context

This file provides project context for Claude-based development environments (Claude Code, claude.ai, etc.).
For Cursor IDE users, see `.cursor/rules/` for auto-loaded context rules.

**For non-Cursor environments:** See `.cursor/prompts/` for task-specific guides:
- `run-tests.md` - Run pytest with various options
- `troubleshoot-tests.md` - Diagnose test failures
- `connect-cluster.md` - Set up OpenShift cluster access
- `deploy-chart.md` - Deploy the Helm chart
- `check-logs.md` - View component logs
- `debug-e2e.md` - Debug E2E test failures
- `download-ci-artifacts.md` - Download CI artifacts from Prow/GCS

## Project Overview

This is a Helm chart for deploying Red Hat Cost Management on-premise (cost-onprem).
It includes comprehensive pytest-based testing infrastructure.

### Key Directories
- `cost-onprem/` - Helm chart templates and values
- `tests/` - Pytest test suite
- `scripts/` - Deployment and testing scripts
- `docs/` - Documentation

### Requirements
- **Python 3.10+** (CI uses Python 3.11)
- OpenShift CLI (`oc`) with cluster access
- Helm 3.x
- `gcloud` CLI (for downloading CI artifacts)

### Code Style
- Python: Follow PEP 8, use type hints where helpful
- Bash: Use `set -euo pipefail`, quote variables
- YAML: 2-space indentation for Helm templates
- Tests: Use descriptive names, include docstrings

---

## Testing Infrastructure

### OpenShift CI Execution Flow

In OpenShift CI, tests are executed via the `insights-onprem-cost-onprem-chart-e2e` step:

```
CI Step Registry: insights-onprem/cost-onprem-chart/e2e/
├── insights-onprem-cost-onprem-chart-e2e-commands.sh  # Main CI script
├── insights-onprem-cost-onprem-chart-e2e-ref.yaml     # Step definition
```

**CI Execution Sequence:**
1. Dependencies: Installs yq, kubectl, helm, oc
2. S4 Setup: Reads config from `insights-onprem-s4-deploy` step
3. Cost Management Operator: Installs via OLM (stable channel)
4. Helm Wrapper: Injects S4 storage config for cost-onprem chart
5. Deploy & Test: Runs `scripts/deploy-test-cost-onprem.sh --namespace cost-onprem --verbose`

**Default CI Test Run:**
```bash
# What CI executes (via deploy-test-cost-onprem.sh):
NAMESPACE=cost-onprem ./scripts/run-pytest.sh

# Equivalent to:
pytest -m "not extended" --junit-xml=reports/junit.xml
```

**CI runs ~88 tests in ~3 minutes** (excludes extended tests that require ODF/S3).

### Running Tests Locally

```bash
# Run all tests (including UI)
NAMESPACE=cost-onprem ./scripts/run-pytest.sh

# Run all tests except UI
NAMESPACE=cost-onprem ./scripts/run-pytest.sh --no-ui

# Specific suites
./scripts/run-pytest.sh --helm
./scripts/run-pytest.sh --auth
./scripts/run-pytest.sh --e2e
./scripts/run-pytest.sh --infrastructure
./scripts/run-pytest.sh --ros
./scripts/run-pytest.sh --ui
```

### Test Markers
- `component` - Single-component tests
- `integration` - Multi-component tests
- `extended` - Long-running tests (skipped by default in CI)
- `smoke` - Quick validation tests

### Test Cleanup
```bash
E2E_CLEANUP_BEFORE=true   # Clean before tests (default)
E2E_CLEANUP_AFTER=true    # Clean after tests (default)
E2E_RESTART_SERVICES=false # Restart Valkey/listener (optional)
```

---

## Kubernetes Label Conventions

**IMPORTANT:** The chart uses `app.kubernetes.io/component` for pod selection, NOT `app.kubernetes.io/name`.

| Component | Label Selector |
|-----------|----------------|
| Database | `app.kubernetes.io/component=database` |
| Ingress | `app.kubernetes.io/component=ingress` |
| Kruize | `app.kubernetes.io/component=ros-optimization` |
| ROS API | `app.kubernetes.io/component=ros-api` |
| ROS Processor | `app.kubernetes.io/component=ros-processor` |
| Cache (Valkey) | `app.kubernetes.io/component=cache` |
| Koku Listener | `app.kubernetes.io/component=listener` |
| MASU | `app.kubernetes.io/component=cost-processor` |
| Celery Workers | `app.kubernetes.io/component=cost-worker` |

### Common Commands
```bash
# Check pod status
kubectl get pods -n cost-onprem -l app.kubernetes.io/instance=cost-onprem

# View logs by component
kubectl logs -n cost-onprem -l app.kubernetes.io/component=listener --tail=100
kubectl logs -n cost-onprem -l app.kubernetes.io/component=ros-processor --tail=100
```

---

## Troubleshooting Guide

### Tests Skipping with "Database pod not found"
Tests use `app.kubernetes.io/component=database` label selector.
```bash
kubectl get pods -n cost-onprem -l app.kubernetes.io/component=database
```

### S3 SignatureDoesNotMatch Errors
**Root Cause:** boto3 defaults to virtual-hosted style URLs which don't work with NooBaa/Ceph RGW.
**Fix:** Chart configures boto3 for path-style S3 addressing via:
- `cost-onprem-aws-config` ConfigMap (sets `addressing_style = path`)
- `AWS_CONFIG_FILE=/etc/aws/config` environment variable

### ROS Processor TLS Certificate Errors
**Symptom:** "x509: certificate signed by unknown authority" or CSV parse errors.
**Root Cause:** Go's `x509.SystemCertPool()` doesn't include OpenShift service CA.
**Fix:** Chart uses `initContainer.prepareCABundle` to combine CAs.

### Summary Tables Not Populated (test_06)
**Root Cause:** `manifest.json` must include `start` and `end` date fields.
**Fix:** `tests/utils.py` `create_upload_package()` includes these fields.

### Kruize Experiments Not Created (test_07)
**Possible Causes:**
1. TLS certificate issues
2. S3 URL encoding issues
3. Wrong data format - NISE must use `--ros-ocp-info` flag

**Verification:**
```bash
kubectl logs -n cost-onprem -l app.kubernetes.io/component=ros-processor --tail=50
```

### Helm Upgrade "field is immutable"
**Root Cause:** Label changes require fresh install.
```bash
helm uninstall cost-onprem -n cost-onprem
helm install cost-onprem ./cost-onprem -n cost-onprem -f openshift-values.yaml --wait
```

---

## NISE Data Generation

The E2E tests use NISE (koku-nise) to generate proper OCP cost data.

### Basic Usage
```bash
nise report ocp --ros-ocp-info --static-report-file static.yml --write-monthly
```

### Key Flags
- `--ros-ocp-info` - Generate container-level data for ROS processor (REQUIRED for ROS tests)
- `--write-monthly` - Organize output by month
- `--static-report-file` - Use predefined workload configuration

### Manifest Structure
Upload tarball must have proper manifest.json:
```json
{
  "uuid": "...",
  "cluster_id": "...",
  "version": "...",
  "date": "2026-01-22",
  "start": "2026-01-20",
  "end": "2026-01-22",
  "files": ["pod_usage.csv"],
  "resource_optimization_files": ["ros_usage.csv"]
}
```

### Important Notes
- `files` array: Pod-level data for Koku processing
- `resource_optimization_files` array: Container-level data for ROS processor
- Both `start` and `end` dates are REQUIRED for summary table population

---

## Cluster Access

### Required Information

To connect to an OpenShift cluster for testing/troubleshooting, you need:
1. **Cluster API URL**: e.g., `api.ocp-edge94.qe.lab.redhat.com:6443`
2. **Username**: e.g., `kubeadmin`
3. **Password**: The cluster admin password

### Login Command

```bash
oc login -s <CLUSTER_API_URL> -u <USERNAME> --password <PASSWORD>
```

### Verify Connection

```bash
oc whoami                                    # Check logged in user
oc cluster-info                              # Check cluster info
kubectl get pods -n cost-onprem              # Check deployment
```

### Environment Variables

```bash
export NAMESPACE="cost-onprem"               # Target namespace
export KEYCLOAK_NAMESPACE="keycloak"         # Keycloak namespace
export HELM_RELEASE_NAME="cost-onprem"       # Helm release name
```

---

## Quick Reference Commands

### Run Tests
```bash
# CI mode (~88 tests, ~3 min)
NAMESPACE=cost-onprem ./scripts/run-pytest.sh

# Extended tests (~15 min, requires ODF)
NAMESPACE=cost-onprem ./scripts/run-pytest.sh --extended

# Specific suite
./scripts/run-pytest.sh --e2e
```

### Check Logs
```bash
# Koku listener
kubectl logs -n cost-onprem -l app.kubernetes.io/component=listener --tail=100

# ROS processor
kubectl logs -n cost-onprem -l app.kubernetes.io/component=ros-processor --tail=100

# Kruize
kubectl logs -n cost-onprem -l app.kubernetes.io/component=ros-optimization --tail=100
```

### Deploy Chart
```bash
# Full deployment + tests
./scripts/deploy-test-cost-onprem.sh --namespace cost-onprem --verbose

# Tests only (existing deployment)
./scripts/deploy-test-cost-onprem.sh --tests-only
```

### Troubleshoot
```bash
# All pods
kubectl get pods -n cost-onprem -l app.kubernetes.io/instance=cost-onprem

# Recent events
kubectl get events -n cost-onprem --sort-by='.lastTimestamp' | tail -20

# Search for errors
kubectl logs -n cost-onprem -l app.kubernetes.io/component=ros-processor | grep -i error
```

### Download CI Artifacts
```bash
# Download from Prow URL
./scripts/download-ci-artifacts.sh --url "<PROW_URL>"

# Download by PR and build ID
./scripts/download-ci-artifacts.sh <PR_NUMBER> <BUILD_ID>
```

**Note:** Downloaded artifacts are saved to `ci-artifacts-pr<PR>-<BUILD_ID>/` and should NOT be deleted unless explicitly requested by the user.
