# Running chart tests against koku-server-operator

Findings from exercising the Helm chart pytest suite against an operator-managed
deployment (`CostManagementServiceConfig`) on clusterbot (2026-08-07).

## How to target an operator deploy

```bash
# CR name must match HELM_RELEASE_NAME (resource names are {cr.name}-*).
export NAMESPACE=cost-tests
export HELM_RELEASE_NAME=cost-onprem
export KEYCLOAK_NAMESPACE=keycloak
export DEPLOYMENT_MODE=operator

# Optional when objectStorage.secretName is not {release}-storage-credentials
export STORAGE_SECRET_NAME=cost-tests-s3-credentials

# Skip chart-only suites; focus on runtime suites first
./scripts/run-pytest.sh --auth --infrastructure --smoke --no-ui
```

## What aligns today

| Helm expectation | Operator behavior |
|------------------|-------------------|
| `{release}-koku-api`, `{release}-koku-masu`, `{release}-koku-listener` | Same naming from CR name |
| `{release}-api` OpenShift Route (path `/api`) | `GatewayAPIRoute` |
| `{release}-gateway` Envoy JWT proxy | Implemented (edge stage) |
| Labels `app.kubernetes.io/component=cost-management-api\|cost-processor\|listener\|cache\|database` | Present |
| `{release}-db-credentials`, `{release}-aws-config` | Present when using defaults |
| Keycloak client secrets from `deploy-rhbk.sh` | Same secret name patterns |

## Differences / gaps that break or skip tests

### Missing components (operator not implemented yet)

Referenced by Envoy routes / env vars but **not deployed**:

- **Ingress** (`{release}-ingress`) — gateway `/api/ingress/*` has no backend
- **RBAC** (`{release}-rbac-api`, `{release}-rbac-worker`) — `/api/rbac/` has no backend
- **UI** (`{release}-ui`) — no UI Deployment / OAuth route

Impacted suites: parts of `auth` (ingress ready probe, RBAC gateway),
`api/test_ingress`, `e2e/test_rbac_access`, `ui/*`, ROS paths that call RBAC.

### Celery worker labels

| Helm | Operator |
|------|----------|
| `app.kubernetes.io/component=cost-worker` + `cost-onprem.io/worker-queue=<q>` | `app.kubernetes.io/component=cost-worker-<queue>` (no queue label) |
| Some e2e hints use `worker-ocp` / `worker-summary` | Actual: `cost-worker-ocp`, `cost-worker-summary` |

### Storage credentials secret name

Operator uses `spec.objectStorage.secretName` when set, else
`{release}-storage-credentials`. Chart tests honor `STORAGE_SECRET_NAME` for a
custom Secret. Prefer naming the Secret `{HELM_RELEASE_NAME}-storage-credentials`
so no override is needed.

### Bundled vs BYOI infra

Kafka/object storage are external in the operator model. Deploy AMQ Streams with
`./scripts/deploy-kafka.sh` (not Redpanda). Infra tests look for
`strimzi.io/kind=Kafka` pods:

```bash
export KAFKA_NAMESPACE=kafka
# Bootstrap (default): cost-onprem-kafka-kafka-bootstrap.kafka.svc:9092
```

## Operator bug found during this run

**Migration / koku pods + `runAsNonRoot`** — koku image `USER koku` (UID 1000,
non-numeric name). With pod-level `runAsNonRoot`, kubelet fails with
`CreateContainerConfigError` unless container `runAsUser: 1000` is set.
See operator PR `fix/migrate-runasuser`.

## Recommended first pytest pass (once CR is Ready)

```bash
NAMESPACE=cost-tests HELM_RELEASE_NAME=cost-onprem DEPLOYMENT_MODE=operator \
  ./scripts/run-pytest.sh --auth --infrastructure --smoke --no-ui
```

Expect skips/failures around Ingress/RBAC/UI until those stages land in the
operator. Auth gateway JWT tests that only need Envoy + Keycloak + koku-api
should be the first green slice.
