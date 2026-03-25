# IQE Testing Setup Guide

This guide covers the prerequisites and setup required to run IQE (Insights QE) tests against the cost-onprem deployment.

## Overview

There are two ways to run IQE tests:
1. **Containerized** (`run-iqe-tests.sh`) - Runs tests in a container on the cluster
2. **Local** (`run-iqe-tests-local.sh`) - Runs tests directly from local repositories

## Prerequisites

### 1. Red Hat Network Access

**Required for both containerized and local testing.**
- Clone IQE repositories from gitlab

You must be connected to the Red Hat internal network (VPN or office network) to:
- Pull IQE container images from `quay.io/cloudservices/iqe-tests`
- Access internal PyPI (`nexus.corp.redhat.com`)

### 2. Quay.io Registry Access (Containerized Tests)

To pull the IQE container image, you need access to the `cloudservices` organization on Quay.io.

**Setup Steps:**

1. Create a user file in the `app-interface` repository:
   ```
   data/teams/insights/users/<your-username>.yml
   ```

2. Use this template:
   ```yaml
   ---
   $schema: /access/user-1.yml

   labels:
     platform: insights

   name: Your Full Name
   org_username: your-kerberos-id
   github_username: your-github-username
   quay_username: your-username

   roles:
   - $ref: /teams/insights/roles/insights-engineers.yml
   - $ref: /teams/insights/roles/hccm.yml
   - $ref: /teams/insights/roles/hccm-qe.yml
   - $ref: /teams/insights/roles/ephemeral-users.yml
   - $ref: /teams/insights/roles/insights-qe.yml
   ```

3. Submit a merge request to `app-interface` and get it approved

4. After merge, your Quay account will be granted pull access to `quay.io/cloudservices/iqe-tests`

### 3. Local Repository Setup (Local Tests)

For local testing, clone these repositories adjacent to `cost-onprem-chart`:

```bash
cd /path/to/workspaces

# IQE Core framework
git clone https://gitlab.cee.redhat.com/insights-qe/iqe-core.git

# Cost Management IQE plugin
git clone https://gitlab.cee.redhat.com/insights-qe/iqe-cost-management-plugin.git

# Your directory structure should look like:
# workspaces/
# ├── cost-onprem-chart/
# ├── iqe-core/
# └── iqe-cost-management-plugin/
```

### 4. Python 3.12 (Local Tests)

The script creates its own virtual environment but requires Python 3.12 to be installed:

```bash
# macOS
brew install python@3.12

# Verify
python3.12 --version
```

You can use a different Python binary by setting `PYTHON_BIN`.

### 5. OpenShift Cluster

