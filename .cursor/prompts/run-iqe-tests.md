# Run IQE Tests

Run IQE (Insights QE) integration tests against the cost-onprem deployment.

## Prerequisites

Before running IQE tests:
1. **Cluster access**: `oc whoami` returns your username
2. **Cost-onprem deployed**: `helm list -n cost-onprem` shows the release
3. **Keycloak configured**: Authentication is set up
4. **Network access**: Connected to Red Hat VPN (for local tests)

## Test Methods

### Containerized Tests (run-iqe-tests.sh)

Runs tests in a container on the cluster. Requires Quay.io access.

```bash
# Basic run (30 min timeout)
./scripts/run-iqe-tests.sh

# Extended timeout for slow clusters
./scripts/run-iqe-tests.sh --timeout 3600

# Custom filter
./scripts/run-iqe-tests.sh --filter "test_api_ocp_source_crud"
```

### Local Tests (run-iqe-tests-local.sh)

Runs tests from local IQE repositories. Requires VPN access.

```bash
# First time: setup virtual environment
./scripts/run-iqe-tests-local.sh --setup

# Run tests with source cleanup
./scripts/run-iqe-tests-local.sh --clean-sources

# Dry run to verify configuration
./scripts/run-iqe-tests-local.sh --dry-run
```

## Common Options

| Option | Containerized | Local | Description |
|--------|--------------|-------|-------------|
| `--filter EXPR` | ✓ | ✓ | Pytest -k filter |
| `--marker EXPR` | ✓ | ✓ | Pytest marker |
| `--timeout SEC` | ✓ | - | Test timeout |
| `--clean-sources` | - | ✓ | Delete sources before tests |
| `--setup` | - | ✓ | Create/update venv |

## Expected Duration

- **3 control plane only**: 1-2+ hours (may timeout)
- **3 CP + 2 workers**: 30-60 minutes
- **Default timeout**: 30 minutes

## Troubleshooting

### Tests stuck waiting for ingestion
Backend is slow. Use a cluster with worker nodes or increase timeout.

### "Failed to pull image"
Need Quay.io access. See `docs/development/iqe-testing-setup.md`.

### Connection refused to gitlab
Not on Red Hat network. Connect to VPN.

## See Also

- Full setup guide: `docs/development/iqe-testing-setup.md`
- Script help: `./scripts/run-iqe-tests-local.sh --help`
