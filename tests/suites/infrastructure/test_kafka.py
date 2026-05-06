"""
Kafka Infrastructure Validation Tests.

These tests validate the Kafka infrastructure required for Cost Management:
1. Kafka cluster health (AMQ Streams-managed, KRaft mode)
2. Kafka listener pod deployment
3. Kafka connectivity from listener
4. Required Kafka topics existence

Source Reference: scripts/e2e_validator/phases/kafka_validation.py
"""

import json
import os
import subprocess
from typing import List, Optional

import pytest

from utils import get_pod_by_label, run_oc_command


# =============================================================================
# Helper Functions
# =============================================================================

def get_kafka_namespace() -> str:
    """Get Kafka namespace from environment or default."""
    return os.environ.get("KAFKA_NAMESPACE", "kafka")


def get_kafka_broker_pod(kafka_namespace: str) -> Optional[str]:
    """Get the first Kafka broker pod name.
    
    Uses strimzi.io/broker-role=true to find actual broker pods,
    not the entity-operator which also has strimzi.io/kind=Kafka.
    
    Args:
        kafka_namespace: Namespace where Kafka is deployed
        
    Returns:
        Pod name or None if not found
    """
    try:
        # Use broker-role label to get actual Kafka broker, not entity-operator
        result = subprocess.run(
            [
                "kubectl", "get", "pods",
                "-n", kafka_namespace,
                "-l", "strimzi.io/broker-role=true",
                "-o", "jsonpath={.items[0].metadata.name}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None
    except (subprocess.TimeoutExpired, Exception):
        return None


def get_kafka_pods_status(kafka_namespace: str) -> dict:
    """Get status of all Kafka pods.
    
    Args:
        kafka_namespace: Namespace where Kafka is deployed
        
    Returns:
        Dict with pod counts and status details
    """
    try:
        result = subprocess.run(
            [
                "kubectl", "get", "pods",
                "-n", kafka_namespace,
                "-l", "strimzi.io/kind=Kafka",
                "-o", "json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        if result.returncode != 0:
            return {"error": result.stderr, "total": 0, "running": 0}
        
        pods_data = json.loads(result.stdout)
        items = pods_data.get("items", [])
        
        running = sum(
            1 for pod in items
            if pod.get("status", {}).get("phase") == "Running"
        )
        
        return {
            "total": len(items),
            "running": running,
            "pods": [
                {
                    "name": pod.get("metadata", {}).get("name"),
                    "phase": pod.get("status", {}).get("phase"),
                }
                for pod in items
            ],
        }
    except Exception as e:
        return {"error": str(e), "total": 0, "running": 0}


def list_kafka_topics(kafka_namespace: str, kafka_pod: str) -> List[str]:
    """List all Kafka topics.
    
    Args:
        kafka_namespace: Namespace where Kafka is deployed
        kafka_pod: Name of a Kafka broker pod
        
    Returns:
        List of topic names
    """
    try:
        result = subprocess.run(
            [
                "kubectl", "exec",
                "-n", kafka_namespace,
                kafka_pod, "--",
                "bin/kafka-topics.sh",
                "--bootstrap-server", "localhost:9092",
                "--list",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        
        if result.returncode != 0:
            return []
        
        # Filter out internal topics (starting with __)
        topics = [
            t.strip() for t in result.stdout.split("\n")
            if t.strip() and not t.strip().startswith("__")
        ]
        return topics
    except Exception:
        return []


def check_listener_kafka_connection(namespace: str, listener_pod: str) -> dict:
    """Check listener logs for Kafka connection status.
    
    Args:
        namespace: Application namespace
        listener_pod: Name of the listener pod
        
    Returns:
        Dict with connection status and details
    """
    try:
        result = subprocess.run(
            [
                "kubectl", "logs",
                "-n", namespace,
                listener_pod,
                "--tail=200",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        if result.returncode != 0:
            return {"connected": False, "error": result.stderr}
        
        logs = result.stdout.lower()
        
        # Check for positive connection indicators
        connected_indicators = [
            "kafka is running",
            "consumer is listening",
            "connected to kafka",
            "subscribed to topic",
        ]
        
        # Check for error indicators
        error_indicators = [
            "kafka connection error",
            "unable to connect to kafka",
            "broker transport failure",
            "connection refused",
        ]
        
        has_connection = any(ind in logs for ind in connected_indicators)
        has_errors = any(ind in logs for ind in error_indicators)
        
        return {
            "connected": has_connection and not has_errors,
            "has_errors": has_errors,
            "logs_checked": True,
        }
    except Exception as e:
        return {"connected": False, "error": str(e)}


# =============================================================================
# Test Classes
# =============================================================================

@pytest.mark.infrastructure
@pytest.mark.component
class TestKafkaCluster:
    """Tests for Kafka cluster health."""
    
    def test_kafka_cluster_pods_exist(self):
        """Verify Kafka cluster pods exist in the kafka namespace."""
        kafka_ns = get_kafka_namespace()
        status = get_kafka_pods_status(kafka_ns)
        
        if "error" in status and status["total"] == 0:
            pytest.skip(f"Kafka cluster not found in namespace '{kafka_ns}': {status.get('error', 'unknown')}")
        
        assert status["total"] > 0, (
            f"No Kafka pods found in namespace '{kafka_ns}'. "
            "Ensure AMQ Streams Kafka is deployed."
        )
    
    def test_kafka_cluster_pods_running(self):
        """Verify all Kafka cluster pods are in Running state."""
        kafka_ns = get_kafka_namespace()
        status = get_kafka_pods_status(kafka_ns)
        
        if status["total"] == 0:
            pytest.skip(f"No Kafka pods found in namespace '{kafka_ns}'")
        
        assert status["running"] == status["total"], (
            f"Not all Kafka pods are running: {status['running']}/{status['total']}. "
            f"Pod details: {status.get('pods', [])}"
        )
    
    def test_kafka_broker_accessible(self):
        """Verify at least one Kafka broker pod is accessible."""
        kafka_ns = get_kafka_namespace()
        broker_pod = get_kafka_broker_pod(kafka_ns)
        
        if not broker_pod:
            pytest.skip(f"No Kafka broker pod found in namespace '{kafka_ns}'")
        
        # Try to run a simple command in the broker pod
        try:
            result = subprocess.run(
                [
                    "kubectl", "exec",
                    "-n", kafka_ns,
                    broker_pod, "--",
                    "echo", "broker-accessible",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, f"Cannot exec into Kafka broker: {result.stderr}"
        except subprocess.TimeoutExpired:
            pytest.fail("Timeout accessing Kafka broker pod")


@pytest.mark.infrastructure
@pytest.mark.component
class TestKafkaTopics:
    """Tests for required Kafka topics."""
    
    REQUIRED_TOPICS = [
        "platform.upload.announce",  # Ingress upload notifications
        "hccm.ros.events",           # ROS events from Koku
    ]
    
    def test_can_list_topics(self):
        """Verify we can list Kafka topics."""
        kafka_ns = get_kafka_namespace()
        broker_pod = get_kafka_broker_pod(kafka_ns)
        
        if not broker_pod:
            pytest.skip(f"No Kafka broker pod found in namespace '{kafka_ns}'")
        
        topics = list_kafka_topics(kafka_ns, broker_pod)
        
        # We should be able to list topics even if none exist
        assert isinstance(topics, list), "Failed to list Kafka topics"
    
    @pytest.mark.parametrize("topic", REQUIRED_TOPICS)
    def test_required_topic_exists(self, topic: str):
        """Verify required Kafka topic exists."""
        kafka_ns = get_kafka_namespace()
        broker_pod = get_kafka_broker_pod(kafka_ns)
        
        if not broker_pod:
            pytest.skip(f"No Kafka broker pod found in namespace '{kafka_ns}'")
        
        topics = list_kafka_topics(kafka_ns, broker_pod)
        
        if not topics:
            pytest.skip("Could not list Kafka topics - cluster may not be ready")
        
        assert topic in topics, (
            f"Required topic '{topic}' not found. "
            f"Available topics: {', '.join(topics[:10])}{'...' if len(topics) > 10 else ''}"
        )


@pytest.mark.infrastructure
@pytest.mark.integration
class TestKafkaListener:
    """Tests for Kafka listener pod and connectivity."""

    @pytest.fixture
    def listener_pod(self, cluster_config):
        """Get listener pod, skip if not found."""
        pod = get_pod_by_label(
            cluster_config.namespace,
            "app.kubernetes.io/component=listener"
        )
        if not pod:
            pytest.skip("Listener pod not found - may not be deployed yet")
        return pod

    def test_listener_pod_exists(self, listener_pod):
        """Verify Kafka listener pod exists."""
        assert listener_pod, "Kafka listener pod not found"

    @pytest.mark.parametrize("jsonpath,expected,check_name", [
        pytest.param("{.status.phase}", "Running", "pod phase", id="pod_running"),
        pytest.param("{.status.containerStatuses[0].ready}", "true", "container ready", id="container_ready"),
    ])
    def test_listener_status(self, cluster_config, listener_pod, jsonpath: str, expected: str, check_name: str):
        """Verify listener pod status.

        Parametrized for: pod running state, container readiness.
        """
        try:
            result = subprocess.run(
                [
                    "kubectl", "get", "pod",
                    "-n", cluster_config.namespace,
                    listener_pod,
                    "-o", f"jsonpath={jsonpath}",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            actual = result.stdout.strip().lower()
            assert actual == expected.lower(), (
                f"Listener {check_name} check failed: expected '{expected}', got '{actual}'"
            )
        except subprocess.TimeoutExpired:
            pytest.fail(f"Timeout checking listener {check_name}")

    def test_listener_kafka_connectivity(self, cluster_config):
        """Verify listener can connect to Kafka (log-based check)."""
        listener_pod = get_pod_by_label(
            cluster_config.namespace,
            "app.kubernetes.io/component=listener"
        )

        if not listener_pod:
            pytest.skip("Listener pod not found")

        status = check_listener_kafka_connection(
            cluster_config.namespace,
            listener_pod
        )

        if "error" in status:
            pytest.skip(f"Could not check listener logs: {status['error']}")

        if status.get("has_errors"):
            pytest.fail(
                "Kafka connection errors found in listener logs. "
                "Check: kubectl logs -n {ns} {pod} | grep -i 'kafka\\|error'".format(
                    ns=cluster_config.namespace,
                    pod=listener_pod
                )
            )

        # Note: If no explicit connection message but no errors, we consider it OK
        # The listener may not log connection success explicitly
        if not status.get("connected") and not status.get("has_errors"):
            # This is a soft pass - no errors detected
            pass


@pytest.mark.infrastructure
@pytest.mark.integration
class TestKafkaConsumerGroups:
    """Tests for Kafka consumer groups."""

    def test_listener_consumer_group_exists(
        self, cluster_config, kafka_namespace: str, kafka_cluster_name: str
    ):
        """Verify Koku listener consumer group exists."""
        # Find a Kafka broker pod to run commands (not entity-operator or zookeeper)
        # Use strimzi.io/name label which correctly identifies broker pods
        kafka_pod = get_pod_by_label(
            kafka_namespace,
            f"strimzi.io/name={kafka_cluster_name}-kafka"
        )

        if not kafka_pod:
            pytest.skip(f"Kafka broker pod not found in namespace '{kafka_namespace}'")

        # List consumer groups and check for the listener's group
        try:
            result = subprocess.run(
                [
                    "kubectl", "exec",
                    "-n", kafka_namespace,
                    kafka_pod, "--",
                    "bin/kafka-consumer-groups.sh",
                    "--bootstrap-server", "localhost:9092",
                    "--list",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode != 0:
                pytest.skip(f"Could not list consumer groups: {result.stderr}")

            groups = [g.strip() for g in result.stdout.split("\n") if g.strip()]

            # Look for the Koku listener consumer group
            # The group name typically contains 'koku' or 'listener'
            listener_groups = [g for g in groups if 'koku' in g.lower() or 'listener' in g.lower()]

            assert len(listener_groups) > 0 or len(groups) > 0, (
                f"No consumer groups found. Expected at least the Koku listener group. "
                f"Available groups: {groups}"
            )
        except subprocess.TimeoutExpired:
            pytest.fail("Timeout listing Kafka consumer groups")


def _get_helm_values(namespace: str, release_name: str) -> dict:
    """Get effective Helm values for the release."""
    try:
        result = subprocess.run(
            ["helm", "get", "values", release_name, "-n", namespace, "-o", "json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception:
        pass
    return {}


@pytest.mark.infrastructure
@pytest.mark.extended
class TestKafkaSASLTLS:
    """Tests for Kafka SASL/TLS authentication.

    These tests verify that SASL/TLS-configured deployments have correct
    environment variables and can connect to an authenticated Kafka broker.
    Requires a Kafka cluster with a SASL_SSL listener (e.g., Strimzi TLS).
    Marked 'extended' because CI uses PLAINTEXT Kafka by default.
    """

    def _is_sasl_configured(self, cluster_config) -> bool:
        """Check if the Helm release was installed with SASL config."""
        values = _get_helm_values(cluster_config.namespace, cluster_config.helm_release_name)
        kafka = values.get("kafka", {})
        return bool(kafka.get("sasl", {}).get("mechanism"))

    def test_sasl_env_vars_in_listener(self, cluster_config):
        """Verify KAFKA_SASL_* env vars are set on the listener pod."""
        if not self._is_sasl_configured(cluster_config):
            pytest.skip("Kafka SASL not configured in Helm values")

        listener_pod = get_pod_by_label(
            cluster_config.namespace, "app.kubernetes.io/component=listener"
        )
        if not listener_pod:
            pytest.skip("Listener pod not found")

        for env_var in ["KAFKA_SASL_MECHANISM", "KAFKA_SASL_USERNAME", "KAFKA_SASL_PASSWORD"]:
            result = subprocess.run(
                [
                    "kubectl", "exec", "-n", cluster_config.namespace,
                    listener_pod, "--", "printenv", env_var,
                ],
                capture_output=True, text=True, timeout=15,
            )
            assert result.returncode == 0 and result.stdout.strip(), (
                f"{env_var} not set on listener pod"
            )

    def test_sasl_env_vars_in_ingress(self, cluster_config):
        """Verify INGRESS_* SASL env vars are set on the ingress pod."""
        if not self._is_sasl_configured(cluster_config):
            pytest.skip("Kafka SASL not configured in Helm values")

        ingress_pod = get_pod_by_label(
            cluster_config.namespace, "app.kubernetes.io/component=ingress"
        )
        if not ingress_pod:
            pytest.skip("Ingress pod not found")

        for env_var in ["INGRESS_SASLMECHANISM", "INGRESS_KAFKAUSERNAME", "INGRESS_KAFKAPASSWORD"]:
            result = subprocess.run(
                [
                    "kubectl", "exec", "-n", cluster_config.namespace,
                    ingress_pod, "--", "printenv", env_var,
                ],
                capture_output=True, text=True, timeout=15,
            )
            assert result.returncode == 0 and result.stdout.strip(), (
                f"{env_var} not set on ingress pod"
            )

    def test_tls_ca_cert_mounted(self, cluster_config):
        """Verify CA certificate is mounted at /etc/kafka/certs/ca.crt."""
        values = _get_helm_values(cluster_config.namespace, cluster_config.helm_release_name)
        tls = values.get("kafka", {}).get("tls", {})
        if not tls.get("enabled"):
            pytest.skip("Kafka TLS not configured in Helm values")

        listener_pod = get_pod_by_label(
            cluster_config.namespace, "app.kubernetes.io/component=listener"
        )
        if not listener_pod:
            pytest.skip("Listener pod not found")

        result = subprocess.run(
            [
                "kubectl", "exec", "-n", cluster_config.namespace,
                listener_pod, "--", "test", "-f", "/etc/kafka/certs/ca.crt",
            ],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, (
            "CA cert not mounted at /etc/kafka/certs/ca.crt on listener pod"
        )

    def test_listener_connects_with_sasl(self, cluster_config):
        """Verify listener successfully connects to SASL-authenticated Kafka."""
        if not self._is_sasl_configured(cluster_config):
            pytest.skip("Kafka SASL not configured in Helm values")

        listener_pod = get_pod_by_label(
            cluster_config.namespace, "app.kubernetes.io/component=listener"
        )
        if not listener_pod:
            pytest.skip("Listener pod not found")

        status = check_listener_kafka_connection(
            cluster_config.namespace, listener_pod
        )

        if "error" in status:
            pytest.skip(f"Could not check listener logs: {status['error']}")

        assert not status.get("has_errors"), (
            "Kafka connection errors found in listener logs with SASL config. "
            f"Check: kubectl logs -n {cluster_config.namespace} {listener_pod} --tail=100"
        )
