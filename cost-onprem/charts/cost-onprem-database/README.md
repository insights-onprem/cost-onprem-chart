# cost-onprem-database

PostgreSQL database subchart for Cost Management On-Premise.

## Overview

This subchart deploys a single-instance PostgreSQL 16 StatefulSet with init scripts that create the required databases and users for ROS, Kruize, and Koku services. It is designed to be deployed as a conditional dependency of the parent `cost-onprem` chart (`database.deploy: true`), but can also be installed standalone for development or testing.

## Standalone Installation

### Prerequisites

Create the credentials secret before installing:

```bash
kubectl create namespace cost-onprem

kubectl create secret generic cost-onprem-db-credentials \
  --namespace cost-onprem \
  --from-literal=postgres-user=postgres \
  --from-literal=postgres-password="$(openssl rand -base64 32 | tr -d '=+/' | cut -c1-32)" \
  --from-literal=ros-user=ros_user \
  --from-literal=ros-password="$(openssl rand -base64 32 | tr -d '=+/' | cut -c1-32)" \
  --from-literal=kruize-user=kruize_user \
  --from-literal=kruize-password="$(openssl rand -base64 32 | tr -d '=+/' | cut -c1-32)" \
  --from-literal=koku-user=koku_user \
  --from-literal=koku-password="$(openssl rand -base64 32 | tr -d '=+/' | cut -c1-32)"
```

### Install

```bash
helm install cost-onprem-db ./cost-onprem/charts/cost-onprem-database \
  --namespace cost-onprem \
  --set global.databases.ros.name=costonprem_ros \
  --set global.databases.kruize.name=costonprem_kruize \
  --set global.databases.koku.name=costonprem_koku \
  --set global.storageClass=ocs-storagecluster-ceph-rbd
```

### Verify

```bash
# Check the StatefulSet is running
kubectl get statefulset -n cost-onprem -l app.kubernetes.io/component=database

# Check the init script created all databases
kubectl exec -n cost-onprem cost-onprem-db-database-0 -- \
  psql -U postgres -c "\l" | grep costonprem
```

## Values

| Key | Default | Description |
|-----|---------|-------------|
| `nameOverride` | `database` | Override for resource naming (produces `<release>-database`) |
| `image.repository` | `quay.io/insights-onprem/postgresql` | PostgreSQL image |
| `image.tag` | `16` | Image tag |
| `port` | `5432` | PostgreSQL listen port |
| `storage.size` | `30Gi` | PVC size for database data |
| `secretName` | `""` | Override credentials secret name (default: `<release>-db-credentials`) |
| `global.databases.ros.name` | *(required)* | ROS database name |
| `global.databases.kruize.name` | *(required)* | Kruize database name |
| `global.databases.koku.name` | *(required)* | Koku database name |
| `global.storageClass` | `ocs-storagecluster-ceph-rbd` | StorageClass for the PVC |
| `global.volumeMode` | `Filesystem` | PVC volume mode |
| `global.parentChartName` | `cost-onprem` | Used for selector label consistency with parent chart |

## Helm Hooks

All database resources use `pre-install,pre-upgrade` hooks to ensure the database is ready before application pods and migration jobs start:

| Resource | Hook Weight | Purpose |
|----------|-------------|---------|
| ConfigMap (init script) | `-15` | Created first; mounted by StatefulSet |
| StatefulSet | `-10` | PostgreSQL pod with init script execution |
| Service | `-10` | ClusterIP service for database connectivity |

## Notes

- **Not intended for production.** This subchart deploys a single-instance PostgreSQL without replication, backup, or HA. Production deployments should use an external managed database (BYOI) by setting `database.deploy: false` in the parent chart.
- The `global.databases.*` values are the single source of truth for database names, shared between this subchart (init script) and the parent chart (application connection strings).
- The credentials secret must exist before the StatefulSet starts. When deployed via the parent chart, the install script creates it automatically.
