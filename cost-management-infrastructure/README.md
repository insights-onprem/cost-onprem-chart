# Cost Management Infrastructure Chart

This Helm chart deploys the infrastructure components for Cost Management, specifically PostgreSQL. This follows the SaaS pattern where infrastructure is managed separately from the application.

## Architecture

```
┌─────────────────────────────────────┐
│  Cost Management Infrastructure    │
│  (This Chart)                       │
│                                     │
│  ┌─────────────────────────────┐   │
│  │   PostgreSQL StatefulSet    │   │
│  │   - Persistent Storage      │   │
│  │   - Database: koku          │   │
│  │   - User: koku              │   │
│  │   - Extensions & Roles      │   │
│  └─────────────────────────────┘   │
└─────────────────────────────────────┘
                 ▲
                 │ External Connection
                 │
┌─────────────────────────────────────┐
│  Cost Management Application        │
│  (cost-management-onprem chart)     │
│                                     │
│  - Koku API Pods                    │
│  - Celery Workers                   │
│  - Redis (cache)                    │
│  - Connects to external PostgreSQL  │
└─────────────────────────────────────┘
```

## Why Separate Infrastructure?

1. **Independent Lifecycle**: Redeploy application without losing data
2. **SaaS Parity**: Mirrors production architecture where infrastructure is managed separately
3. **Customer Choice**: Customers can use their own existing PostgreSQL
4. **Proper Migrations**: Database initialization happens before application deployment

## PostgreSQL Configuration

This chart uses the official Red Hat PostgreSQL 13 image (`registry.redhat.io/rhel8/postgresql-13:1-109`).

**Environment Variables:**
- `POSTGRESQL_DATABASE`: Database name (default: koku)
- `POSTGRESQL_USER`: Database user (default: koku)
- `POSTGRESQL_PASSWORD`: User password (auto-generated, stored in secret)
- `PGDATA`: Data directory (/var/lib/postgresql/data/pgdata)

The `postgres` superuser is accessible only via local connections for security.

## Prerequisites

- Kubernetes/OpenShift cluster
- `kubectl` or `oc` CLI
- Helm 3.x
- Storage class for persistent volumes (default will be used)

## Quick Start

### 1. Bootstrap Infrastructure

Use the bootstrap script to deploy and initialize everything:

```bash
./scripts/bootstrap-infrastructure.sh --namespace cost-mgmt
```

This script will:
- Deploy PostgreSQL with persistent storage
- Wait for database to be ready
- Create required extensions (`pg_stat_statements`)
- Create required roles (`hive`)
- Run Django database migrations
- Validate the setup

### 2. Deploy Application

After infrastructure is ready, deploy the application:

```bash
helm upgrade --install cost-mgmt ./cost-management-onprem \
  --namespace cost-mgmt
  # Database connection is pre-configured in values.yaml:
  # host: postgres
  # secretName: postgres-credentials
```

## Manual Deployment

If you prefer to deploy manually:

### 1. Deploy Infrastructure Chart

```bash
helm upgrade --install cost-mgmt-infra ./cost-management-infrastructure \
  --namespace cost-mgmt \
  --create-namespace \
  --wait
```

### 2. Initialize Database

Wait for PostgreSQL to be ready:

```bash
kubectl wait --for=condition=ready pod/postgres-0 -n cost-mgmt --timeout=5m
```

Create extensions and roles:

```bash
POD=postgres-0
kubectl exec $POD -n cost-mgmt -- psql -U postgres -d koku -c \
  "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"

kubectl exec $POD -n cost-mgmt -- psql -U postgres -d postgres -c \
  "CREATE ROLE hive WITH LOGIN PASSWORD 'hive';"

kubectl exec $POD -n cost-mgmt -- psql -U postgres -d postgres -c \
  "CREATE DATABASE hive OWNER hive;"
```

### 3. Run Migrations

See the bootstrap script for migration job creation, or use the application's migration job.

## Configuration

### Using External PostgreSQL

To use your own PostgreSQL instead of deploying one:

1. Set `postgresql.enabled: false` in values.yaml
2. Create a secret with credentials:

