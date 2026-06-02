# Test Data Setup Guide

This guide covers how to set up test data for Cost On-Prem validation using the `setup-test-data.sh` script.

For detailed information on specific test types, see:
- [E2E Scenarios](../../tests/suites/e2e/README.md) - YAML-driven scenario definitions
- [Performance Profiles](../../tests/suites/performance/README.md) - Production-based sizing profiles
- [NISE Templates](../../tests/data/nise_templates/README.md) - Available data templates

## Quick Start

```bash
# List available scenarios
./scripts/setup-test-data.sh --list

# Set up data for E2E testing
./scripts/setup-test-data.sh --scenario baseline

# Set up data for performance testing
./scripts/setup-test-data.sh --scenario perf-small

# Clean up test data
./scripts/setup-test-data.sh --clean
```

## Available Scenarios

| Scenario | Clusters | Nodes | Days | ROS | Upload | Processing | Use Case |
|----------|----------|-------|------|-----|--------|------------|----------|
| `minimal` | 1 | 1 | 1 | No | <30s | <2min | Smoke tests |
| `baseline` | 1 | 2 | 7 | Yes | <2min | <10min | E2E tests |
| `perf-small` | 1 | 15 | 30 | Yes | <5min | <30min | Perf baseline |
| `perf-medium` | 2 | 49 | 30 | Yes | <15min | <60min | Scale testing |
| `perf-large` | 7 | 133 | 30 | Yes | <45min | <3hr | Stress testing |
| `ros` | 1 | 3 | 7 | Yes | <2min | <15min | ROS testing |

