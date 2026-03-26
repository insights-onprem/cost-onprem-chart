# Run IQE Tests

Run IQE (Insights QE) integration tests against the cost-onprem deployment.

## Which Script?

| Goal | Command |
|------|---------|
| Containerized tests with CPU boost (recommended) | `./scripts/deploy-test-cost-onprem.sh --tests-only --run-iqe --listener-cpu max --iqe-profile smoke` |
| Containerized tests (standalone) | `./scripts/run-iqe-tests.sh --profile smoke` |
| Local tests from IQE repo clones | `./scripts/run-iqe-tests-local.sh --profile smoke` |

`deploy-test-cost-onprem.sh --tests-only --run-iqe` wraps `run-iqe-tests.sh` and adds
listener CPU boosting (`--listener-cpu`) which significantly speeds up data ingestion.
Use the standalone `run-iqe-tests.sh` when you only need a simple pod-based run without
CPU management.

## Prerequisites

Before running IQE tests:
1. **Cluster access**: `oc whoami` returns your username
2. **Cost-onprem deployed**: `helm list -n cost-onprem` shows the release
3. **Keycloak configured**: Authentication is set up
4. **Network access**: Connected to Red Hat VPN

## Test Profiles

Use `--profile` (or `--iqe-profile` with `deploy-test-cost-onprem.sh`) to select scope:

| Profile | Tests | Duration | Use Case |
|---------|-------|----------|----------|
| `smoke` | ~43 | ~17 min | PR checks, quick validation |
| `extended` | ~2100 | ~33 min | Daily CI, broader coverage |
| `stable` | ~2350 | ~40 min | Weekly CI, comprehensive |
| `full` | ~3324 | ~60 min | Release validation (expect failures) |

> Durations measured with `--listener-cpu max` on test cluster (3 workers, 12 CPU / 32GB each).

## Containerized Tests

### With listener CPU boost (recommended)

```bash
# Smoke tests with CPU boost — skip deployment, just run IQE
./scripts/deploy-test-cost-onprem.sh --tests-only --run-iqe \
    --listener-cpu max --iqe-profile smoke

# Extended daily CI run
./scripts/deploy-test-cost-onprem.sh --tests-only --run-iqe \
    --listener-cpu max --iqe-profile extended
```

### Standalone

```bash
# Quick smoke tests
./scripts/run-iqe-tests.sh --profile smoke

# With source cleanup
./scripts/run-iqe-tests.sh --profile smoke --clean-sources

# Custom filter
./scripts/run-iqe-tests.sh --filter "test_api_ocp_source_crud"

# Keep pod for debugging
./scripts/run-iqe-tests.sh --profile smoke --keep-pod
```

## Local Tests

Runs tests directly from local IQE repository clones. Requires VPN access.

```bash
# First time: setup virtual environment
./scripts/run-iqe-tests-local.sh --setup

# Run smoke tests with source cleanup
./scripts/run-iqe-tests-local.sh --profile smoke --clean-sources

# Dry run to verify configuration
./scripts/run-iqe-tests-local.sh --profile smoke --dry-run
```

## Common Options

| Option | deploy-test | run-iqe-tests | run-iqe-tests-local | Description |
|--------|:-----------:|:-------------:|:-------------------:|-------------|
| `--profile` / `--iqe-profile` | `--iqe-profile` | `--profile` | `--profile` | Test profile (smoke/extended/stable/full) |
| `--filter EXPR` | `--iqe-filter` | `--filter` | `--filter` | Pytest -k filter |
| `--clean-sources` | - | yes | yes | Delete sources before tests |
| `--keep-sources` | - | yes | - | Reuse sources from previous run |
| `--listener-cpu` | yes | - | - | Boost listener CPU (e.g., `max`, `1000m`) |
| `--keep-pod` | - | yes | - | Keep IQE pod for debugging |
| `--timeout SEC` | - | yes | - | Test timeout (default 14400s) |
| `--setup` | - | - | yes | Create/update local venv |
| `--dry-run` | - | - | yes | Show config without running |

## Troubleshooting

### Tests stuck waiting for ingestion
Backend is slow. Boost listener CPU with `--listener-cpu max` or use a cluster with worker nodes.

### "Failed to pull image"
Need Quay.io access and VPN. See `docs/development/iqe-testing-setup.md`.

### "Cannot resolve" masu route hostname (local tests)
QE lab clusters lack wildcard DNS. Add the hostname to `/etc/hosts` — the script prints the exact entry needed.

## See Also

- Full setup guide: `docs/development/iqe-testing-setup.md`
- Skip groups and known issues: `docs/development/skipped-iqe-tests.md`
- Script help: `./scripts/run-iqe-tests.sh --help`
- Analyze test run logs: `@analyze-test-run`
