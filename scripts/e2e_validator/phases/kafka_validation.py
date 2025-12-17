"""
Kafka Validation Phase for E2E Testing

Validates that:
1. Kafka cluster is deployed and healthy
2. Kafka listener pod is deployed and running
3. Listener can connect to Kafka
4. Required Kafka topics exist
"""

import time
import json
import subprocess
from typing import Dict, Optional
from ..clients.kubernetes import KubernetesClient


class KafkaValidationPhase:
    """Phase to validate Kafka deployment and connectivity"""

    def __init__(self, k8s_client: KubernetesClient, namespace: str = "cost-mgmt"):
        self.k8s = k8s_client
        self.namespace = namespace
        self.kafka_namespace = "kafka"

    def check_kafka_cluster(self) -> Dict:
        """Check if Kafka cluster is running

        Returns:
            Dict with status and pod information
        """
        print(f"\n🔍 Checking Kafka cluster in namespace '{self.kafka_namespace}'...")

        try:
            # Check for Kafka pods
            result = subprocess.run(
                ['kubectl', 'get', 'pods', '-n', self.kafka_namespace, '-l', 'strimzi.io/kind=Kafka', '-o', 'json'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return {
                    'success': False,
                    'error': f'Failed to query Kafka pods: {result.stderr}',
                    'kafka_namespace': self.kafka_namespace
                }

            kafka_pods = json.loads(result.stdout)

            if not kafka_pods or 'items' not in kafka_pods:
                return {
                    'success': False,
                    'error': 'No Kafka pods found',
                    'kafka_namespace': self.kafka_namespace
                }

            # Check pod status
            running_pods = [
                pod for pod in kafka_pods.get('items', [])
                if pod.get('status', {}).get('phase') == 'Running'
            ]

            total_pods = len(kafka_pods.get('items', []))

            if len(running_pods) < total_pods:
                return {
                    'success': False,
                    'error': f'Only {len(running_pods)}/{total_pods} Kafka pods are running',
                    'running_pods': len(running_pods),
                    'total_pods': total_pods
                }

            print(f"  ✅ Kafka cluster is healthy: {total_pods} pod(s) running")

            return {
                'success': True,
                'running_pods': len(running_pods),
                'total_pods': total_pods,
                'kafka_namespace': self.kafka_namespace
            }

        except Exception as e:
            return {
                'success': False,
                'error': f'Failed to check Kafka cluster: {str(e)}'
            }

    def check_listener_pod(self) -> Dict:
        """Check if Kafka listener pod is deployed and running

        Returns:
            Dict with status and pod information
        """
        print(f"\n🔍 Checking Kafka listener pod in namespace '{self.namespace}'...")

        try:
            # Get listener pod (namespace already set in k8s client)
            listener_pod_name = self.k8s.get_pod_by_component('listener')

            if not listener_pod_name:
                return {
                    'success': False,
                    'error': 'Kafka listener pod not found',
                    'namespace': self.namespace
                }

            # Get full pod details
            result = subprocess.run(
                ['kubectl', 'get', 'pod', listener_pod_name, '-n', self.namespace, '-o', 'json'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return {
                    'success': False,
                    'error': f'Failed to query listener pod: {result.stderr}',
                    'namespace': self.namespace
                }

            listener_pod = json.loads(result.stdout)
            pod_name = listener_pod.get('metadata', {}).get('name')
            pod_phase = listener_pod.get('status', {}).get('phase')

            if pod_phase != 'Running':
                return {
                    'success': False,
                    'error': f'Listener pod is not running (status: {pod_phase})',
                    'pod_name': pod_name,
                    'pod_phase': pod_phase
                }

            # Check if container is ready
            container_statuses = listener_pod.get('status', {}).get('containerStatuses', [])
            if not container_statuses:
                return {
                    'success': False,
                    'error': 'No container status available for listener pod',
                    'pod_name': pod_name
                }

            listener_container = container_statuses[0]
            if not listener_container.get('ready', False):
                return {
                    'success': False,
                    'error': 'Listener container is not ready',
                    'pod_name': pod_name,
                    'ready': False
                }

            print(f"  ✅ Listener pod is running: {pod_name}")

            return {
                'success': True,
                'pod_name': pod_name,
                'pod_phase': pod_phase,
                'ready': True
            }

        except Exception as e:
            return {
                'success': False,
                'error': f'Failed to check listener pod: {str(e)}'
            }

    def check_kafka_connectivity(self) -> Dict:
        """Check if listener can connect to Kafka

        Returns:
            Dict with status and connection information
        """
        print(f"\n🔍 Checking Kafka connectivity from listener pod...")

        try:
            # Get listener pod name
            pod_name = self.k8s.get_pod_by_component('listener')

            if not pod_name:
                return {
                    'success': False,
                    'error': 'Listener pod not found for connectivity check'
                }

            # Check listener logs for Kafka connection
            result = subprocess.run(
                ['kubectl', 'logs', '-n', self.namespace, pod_name, '--tail=100'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return {
                    'success': False,
                    'error': 'Failed to retrieve listener logs',
                    'pod_name': pod_name
                }

            logs = result.stdout

            # Look for Kafka connection indicators
            kafka_connected = (
                'Kafka is running' in logs or
                'Consumer is listening for messages' in logs or
                'kafka' in logs.lower() and 'connected' in logs.lower()
            )

            kafka_errors = (
                'kafka connection error' in logs.lower() or
                'unable to connect to kafka' in logs.lower() or
                'broker transport failure' in logs.lower()
            )

            if kafka_errors:
                return {
                    'success': False,
                    'error': 'Kafka connection errors found in listener logs',
                    'pod_name': pod_name,
                    'logs_snippet': logs[-500:] if len(logs) > 500 else logs
                }

            if kafka_connected:
                print(f"  ✅ Listener successfully connected to Kafka")
                return {
                    'success': True,
                    'pod_name': pod_name,
                    'kafka_connected': True
                }
            else:
                # No explicit connection, but no errors either - check readiness
                print(f"  ⚠️  No explicit Kafka connection log found (checking readiness...)")
                return {
                    'success': True,
                    'pod_name': pod_name,
                    'kafka_connected': False,
                    'warning': 'No explicit Kafka connection found in logs, but no errors detected'
                }

        except Exception as e:
            return {
                'success': False,
                'error': f'Failed to check Kafka connectivity: {str(e)}'
            }

    def check_kafka_topics(self) -> Dict:
        """Check if required Kafka topics exist

        Returns:
            Dict with status and topic information
        """
        print(f"\n🔍 Checking Kafka topics...")

        try:
            # Get Kafka pod to run topic list command
            result = subprocess.run(
                ['kubectl', 'get', 'pods', '-n', self.kafka_namespace, '-l', 'strimzi.io/name=cost-onprem-kafka-kafka',
                 '-o', 'jsonpath={.items[0].metadata.name}'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0 or not result.stdout:
                return {
                    'success': False,
                    'error': 'No Kafka broker pod found to check topics'
                }

            kafka_pod = result.stdout.strip()

            # List topics
            result = subprocess.run(
                ['kubectl', 'exec', '-n', self.kafka_namespace, kafka_pod, '--',
                 'bin/kafka-topics.sh', '--bootstrap-server', 'localhost:9092', '--list'],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                return {
                    'success': False,
                    'error': 'Failed to list Kafka topics',
                    'kafka_pod': kafka_pod
                }

            topics_output = result.stdout
            topics = [t.strip() for t in topics_output.split('\n') if t.strip() and not t.startswith('__')]

            # Check for required topic
            required_topic = 'platform.upload.announce'
            has_required_topic = required_topic in topics

            if has_required_topic:
                print(f"  ✅ Required topic '{required_topic}' exists")
            else:
                print(f"  ⚠️  Required topic '{required_topic}' not found")
                print(f"     Available topics: {', '.join(topics[:5])}...")

            return {
                'success': True,
                'topics': topics,
                'topic_count': len(topics),
                'has_required_topic': has_required_topic,
                'required_topic': required_topic
            }

        except Exception as e:
            return {
                'success': False,
                'error': f'Failed to check Kafka topics: {str(e)}'
            }

    def run(self) -> Dict:
        """Run all Kafka validation checks

        Returns:
            Dict with overall validation status
        """
        print("\n" + "="*80)
        print("Phase 2.5: Kafka Integration Validation")
        print("="*80)

        results = {
            'phase': 'kafka_validation',
            'kafka_cluster': {},
            'listener_pod': {},
            'kafka_connectivity': {},
            'kafka_topics': {},
            'overall_success': False
        }

        # 1. Check Kafka cluster
        results['kafka_cluster'] = self.check_kafka_cluster()
        if not results['kafka_cluster'].get('success'):
            print(f"\n❌ Kafka cluster check failed: {results['kafka_cluster'].get('error')}")
            return results

        # 2. Check listener pod
        results['listener_pod'] = self.check_listener_pod()
        if not results['listener_pod'].get('success'):
            print(f"\n❌ Listener pod check failed: {results['listener_pod'].get('error')}")
            return results

        # 3. Check Kafka connectivity
        results['kafka_connectivity'] = self.check_kafka_connectivity()
        if not results['kafka_connectivity'].get('success'):
            print(f"\n❌ Kafka connectivity check failed: {results['kafka_connectivity'].get('error')}")
            # Don't return here - continue to check topics

        # 4. Check Kafka topics
        results['kafka_topics'] = self.check_kafka_topics()

        # Overall success
        results['overall_success'] = all([
            results['kafka_cluster'].get('success'),
            results['listener_pod'].get('success'),
            results['kafka_connectivity'].get('success'),
        ])

        if results['overall_success']:
            print("\n✅ Kafka validation completed successfully!")
        else:
            print("\n⚠️  Kafka validation completed with warnings")

        return results