The `perf-*` scenarios align with the [Performance Profiles](../../tests/suites/performance/README.md#performance-profiles) based on production customer data.

## Prerequisites

1. **Cost On-Prem deployed** and healthy:
   ```bash
   ./scripts/run-pytest.sh --smoke
   ```

2. **Environment variables** (script auto-detects most):
   ```bash
   export NAMESPACE=cost-onprem           # Default
   export HELM_RELEASE_NAME=cost-onprem   # Default
   export KAFKA_NAMESPACE=kafka           # If separate namespace
   ```

## Script Options

```
./scripts/setup-test-data.sh [OPTIONS]

OPTIONS:
    --scenario <name>     Scenario to set up (required unless --clean)
    --list                List available scenarios
    --days <n>            Override days of data (default: scenario-specific)
    --clusters <n>        Override cluster count (default: scenario-specific)
    --source-prefix <s>   Prefix for source names (default: e2e)
    --no-wait             Don't wait for processing to complete
    --no-cleanup          Keep data after script exits
    --dry-run             Show what would be done
    --clean               Clean test data only
    --clean-prefix <s>    Clean sources matching prefix
```

## Pre-Test Environment Preparation

### Clean Environment Setup

Before running tests that depend on specific data states:

```bash
# 1. Clean existing test data
./scripts/setup-test-data.sh --clean

# This removes:
# - Sources with e2e-pytest- prefix
# - Database records for test clusters
# - Manifests and reports from test uploads
```

### Verify Clean State

```bash
# Check for existing test sources
oc exec -n cost-onprem deploy/cost-onprem-koku-api -- \
    psql -h localhost -U koku -d koku -c \
    "SELECT name FROM api_sources WHERE name LIKE 'e2e-%' OR name LIKE 'perf-%';"

# Should return: (0 rows)
```

### Full Reset (Development Only)

For complete environment reset:

```bash
# WARNING: This removes ALL data, not just test data
./scripts/setup-test-data.sh --reset-all

# Alternatively, redeploy
helm uninstall cost-onprem -n cost-onprem
# ... redeploy ...
```

## Scenario-Based Test Execution

### Pattern: Setup → Test → Cleanup

```bash
# 1. Setup data for scenario
./scripts/setup-test-data.sh --scenario baseline

# 2. Run tests that need the data
pytest tests/suites/e2e/test_complete_flow.py -v

# 3. Cleanup (optional, tests should self-cleanup)
./scripts/setup-test-data.sh --clean
```

### Pattern: Persistent Data for Manual Testing

```bash
# Setup data and keep it
./scripts/setup-test-data.sh --scenario perf-small --no-cleanup

# Data will persist across test runs
# Source names are printed for reference:
#   Source: perf-source-abc123
#   Cluster: perf-cluster-abc123

# Manually cleanup when done
./scripts/setup-test-data.sh --clean --source perf-source-abc123
```

### Pattern: Pre-Populated Environment for Exploration

```bash
# Setup multiple scenarios for UI exploration
./scripts/setup-test-data.sh --scenario baseline --source-prefix demo-baseline
./scripts/setup-test-data.sh --scenario ros --source-prefix demo-ros

# Environment now has:
# - demo-baseline-* source with standard cost data
# - demo-ros-* source with ROS recommendations

# Access UI to explore data
# Cleanup when done
./scripts/setup-test-data.sh --clean --source-prefix demo-
```

## Data Validation

### Verify Data Was Processed

```bash
# Check source was created
oc exec -n cost-onprem deploy/cost-onprem-koku-api -- \
    psql -h localhost -U koku -d koku -c \
    "SELECT id, name, source_type FROM api_sources WHERE name LIKE '%your-source%';"

# Check manifests were processed
oc exec -n cost-onprem deploy/cost-onprem-koku-api -- \
    psql -h localhost -U koku -d koku -c \
    "SELECT cluster_id, manifest_id, state FROM reporting_ocpusagereportmanifest ORDER BY creation_datetime DESC LIMIT 5;"

# Check summary tables have data
oc exec -n cost-onprem deploy/cost-onprem-koku-api -- \
    psql -h localhost -U koku -d koku -c \
    "SELECT COUNT(*) FROM reporting_ocpusagelineitem_daily_summary WHERE cluster_id = 'your-cluster-id';"
```

### Verify ROS Data (if applicable)

```bash
# Check Kruize experiments
oc exec -n cost-onprem deploy/cost-onprem-koku-api -- \
    psql -h localhost -U kruize -d costonprem_kruize -c \
    "SELECT experiment_name, cluster_name FROM public.kruize_experiments WHERE cluster_name LIKE '%your-cluster%';"

# Check recommendations exist
oc exec -n cost-onprem deploy/cost-onprem-koku-api -- \
    psql -h localhost -U kruize -d costonprem_kruize -c \
    "SELECT COUNT(*) FROM public.kruize_recommendations WHERE experiment_name LIKE '%your-cluster%';"
```

## Troubleshooting

### Data Not Appearing in API

1. **Check manifest processing**:
   ```bash
   # Look for processing errors
   oc logs -n cost-onprem -l app.kubernetes.io/component=koku-ocp-worker --tail=100 | grep -i error
   ```

2. **Check summary job ran**:
   ```bash
   oc exec -n cost-onprem deploy/cost-onprem-koku-api -- \
       psql -h localhost -U koku -d koku -c \
       "SELECT * FROM api_dataexportstatus ORDER BY updated_timestamp DESC LIMIT 5;"
   ```

3. **Flush cache** (if API returns stale data):
   ```bash
   oc exec -n cost-onprem deploy/cost-onprem-valkey -- valkey-cli FLUSHALL
   ```

### Upload Fails with Timeout

Gateway timeouts occur with large files (>20MB). Options:

1. **Reduce data size**:
   ```bash
   ./scripts/setup-test-data.sh --scenario baseline --days 3  # Instead of 7
   ```

2. **Increase timeout** (temporary, requires chart config):
   ```yaml
   # values.yaml
   koku:
     ingress:
       annotations:
         haproxy.router.openshift.io/timeout: 10m
   ```

### ROS Recommendations Not Generating

Kruize needs sufficient data history (typically 7+ days):

1. **Ensure 7 days of data**:
   ```bash
   ./scripts/setup-test-data.sh --scenario ros --days 7
   ```

2. **Check ROS queue**:
   ```bash
   oc exec -n kafka kafka-cluster-kafka-0 -- \
       bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 \
       --describe --group ros-processor
   ```

3. **Check Kruize logs**:
   ```bash
   oc logs -n cost-onprem -l app.kubernetes.io/component=kruize --tail=100
   ```

## Integration with CI/CD

### CI Pre-Test Data Setup

```yaml
# .github/workflows/e2e.yml
- name: Setup test data
  run: |
    ./scripts/setup-test-data.sh --scenario baseline --wait
    
- name: Run E2E tests
  run: |
    ./scripts/run-pytest.sh --e2e
```

### OpenShift CI Integration

The `deploy-test-cost-onprem.sh` script can setup data automatically:

```bash
# Include data setup in deployment
./scripts/deploy-test-cost-onprem.sh --setup-test-data baseline

# Or separately after deployment
./scripts/deploy-test-cost-onprem.sh
./scripts/setup-test-data.sh --scenario baseline --wait
./scripts/run-pytest.sh --e2e
```

## Related Documentation

- [Test Suite README](../../tests/README.md) - Test framework overview
- [Performance Testing](../../tests/suites/performance/README.md) - Performance test details
- [E2E Testing](../../tests/suites/e2e/README.md) - E2E scenario tests
- [NISE Templates](../../tests/data/nise_templates/README.md) - Available data templates
- [Sizing Guide](../performance/sizing-guide.md) - Resource requirements per profile
