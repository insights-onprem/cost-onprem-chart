"""
Kafka Producer Client for E2E Testing
======================================

Sends OCP report announcements to Kafka to trigger MASU processing.
"""

import json
import base64
import uuid
import subprocess
from typing import List, Dict


class KafkaProducerClient:
    """Kafka producer for sending OCP report announcements"""

    def __init__(self, kafka_bootstrap_servers: str = "kafka-cluster-kafka-bootstrap.kafka.svc:9092"):
        """Initialize Kafka producer

        Args:
            kafka_bootstrap_servers: Kafka bootstrap servers (default: in-cluster service)
        """
        self.kafka_bootstrap = kafka_bootstrap_servers
        self.topic = "platform.upload.announce"

    def send_ocp_report_message(self,
                                 request_id: str,
                                 tarball_url: str,
                                 org_id: str,
                                 cluster_id: str) -> Dict:
        """Send OCP report announcement to Kafka

        This simulates the koku-metrics-operator announcing that OCP usage reports
        are available for processing. The message format matches the production
        workflow where a TAR.GZ file is uploaded to S3 and a Kafka message points
        to its download URL.

        Args:
            request_id: Unique request ID for this upload
            tarball_url: Full S3 URL to the TAR.GZ file (e.g., https://s3.../bucket/file.tar.gz)
            org_id: Organization ID (tenant schema name)
            cluster_id: OpenShift cluster identifier

        Returns:
            Dict with success status and message details
        """
        # Build x-rh-identity header
        identity = {
            "identity": {
                "account_number": "10001",
                "org_id": org_id,
                "type": "System",
                "internal": {
                    "org_id": org_id
                },
                "system": {
                    "cluster_id": cluster_id
                }
            },
            "entitlements": {
                "cost_management": {"is_entitled": True},
                "insights": {"is_entitled": True}
            }
        }

        b64_identity = base64.b64encode(json.dumps(identity).encode('utf-8')).decode('utf-8')

        # Build Kafka message payload (matches koku-metrics-operator format)
        message = {
            "request_id": request_id,
            "account": "10001",
            "org_id": org_id,
            "b64_identity": b64_identity,
            "service": "hccm",  # Hybrid Cloud Cost Management
            "url": tarball_url  # TAR.GZ download URL (key field for listener)
        }

        message_json = json.dumps(message)

        print(f"\n📨 Sending Kafka message to trigger OCP processing...")
        print(f"  Topic: {self.topic}")
        print(f"  Request ID: {request_id}")
        print(f"  Cluster ID: {cluster_id}")
        print(f"  Tarball URL: {tarball_url}")

        try:
            # Use kubectl/oc exec to send message from inside the cluster
            # This avoids needing to configure external Kafka access
            kafka_pod = self._get_kafka_pod()
            if not kafka_pod:
                return {
                    'success': False,
                    'error': 'No Kafka broker pods found'
                }

            # Detect cluster type (OpenShift vs Kubernetes)
            import subprocess as sp
            try:
                context_result = sp.run(['kubectl', 'config', 'current-context'], capture_output=True, text=True, check=True)
                current_context = context_result.stdout.strip()
                cli_tool = 'oc' if ('openshift' in current_context.lower() or '/' in current_context) else 'kubectl'
            except:
                cli_tool = 'kubectl'  # fallback

            # Use kafka-console-producer to send message WITH HEADERS
            # CRITICAL: The listener checks for "service" in message HEADERS, not payload!
            # Format: "header_key:header_value\tmessage_json" (tab separates headers from body)
            cmd = [
                cli_tool, 'exec', '-n', 'kafka', kafka_pod, '--',
                'bash', '-c',
                f'printf "service:hccm\\t%s\\n" \'{message_json}\' | '
                f'/opt/kafka/bin/kafka-console-producer.sh '
                f'--bootstrap-server localhost:9092 '
                f'--topic {self.topic} '
                f'--property "parse.headers=true" '
                f'--request-required-acks 1'
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                return {
                    'success': False,
                    'error': f'Failed to send Kafka message: {result.stderr}',
                    'request_id': request_id
                }

            print(f"  ✅ Kafka message sent successfully")

            return {
                'success': True,
                'request_id': request_id,
                'topic': self.topic,
                'message': message
            }

        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'error': 'Timeout sending Kafka message',
                'request_id': request_id
            }
        except Exception as e:
            return {
                'success': False,
                'error': f'Failed to send Kafka message: {str(e)}',
                'request_id': request_id
            }

    def _get_kafka_pod(self) -> str:
        """Get a Kafka broker pod name

        Returns:
            Pod name or None if not found
        """
        try:
            # Detect cluster type
            context_result = subprocess.run(['kubectl', 'config', 'current-context'], capture_output=True, text=True, check=True)
            current_context = context_result.stdout.strip()
            cli_tool = 'oc' if ('openshift' in current_context.lower() or '/' in current_context) else 'kubectl'
        except:
            cli_tool = 'kubectl'  # fallback

        try:
            result = subprocess.run(
                [cli_tool, 'get', 'pods', '-n', 'kafka',
                 '-l', 'strimzi.io/component-type=kafka',
                 '-o', 'jsonpath={.items[0].metadata.name}'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()

            return None

        except Exception:
            return None

    def verify_kafka_connectivity(self) -> Dict:
        """Verify that we can connect to Kafka

        Returns:
            Dict with connectivity status
        """
        print("\n🔍 Verifying Kafka connectivity...")

        kafka_pod = self._get_kafka_pod()
        if not kafka_pod:
            return {
                'success': False,
                'error': 'No Kafka broker pods found'
            }

        # Detect cluster type
        try:
            context_result = subprocess.run(['kubectl', 'config', 'current-context'], capture_output=True, text=True, check=True)
            current_context = context_result.stdout.strip()
            cli_tool = 'oc' if ('openshift' in current_context.lower() or '/' in current_context) else 'kubectl'
        except:
            cli_tool = 'kubectl'  # fallback

        try:
            # Test Kafka connectivity by listing topics
            result = subprocess.run(
                [cli_tool, 'exec', '-n', 'kafka', kafka_pod, '--',
                 '/opt/kafka/bin/kafka-topics.sh',
                 '--bootstrap-server', 'localhost:9092',
                 '--list'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return {
                    'success': False,
                    'error': f'Failed to list Kafka topics: {result.stderr}'
                }

            topics = result.stdout.strip().split('\n')

            # Check if our required topic exists
            topic_exists = self.topic in topics

            print(f"  ✅ Kafka connectivity verified")
            print(f"  Topics: {len(topics)}")
            print(f"  Required topic '{self.topic}': {'✅ exists' if topic_exists else '⚠️  not found'}")

            return {
                'success': True,
                'topics': topics,
                'topic_exists': topic_exists,
                'kafka_pod': kafka_pod
            }

        except Exception as e:
            return {
                'success': False,
                'error': f'Failed to verify Kafka connectivity: {str(e)}'
            }

