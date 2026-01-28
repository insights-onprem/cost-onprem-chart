# Sources API Provider Creation Flow

This document describes provider creation using Koku's Sources API endpoints, which provides a unified source management system for on-premise deployments.

## Architecture Overview

```
┌─────────────────┐
│   User/Script   │
└────────┬────────┘
         │ HTTP POST/DELETE
         ▼
┌─────────────────────┐
│   Koku API          │ ← External route (/api/cost-management/v1/sources/)
│   (Sources API)     │
└──────────┬──────────┘
           │ Kafka Publish
           │ (create/update/delete events)
           ▼
┌─────────────────────┐
│  Kafka Topic        │
│  platform.sources.  │
│  event-stream       │
└──────────┬──────────┘
           │ Consume (multiple consumers)
           ├─────────────────┬─────────────────┐
           ▼                 ▼                 ▼
┌─────────────────────┐ ┌─────────────────┐ ┌─────────────────────┐
│  Koku Sources       │ │  ROS Housekeeper│ │  Other Consumers    │
│  Listener           │ │  (delete events)│ │  (if any)           │
└──────────┬──────────┘ └─────────────────┘ └─────────────────────┘
           │
           │ ProviderBuilder.create_provider_from_source()
           ▼
┌─────────────────────┐
│  Tenant Provisioning│
│  - Create schema    │
│  - Run migrations   │
│  - Create provider  │
└─────────────────────┘
```

This architecture simplifies the deployment by removing the separate sources-api-go service. Koku now provides all Sources API endpoints directly.

## Components

| Component | Template | Purpose |
|-----------|----------|---------|
| Koku API | `cost-onprem/templates/cost-management/api/` | HTTP endpoints for source management via `/api/cost-management/v1/sources/` |
| Koku Sources Listener | `cost-onprem/templates/cost-management/listener/` | Kafka consumer for source events |
| Koku API Route | `cost-onprem/templates/ingress/routes.yaml` | External HTTP access via `/api/cost-management/` |

## Sources API Route

The Sources API is accessed through Koku's Cost Management API route:

```bash
KOKU_API_URL=$(oc get route cost-onprem-api -n cost-onprem -o jsonpath='{.spec.host}')
echo "Sources API: https://$KOKU_API_URL/api/cost-management/v1/sources/"
```

**Note**: The route `cost-onprem-api` provides access to both:
- Cost Management API: `/api/cost-management/v1/`
- Sources API: `/api/cost-management/v1/sources/`

## Koku Sources Listener

The Koku listener deployment runs `python manage.py sources_listener` which:
- Subscribes to `platform.sources.event-stream` Kafka topic
- Processes source/application create/update/delete events
- Creates providers via `ProviderBuilder.create_provider_from_source()`

Key environment variables:

| Variable | Value | Purpose |
|----------|-------|---------|
| `SOURCES` | `true` | Enables sources listener mode |
| `KAFKA_CONNECT` | `true` | Enables Kafka connectivity |
| `ONPREM` | `"true"` | Enables on-premise mode (uses hardcoded application_type_id: 0) |

## Testing the Flow

```bash
# Run E2E test (uses Koku Sources API automatically)
./scripts/test-ocp-dataflow-jwt.sh --namespace cost-onprem
```

## Flow Details

1. **E2E test discovers Koku API route**

2. **Creates source via HTTP POST to Koku API**
   ```
   POST /api/cost-management/v1/sources/
   {"name": "OCP Test Provider", "source_type_id": "3"}

   POST /api/cost-management/v1/applications/
   {"source_id": "123", "application_type_id": "0", "extra": {"bucket": "koku-bucket", "cluster_id": "test-cluster-123"}}
   ```

   **Note**: For on-premise deployments, `application_type_id` is hardcoded to `0` for cost-management applications.

3. **Koku API publishes to Kafka**
   ```
   Topic: platform.sources.event-stream
   Events: 
   - application.create (for provider creation)
   - application.delete (for source deletion)
   - source.delete (for source deletion)
   ```

4. **Multiple consumers process events:**
   - **Koku Sources Listener**: Consumes create/update events and provisions tenants
   - **ROS Housekeeper**: Consumes delete events to clean up ROS data when sources are deleted

5. **Provider created in database** (for create events)

## CMMO (Cost Management Metrics Operator) Configuration

The Cost Management Metrics Operator is configured to use Koku's Sources API:

```yaml
# In CostManagementMetricsConfig spec
source:
  create_source: true
  check_cycle: 1440  # 24 hours
  sources_path: "/api/cost-management/v1/"  # Points to Koku API
  name: ""
```

This ensures CMMO creates and manages sources via Koku's Sources API endpoints instead of a separate sources-api-go service.

## Comparison: Previous Architecture vs Current

| Aspect | Previous (sources-api-go) | Current (Koku Sources API) |
|--------|---------------------------|---------------------------|
| Service | Separate sources-api-go deployment | Integrated in Koku API |
| Route | `/api/sources/` | `/api/cost-management/v1/sources/` |
| Database | Separate sources database | Unified koku database |
| Kafka Events | Published by sources-api-go | Published by Koku API |
| Listener | Separate sources-listener pod | Integrated in Koku listener |
| CMMO Config | `sources_path: "/api/sources/v1.0/"` | `sources_path: "/api/cost-management/v1/"` |

## Benefits

- **Simplified Architecture**: One less service to deploy and maintain
- **Reduced Resource Consumption**: Fewer pods and lower memory footprint
- **Unified Database**: All source data in the same database as cost data
- **Easier Debugging**: Single service for both cost management and source management
- **Consistent API**: All APIs accessible through the same route and authentication

## ROS-OCP-Backend Integration

The ros-ocp-backend housekeeper no longer queries sources-api-go for application type IDs. Instead:

- **ONPREM mode**: Uses hardcoded `application_type_id: 0` for cost-management applications
- **Kafka Events**: Receives source deletion events directly from Koku via `platform.sources.event-stream` topic
  - **Koku publishes delete events**: When a source is deleted via Koku API, Koku publishes `source.delete` and `application.delete` events to the `platform.sources.event-stream` topic
  - **ROS Housekeeper consumes**: The ros-ocp-backend housekeeper subscribes to this topic and processes delete events to clean up ROS-related data
  - **Environment Variable**: `SOURCES_EVENT_TOPIC: "platform.sources.event-stream"` configures the topic name
- **Environment Variable**: `ONPREM: "True"` enables on-premise mode

## Troubleshooting

### Sources Not Created

**Check Koku API logs:**
```bash
oc logs -n cost-onprem -l app.kubernetes.io/component=cost-management-api-writes --tail=50
```

**Check Koku listener logs:**
```bash
oc logs -n cost-onprem -l app.kubernetes.io/component=koku-listener --tail=50 | grep -i source
```

### Kafka Events Not Processed

**Verify Kafka topic exists:**
```bash
oc exec -n kafka cost-onprem-kafka-kafka-0 -- bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 \
  --list | grep platform.sources.event-stream
```

**Check listener connectivity:**
```bash
oc logs -n cost-onprem -l app.kubernetes.io/component=koku-listener | grep -i kafka
```

### CMMO Cannot Create Sources

**Verify CMMO configuration:**
```bash
oc get costmanagementmetricsconfig -n costmanagement-metrics-operator -o yaml | grep sources_path
# Should show: sources_path: "/api/cost-management/v1/"
```

**Check Koku API route:**
```bash
oc get route cost-onprem-api -n cost-onprem
```
