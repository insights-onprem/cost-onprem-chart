"""
Soak/Stability Performance Tests (PERF-SOAK-*).

Long-running tests to validate system stability under sustained load per FLPATH-4036.

These tests are designed to run over extended periods (hours to days) and detect:
- Memory leaks
- Resource exhaustion
- Queue starvation
- Performance degradation over time

Test IDs:
- PERF-SOAK-001: Continuous operation stability
- PERF-SOAK-002: Memory leak detection
- PERF-SOAK-003: Disk usage monitoring
- PERF-SOAK-004: Queue health monitoring

Usage:
    # Run 1-hour soak test (default)
    pytest -m "performance and soak" --soak-duration=3600
    
    # Run 7-day soak test
    SOAK_DURATION_HOURS=168 pytest -m "performance and soak"
    
    # Quick validation (15 minutes)
    SOAK_DURATION_HOURS=0.25 pytest -m "performance and soak"

Environment Variables:
    SOAK_DURATION_HOURS: Test duration in hours (default: 1)
    SOAK_UPLOAD_INTERVAL_MINUTES: Interval between uploads (default: 15)
    SOAK_QUERY_INTERVAL_MINUTES: Interval between API queries (default: 5)
    SOAK_METRICS_INTERVAL_SECONDS: Metrics collection interval (default: 60)
"""

import json
import os
import tempfile
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest
import requests

from conftest import ClusterConfig, JWTToken
from e2e_helpers import (
    NISEConfig,
    cleanup_database_records,
    delete_source,
    ensure_nise_available,
    generate_cluster_id,
    generate_nise_data,
    register_source,
    upload_with_retry,
    wait_for_provider,
    wait_for_summary_tables,
)
from utils import (
    create_upload_package_from_files,
    exec_in_pod,
    execute_db_query,
    get_pod_by_label,
    get_secret_value,
    run_oc_command,
)

from .conftest import (
    PerfCleanupTracker,
    PerfResultCollector,
    PerformanceResult,
    save_perf_result,
)


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class SoakConfig:
    """Configuration for soak tests."""
    
    duration_hours: float = field(
        default_factory=lambda: float(os.environ.get("SOAK_DURATION_HOURS", "1"))
    )
    upload_interval_minutes: float = field(
        default_factory=lambda: float(os.environ.get("SOAK_UPLOAD_INTERVAL_MINUTES", "15"))
    )
    query_interval_minutes: float = field(
        default_factory=lambda: float(os.environ.get("SOAK_QUERY_INTERVAL_MINUTES", "5"))
    )
    metrics_interval_seconds: float = field(
        default_factory=lambda: float(os.environ.get("SOAK_METRICS_INTERVAL_SECONDS", "60"))
    )
    
    @property
    def duration_seconds(self) -> float:
        return self.duration_hours * 3600
    
    @property
    def upload_interval_seconds(self) -> float:
        return self.upload_interval_minutes * 60
    
    @property
    def query_interval_seconds(self) -> float:
        return self.query_interval_minutes * 60


@dataclass
class MetricSample:
    """A single metrics sample."""
    timestamp: str
    elapsed_seconds: float
    memory_mb: Dict[str, float]  # pod_name -> memory
    cpu_cores: Dict[str, float]  # pod_name -> cpu
    disk_usage_gb: Dict[str, float]  # component -> disk
    queue_depths: Dict[str, int]  # queue_name -> depth
    error_count: int = 0
    

