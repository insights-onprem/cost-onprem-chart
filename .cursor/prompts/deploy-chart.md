# Deploy Cost On-Prem Helm Chart

Deploy the cost-onprem Helm chart to an OpenShift cluster.

## Prerequisites

1. **Cluster access**: `oc whoami` returns your username
2. **Helm installed**: `helm version`
3. **Namespace created**: `oc create namespace cost-onprem`

## Full Deployment (Recommended)

The `deploy-test-cost-onprem.sh` script handles everything:

```bash
./scripts/deploy-test-cost-onprem.sh --namespace cost-onprem --verbose
```

This will:
1. Deploy Red Hat Build of Keycloak (RHBK)
2. Deploy AMQ Streams/Kafka
3. Install the cost-onprem Helm chart
4. Configure TLS certificates
5. Run the pytest test suite

## Manual Helm Installation

If you need to install just the Helm chart:

```bash
# Create namespace
oc create namespace cost-onprem

# Install with OpenShift values
helm install cost-onprem ./cost-onprem \
  -n cost-onprem \
  -f openshift-values.yaml \
  --wait

# Or upgrade existing release
helm upgrade cost-onprem ./cost-onprem \
  -n cost-onprem \
  -f openshift-values.yaml \
  --wait
```

## Skip Specific Steps

```bash
# Skip Keycloak deployment
./scripts/deploy-test-cost-onprem.sh --skip-rhbk

# Skip Kafka/AMQ Streams deployment
./scripts/deploy-test-cost-onprem.sh --skip-kafka

# Skip Helm chart installation
./scripts/deploy-test-cost-onprem.sh --skip-helm

# Skip TLS configuration
./scripts/deploy-test-cost-onprem.sh --skip-tls

# Deploy only — skip chart tests
./scripts/deploy-test-cost-onprem.sh --skip-chart-tests
```

## Tests Against Existing Deployment

```bash
# Run chart tests only (no redeploy)
./scripts/deploy-test-cost-onprem.sh --skip-deploy

# Run only IQE integration tests (no deploy, no chart tests)
./scripts/deploy-test-cost-onprem.sh --iqe-only --iqe-profile smoke
```

## Performance Testing

Performance tests use `scripts/lib/perf-testing.sh` to automatically scale the
deployment (replicas, resources, timeouts) based on the selected profile before
running tests. See `@run-tests` for the full profile scaling matrix.

```bash
# Deploy + run performance tests
./scripts/deploy-test-cost-onprem.sh --namespace cost-onprem --run-perf --perf-profile medium

# Performance tests only (skip deploy, uses existing cluster)
./scripts/deploy-test-cost-onprem.sh --perf-only --perf-profile medium

# Specific perf suite
./scripts/deploy-test-cost-onprem.sh --perf-only --perf-suite ros,api
```

### Storage Backend Options

The deploy script auto-detects the storage class. For non-ODF clusters:

```bash
# S4 backend (auto-toolbox deploys)
./scripts/deploy-test-cost-onprem.sh --namespace cost-onprem --deploy-s4

# With explicit S3 credentials (when S4 is already deployed)
S3_ENDPOINT="s4.s4.svc.cluster.local" S3_PORT="7480" S3_USE_SSL="false" \
  ./scripts/deploy-test-cost-onprem.sh --namespace cost-onprem --skip-rhbk --skip-kafka

# Kafka broker storage override (for constrained clusters)
KAFKA_BROKER_STORAGE=10Gi ./scripts/deploy-test-cost-onprem.sh --namespace cost-onprem
```

## Dry Run

Preview what would be done without making changes:

```bash
./scripts/deploy-test-cost-onprem.sh --dry-run --verbose
```

## Troubleshooting Deployment

### "field is immutable" during upgrade
Label changes require fresh install:
```bash
helm uninstall cost-onprem -n cost-onprem
helm install cost-onprem ./cost-onprem -n cost-onprem -f openshift-values.yaml --wait
```

### Pods stuck in Pending
Check for resource constraints:
```bash
kubectl describe pod -n cost-onprem <pod-name>
kubectl get events -n cost-onprem --sort-by='.lastTimestamp'
```

## After Modifying deploy-test-cost-onprem.sh

If you change flag parsing or summary output, validate all permutations locally:
```bash
./scripts/qe/test-gh-workflow-locally.sh .github/workflows/validate-deploy-test-script.yml
```

This also runs automatically on PRs that touch the script.