You need an OpenShift cluster with:
- **Minimum**: 3 control plane nodes (tests may be slow/timeout; we'll need to investigate marking slow tests)
- **Recommended**: 3 control plane + 2 worker nodes
- Cost-onprem chart deployed with Keycloak authentication

## Running Containerized Tests

```bash
# Basic run
./scripts/run-iqe-tests.sh

# With custom marker
./scripts/run-iqe-tests.sh --marker "cost_ocp_on_prem"

# With test filter
./scripts/run-iqe-tests.sh --filter "not ai_workloads"

# Increase timeout (default 14400s / 4 hours)
./scripts/run-iqe-tests.sh --timeout 7200
```

## Running Local Tests

### First-Time Setup

```bash
# Create virtual environment and install dependencies
./scripts/run-iqe-tests-local.sh --setup
```

This will:
- Create a Python 3.12 virtual environment at `.venv-iqe/`
- Install `iqe-core` from your local clone
- Install `iqe-cost-management-plugin` from your local clone
- Configure PyPI to use Red Hat internal index

### Running Tests

```bash
# Run all on-prem tests (with default filters for known issues)
./scripts/run-iqe-tests-local.sh

# Clean up sources before running (recommended for fresh runs)
./scripts/run-iqe-tests-local.sh --clean-sources

# Custom test filter
./scripts/run-iqe-tests-local.sh --filter "test_api_ocp_source_crud"

# Dry run to see configuration
./scripts/run-iqe-tests-local.sh --dry-run
```

### DNS / `/etc/hosts` Requirement

The local test runner creates an OpenShift Route to reach the masu service.
QE lab clusters often lack wildcard DNS, so the route hostname may not resolve.
If the script exits with `Cannot resolve <hostname>`, add the entry it suggests
to `/etc/hosts`:

```bash
# The script will print the exact line to add, e.g.:
# Cannot resolve cost-onprem-masu-iqe-cost-onprem.apps.ocp-edge94.qe.lab.redhat.com
# Add to /etc/hosts:  10.46.46.73 cost-onprem-masu-iqe-cost-onprem.apps.ocp-edge94.qe.lab.redhat.com

# Use the IP from your existing cluster route entries:
grep apps /etc/hosts
# Then add the new hostname on the same IP
sudo vi /etc/hosts
```

This is only needed once per cluster. The Route is cleaned up automatically
when the script exits.

### Local Test Options

| Option | Description |
|--------|-------------|
| `--setup` | Create/update virtual environment |
| `--clean-sources` | Delete all sources before running tests |
| `--filter EXPR` | Pytest -k filter expression |
| `--profile PROFILE` | Test profile (`smoke`, `extended`, `stable`, `full`) |
| `--marker EXPR` | Pytest marker expression (default: `cost_ocp_on_prem`) |
| `--nise-version VER` | Override koku-nise version |
| `--dry-run` | Show configuration without executing |
| `--verbose` | Enable verbose output |

## Test Duration and Resources

### Expected Test Duration

| Profile | Optimized | Unoptimized | Notes |
|---------|-----------|-------------|-------|
| `smoke` | ~17 min | ~30 min | PR validation |
| `extended` | ~25 min | ~50 min | Daily CI |
| `stable` | ~33 min | ~60 min | Weekly CI |
| `full` | ~42 min | **8+ hours** | Release validation (fail-fast required) |

> Optimized = listener CPU max + fail-fast + masu route (local).
> Unoptimized = default listener CPU, no fail-fast, port-forward (local).
> Full profile without fail-fast stalls indefinitely on GPU/MIG fixtures.

### Why Tests Take Long

IQE cost-management tests are I/O and backend-bound, not compute-bound:
- Each source creation waits up to 10 minutes for data ingestion
- Tests run sequentially (no `@pytest.mark.parallel` markers)
- Multiple sources are created across the test suite
- The listener processes uploaded files serially via a single Kafka consumer

### Resource Bottlenecks

The backend processing speed depends on:
- **Koku Listener** - Processes uploaded files (single-threaded, CPU-bound)
- **Celery workers** - Run summarization tasks after ingestion
- **PostgreSQL** - Handle cost data queries
- **Kafka** - Message queue throughput

A cluster with dedicated worker nodes allows these workloads to run without competing with the control plane.

## Test Performance Optimizations

Three optimizations significantly reduce test run times. All are safe for
regular use.

### 1. Listener CPU Boost (`--listener-cpu`)

The koku listener is the primary bottleneck — it processes uploaded CSV files
serially through a single Kafka consumer. By default it runs with a low CPU
limit, which throttles parquet conversion and SQL insertion.

**Containerized (CI):**
```bash
./scripts/deploy-test-cost-onprem.sh --tests-only --run-iqe \
    --listener-cpu max --iqe-profile extended
```

**What `max` does:** Temporarily patches the listener deployment to use the
node's full allocatable CPU (typically 4 cores), then restores the original
limit after the test run. Any specific value like `1000m` or `2000m` also works.

**Impact:** ~40-50% faster source processing. A source that takes 5 min at
default CPU processes in ~2-3 min at max.

> The local script (`run-iqe-tests-local.sh`) does not manage listener CPU.
> To boost it for local runs, patch manually before running:
> ```bash
> kubectl patch deploy cost-onprem-koku-listener -n cost-onprem \
>     -p '{"spec":{"template":{"spec":{"containers":[{"name":"koku-listener","resources":{"limits":{"cpu":"4"},"requests":{"cpu":"2"}}}]}}}}'
> ```

### 2. Fail-Fast Processing Detection (IQE Plugin)

When backend processing fails permanently (e.g., GPU/MIG schema mismatch),
the IQE plugin now detects it within ~10 seconds instead of polling for 30+
minutes until timeout.

**Branch:** `flpath-3369-updates-for-cost-onprem` in `iqe-cost-management-plugin`

**What it does:** Two checks added to the ingestion polling loop:
- `check_processing_failed()` — catches `status.processing.state == "failed"`
- `check_manifest_stalled()` — catches partial file processing with null
  `manifest_complete_date`

Both call `pytest.fail()` with an actionable error message including the
source UUID, period, file count, and assembly ID.

**Impact:** Full profile drops from 8+ hours to ~42 min. Each stalled source
saves 30+ min of wasted polling.

**Safety:** Confirmed that backend `kafka_msg_handler.py` commits the Kafka
offset on `ReportProcessorError`, meaning failed messages are never retried.
Fail-fast does not interfere with any self-healing mechanism.

### 3. Masu Route (Local Tests Only)

The local test runner needs to reach the masu service from outside the cluster.
Previously this used `kubectl port-forward`, which would silently die mid-run
causing connection refused errors.

**What it does:** The script now creates an edge-terminated OpenShift Route for
the masu service and configures the IQE plugin to use HTTPS with the route
hostname. The route is cleaned up on exit.

**Impact:** Zero connection errors (previously 1,734 in a full profile run).
No background processes, no PID tracking, no self-healing loop.

See the "DNS / `/etc/hosts` Requirement" section above if the route hostname
doesn't resolve.

## Troubleshooting

### "Failed to pull image" Error

Ensure your Quay account has access:
```bash
# Test pull access
podman pull quay.io/cloudservices/iqe-tests:cost-management
```
### "Connection refused" to gitlab

You're not on the Red Hat network. Connect to VPN and retry.


If this fails, verify your `app-interface` user file has been merged.

### "Cannot resolve" Masu Route Hostname (Local Tests)

The local script creates an OpenShift Route for masu. If the hostname can't be
resolved (common on QE lab clusters without wildcard DNS), the script exits with
the exact `/etc/hosts` entry to add. See the "DNS / `/etc/hosts` Requirement"
section above.

### Tests Stuck on "Line item summary update not complete"

The backend is slow processing data. Options:
1. Wait longer (can take 10+ minutes per source)
2. Use a cluster with more resources
3. Skip the problematic test with `--filter "not test_name"`

### Jinja2 UndefinedError for 'main'

The local script sets environment variables directly. If you see this error, ensure you're using the latest `run-iqe-tests-local.sh` which sets `DYNACONF_*` variables correctly.

### IntegrityError on Source Update

This is a known backend bug (PATCH creates instead of updates). The default filter skips affected tests. See `FLPATH-sources-update-integrityerror.md` for details.

## Known Test Issues

The default `IQE_FILTER` in `run-iqe-tests-local.sh` skips:
- `ai_workloads` - Not applicable to on-prem
- `distro` - Not applicable to on-prem  
- `test_api_cost_model_rates_update_to_tag_based` - Backend processing timeout

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `NAMESPACE` | `cost-onprem` | Target Kubernetes namespace |
| `IQE_MARKER` | `cost_ocp_on_prem` | Pytest marker expression |
| `IQE_FILTER` | (see script) | Pytest -k filter |
| `IQE_TIMEOUT` | `14400` | Test timeout in seconds (containerized, 4 hours) |
| `IQE_CORE_PATH` | `../iqe-core` | Path to iqe-core repo |
| `IQE_PLUGIN_PATH` | `../iqe-cost-management-plugin` | Path to plugin repo |
| `VENV_PATH` | `.venv-iqe` | Virtual environment path |

## See Also

- [IQE Core](https://gitlab.cee.redhat.com/insights-qe/iqe-core) (Red Hat internal)
- [Cost Management Plugin](https://gitlab.cee.redhat.com/insights-qe/iqe-cost-management-plugin) (Red Hat internal)