@dataclass
class SoakTestState:
    """Tracks state during a soak test run.
    
    Thread-safe for concurrent access from background workers.
    """
    
    start_time: float = field(default_factory=time.time)
    # Use deque for thread-safe append operations
    _samples: deque = field(default_factory=lambda: deque(maxlen=10000))
    _errors: deque = field(default_factory=lambda: deque(maxlen=1000))
    _lock: threading.Lock = field(default_factory=threading.Lock)
    uploads_completed: int = 0
    uploads_failed: int = 0
    queries_completed: int = 0
    queries_failed: int = 0
    stop_event: threading.Event = field(default_factory=threading.Event)
    
    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.start_time
    
    @property
    def samples(self) -> List[MetricSample]:
        """Return samples as list (thread-safe copy)."""
        return list(self._samples)
    
    @property
    def errors(self) -> List[str]:
        """Return errors as list (thread-safe copy)."""
        return list(self._errors)
    
    def add_sample(self, sample: MetricSample):
        """Thread-safe sample addition."""
        self._samples.append(sample)
    
    def add_error(self, error: str):
        """Thread-safe error addition."""
        self._errors.append(f"[{datetime.now(timezone.utc).isoformat()}] {error}")
    
    def increment_uploads(self, success: bool = True):
        """Thread-safe upload counter increment."""
        with self._lock:
            if success:
                self.uploads_completed += 1
            else:
                self.uploads_failed += 1
    
    def increment_queries(self, success: bool = True):
        """Thread-safe query counter increment."""
        with self._lock:
            if success:
                self.queries_completed += 1
            else:
                self.queries_failed += 1


# =============================================================================
# Metrics Collection
# =============================================================================

