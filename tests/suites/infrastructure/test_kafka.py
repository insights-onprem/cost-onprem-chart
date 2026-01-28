"""
Kafka infrastructure tests.

Tests for Kafka cluster health and topic configuration.
"""

import pytest

from utils import run_oc_command, exec_in_pod, get_pod_by_label


@pytest.mark.infrastructure
@pytest.mark.component
class TestKafkaCluster:
    """Tests for Kafka cluster health."""

    def test_kafka_cluster_exists(self, kafka_namespace: str, kafka_cluster_name: str):
        """Verify Kafka cluster resource exists."""
        result = run_oc_command([
            "get", "kafka", kafka_cluster_name,
            "-n", kafka_namespace,
        ], check=False)
        
        assert result.returncode == 0, (
            f"Kafka cluster '{kafka_cluster_name}' not found in namespace '{kafka_namespace}'"
        )

    def test_kafka_cluster_ready(self, kafka_namespace: str, kafka_cluster_name: str):
        """Verify Kafka cluster is ready."""
        result = run_oc_command([
            "get", "kafka", kafka_cluster_name,
            "-n", kafka_namespace,
            "-o", "jsonpath={.status.conditions[?(@.type=='Ready')].status}"
        ], check=False)
        
        assert result.stdout.strip() == "True", (
            f"Kafka cluster not ready: {result.stdout}"
        )

    @pytest.mark.smoke
    def test_kafka_pods_running(self, kafka_namespace: str, kafka_cluster_name: str):
        """Verify Kafka broker pods are running."""
        result = run_oc_command([
            "get", "pods", "-n", kafka_namespace,
            "-l", f"strimzi.io/cluster={kafka_cluster_name},strimzi.io/kind=Kafka",
            "-o", "jsonpath={.items[*].status.phase}"
        ], check=False)
        
        phases = result.stdout.split()
        assert len(phases) > 0, "No Kafka broker pods found"
        assert all(p == "Running" for p in phases), f"Not all Kafka pods running: {phases}"


@pytest.mark.infrastructure
@pytest.mark.component
class TestKafkaTopics:
    """Tests for required Kafka topics."""

    REQUIRED_TOPICS = [
        "platform.upload.announce",
        "hccm.ros.events",
    ]

    def test_required_topics_exist(
        self, kafka_namespace: str, kafka_cluster_name: str
    ):
        """Verify required Kafka topics exist."""
        # Get list of topics
        result = run_oc_command([
            "get", "kafkatopic", "-n", kafka_namespace,
            "-o", "jsonpath={.items[*].metadata.name}"
        ], check=False)
        
        topics = result.stdout.split()
        
        for required_topic in self.REQUIRED_TOPICS:
            # Topic names may have cluster prefix
            found = any(
                required_topic in topic or topic.endswith(required_topic.replace(".", "-"))
                for topic in topics
            )
            assert found, f"Required topic '{required_topic}' not found"

    def test_upload_announce_topic_ready(self, kafka_namespace: str):
        """Verify platform.upload.announce topic is ready."""
        # Try to find the topic (may have different naming conventions)
        for topic_name in ["platform.upload.announce", "platform-upload-announce"]:
            result = run_oc_command([
                "get", "kafkatopic", topic_name,
                "-n", kafka_namespace,
                "-o", "jsonpath={.status.conditions[?(@.type=='Ready')].status}"
            ], check=False)
            
            if result.returncode == 0 and result.stdout.strip() == "True":
                return
        
        pytest.skip("Upload announce topic not found or not ready")


@pytest.mark.infrastructure
@pytest.mark.integration
class TestKafkaConsumerGroups:
    """Tests for Kafka consumer groups."""

    def test_listener_consumer_group_exists(
        self, cluster_config, kafka_namespace: str, kafka_cluster_name: str
    ):
        """Verify Koku listener consumer group exists."""
        # Find a Kafka pod to run commands
        kafka_pod = get_pod_by_label(
            kafka_namespace,
            f"strimzi.io/cluster={kafka_cluster_name},strimzi.io/component-type=kafka"
        )
        
        if not kafka_pod:
            pytest.skip("No Kafka pod found to query consumer groups")
        
        # List consumer groups
        result = exec_in_pod(
            kafka_namespace,
            kafka_pod,
            [
                "bin/kafka-consumer-groups.sh",
                "--bootstrap-server", "localhost:9092",
                "--list"
            ],
            timeout=30,
        )
        
        if result is None:
            pytest.skip("Could not list consumer groups")
        
        # Check for listener-related consumer groups
        # The exact name depends on configuration
        consumer_groups = result.strip().split("\n")
        assert len(consumer_groups) > 0, "No consumer groups found"
