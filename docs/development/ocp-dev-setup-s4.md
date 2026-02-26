# OCP Development Setup with S4

Guide for setting up a development environment on OpenShift (OCP) using S4
(Super Simple Storage Service) for S3-compatible object storage. S4 is an
open-source Ceph RGW with a SQLite backend.

> **This setup is for development and testing only.** Production deployments
> should use dedicated S3 storage (AWS S3, ODF with Direct Ceph RGW, or another S3 provider).

## When to Use This

Use S4 when:

- Your OCP cluster does not have dedicated object storage
- You are on a Single Node OpenShift (SNO) or resource-constrained cluster
- You want a lightweight S3-compatible backend without extra operators
- You are developing or testing chart changes locally

## Cluster Requirements

### Minimum Resources (with S4)

| Resource | Requirement |
|----------|-------------|
| **Nodes** | 1 (SNO supported) |
| **CPU** | 10 cores |
| **Memory** | 22 Gi |
| **Storage** | 10 Gi block storage |

S4 runs as a single-replica Deployment with a PersistentVolumeClaim using the
cluster's default StorageClass. On SNO with LVMS, this typically uses a local
volume on the node's disk.

### Infrastructure Dependencies

The following must be deployed **before** the chart:

| Component | How to Deploy | Notes |
|-----------|---------------|-------|
| **Kafka (Strimzi)** | `./scripts/deploy-strimzi.sh` | Required for event streaming |
| **Keycloak (RHBK)** | `./scripts/deploy-rhbk.sh` | Required for JWT authentication |
| **S4** | `./scripts/deploy-s4-test.sh cost-onprem` | S3 storage for dev/test |

## Step-by-Step Setup

### 1. Deploy infrastructure

```bash
# Deploy Strimzi (Kafka)
./scripts/deploy-strimzi.sh

# Deploy Red Hat Build of Keycloak
./scripts/deploy-rhbk.sh

# Deploy S4 into the chart namespace
./scripts/deploy-s4-test.sh cost-onprem
```

### 2. Install the Helm chart

```bash
S3_ENDPOINT=s4.cost-onprem.svc.cluster.local S3_PORT=7480 S3_USE_SSL=false \
  ./scripts/install-helm-chart.sh
```

The install script detects `S3_ENDPOINT` and automatically:

- Locates the `s4-credentials` secret (by parsing the namespace from the FQDN)
- Creates the `cost-onprem-storage-credentials` secret used by the chart
- Passes `objectStorage.endpoint`, `objectStorage.port=7480`, `objectStorage.useSSL=false` to Helm
- Creates the S3 buckets (names read from `values.yaml`: `insights-upload-perma`, `koku-bucket`, `ros-data`)

### 3. Verify the deployment

```bash
# Check all pods are running
kubectl get pods -n cost-onprem -l app.kubernetes.io/instance=cost-onprem

# Verify S3 connectivity (ingress should show the S4 endpoint)
kubectl get deployment cost-onprem-ingress -n cost-onprem \
  -o jsonpath='{.spec.template.spec.containers[0].env}' | python3 -m json.tool | grep -A1 MINIOENDPOINT

# Check ingress logs for upload errors
kubectl logs -n cost-onprem -l app.kubernetes.io/component=ingress --tail=20
```

### 4. Run tests

```bash
NAMESPACE=cost-onprem ./scripts/run-pytest.sh
```

## How It Works

The chart itself has no S4-specific configuration. The `objectStorage.*` values
in `values.yaml` are generic S3 settings:

| Helm Value | Set By Install Script | Effect |
|------------|----------------------|--------|
| `objectStorage.endpoint` | `s4.cost-onprem.svc.cluster.local` | S3 hostname for all components |
| `objectStorage.port` | `7480` | Ceph RGW S3 API port |
| `objectStorage.useSSL` | `false` | Use HTTP instead of HTTPS |

These values flow into the chart's template helpers:

- **Ingress**: `INGRESS_MINIOENDPOINT` gets `hostname:port` (env var name is from upstream insights-ingress-go)
- **Koku/MASU**: `S3_ENDPOINT` gets `http://s4.cost-onprem.svc.cluster.local:7480`
- **Init containers**: TCP check against `endpoint:port`

Bucket names are defined in `values.yaml` and referenced via standardized helpers:

| Helm Value | Default | Used By |
|------------|---------|---------|
| `ingress.storage.bucket` | `insights-upload-perma` | Ingress (`INGRESS_STAGEBUCKET`) |
| `costManagement.storage.bucketName` | `koku-bucket` | Koku (`REQUESTED_BUCKET`) |
| `costManagement.storage.rosBucketName` | `ros-data` | Koku (`REQUESTED_ROS_BUCKET`) |

The install script reads these bucket names from `values.yaml` and creates them
before Helm runs.

## Troubleshooting

### Ingress upload fails with connection errors to S4

The S4 Service is not reachable. Check:

```bash
kubectl get svc s4 -n cost-onprem
kubectl get pods -l app.kubernetes.io/name=s4 -n cost-onprem
```

### Bucket creation fails

Verify S4 credentials:

```bash
kubectl get secret s4-credentials -n cost-onprem -o jsonpath='{.data.access-key}' | base64 -d
kubectl get secret cost-onprem-storage-credentials -n cost-onprem -o jsonpath='{.data.access-key}' | base64 -d
```

Both should return the same access key.

### "S3 endpoint not configured" error during helm install

Make sure `S3_ENDPOINT`, `S3_PORT`, and `S3_USE_SSL` are set when running the install script:

```bash
S3_ENDPOINT=s4.cost-onprem.svc.cluster.local S3_PORT=7480 S3_USE_SSL=false \
  ./scripts/install-helm-chart.sh
```

### S4 pod not starting

Check events and logs:

```bash
kubectl describe pod -l app.kubernetes.io/name=s4 -n cost-onprem
kubectl logs -l app.kubernetes.io/name=s4 -n cost-onprem
```

## Cleanup

```bash
# Remove S4 resources from the chart namespace
./scripts/deploy-s4-test.sh cost-onprem cleanup

# Or if deployed to a separate namespace, delete the whole namespace
kubectl delete namespace s4-test
```

[← Back to Development Documentation](README.md)
