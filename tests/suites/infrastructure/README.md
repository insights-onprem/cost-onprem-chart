# Infrastructure Test Suite

Tests for validating infrastructure components required by Cost Management.

## Kafka Validation (`test_kafka.py`)

Validates the AMQ Streams Kafka cluster and Koku listener connectivity.

| Test Class | Test | Description |
|------------|------|-------------|
| `TestKafkaCluster` | `test_kafka_cluster_pods_exist` | Verifies Kafka broker pods exist in the kafka namespace |
| | `test_kafka_cluster_pods_running` | Verifies all Kafka pods are in Running state |
| | `test_kafka_broker_accessible` | Verifies we can exec into a Kafka broker pod |
| `TestKafkaTopics` | `test_can_list_topics` | Verifies we can list Kafka topics |
| | `test_required_topic_exists[platform.upload.announce]` | Verifies ingress upload topic exists |
| | `test_required_topic_exists[hccm.ros.events]` | Verifies ROS events topic exists |
| `TestKafkaListener` | `test_listener_pod_exists` | Verifies Koku listener pod exists |
| | `test_listener_pod_running` | Verifies listener pod is Running |
| | `test_listener_container_ready` | Verifies listener container is ready |
| | `test_listener_kafka_connectivity` | Checks listener logs for Kafka connection status |

**Key Implementation Detail**: Uses `strimzi.io/broker-role=true` label to find actual Kafka brokers (not the entity-operator which also has `strimzi.io/kind=Kafka`).

**Markers**: `@pytest.mark.infrastructure`, `@pytest.mark.component`, `@pytest.mark.integration`

---

### S3/Storage Preflight (`test_storage.py`)

Validates S3 storage infrastructure using Python/boto3 executed inside pods.

| Test Class | Test | Description |
|------------|------|-------------|
| `TestS3Endpoint` | `test_s3_endpoint_discoverable` | Verifies S3 endpoint can be discovered from cluster |
| | `test_s3_credentials_available` | Verifies S3 credentials exist in secrets |
| `TestS3Connectivity` | `test_s3_reachable_from_cluster` | Verifies S3 is reachable from within the cluster |
| `TestS3Buckets` | `test_required_bucket_exists[koku-bucket]` | Verifies main cost data bucket exists |
| | `test_optional_bucket_exists[ros-data]` | Checks if ROS data bucket exists (optional) |
| `TestS3DataPaths` | `test_can_list_bucket_contents` | Verifies we can list objects in koku-bucket |

**Key Implementation Detail**: Uses Python/boto3 scripts executed via `kubectl exec` in the MASU pod because AWS CLI is not installed in Koku containers. Uses `addressing_style: "path"` for on-prem S3 compatibility.

**S3 Endpoint Discovery Order**:
1. OpenShift route (`oc get route -n openshift-storage s3`)
2. MASU pod environment variable (`S3_ENDPOINT`)
3. Default ODF endpoint (`https://s3.openshift-storage.svc:443`)

**Markers**: `@pytest.mark.infrastructure`, `@pytest.mark.component`, `@pytest.mark.integration`

---

## Running Infrastructure Tests

```bash
# Run all infrastructure tests
./scripts/run-pytest.sh -- -m infrastructure

# Run only Kafka tests
pytest tests/suites/infrastructure/test_kafka.py -v

# Run only S3/storage tests
pytest tests/suites/infrastructure/test_storage.py -v
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `KAFKA_NAMESPACE` | `kafka` | Namespace where Kafka is deployed |
| `NAMESPACE` | `cost-onprem` | Application namespace |

---

## Database Tests (`test_database.py`)

Validates database schema, migrations, and FLPATH-3265 hive workaround removal.

| Test Class | Test | Description |
|------------|------|-------------|
| `TestDatabaseSchema` | `test_api_provider_table_exists` | Verifies core Koku api_provider table exists |
| | `test_api_customer_table_exists` | Verifies api_customer table exists |
| | `test_manifest_table_exists` | Verifies cost usage report manifest table exists |
| `TestDatabaseMigrations` | `test_django_migrations_table_exists` | Verifies Django migrations table exists |
| | `test_migrations_applied` | Verifies migrations have been applied (count > 0) |
| | `test_no_pending_migrations` | Verifies expected Django apps have migrations |
| | `test_migration_job_logs_success` | Validates migration job logs for success indicators |
| `TestHiveWorkaround` | `test_hive_role_not_created` | Verifies hive role does NOT exist (FLPATH-3265) |
| | `test_hive_database_not_created` | Verifies hive database does NOT exist (FLPATH-3265) |
| | `test_koku_user_lacks_createrole_privilege` | Verifies koku_user lacks CREATEROLE (FLPATH-3265) |
| | `test_koku_user_lacks_createdb_privilege` | Verifies koku_user lacks CREATEDB (FLPATH-3265) |
| | `test_migration_skipped_hive_creation` | Validates migration logs show hive creation skipped |
| `TestKruizeDatabase` | `test_kruize_experiments_table_exists` | Verifies kruize_experiments table exists |
| | `test_kruize_recommendations_table_exists` | Verifies kruize_recommendations table exists |

**FLPATH-3265 Context**: The hive role and database were previously created as a workaround because Koku migration 0039 tried to create them but koku_user lacked CREATEROLE/CREATEDB privileges. With the fix in [Koku PR #5900](https://github.com/project-koku/koku/pull/5900), migration 0039 is skipped when ONPREM=True, so the workaround was removed in [Chart PR #96](https://github.com/insights-onprem/cost-onprem-chart/pull/96).

**Markers**: `@pytest.mark.infrastructure`, `@pytest.mark.component`

---

## Related Files

- `test_preflight.py` - Pod health and basic connectivity tests
- `test_database.py` - Database schema and migration tests
- `conftest.py` - Shared fixtures for infrastructure tests

## Log Validation Utilities

The test suite includes reusable log validation utilities in `tests/utils.py`:

```python
from utils import (
    get_pod_logs,
    get_job_logs,
    get_deployment_logs,
    search_logs,
    assert_log_contains,
    assert_log_not_contains,
    validate_logs,
)

# Get logs from a migration job
logs = get_job_logs("cost-onprem", "cost-onprem-koku-migrate", container="migrate")

# Assert specific patterns
assert_log_contains(logs, "Migrations completed successfully")
assert_log_not_contains(logs, "Migration failed")

# Validate multiple patterns at once
validate_logs(
    logs,
    must_contain=["Migrations completed successfully", "Migration lock"],
    must_not_contain=["Migration failed", "ERROR", "Traceback"],
)

# Search with regex
result = search_logs(logs, r"Applied \d+ migrations", regex=True)
if result.found:
    print(f"Found {len(result.matches)} matching lines")
```
