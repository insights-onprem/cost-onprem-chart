# OCP Development Setup with MinIO

Guide for setting up a development environment on OpenShift (OCP) using MinIO
instead of production S3 storage for S3-compatible object storage.

> **This setup is for development and testing only.** Production deployments
> should use dedicated S3 storage (AWS S3, ODF with Direct Ceph RGW, or another S3 provider).

## When to Use This

Use MinIO when:

- Your OCP cluster does not have dedicated object storage
- You are on a Single Node OpenShift (SNO) or resource-constrained cluster
- You want a lightweight S3-compatible backend without extra operators
- You are developing or testing chart changes locally

## Cluster Requirements

### Minimum Resources (with MinIO)

| Resource | Requirement |
|----------|-------------|
| **Nodes** | 1 (SNO supported) |
| **CPU** | 10 cores |
| **Memory** | 22 Gi |
| **Storage** | 10 Gi block storage |

MinIO runs as a single-replica Deployment with a PersistentVolumeClaim using the
cluster's default StorageClass. On SNO with LVMS, this typically uses a local
volume on the node's disk.

### Infrastructure Dependencies

The following must be deployed **before** the chart:

| Component | How to Deploy | Notes |
|-----------|---------------|-------|
| **Kafka (Strimzi)** | `./scripts/deploy-strimzi.sh` | Required for event streaming |
| **Keycloak (RHBK)** | `./scripts/deploy-rhbk.sh` | Required for JWT authentication |
| **MinIO** | `./scripts/deploy-minio-test.sh cost-onprem` | S3 storage for dev/test |

## Step-by-Step Setup

### 1. Deploy infrastructure

```bash
# Deploy Strimzi (Kafka)
./scripts/deploy-strimzi.sh

# Deploy Red Hat Build of Keycloak
./scripts/deploy-rhbk.sh

# Deploy MinIO into the chart namespace
./scripts/deploy-minio-test.sh cost-onprem
```

### 2. Install the Helm chart

```bash
MINIO_ENDPOINT=http://minio.cost-onprem.svc.cluster.local \
  ./scripts/install-helm-chart.sh
```

The install script detects `MINIO_ENDPOINT` and automatically:

- Locates the `minio-credentials` secret (by parsing the namespace from the FQDN)
- Creates the `cost-onprem-storage-credentials` secret used by the chart
- Passes `objectStorage.endpoint`, `objectStorage.port=80`, `objectStorage.useSSL=false` to Helm
- Creates the S3 buckets (names read from `values.yaml`: `insights-upload-perma`, `koku-bucket`, `ros-data`)

### 3. Verify the deployment

```bash
# Check all pods are running
kubectl get pods -n cost-onprem -l app.kubernetes.io/instance=cost-onprem

# Verify S3 connectivity (ingress should show the MinIO endpoint)
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

The chart itself has no MinIO-specific configuration. The `objectStorage.*` values
in `values.yaml` are generic S3 settings:

| Helm Value | Set By Install Script | Effect |
|------------|----------------------|--------|
| `objectStorage.endpoint` | `minio.cost-onprem.svc.cluster.local` | S3 hostname for all components |
| `objectStorage.port` | `80` | Service port (MinIO Service maps 80 → 9000) |
| `objectStorage.useSSL` | `false` | Use HTTP instead of HTTPS |

These values flow into the chart's template helpers:

- **Ingress**: `INGRESS_MINIOENDPOINT` gets the hostname
- **Koku/MASU**: `S3_ENDPOINT` gets `http://minio.cost-onprem.svc.cluster.local`
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

### Ingress upload fails with "dial tcp ...:80: i/o timeout"

The MinIO Service is not reachable. Check:

```bash
kubectl get svc minio -n cost-onprem
kubectl get pods -l app=minio -n cost-onprem
```

### Ingress upload fails with port 9000 errors

The MinIO Service is still using port 9000 (old configuration). Redeploy:

```bash
./scripts/deploy-minio-test.sh cost-onprem
```

The updated script creates the Service with `port: 80, targetPort: 9000`.

### Bucket creation fails

Verify MinIO credentials:

```bash
kubectl get secret minio-credentials -n cost-onprem -o jsonpath='{.data.access-key}' | base64 -d
kubectl get secret cost-onprem-storage-credentials -n cost-onprem -o jsonpath='{.data.access-key}' | base64 -d
```

Both should return the same access key.

### "S3 endpoint not configured" error during helm install

Make sure `MINIO_ENDPOINT` is set when running the install script:

```bash
MINIO_ENDPOINT=http://minio.cost-onprem.svc.cluster.local ./scripts/install-helm-chart.sh
```

## Cleanup

```bash
# Remove MinIO resources from the chart namespace
./scripts/deploy-minio-test.sh cost-onprem cleanup

# Or if deployed to a separate namespace, delete the whole namespace
kubectl delete namespace minio-test
```

[← Back to Development Documentation](README.md)
