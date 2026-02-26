# Troubleshoot Test Failures

Diagnose and fix failing tests in the cost-onprem test suite.

## Required Information

Please provide:
1. **Error output**: Paste the pytest output, JUnit XML, or CI build log
2. **Environment**: Local cluster or CI (`pull-ci-insights-onprem-cost-onprem-chart-main-e2e`)?
3. **Namespace**: Which namespace is the deployment in? (default: `cost-onprem`)

### For CI Failures

If this is a CI failure, provide the gcsweb URL or build ID:
```
https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/insights-onprem_cost-onprem-chart/<PR>/pull-ci-insights-onprem-cost-onprem-chart-main-e2e/<BUILD_ID>/
```

Download artifacts with:
```bash
./scripts/download-ci-artifacts.sh --url "<PASTE_URL_HERE>"
```

## CI Job Structure

The `pull-ci-insights-onprem-cost-onprem-chart-main-e2e` job runs:
1. `insights-onprem-s4-deploy` - Deploy S4 (~40s)
2. `insights-onprem-cost-onprem-chart-e2e` - **Runs pytest** (~18m)

Pytest output is in: `artifacts/e2e/insights-onprem-cost-onprem-chart-e2e/build-log.txt`

## Common Failure Patterns

### "Database pod not found"
Tests use `app.kubernetes.io/component=database` label selector.
```bash
kubectl get pods -n ${NAMESPACE} -l app.kubernetes.io/component=database
```

### "Route not found" / "Ingress service returning 503"
```bash
kubectl get routes -n ${NAMESPACE}
kubectl get pods -n ${NAMESPACE} -l app.kubernetes.io/component=ingress
kubectl logs -n ${NAMESPACE} -l app.kubernetes.io/component=ingress --tail=50
```

### "JWT token expired" / "401 Unauthorized"
```bash
# Check Keycloak is running
kubectl get pods -n ${KEYCLOAK_NAMESPACE:-keycloak}

# Verify client secret exists
kubectl get secret -n ${NAMESPACE} | grep keycloak
```

### "Summary tables not populated" (test_06)
```bash
# Check Koku listener logs for processing status
kubectl logs -n ${NAMESPACE} -l app.kubernetes.io/component=listener --tail=100

# Verify manifest has start/end dates
# The manifest.json must include "start" and "end" fields
```

### "Kruize experiments not created" (test_07)
```bash
# Check ROS processor logs
kubectl logs -n ${NAMESPACE} -l app.kubernetes.io/component=ros-processor --tail=100

# Look for TLS or S3 errors
kubectl logs -n ${NAMESPACE} -l app.kubernetes.io/component=ros-processor | grep -i "error\|x509\|signature"
```

### S3 SignatureDoesNotMatch
```bash
# Verify boto3 path-style addressing is configured
kubectl get configmap -n ${NAMESPACE} cost-onprem-aws-config -o yaml

# Check AWS_CONFIG_FILE env var is set
kubectl get deployment -n ${NAMESPACE} -o yaml | grep -A2 AWS_CONFIG_FILE
```

## Diagnostic Commands

```bash
# All pods status
kubectl get pods -n ${NAMESPACE} -l app.kubernetes.io/instance=cost-onprem

# Recent events
kubectl get events -n ${NAMESPACE} --sort-by='.lastTimestamp' | tail -20

# Component logs
kubectl logs -n ${NAMESPACE} -l app.kubernetes.io/component=listener --tail=100
kubectl logs -n ${NAMESPACE} -l app.kubernetes.io/component=ros-processor --tail=100
kubectl logs -n ${NAMESPACE} -l app.kubernetes.io/component=cost-processor --tail=100
```

## Re-running Failed Tests

```bash
# Run specific test
pytest tests/suites/e2e/test_complete_flow.py::TestCompleteDataFlow::test_06_summary_tables_populated -v

# Run with verbose output
pytest -v -s --tb=long

# Run with debugger on failure
pytest -x --pdb
```

## CI vs Local Differences

| Aspect | CI | Local |
|--------|-----|-------|
| Namespace | `cost-onprem` | Often `cost-onprem-ocp` |
| S3 Backend | S4 | ODF/NooBaa |
| Extended tests | Skipped by default | Can run with `--extended` |
| Cluster | Fresh pool cluster | Persistent dev cluster |
