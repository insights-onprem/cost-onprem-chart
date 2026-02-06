# Interpod (Pod-to-Pod) Cluster Tests

This test suite executes commands inside the cluster using a dedicated test-runner pod. This allows testing internal pod-to-pod service communication without going through the external gateway.

## Purpose

These tests verify:
- **Pod-to-pod communication**: Services can communicate via ClusterIP
- **X-Rh-Identity handling**: Backend services correctly process identity headers
- **Data processing**: Verify data flows through the internal pipeline
- **Service health**: Internal health endpoints respond correctly

## Test Files

| File | Description |
|------|-------------|
| `test_koku_api.py` | Direct Koku API tests (bypassing gateway) |

## Running Tests

```bash
# Run all interpod tests
pytest -m interpod

# Run specific test file
pytest tests/suites/interpod/test_koku_api.py

# Run with verbose output
pytest -m interpod -v
```

## Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `test_runner_pod` | session | Dedicated pod for executing commands |
| `internal_api_url` | session | Internal Koku API URL (ClusterIP) |
| `internal_ros_api_url` | session | Internal ROS API URL (ClusterIP) |
| `internal_curl` | function | Helper to execute curl in test-runner pod |
| `internal_identity_header` | function | Pre-built X-Rh-Identity header |

## Test Runner Pod

The test-runner pod is a dedicated UBI9 container that provides:
- Consistent environment for all interpod tests
- Isolation from application pods
- Standard tools (curl available via exec)
- Clean logs (test output separate from app logs)

### Pod Specification

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: cost-onprem-test-runner
  labels:
    app.kubernetes.io/name: test-runner
    app.kubernetes.io/component: testing
spec:
  restartPolicy: Never
  containers:
  - name: runner
    image: registry.access.redhat.com/ubi9/ubi:latest
    command: ["sleep", "infinity"]
    resources:
      requests: {memory: "64Mi", cpu: "100m"}
      limits: {memory: "256Mi", cpu: "500m"}
```

## Architecture

```
┌─────────────────┐
│   Test Client   │
│    (pytest)     │
└────────┬────────┘
         │ kubectl exec
         ▼
┌─────────────────┐
│  Test Runner    │
│     Pod         │
│  (UBI9 + curl)  │
└────────┬────────┘
         │ HTTP (internal)
         ▼
┌─────────────────┐
│  ClusterIP      │
│   Services      │
│ (koku, ros...)  │
└─────────────────┘
```

## Why Interpod Tests?

Some scenarios require testing internal behavior:

1. **Bypass gateway**: Test backend logic without gateway overhead
2. **X-Rh-Identity injection**: Verify services handle identity headers correctly
3. **Pod-to-pod**: Test internal communication patterns
4. **Performance**: Faster than external route (no TLS termination)

## Notes

- The test-runner pod is created at session start and cleaned up at session end
- Set `E2E_CLEANUP_AFTER=false` to preserve the pod for debugging
- Tests should include the `@pytest.mark.interpod` marker
