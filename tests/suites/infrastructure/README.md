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

## Related Files

- `test_preflight.py` - Pod health and basic connectivity tests
- `test_database.py` - Database schema and migration tests
- `conftest.py` - Shared fixtures for infrastructure tests
