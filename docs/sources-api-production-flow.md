# Sources API Provider Creation Flow

This document describes provider creation using the Sources API, which mirrors Red Hat's production architecture.

## Architecture Overview

```
┌─────────────────┐
│   User/Script   │
└────────┬────────┘
         │ HTTP POST
         ▼
┌─────────────────────┐
│  Sources API (Go)   │ ← External route
└──────────┬──────────┘
           │ Kafka Publish
           ▼
┌─────────────────────┐
│  Kafka Topic        │
│  platform.sources.  │
│  event-stream       │
└──────────┬──────────┘
           │ Consume
           ▼
┌─────────────────────┐
│  Sources Listener   │ ← cost-onprem-sources-listener pod
└──────────┬──────────┘
           │ ProviderBuilder.create_provider_from_source()
           ▼
┌─────────────────────┐
│  Tenant Provisioning│
│  - Create schema    │
│  - Run migrations   │
│  - Create provider  │
└─────────────────────┘
```

This is the same flow used by console.redhat.com.

## Components

| Component | Template | Purpose |
|-----------|----------|---------|
| Sources API | `cost-onprem/templates/sources-api/deployment.yaml` | HTTP endpoints for source management |
| Sources Listener | `cost-onprem/templates/cost-management/sources/deployment-sources-listener.yaml` | Kafka consumer for source events |
| Sources API Route | `cost-onprem/templates/ingress/routes.yaml` | External HTTP access |

## Sources API Route

The route is defined in `cost-onprem/templates/ingress/routes.yaml`.

Get the route URL:

```bash
SOURCES_API_URL=$(oc get route sources-api -n cost-onprem -o jsonpath='{.spec.host}')
echo "Sources API: https://$SOURCES_API_URL"
```

## Sources Listener

The sources listener deployment runs `python manage.py sources_listener` which:
- Subscribes to `platform.sources.event-stream` Kafka topic
- Processes source/application create/update/delete events
- Creates providers via `ProviderBuilder.create_provider_from_source()`

Key environment variables:

| Variable | Value | Purpose |
|----------|-------|---------|
| `SOURCES` | `true` | Enables sources listener mode |
| `KAFKA_CONNECT` | `true` | Enables Kafka connectivity |
| `SOURCES_API_SVC_HOST` | `<release>-sources-api.<namespace>.svc.cluster.local` | Sources API endpoint |
| `SOURCES_API_SVC_PORT` | `8000` | Sources API port |

## Testing the Flow

```bash
# Run E2E test (uses Sources API automatically)
./scripts/cost-mgmt-ocp-dataflow.sh --namespace cost-onprem
```

## Flow Details

1. **E2E test discovers Sources API route**

2. **Creates source via HTTP POST**
   ```
   POST /api/sources/v3.1/sources
   {"name": "OCP Test Provider", "source_type_id": "3"}

   POST /api/sources/v3.1/applications
   {"source_id": "123", "application_type_id": "2", "extra": {"bucket": "cost-data", "cluster_id": "test-cluster-123"}}
   ```

3. **Sources API publishes to Kafka**
   ```
   Topic: platform.sources.event-stream
   Event: application.create
   ```

4. **Sources Listener consumes message and provisions tenant**

5. **Provider created in database**

## Comparison: Django ORM vs Sources API

| Aspect | Django ORM (kubectl exec) | Sources API |
|--------|---------------------------|-------------|
| Method | Direct database access | HTTP POST |
| Flow | Bypasses production code | Production code path |
| Kafka | Not tested | Tested |
| Listener | Not tested | Tested |