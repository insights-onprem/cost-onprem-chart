"""Kafka monitoring and throughput helpers for performance tests (COST-7638).

Provides helpers to query Kafka consumer group lag, broker metrics,
and topic metadata from AMQ Streams brokers.
"""

import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from utils import get_pod_by_label, run_oc_command


def get_kafka_namespace() -> str:
    return os.environ.get("KAFKA_NAMESPACE", "kafka")


def get_kafka_cluster_name(kafka_namespace: str) -> str:
    result = run_oc_command(
        ["get", "kafka", "-n", kafka_namespace,
         "-o", "jsonpath={.items[0].metadata.name}"],
        check=False,
    )
    name = result.stdout.strip()
    return name if name else "cost-onprem-kafka"


def get_kafka_broker_pod(kafka_namespace: str) -> Optional[str]:
    """Find a Kafka broker pod (not entity-operator)."""
    pod = get_pod_by_label(kafka_namespace, "strimzi.io/broker-role=true")
    if pod:
        return pod
    cluster_name = get_kafka_cluster_name(kafka_namespace)
    return get_pod_by_label(
        kafka_namespace, f"strimzi.io/name={cluster_name}-kafka"
    )


def get_consumer_group_lag(
    kafka_namespace: str,
    broker_pod: str,
    group_filter: Optional[str] = None,
) -> Dict[str, Dict[str, int]]:
    """Query consumer group lag via kafka-consumer-groups.sh.

    Returns {group_name: {topic: lag}} for groups matching *group_filter*.
    """
    result = run_oc_command(
        ["exec", "-n", kafka_namespace, broker_pod, "--",
         "bin/kafka-consumer-groups.sh",
         "--bootstrap-server", "localhost:9092",
         "--all-groups", "--describe"],
        check=False, timeout=60,
    )
    if result.returncode != 0:
        return {}

    groups: Dict[str, Dict[str, int]] = {}
    current_group = ""
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("GROUP"):
            continue
        if line.startswith("Consumer group"):
            m = re.search(r"'([^']+)'", line)
            if m:
                current_group = m.group(1)
            continue
        parts = line.split()
        if len(parts) >= 6 and current_group:
            group, topic = parts[0], parts[1]
            if group:
                current_group = group
            lag_str = parts[5]
            if lag_str.isdigit():
                if group_filter and group_filter not in current_group:
                    continue
                groups.setdefault(current_group, {})
                groups[current_group][topic] = (
                    groups[current_group].get(topic, 0) + int(lag_str)
                )
    return groups


def get_total_consumer_lag(
    kafka_namespace: str, broker_pod: str
) -> Dict[str, int]:
    """Return total lag per consumer group."""
    raw = get_consumer_group_lag(kafka_namespace, broker_pod)
    return {g: sum(topics.values()) for g, topics in raw.items()}


def get_topic_partition_count(
    kafka_namespace: str, broker_pod: str, topic: str
) -> int:
    """Return the partition count for a given topic."""
    result = run_oc_command(
        ["exec", "-n", kafka_namespace, broker_pod, "--",
         "bin/kafka-topics.sh",
         "--bootstrap-server", "localhost:9092",
         "--describe", "--topic", topic],
        check=False, timeout=30,
    )
    if result.returncode != 0:
        return -1
    count = 0
    for line in result.stdout.splitlines():
        if line.strip().startswith("Partition:"):
            count += 1
        elif "PartitionCount:" in line:
            m = re.search(r"PartitionCount:\s*(\d+)", line)
            if m:
                return int(m.group(1))
    return count


def get_broker_resource_usage(kafka_namespace: str) -> Dict[str, Any]:
    """Capture broker pod CPU/memory via ``oc adm top pod``."""
    result = run_oc_command(
        ["adm", "top", "pod", "-n", kafka_namespace,
         "-l", "strimzi.io/kind=Kafka", "--no-headers"],
        check=False, timeout=30,
    )
    if result.returncode != 0:
        return {}
    pods = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            pods[parts[0]] = {"cpu": parts[1], "memory": parts[2]}
    return pods


def get_broker_disk_usage(
    kafka_namespace: str, broker_pod: str
) -> Dict[str, Any]:
    """Get Kafka data directory disk usage."""
    result = run_oc_command(
        ["exec", "-n", kafka_namespace, broker_pod, "--",
         "df", "-BM", "/var/lib/kafka/data-0"],
        check=False, timeout=15,
    )
    if result.returncode != 0:
        return {}
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4:
            return {
                "total_mb": parts[1].rstrip("M"),
                "used_mb": parts[2].rstrip("M"),
                "avail_mb": parts[3].rstrip("M"),
            }
    return {}


# ---------------------------------------------------------------------------
# Kafka Monitor — background polling thread
# ---------------------------------------------------------------------------

@dataclass
class KafkaSnapshot:
    """Point-in-time Kafka consumer lag and broker metrics."""
    timestamp: float
    consumer_lag: Dict[str, int] = field(default_factory=dict)
    broker_resources: Dict[str, Any] = field(default_factory=dict)

    def total_lag(self) -> int:
        return sum(self.consumer_lag.values())

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["total_lag"] = self.total_lag()
        return d


class KafkaMonitor:
    """Background thread that polls Kafka consumer lag during a test run.

    Usage::

        monitor = KafkaMonitor(kafka_namespace, broker_pod)
        monitor.start()
        # ... run workload ...
        monitor.stop()
        for snap in monitor.snapshots:
            print(snap.total_lag())
    """

    def __init__(
        self,
        kafka_namespace: str,
        broker_pod: str,
        poll_interval: float = 5.0,
    ):
        self.kafka_namespace = kafka_namespace
        self.broker_pod = broker_pod
        self.poll_interval = poll_interval
        self.snapshots: List[KafkaSnapshot] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            lag = get_total_consumer_lag(self.kafka_namespace, self.broker_pod)
            resources = get_broker_resource_usage(self.kafka_namespace)
            self.snapshots.append(
                KafkaSnapshot(
                    timestamp=time.time(),
                    consumer_lag=lag,
                    broker_resources=resources,
                )
            )
            self._stop.wait(self.poll_interval)

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="kafka-monitor"
        )
        self._thread.start()

    def stop(self) -> List[KafkaSnapshot]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=15)
        return self.snapshots

    def peak_lag(self) -> int:
        if not self.snapshots:
            return 0
        return max(s.total_lag() for s in self.snapshots)

    def lag_recovery_time(self, threshold: int = 0) -> Optional[float]:
        """Seconds from peak lag until lag drops to *threshold*.

        Returns None if lag never recovered.
        """
        if not self.snapshots:
            return None
        peak_idx = max(range(len(self.snapshots)),
                       key=lambda i: self.snapshots[i].total_lag())
        for snap in self.snapshots[peak_idx:]:
            if snap.total_lag() <= threshold:
                return snap.timestamp - self.snapshots[peak_idx].timestamp
        return None
