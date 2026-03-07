# IQE Testing Setup Guide

This guide covers the prerequisites and setup required to run IQE (Insights QE) tests against the cost-onprem deployment.

## Overview

There are two ways to run IQE tests:
1. **Containerized** (`run-iqe-tests.sh`) - Runs tests in a container on the cluster
2. **Local** (`run-iqe-tests-local.sh`) - Runs tests directly from local repositories

## Prerequisites

### 1. Red Hat Network Access

**Required for both containerized and local testing.**

You must be connected to the Red Hat internal network (VPN or office network) to:
- Pull IQE container images from `quay.io/cloudservices/iqe-tests`
- Clone IQE repositories from `gitlab.cee.redhat.com`
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

# IQE Core framework (requires RH network)
git clone git@gitlab.cee.redhat.com:insights-qe/iqe-core.git

# Cost Management IQE plugin (requires RH network)
git clone git@gitlab.cee.redhat.com:insights-qe/iqe-cost-management-plugin.git

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

# Increase timeout (default 1800s)
./scripts/run-iqe-tests.sh --timeout 3600
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

### Local Test Options

| Option | Description |
|--------|-------------|
| `--setup` | Create/update virtual environment |
| `--clean-sources` | Delete all sources before running tests |
| `--filter EXPR` | Pytest -k filter expression |
| `--marker EXPR` | Pytest marker expression (default: `cost_ocp_on_prem`) |
| `--skip-portforward` | Don't start masu port-forward |
| `--nise-version VER` | Override koku-nise version |
| `--dry-run` | Show configuration without executing |
| `--verbose` | Enable verbose output |

## Test Duration and Resources

### Expected Test Duration

| Environment | Duration | Notes |
|-------------|----------|-------|
| 3 control plane only | 1-2+ hours | May timeout on ingestion waits |
| 3 CP + 2 workers | 30-60 minutes | Recommended configuration |
| Containerized (default timeout) | 30 minutes | May need `--timeout 3600` |

### Why Tests Take Long

IQE cost-management tests are I/O and backend-bound, not compute-bound:
- Each source creation waits up to 10 minutes for data ingestion
- Tests run sequentially (no `@pytest.mark.parallel` markers)
- Multiple sources are created across the test suite

### Resource Bottlenecks

The backend processing speed depends on:
- **Celery workers** - Process ingestion tasks
- **PostgreSQL** - Handle cost data queries
- **Kafka** - Message queue throughput

A cluster with dedicated worker nodes allows these workloads to run without competing with the control plane.

## Troubleshooting

### "Failed to pull image" Error

Ensure your Quay account has access:
```bash
# Test pull access
podman pull quay.io/cloudservices/iqe-tests:cost-management
```

If this fails, verify your `app-interface` user file has been merged.

### "Connection refused" to gitlab.cee.redhat.com

You're not on the Red Hat network. Connect to VPN and retry.

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
| `IQE_TIMEOUT` | `1800` | Test timeout in seconds (containerized) |
| `IQE_CORE_PATH` | `../iqe-core` | Path to iqe-core repo |
| `IQE_PLUGIN_PATH` | `../iqe-cost-management-plugin` | Path to plugin repo |
| `VENV_PATH` | `.venv-iqe` | Virtual environment path |

## See Also

- [IQE Core Documentation](https://gitlab.cee.redhat.com/insights-qe/iqe-core)
- [Cost Management Plugin](https://gitlab.cee.redhat.com/insights-qe/iqe-cost-management-plugin)
- [app-interface Repository](https://gitlab.cee.redhat.com/service/app-interface)