```bash
kubectl create secret generic my-postgresql-credentials \
  --from-literal=database=koku \
  --from-literal=username=koku \
  --from-literal=password=<your-password> \
  -n cost-mgmt
```

3. Configure the application chart to use your database:

```bash
# Update values.yaml:
# costManagement.database.host: my-postgresql-host
# costManagement.database.secretName: my-postgresql-credentials

helm upgrade --install cost-mgmt ./cost-management-onprem \
  --namespace cost-mgmt
```

### Custom Storage Class

```bash
helm upgrade --install cost-mgmt-infra ./cost-management-infrastructure \
  --set postgresql.persistence.storageClassName=my-storage-class
```

### Resource Limits

```bash
helm upgrade --install cost-mgmt-infra ./cost-management-infrastructure \
  --set postgresql.resources.requests.cpu=1000m \
  --set postgresql.resources.requests.memory=2Gi
```

## Database Initialization Details

The bootstrap script performs these initialization steps:

1. **Extension Creation**: `pg_stat_statements` for query performance monitoring
2. **Role Creation**: `hive` role required by Django migration `0039_create_hive_db`
3. **Migration Check**: Verifies which migrations need to run
4. **Migration Execution**: Runs pending Django migrations

## Troubleshooting

### PostgreSQL Pod Not Starting

Check events:
```bash
kubectl describe pod postgres-0 -n cost-mgmt
```

Check PVC status:
```bash
kubectl get pvc -n cost-mgmt
```

### Connection Issues

Test database connectivity:
```bash
kubectl exec postgres-0 -n cost-mgmt -- \
  psql -U postgres -d koku -c "SELECT version();"
```

### Migration Failures

View migration job logs:
```bash
kubectl logs job/cost-mgmt-migration-<timestamp> -n cost-mgmt
```

## Maintenance

### Backup Database

```bash
kubectl exec postgres-0 -n cost-mgmt -- \
  pg_dump -U koku koku > backup-$(date +%Y%m%d).sql
```

### Restore Database

```bash
cat backup.sql | kubectl exec -i postgres-0 -n cost-mgmt -- \
  psql -U koku koku
```

### Upgrade PostgreSQL

1. Backup database
2. Update image version in values.yaml
3. Helm upgrade infrastructure chart
4. Verify application connectivity

## Values Reference

| Parameter | Description | Default |
|-----------|-------------|---------|
| `postgresql.enabled` | Deploy PostgreSQL | `true` |
| `postgresql.image.repository` | PostgreSQL image | `quay.io/sclorg/postgresql-13-c9s` |
| `postgresql.database.name` | Database name | `koku` |
| `postgresql.database.user` | Database user | `koku` |
| `postgresql.persistence.size` | PVC size | `20Gi` |
| `postgresql.resources.requests.cpu` | CPU request | `500m` |
| `postgresql.resources.requests.memory` | Memory request | `1Gi` |

## Support

For issues or questions:
- Check application logs: `kubectl logs -n cost-mgmt -l app.kubernetes.io/component=api`
- Check database logs: `kubectl logs -n cost-mgmt postgres-0`
- Run bootstrap script: `./scripts/bootstrap-infrastructure.sh --namespace cost-mgmt`

## Services Created

| Resource | Name | Type | Description |
|----------|------|------|-------------|
| StatefulSet | `postgres` | Workload | PostgreSQL database |
| Service | `postgres` | ClusterIP | Database service endpoint |
| Secret | `postgres-credentials` | Opaque | Database credentials (auto-generated) |
| PVC | `postgresql-data-postgres-0` | Storage | Persistent database storage |


- Run bootstrap script: `./scripts/bootstrap-infrastructure.sh --namespace cost-mgmt`

## Services Created

| Resource | Name | Type | Description |
|----------|------|------|-------------|
| StatefulSet | `postgres` | Workload | PostgreSQL database |
| Service | `postgres` | ClusterIP | Database service endpoint |
| Secret | `postgres-credentials` | Opaque | Database credentials (auto-generated) |
| PVC | `postgresql-data-postgres-0` | Storage | Persistent database storage |