def collect_pod_resources(namespace: str) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Collect CPU and memory usage for all Cost On-Prem pods.
    
    Returns:
        Tuple of (memory_dict, cpu_dict) with pod_name -> value mappings
    """
    memory = {}
    cpu = {}
    
    result = run_oc_command([
        "adm", "top", "pod", "-n", namespace, "--no-headers"
    ], check=False)
    
    if result.returncode != 0:
        return memory, cpu
    
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        
        parts = line.split()
        if len(parts) >= 3:
            pod_name = parts[0]
            cpu_str = parts[1]
            mem_str = parts[2]
            
            # Parse CPU
            if cpu_str.endswith("m"):
                cpu[pod_name] = float(cpu_str[:-1]) / 1000
            elif cpu_str.isdigit():
                cpu[pod_name] = float(cpu_str)
            
            # Parse memory
            if mem_str.endswith("Mi"):
                memory[pod_name] = float(mem_str[:-2])
            elif mem_str.endswith("Gi"):
                memory[pod_name] = float(mem_str[:-2]) * 1024
            elif mem_str.endswith("Ki"):
                memory[pod_name] = float(mem_str[:-2]) / 1024
    
    return memory, cpu


def collect_disk_usage(namespace: str) -> Dict[str, float]:
    """Collect disk usage for persistent volumes.
    
    Returns:
        Dict with component -> disk_usage_gb mappings
    """
    disk = {}
    
    # PostgreSQL disk usage
    db_pod = get_pod_by_label(namespace, "app.kubernetes.io/component=database")
    if db_pod:
        result = run_oc_command([
            "exec", "-n", namespace, db_pod, "--",
            "df", "-BG", "/var/lib/pgsql/data"
        ], check=False)
        
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 3:
                    used = parts[2].replace("G", "")
                    if used.isdigit():
                        disk["postgresql"] = float(used)
    
    # Kafka disk usage
    result = run_oc_command([
        "exec", "-n", namespace, "kafka-cluster-kafka-0", "--",
        "df", "-BG", "/var/lib/kafka/data-0"
    ], check=False)
    
    if result.returncode == 0:
        lines = result.stdout.strip().split("\n")
        if len(lines) > 1:
            parts = lines[1].split()
            if len(parts) >= 3:
                used = parts[2].replace("G", "")
                if used.isdigit():
                    disk["kafka"] = float(used)
    
    # MinIO/S3 disk usage
    minio_pod = get_pod_by_label(namespace, "app.kubernetes.io/name=minio")
    if minio_pod:
        result = run_oc_command([
            "exec", "-n", namespace, minio_pod, "--",
            "df", "-BG", "/data"
        ], check=False)
        
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 3:
                    used = parts[2].replace("G", "")
                    if used.isdigit():
                        disk["minio"] = float(used)
    
    return disk


def collect_queue_depths(namespace: str) -> Dict[str, int]:
    """Collect queue depths from Kafka and Valkey.
    
    Returns:
        Dict with queue_name -> depth mappings
    """
    queues = {}
    
    # Kafka may be in a separate namespace
    kafka_namespace = os.environ.get("KAFKA_NAMESPACE", "kafka")
    helm_release = os.environ.get("HELM_RELEASE_NAME", "cost-onprem")
    
    # Find Kafka broker pod dynamically
    kafka_pod = get_pod_by_label(kafka_namespace, f"app.kubernetes.io/name={helm_release}-kafka")
    if not kafka_pod:
        # Try alternate label
        kafka_pod = get_pod_by_label(kafka_namespace, "strimzi.io/kind=Kafka")
    
    if kafka_pod:
        # Kafka consumer lag
        result = run_oc_command([
            "exec", "-n", kafka_namespace, kafka_pod, "--",
            "bin/kafka-consumer-groups.sh",
            "--bootstrap-server", "localhost:9092",
            "--all-groups",
            "--describe"
        ], check=False)
        
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if "koku" in line.lower() or "ros" in line.lower():
                    parts = line.split()
                    if len(parts) >= 6:
                        group = parts[0]
                        topic = parts[1]
                        lag = parts[5]
                        if lag.isdigit():
                            queues[f"kafka/{group}/{topic}"] = int(lag)
    
    # Valkey queue lengths
    valkey_pod = get_pod_by_label(namespace, "app.kubernetes.io/component=valkey")
    if valkey_pod:
        for queue in ["ocp", "summary", "ros"]:
            result = run_oc_command([
                "exec", "-n", namespace, valkey_pod, "--",
                "valkey-cli", "LLEN", f"celery:{queue}"
            ], check=False)
            
            if result.returncode == 0 and result.stdout.strip().isdigit():
                queues[f"celery/{queue}"] = int(result.stdout.strip())
    
    return queues


def collect_metrics(namespace: str, start_time: float) -> MetricSample:
    """Collect all metrics for a single sample."""
    memory, cpu = collect_pod_resources(namespace)
    disk = collect_disk_usage(namespace)
    queues = collect_queue_depths(namespace)
    
    return MetricSample(
        timestamp=datetime.now(timezone.utc).isoformat(),
        elapsed_seconds=time.time() - start_time,
        memory_mb=memory,
        cpu_cores=cpu,
        disk_usage_gb=disk,
        queue_depths=queues,
    )


# =============================================================================
# Background Workers
# =============================================================================

def metrics_collector_worker(
    namespace: str,
    state: SoakTestState,
    interval_seconds: float,
):
    """Background worker that collects metrics at regular intervals."""
    while not state.stop_event.is_set():
        try:
            sample = collect_metrics(namespace, state.start_time)
            state.add_sample(sample)
        except Exception as e:
            state.add_error(f"Metrics collection failed: {e}")
        
        state.stop_event.wait(interval_seconds)


def upload_worker(
    namespace: str,
    gateway_url: str,
    upload_url: str,
    jwt_token: str,
    cluster_id: str,
    state: SoakTestState,
    interval_seconds: float,
    db_pod: Optional[str] = None,
):
    """Background worker that performs periodic uploads with processing verification.

    After every 3rd successful upload, verifies that data has been ingested by
    checking summary tables. This catches silent data drops without adding
    excessive overhead on every upload cycle.
    """
    ensure_nise_available()
    
    upload_count = 0
    uploads_since_verify = 0
    VERIFY_EVERY_N = 3
    
    while not state.stop_event.is_set():
        try:
            # Generate 1 day of data
            end_date = datetime.now(timezone.utc)
            start_date = end_date - timedelta(days=1)
            
            with tempfile.TemporaryDirectory() as temp_dir:
                nise_result = generate_nise_data(
                    NISEConfig(
                        start_date=start_date,
                        end_date=end_date,
                        cluster_id=cluster_id,
                        output_dir=temp_dir,
                    )
                )
                
                if nise_result.get("success"):
                    pod_usage_files = nise_result.get("pod_usage_files", [])
                    ros_usage_files = nise_result.get("ros_usage_files", [])
                    
                    if pod_usage_files:
                        tar_path = create_upload_package_from_files(
                            pod_usage_files,
                            ros_usage_files if ros_usage_files else [],
                            cluster_id,
                            start_date=start_date,
                            end_date=end_date,
                        )
                        
                        if upload_with_retry(
                            upload_url,
                            tar_path,
                            jwt_token,
                            "application/vnd.redhat.hccm.filename+tgz",
                            max_retries=3,
                        ):
                            state.increment_uploads(success=True)
                            upload_count += 1
                            uploads_since_verify += 1
                            
                            # Periodically verify processing
                            if db_pod and uploads_since_verify >= VERIFY_EVERY_N:
                                uploads_since_verify = 0
                                try:
                                    schema = wait_for_summary_tables(
                                        namespace, db_pod, cluster_id,
                                        timeout=120, interval=30,
                                    )
                                    if not schema:
                                        state.add_error(
                                            f"Processing verification failed after upload {upload_count}: "
                                            "summary tables not populated"
                                        )
                                except Exception as ve:
                                    state.add_error(f"Processing verification error: {ve}")
                        else:
                            state.increment_uploads(success=False)
                            state.add_error(f"Upload {upload_count + 1} failed")
                else:
                    state.increment_uploads(success=False)
                    state.add_error(f"NISE generation failed for upload {upload_count + 1}")
        except Exception as e:
            state.increment_uploads(success=False)
            state.add_error(f"Upload worker error: {e}")
        
        state.stop_event.wait(interval_seconds)


def query_worker(
    gateway_url: str,
    jwt_token: str,
    state: SoakTestState,
    interval_seconds: float,
):
    """Background worker that performs periodic API queries."""
    endpoints = [
        "/api/cost-management/v1/sources/",
        "/api/cost-management/v1/reports/openshift/costs/?filter[time_scope_units]=month&filter[time_scope_value]=-1",
        "/api/cost-management/v1/reports/openshift/memory/?filter[time_scope_units]=month&filter[time_scope_value]=-1",
        "/api/cost-management/v1/recommendations/openshift",
    ]
    
    query_idx = 0
    while not state.stop_event.is_set():
        try:
            endpoint = endpoints[query_idx % len(endpoints)]
            url = f"{gateway_url}{endpoint}"
            
            response = requests.get(
                url,
                headers={"Authorization": f"Bearer {jwt_token}"},
                timeout=30,
                verify=False,
            )
            
            if response.status_code in [200, 404]:
                state.increment_queries(success=True)
            else:
                state.increment_queries(success=False)
                state.add_error(f"Query failed: {endpoint} returned {response.status_code}")
            
            query_idx += 1
        except Exception as e:
            state.increment_queries(success=False)
            state.add_error(f"Query worker error: {e}")
        
        state.stop_event.wait(interval_seconds)


# =============================================================================
# Analysis Functions
# =============================================================================

def analyze_memory_trend(samples: List[MetricSample]) -> Dict[str, Any]:
    """Analyze memory usage trend over time.
    
    Returns:
        Dict with per-pod memory analysis including growth rate.
    """
    if len(samples) < 2:
        return {"error": "Insufficient samples"}
    
    analysis = {}
    
    # Get all pod names that appear in samples
    all_pods = set()
    for sample in samples:
        all_pods.update(sample.memory_mb.keys())
    
    for pod in all_pods:
        # Get time series for this pod
        times = []
        values = []
        for sample in samples:
            if pod in sample.memory_mb:
                times.append(sample.elapsed_seconds)
                values.append(sample.memory_mb[pod])
        
        if len(values) < 2:
            continue
        
        # Calculate statistics
        initial = values[0]
        final = values[-1]
        peak = max(values)
        avg = sum(values) / len(values)
        
        # Calculate growth rate (MB per hour)
        duration_hours = (times[-1] - times[0]) / 3600
        if duration_hours > 0:
            growth_rate = (final - initial) / duration_hours
            growth_pct = ((final - initial) / initial * 100) if initial > 0 else 0
        else:
            growth_rate = 0
            growth_pct = 0
        
        analysis[pod] = {
            "initial_mb": initial,
            "final_mb": final,
            "peak_mb": peak,
            "avg_mb": avg,
            "growth_rate_mb_per_hour": growth_rate,
            "growth_pct": growth_pct,
            "sample_count": len(values),
        }
    
    return analysis


def analyze_disk_trend(samples: List[MetricSample]) -> Dict[str, Any]:
    """Analyze disk usage trend over time."""
    if len(samples) < 2:
        return {"error": "Insufficient samples"}
    
    analysis = {}
    
    all_components = set()
    for sample in samples:
        all_components.update(sample.disk_usage_gb.keys())
    
    for component in all_components:
        times = []
        values = []
        for sample in samples:
            if component in sample.disk_usage_gb:
                times.append(sample.elapsed_seconds)
                values.append(sample.disk_usage_gb[component])
        
        if len(values) < 2:
            continue
        
        initial = values[0]
        final = values[-1]
        peak = max(values)
        
        duration_hours = (times[-1] - times[0]) / 3600
        growth_rate = (final - initial) / duration_hours if duration_hours > 0 else 0
        
        analysis[component] = {
            "initial_gb": initial,
            "final_gb": final,
            "peak_gb": peak,
            "growth_rate_gb_per_hour": growth_rate,
            "sample_count": len(values),
        }
    
    return analysis


def analyze_queue_health(samples: List[MetricSample]) -> Dict[str, Any]:
    """Analyze queue health over time."""
    if len(samples) < 2:
        return {"error": "Insufficient samples"}
    
    analysis = {}
    
    all_queues = set()
    for sample in samples:
        all_queues.update(sample.queue_depths.keys())
    
    for queue in all_queues:
        values = [s.queue_depths.get(queue, 0) for s in samples]
        
        peak = max(values)
        avg = sum(values) / len(values)
        
        # Count how often queue was non-empty
        non_empty_count = sum(1 for v in values if v > 0)
        non_empty_pct = non_empty_count / len(values) * 100
        
        analysis[queue] = {
            "peak_depth": peak,
            "avg_depth": avg,
            "non_empty_pct": non_empty_pct,
            "sample_count": len(values),
        }
    
    return analysis


# =============================================================================
# Test Classes
# =============================================================================

@pytest.mark.performance
@pytest.mark.soak
class TestSoakStability:
    """Soak/stability tests (PERF-SOAK-*)."""

    @pytest.fixture(scope="class")
    def soak_config(self) -> SoakConfig:
        """Get soak test configuration."""
        return SoakConfig()

    @pytest.fixture(scope="class")
    def gateway_url(self, cluster_config) -> str:
        """Get gateway URL."""
        gateway_route = run_oc_command([
            "get", "route", "-n", cluster_config.namespace,
            f"{cluster_config.helm_release_name}-api",
            "-o", "jsonpath={.spec.host}"
        ], check=False)
        
        if gateway_route.returncode != 0 or not gateway_route.stdout.strip():
            pytest.skip("Gateway route not found")
        
        return f"https://{gateway_route.stdout.strip()}"

    @pytest.fixture(scope="class")
    def upload_url(self, gateway_url) -> str:
        """Get upload URL."""
        return f"{gateway_url}/api/ingress/v1/upload"

    def test_perf_soak_001_continuous_operation(
        self,
        cluster_config,
        perf_cleanup,
        perf_result,
        perf_collector,
        soak_config,
        gateway_url,
        upload_url,
        jwt_token: JWTToken,
        rh_identity_header: str,
    ):
        """PERF-SOAK-001: Continuous operation stability test.
        
        Runs for the configured duration with:
        - Periodic data uploads
        - Periodic API queries
        - Continuous metrics collection
        
        Validates:
        - No OOM kills
        - Sustained throughput
        - System responsiveness
        """
        cluster_id = generate_cluster_id()
        source_name = f"perf-soak-001-{uuid.uuid4().hex[:8]}"
        
        # Register source
        source = register_source(
            cluster_config.namespace,
            gateway_url,
            jwt_token.access_token,
            source_name,
            cluster_id,
        )
        
        perf_cleanup.track(
            source_id=source.source_id,
            cluster_id=cluster_id,
            source_name=source_name,
        )
        
        # Initialize state
        state = SoakTestState()
        
        # Get DB pod for processing verification
        db_pod = get_pod_by_label(cluster_config.namespace, "app.kubernetes.io/component=database")
        
        # Start background workers
        threads = []
        
        # Metrics collector
        metrics_thread = threading.Thread(
            target=metrics_collector_worker,
            args=(cluster_config.namespace, state, soak_config.metrics_interval_seconds),
        )
        metrics_thread.start()
        threads.append(metrics_thread)
        
        # Upload worker
        upload_thread = threading.Thread(
            target=upload_worker,
            args=(
                cluster_config.namespace,
                gateway_url,
                upload_url,
                jwt_token.access_token,
                cluster_id,
                state,
                soak_config.upload_interval_seconds,
                db_pod,
            ),
        )
        upload_thread.start()
        threads.append(upload_thread)
        
        # Query worker
        query_thread = threading.Thread(
            target=query_worker,
            args=(
                gateway_url,
                jwt_token.access_token,
                state,
                soak_config.query_interval_seconds,
            ),
        )
        query_thread.start()
        threads.append(query_thread)
        
        print(f"\n=== PERF-SOAK-001: Starting {soak_config.duration_hours}h soak test ===")
        print(f"  Upload interval: {soak_config.upload_interval_minutes} minutes")
        print(f"  Query interval: {soak_config.query_interval_minutes} minutes")
        print(f"  Metrics interval: {soak_config.metrics_interval_seconds} seconds")
        
        try:
            # Run for configured duration
            start = time.time()
            while time.time() - start < soak_config.duration_seconds:
                elapsed = time.time() - start
                remaining = soak_config.duration_seconds - elapsed
                
                # Progress update every 5 minutes
                if int(elapsed) % 300 == 0:
                    print(f"  Progress: {elapsed/3600:.1f}h elapsed, {remaining/3600:.1f}h remaining")
                    print(f"    Uploads: {state.uploads_completed} ok, {state.uploads_failed} failed")
                    print(f"    Queries: {state.queries_completed} ok, {state.queries_failed} failed")
                    print(f"    Samples: {len(state.samples)}")
                
                time.sleep(60)  # Check every minute
        finally:
            # Stop workers
            state.stop_event.set()
            for t in threads:
                t.join(timeout=30)
        
        # Analyze results
        memory_analysis = analyze_memory_trend(state.samples)
        disk_analysis = analyze_disk_trend(state.samples)
        queue_analysis = analyze_queue_health(state.samples)
        
        # Check for OOM (pod restarts)
        result = run_oc_command([
            "get", "pods", "-n", cluster_config.namespace,
            "-o", "jsonpath={range .items[*]}{.metadata.name}:{.status.containerStatuses[0].restartCount}{\"\\n\"}{end}"
        ], check=False)
        
        restarts = {}
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if ":" in line:
                    pod, count = line.split(":", 1)
                    if count.isdigit() and int(count) > 0:
                        restarts[pod] = int(count)
        
        # Build result
        perf_result.metrics = {
            "duration_hours": soak_config.duration_hours,
            "duration_actual_seconds": state.elapsed_seconds,
            "uploads_completed": state.uploads_completed,
            "uploads_failed": state.uploads_failed,
            "queries_completed": state.queries_completed,
            "queries_failed": state.queries_failed,
            "sample_count": len(state.samples),
            "pod_restarts": restarts,
            "memory_analysis": memory_analysis,
            "disk_analysis": disk_analysis,
            "queue_analysis": queue_analysis,
            "errors": state.errors[-20:],  # Last 20 errors
        }
        
        perf_result.passed = (
            state.uploads_failed == 0 and
            state.queries_failed / max(state.queries_completed, 1) < 0.05 and
            len(restarts) == 0
        )
        
        perf_collector.add_result(perf_result)
        
        print(f"\n=== PERF-SOAK-001 Results ===")
        print(f"  Duration: {state.elapsed_seconds/3600:.2f} hours")
        print(f"  Uploads: {state.uploads_completed} ok, {state.uploads_failed} failed")
        print(f"  Queries: {state.queries_completed} ok, {state.queries_failed} failed")
        print(f"  Pod restarts: {restarts if restarts else 'None'}")
        print(f"  Errors: {len(state.errors)}")
        
        assert len(restarts) == 0, f"Pod restarts detected (possible OOM): {restarts}"
        assert state.uploads_failed == 0, f"{state.uploads_failed} uploads failed"

    def test_perf_soak_002_memory_leak_detection(
        self,
        cluster_config,
        perf_result,
        perf_collector,
        soak_config,
    ):
        """PERF-SOAK-002: Memory leak detection.
        
        Monitors memory growth over time. Requires SOAK-001 to have run first
        or can run standalone for shorter periods.
        
        Success criteria: < 5% memory growth per day
        """
        # Collect samples for the configured duration (or minimum 60 seconds for validation)
        duration = max(soak_config.duration_seconds, 60)
        interval = soak_config.metrics_interval_seconds
        
        samples = []
        start_time = time.time()
        
        print(f"\n=== PERF-SOAK-002: Collecting memory samples for {duration/60:.0f} minutes ===")
        
        while time.time() - start_time < duration:
            sample = collect_metrics(cluster_config.namespace, start_time)
            samples.append(sample)
            
            elapsed = time.time() - start_time
            if int(elapsed) % 300 == 0:
                print(f"  Progress: {elapsed/60:.0f} minutes, {len(samples)} samples")
            
            time.sleep(interval)
        
        # Analyze memory trend
        memory_analysis = analyze_memory_trend(samples)
        
        # Calculate overall memory health
        leak_detected = False
        leak_pods = []
        
        for pod, stats in memory_analysis.items():
            if isinstance(stats, dict) and "growth_pct" in stats:
                # Extrapolate to daily growth
                duration_hours = samples[-1].elapsed_seconds / 3600 if samples else 0
                if duration_hours > 0:
                    daily_growth_pct = stats["growth_pct"] * (24 / duration_hours)
                    if daily_growth_pct > 5:
                        leak_detected = True
                        leak_pods.append({
                            "pod": pod,
                            "daily_growth_pct": daily_growth_pct,
                            "initial_mb": stats["initial_mb"],
                            "final_mb": stats["final_mb"],
                        })
        
        perf_result.metrics = {
            "duration_seconds": duration,
            "sample_count": len(samples),
            "memory_analysis": memory_analysis,
            "leak_detected": leak_detected,
            "leak_pods": leak_pods,
        }
        
        perf_result.passed = not leak_detected
        perf_collector.add_result(perf_result)
        
        print(f"\n=== PERF-SOAK-002 Results ===")
        print(f"  Duration: {duration/60:.0f} minutes")
        print(f"  Samples: {len(samples)}")
        print(f"  Leak detected: {leak_detected}")
        if leak_pods:
            for p in leak_pods:
                print(f"    - {p['pod']}: {p['daily_growth_pct']:.1f}% daily growth")
        
        assert not leak_detected, f"Memory leak detected in pods: {[p['pod'] for p in leak_pods]}"

    def test_perf_soak_003_disk_usage_monitoring(
        self,
        cluster_config,
        perf_result,
        perf_collector,
        soak_config,
    ):
        """PERF-SOAK-003: Disk usage monitoring.
        
        Monitors disk usage trends for PostgreSQL, Kafka, and MinIO.
        
        Success criteria: No disk exhaustion warnings
        """
        # Collect samples for the configured duration (or minimum 60 seconds for validation)
        duration = max(soak_config.duration_seconds, 60)
        interval = soak_config.metrics_interval_seconds
        
        samples = []
        start_time = time.time()
        
        print(f"\n=== PERF-SOAK-003: Monitoring disk usage for {duration/60:.0f} minutes ===")
        
        while time.time() - start_time < duration:
            sample = collect_metrics(cluster_config.namespace, start_time)
            samples.append(sample)
            time.sleep(interval)
        
        # Analyze disk trend
        disk_analysis = analyze_disk_trend(samples)
        
        # Check for concerning growth rates
        warnings = []
        for component, stats in disk_analysis.items():
            if isinstance(stats, dict) and "growth_rate_gb_per_hour" in stats:
                # Project to 7 days
                projected_growth = stats["growth_rate_gb_per_hour"] * 24 * 7
                if projected_growth > 50:  # > 50GB in 7 days
                    warnings.append({
                        "component": component,
                        "current_gb": stats["final_gb"],
                        "projected_7day_growth_gb": projected_growth,
                    })
        
        perf_result.metrics = {
            "duration_seconds": duration,
            "sample_count": len(samples),
            "disk_analysis": disk_analysis,
            "warnings": warnings,
        }
        
        perf_result.passed = len(warnings) == 0
        perf_collector.add_result(perf_result)
        
        print(f"\n=== PERF-SOAK-003 Results ===")
        print(f"  Duration: {duration/60:.0f} minutes")
        for component, stats in disk_analysis.items():
            if isinstance(stats, dict):
                print(f"  {component}:")
                print(f"    Current: {stats.get('final_gb', 'N/A')} GB")
                print(f"    Growth rate: {stats.get('growth_rate_gb_per_hour', 0):.2f} GB/hour")
        
        if warnings:
            print(f"  Warnings: {len(warnings)}")
            for w in warnings:
                print(f"    - {w['component']}: {w['projected_7day_growth_gb']:.1f} GB projected growth in 7 days")

    def test_perf_soak_004_queue_health_monitoring(
        self,
        cluster_config,
        perf_result,
        perf_collector,
        soak_config,
    ):
        """PERF-SOAK-004: Queue health monitoring.
        
        Monitors Celery and Kafka queue depths to detect starvation or backlog.
        
        Success criteria: No sustained queue growth indicating processing backup
        """
        # Collect samples for the configured duration (or minimum 60 seconds for validation)
        duration = max(soak_config.duration_seconds, 60)
        interval = soak_config.metrics_interval_seconds
        
        samples = []
        start_time = time.time()
        
        print(f"\n=== PERF-SOAK-004: Monitoring queue health for {duration/60:.0f} minutes ===")
        
        while time.time() - start_time < duration:
            sample = collect_metrics(cluster_config.namespace, start_time)
            samples.append(sample)
            time.sleep(interval)
        
        # Analyze queue health
        queue_analysis = analyze_queue_health(samples)
        
        # Check for concerning patterns
        concerns = []
        for queue, stats in queue_analysis.items():
            if isinstance(stats, dict):
                # High sustained queue depth
                if stats.get("avg_depth", 0) > 100:
                    concerns.append({
                        "queue": queue,
                        "issue": "high_avg_depth",
                        "avg_depth": stats["avg_depth"],
                        "peak_depth": stats["peak_depth"],
                    })
                # Queue always has items (potential backup)
                if stats.get("non_empty_pct", 0) > 90:
                    concerns.append({
                        "queue": queue,
                        "issue": "sustained_backlog",
                        "non_empty_pct": stats["non_empty_pct"],
                    })
        
        perf_result.metrics = {
            "duration_seconds": duration,
            "sample_count": len(samples),
            "queue_analysis": queue_analysis,
            "concerns": concerns,
        }
        
        perf_result.passed = len(concerns) == 0
        perf_collector.add_result(perf_result)
        
        print(f"\n=== PERF-SOAK-004 Results ===")
        print(f"  Duration: {duration/60:.0f} minutes")
        for queue, stats in queue_analysis.items():
            if isinstance(stats, dict):
                print(f"  {queue}:")
                print(f"    Peak: {stats.get('peak_depth', 0)}, Avg: {stats.get('avg_depth', 0):.1f}")
                print(f"    Non-empty: {stats.get('non_empty_pct', 0):.0f}%")
        
        if concerns:
            print(f"  Concerns: {len(concerns)}")
            for c in concerns:
                print(f"    - {c['queue']}: {c['issue']}")
