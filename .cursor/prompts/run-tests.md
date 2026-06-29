# Run Tests

Run the pytest test suite against the connected OpenShift cluster.

## Prerequisites

Before running tests, ensure you have:
1. **Cluster access**: `oc whoami` returns your username
2. **Namespace exists**: `kubectl get namespace ${NAMESPACE:-cost-onprem}`
3. **Helm release deployed**: `helm list -n ${NAMESPACE:-cost-onprem}`

## Required Environment Variables

```bash
export NAMESPACE="cost-onprem"           # Target namespace (required)
export KEYCLOAK_NAMESPACE="keycloak"     # Keycloak namespace (optional)
export HELM_RELEASE_NAME="cost-onprem"   # Helm release name (optional)
```

## Commands

### Default CI Mode (~88 tests, ~3 minutes)
```bash
NAMESPACE=cost-onprem ./scripts/run-pytest.sh
```

### Extended Tests (requires ODF/S3, ~15 minutes)
```bash
NAMESPACE=cost-onprem ./scripts/run-pytest.sh --extended
```

### Specific Suites
```bash
./scripts/run-pytest.sh --helm           # Helm chart validation
./scripts/run-pytest.sh --auth           # JWT authentication
./scripts/run-pytest.sh --e2e            # End-to-end pipeline
./scripts/run-pytest.sh --infrastructure # DB, S3, Kafka health
./scripts/run-pytest.sh --ros            # ROS/Kruize health
```

### Smoke Tests (quick validation)
```bash
./scripts/run-pytest.sh --smoke
```

## Cleanup Options

```bash
E2E_CLEANUP_BEFORE=true   # Clean before tests (default)
E2E_CLEANUP_AFTER=true    # Clean after tests (default)
E2E_RESTART_SERVICES=false # Restart Valkey/listener (optional)
```

### Performance Tests

Performance tests use profile-based scaling via `scripts/lib/perf-testing.sh`.
The `apply_perf_profile_config()` function automatically adjusts the live cluster
(replica counts, resource limits, timeouts, upload sizes) before tests run.

```bash
# Full deploy + performance tests (baseline profile)
./scripts/deploy-test-cost-onprem.sh --namespace cost-onprem --run-perf

# Performance-only against existing deployment
./scripts/deploy-test-cost-onprem.sh --namespace cost-onprem --perf-only

# With a specific profile (baseline, small, medium, large)
./scripts/deploy-test-cost-onprem.sh --perf-only --perf-profile medium

# Run specific perf suite(s): api, ros, ingestion, scale, soak
./scripts/deploy-test-cost-onprem.sh --perf-only --perf-suite ros
./scripts/deploy-test-cost-onprem.sh --perf-only --perf-suite api,ingestion
```

#### Profile Scaling Matrix

| Profile  | Processor | Listener | OCP Worker | Summary Worker | Kruize CPU | Upload Size | Timeouts |
|----------|-----------|----------|------------|----------------|------------|-------------|----------|
| baseline | 1         | 1        | 1          | 1              | 500m/1000m | 100MB       | 30s      |
| small    | 1         | 2        | 2          | 2              | 1000m/2000m| 100MB       | 30s      |
| medium   | 2         | 2        | 2          | 2              | 1000m/2000m| 200MB       | 180s     |
| large    | 3         | 3        | 3          | 3              | 1000m/2000m| 200MB       | 180s     |

Kruize is always kept at 1 replica (scaling degrades throughput, see PERF-FINDING-014).
Listener CPU is automatically boosted to node max for perf runs.

#### Key Environment Variables

```bash
PERF_PROFILE=medium       # Profile to use (default: baseline)
PERF_SUITE=all            # Suite(s) to run (default: all)
LISTENER_CPU_LIMIT=max    # Listener CPU boost (default: max for perf runs)
```

## Output

- JUnit XML report: `tests/reports/junit.xml`
- Console output with test results
- Performance results: `tests/perf-runs/<run-id>/` (for perf tests)
