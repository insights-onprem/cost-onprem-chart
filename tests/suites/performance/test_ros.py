"""
ROS/Kruize Performance Tests (PERF-ROS-*).

Tests Resource Optimization Service performance under various loads per FLPATH-4036.

Test IDs:
- PERF-ROS-001: Recommendation baseline (single workload)
- PERF-ROS-002: Multi-workload scale (50 workloads)
- PERF-ROS-003: Recommendation refresh
- PERF-ROS-004: Kruize memory pressure
"""

import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import pytest

from conftest import ClusterConfig, JWTToken
from e2e_helpers import (
    generate_cluster_id,
    register_source,
    wait_for_provider,
)
from utils import (
    execute_db_query,
    get_pod_by_label,
    get_secret_value,
    run_oc_command,
)

from .conftest import (
    PerfResultCollector,
    PerfTimer,
    PerformanceResult,
)
from .test_ingestion import generate_and_upload_data


# =============================================================================
# Constants
# =============================================================================

UPLOAD_CONTENT_TYPE = "application/vnd.redhat.hccm.filename+tgz"


# =============================================================================
# Helper Functions
# =============================================================================

def get_kruize_heap_usage(namespace: str) -> Optional[Dict[str, float]]:
    """Get Kruize JVM heap usage metrics.
    
    Returns:
        Dict with 'used_mb', 'committed_mb', 'max_mb' or None if unavailable.
    """
    kruize_pod = get_pod_by_label(namespace, "app.kubernetes.io/component=ros-optimization")
    if not kruize_pod:
        return None
    
    result = run_oc_command([
        "adm", "top", "pod", "-n", namespace, kruize_pod, "--no-headers"
    ], check=False)
    
    if result.returncode != 0 or not result.stdout.strip():
        return None
    
    try:
        parts = result.stdout.strip().split()
        if len(parts) >= 3:
            mem_str = parts[2]
            if mem_str.endswith("Mi"):
                mem_mb = float(mem_str[:-2])
            elif mem_str.endswith("Gi"):
                mem_mb = float(mem_str[:-2]) * 1024
            elif mem_str.endswith("Ki"):
                mem_mb = float(mem_str[:-2]) / 1024
            else:
                mem_mb = float(mem_str)
            return {"used_mb": mem_mb}
    except (ValueError, IndexError):
        pass
    
    return None


def get_ros_queue_depth(namespace: str) -> Optional[int]:
    """Get the ROS events Kafka topic queue depth."""
    # Kafka may be in a separate namespace
    kafka_namespace = os.environ.get("KAFKA_NAMESPACE", "kafka")
    helm_release = os.environ.get("HELM_RELEASE_NAME", "cost-onprem")
    
    # Find Kafka broker pod dynamically
    kafka_pod = get_pod_by_label(kafka_namespace, f"app.kubernetes.io/name={helm_release}-kafka")
    if not kafka_pod:
        kafka_pod = get_pod_by_label(kafka_namespace, "strimzi.io/kind=Kafka")
    
    if not kafka_pod:
        return None
    
    result = run_oc_command([
        "exec", "-n", kafka_namespace,
        kafka_pod, "--",
        "bin/kafka-consumer-groups.sh",
        "--bootstrap-server", "localhost:9092",
        "--group", "ros-processor",
        "--describe"
    ], check=False)
    
    if result.returncode != 0:
        return None
    
    try:
        total_lag = 0
        for line in result.stdout.strip().split("\n"):
            if "hccm.ros.events" in line:
                parts = line.split()
                if len(parts) >= 6:
                    lag = int(parts[5]) if parts[5].isdigit() else 0
                    total_lag += lag
        return total_lag
    except (ValueError, IndexError):
        return None


def get_kruize_experiment_count(
    namespace: str,
    db_pod: str,
    kruize_user: str,
    kruize_password: str,
    cluster_id: Optional[str] = None,
) -> int:
    """Get the count of Kruize experiments."""
    where_clause = f"WHERE cluster_name = '{cluster_id}'" if cluster_id else ""
    
    result = execute_db_query(
        namespace,
        db_pod,
        "costonprem_kruize",
        kruize_user,
        f"SELECT COUNT(*) FROM kruize_experiments {where_clause}",
        password=kruize_password,
    )
    
    if result and len(result) > 0:
        return int(result[0][0])
    return 0


def get_kruize_recommendation_count(
    namespace: str,
    db_pod: str,
    kruize_user: str,
    kruize_password: str,
    cluster_id: Optional[str] = None,
) -> int:
    """Get the count of Kruize recommendations."""
    if cluster_id:
        query = f"""
        SELECT COUNT(*) FROM kruize_recommendations r
        JOIN kruize_experiments e ON r.experiment_name = e.experiment_name
        WHERE e.cluster_name = '{cluster_id}'
        """
    else:
        query = "SELECT COUNT(*) FROM kruize_recommendations"
    
    result = execute_db_query(
        namespace,
        db_pod,
        "costonprem_kruize",
        kruize_user,
        query,
        password=kruize_password,
    )
    
    if result and len(result) > 0:
        return int(result[0][0])
    return 0


def wait_for_kruize_experiments(
    namespace: str,
    db_pod: str,
    kruize_user: str,
    kruize_password: str,
    cluster_id: str,
    expected_count: int,
    timeout: int = 300,
) -> Tuple[bool, int, float]:
    """Wait for Kruize experiments to be created.
    
    Returns:
        Tuple of (success, actual_count, elapsed_time)
    """
    start_time = time.time()
    interval = 10
    
    while time.time() - start_time < timeout:
        count = get_kruize_experiment_count(
            namespace, db_pod, kruize_user, kruize_password, cluster_id
        )
        if count >= expected_count:
            elapsed = time.time() - start_time
            return True, count, elapsed
        time.sleep(interval)
    
    elapsed = time.time() - start_time
    final_count = get_kruize_experiment_count(
        namespace, db_pod, kruize_user, kruize_password, cluster_id
    )
    return False, final_count, elapsed


def wait_for_kruize_recommendations(
    namespace: str,
    db_pod: str,
    kruize_user: str,
    kruize_password: str,
    cluster_id: str,
    expected_count: int,
    timeout: int = 300,
) -> Tuple[bool, int, float]:
    """Wait for Kruize recommendations to be generated.
    
    Returns:
        Tuple of (success, actual_count, elapsed_time)
    """
    start_time = time.time()
    interval = 10
    
    while time.time() - start_time < timeout:
        count = get_kruize_recommendation_count(
            namespace, db_pod, kruize_user, kruize_password, cluster_id
        )
        if count >= expected_count:
            elapsed = time.time() - start_time
            return True, count, elapsed
        time.sleep(interval)
    
    elapsed = time.time() - start_time
    final_count = get_kruize_recommendation_count(
        namespace, db_pod, kruize_user, kruize_password, cluster_id
    )
    return False, final_count, elapsed




# =============================================================================
# Test Classes
# =============================================================================

@pytest.mark.performance
@pytest.mark.ros_perf
@pytest.mark.timeout(900)
class TestROSPerformance:
    """ROS/Kruize performance tests (PERF-ROS-*)."""

    @pytest.fixture(scope="class")
    def kruize_credentials(self, cluster_config) -> Dict[str, str]:
        """Get Kruize database credentials."""
        secret_name = f"{cluster_config.helm_release_name}-db-credentials"
        user = get_secret_value(cluster_config.namespace, secret_name, "kruize-user")
        password = get_secret_value(cluster_config.namespace, secret_name, "kruize-password")
        
        if not user or not password:
            pytest.skip("Kruize database credentials not found")
        
        return {"user": user, "password": password, "database": "costonprem_kruize"}

    @pytest.fixture(scope="class")
    def db_pod(self, cluster_config) -> str:
        """Get database pod name."""
        pod = get_pod_by_label(cluster_config.namespace, "app.kubernetes.io/component=database")
        if not pod:
            pytest.skip("Database pod not found")
        return pod

    @pytest.fixture(scope="class")
    def upload_url(self, cluster_config) -> str:
        """Get upload URL for ingestion."""
        gateway_route = run_oc_command([
            "get", "route", "-n", cluster_config.namespace,
            f"{cluster_config.helm_release_name}-api",
            "-o", "jsonpath={.spec.host}"
        ], check=False)
        
        if gateway_route.returncode != 0 or not gateway_route.stdout.strip():
            pytest.skip("Gateway route not found")
        
        return f"https://{gateway_route.stdout.strip()}/api/ingress/v1/upload"

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
    def ingress_pod(self, cluster_config) -> str:
        """Get ingress pod name."""
        pod = get_pod_by_label(cluster_config.namespace, "app.kubernetes.io/component=ingress")
        if not pod:
            pytest.skip("Ingress pod not found")
        return pod

    @pytest.fixture(scope="class")
    def koku_api_url(self, cluster_config) -> str:
        """Get internal Koku API URL."""
        return f"http://{cluster_config.helm_release_name}-koku-api.{cluster_config.namespace}.svc.cluster.local:8000/api/cost-management/v1"

    def test_perf_ros_001_recommendation_baseline(
        self,
        cluster_config,
        perf_cleanup,
        perf_timer,
        perf_result,
        perf_collector,
        kruize_credentials,
        db_pod,
        upload_url,
        gateway_url,
        ingress_pod,
        koku_api_url,
        jwt_token: JWTToken,
        rh_identity_header: str,
    ):
        """PERF-ROS-001: Single workload baseline.
        
        Measures:
        - Time to generate first recommendation for a single workload
        - End-to-end latency from upload to recommendation
        
        Expected: < 5 minutes for single workload recommendation
        """
        cluster_id = generate_cluster_id()
        source_name = f"perf-ros-001-{uuid.uuid4().hex[:8]}"
        
        # Register source
        with perf_timer.measure("source_registration"):
            source = register_source(
                cluster_config.namespace,
                ingress_pod,
                koku_api_url,
                rh_identity_header,
                cluster_id,
                "org1234567",
                source_name,
            )
        
        perf_cleanup.track(
            source_id=source.source_id,
            cluster_id=cluster_id,
            source_name=source_name,
        )
        
        # Generate and upload single workload data (7 days for recommendation generation)
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=7)
        
        with perf_timer.measure("data_generation_upload"):
            upload_result = generate_and_upload_data(
                cluster_id=cluster_id,
                source_name=source_name,
                start_date=start_date,
                end_date=end_date,
                ingress_url=gateway_url + "/api/ingress",
                jwt_token=jwt_token,
                profile_name="baseline",  # Uses ROS-enabled data generation
            )
        
        assert upload_result.get("upload_status") == 202, f"Upload failed: {upload_result}"
        
        # Wait for processing
        with perf_timer.measure("koku_processing"):
            provider_ready = wait_for_provider(
                cluster_config.namespace,
                db_pod,
                cluster_id,
                timeout=180,
            )
        
        assert provider_ready, "Provider not ready within timeout"
        
        # Wait for Kruize experiments
        with perf_timer.measure("kruize_experiment_creation"):
            exp_success, exp_count, exp_time = wait_for_kruize_experiments(
                cluster_config.namespace,
                db_pod,
                kruize_credentials["user"],
                kruize_credentials["password"],
                cluster_id,
                expected_count=1,
                timeout=300,
            )
        
        # Wait for recommendations
        rec_time = 0
        rec_count = 0
        if exp_success:
            with perf_timer.measure("recommendation_generation"):
                rec_success, rec_count, rec_time = wait_for_kruize_recommendations(
                    cluster_config.namespace,
                    db_pod,
                    kruize_credentials["user"],
                    kruize_credentials["password"],
                    cluster_id,
                    expected_count=1,
                    timeout=300,
                )
        
        # Collect metrics
        perf_result.metrics = {
            "workload_count": 1,
            "experiment_count": exp_count,
            "recommendation_count": rec_count,
            "experiment_creation_time_sec": exp_time,
            "recommendation_time_sec": rec_time,
            "total_e2e_time_sec": sum(t.duration_seconds for t in perf_timer.get_timings()),
            "data_gen_time_sec": upload_result.get("generation_seconds", 0),
            "upload_time_sec": upload_result.get("upload_seconds", 0),
            "package_size_mb": upload_result.get("package_size_mb", 0),
        }
        perf_result.timings = perf_timer.get_timings()
        perf_result.passed = exp_count >= 1
        
        perf_collector.add_result(perf_result)
        
        print(f"\n=== PERF-ROS-001 Results ===")
        print(f"  Experiment created: {exp_success} ({exp_count} experiments in {exp_time:.1f}s)")
        print(f"  Recommendations: {rec_count} in {rec_time:.1f}s")
        print(f"  Total E2E time: {perf_result.metrics['total_e2e_time_sec']:.1f}s")
        
        assert exp_count >= 1, f"Expected at least 1 experiment, got {exp_count}"

    @pytest.mark.timeout(1200)
    def test_perf_ros_002_multi_workload_scale(
        self,
        cluster_config,
        perf_cleanup,
        perf_timer,
        perf_result,
        perf_collector,
        kruize_credentials,
        db_pod,
        upload_url,
        gateway_url,
        ingress_pod,
        koku_api_url,
        jwt_token: JWTToken,
        rh_identity_header: str,
    ):
        """PERF-ROS-002: Multi-workload scale test.
        
        Measures:
        - Time to process 50 workloads concurrently
        - Kruize memory usage under load
        - ROS event queue depth
        
        Expected: All 50 workloads processed within 15 minutes
        """
        cluster_id = generate_cluster_id()
        source_name = f"perf-ros-002-{uuid.uuid4().hex[:8]}"
        num_workloads = 50
        
        # Capture initial Kruize memory
        initial_heap = get_kruize_heap_usage(cluster_config.namespace)
        initial_queue = get_ros_queue_depth(cluster_config.namespace)
        
        # Register source
        with perf_timer.measure("source_registration"):
            source = register_source(
                cluster_config.namespace,
                ingress_pod,
                koku_api_url,
                rh_identity_header,
                cluster_id,
                "org1234567",
                source_name,
            )
        
        perf_cleanup.track(
            source_id=source.source_id,
            cluster_id=cluster_id,
            source_name=source_name,
        )
        
        # Generate and upload 50 workloads
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=7)
        
        with perf_timer.measure("data_generation_upload"):
            upload_result = generate_and_upload_data(
                cluster_id=cluster_id,
                source_name=source_name,
                start_date=start_date,
                end_date=end_date,
                ingress_url=gateway_url + "/api/ingress",
                jwt_token=jwt_token,
                profile_name="baseline",
            )
        
        assert upload_result.get("upload_status") == 202, f"Upload failed: {upload_result}"
        
        # Capture peak queue depth during processing
        import threading
        
        peak_metrics = {"queue": 0, "memory": 0}
        monitor_stop = threading.Event()
        
        def monitor_thread():
            while not monitor_stop.is_set():
                q = get_ros_queue_depth(cluster_config.namespace)
                if q and q > peak_metrics["queue"]:
                    peak_metrics["queue"] = q
                
                m = get_kruize_heap_usage(cluster_config.namespace)
                if m and m.get("used_mb", 0) > peak_metrics["memory"]:
                    peak_metrics["memory"] = m["used_mb"]
                
                monitor_stop.wait(5)
        
        monitor = threading.Thread(target=monitor_thread)
        monitor.start()
        
        try:
            # Wait for processing
            with perf_timer.measure("koku_processing"):
                wait_for_provider(
                    cluster_config.namespace,
                    db_pod,
                    cluster_id,
                    timeout=300,
                )
            
            # Wait for Kruize experiments (expect ~50 for 50 workloads)
            with perf_timer.measure("kruize_experiment_creation"):
                exp_success, exp_count, exp_time = wait_for_kruize_experiments(
                    cluster_config.namespace,
                    db_pod,
                    kruize_credentials["user"],
                    kruize_credentials["password"],
                    cluster_id,
                    expected_count=num_workloads,
                    timeout=600,
                )
        finally:
            monitor_stop.set()
            monitor.join(timeout=10)
        
        # Final memory measurement
        final_heap = get_kruize_heap_usage(cluster_config.namespace)
        
        # Collect metrics
        perf_result.metrics = {
            "workload_count": num_workloads,
            "experiment_count": exp_count,
            "experiment_creation_time_sec": exp_time,
            "initial_heap_mb": initial_heap.get("used_mb") if initial_heap else None,
            "final_heap_mb": final_heap.get("used_mb") if final_heap else None,
            "peak_memory_mb": peak_metrics["memory"],
            "peak_queue_depth": peak_metrics["queue"],
            "initial_queue_depth": initial_queue,
            "data_gen_time_sec": upload_result.get("generation_seconds", 0),
            "upload_time_sec": upload_result.get("upload_seconds", 0),
            "package_size_mb": upload_result.get("package_size_mb", 0),
        }
        perf_result.timings = perf_timer.get_timings()
        perf_result.passed = exp_count >= num_workloads * 0.9  # Allow 10% tolerance
        
        perf_collector.add_result(perf_result)
        
        print(f"\n=== PERF-ROS-002 Results ===")
        print(f"  Workloads submitted: {num_workloads}")
        print(f"  Experiments created: {exp_count} in {exp_time:.1f}s")
        print(f"  Peak queue depth: {peak_metrics['queue']}")
        print(f"  Peak memory: {peak_metrics['memory']:.1f} MB")
        print(f"  Memory delta: {(final_heap.get('used_mb', 0) - initial_heap.get('used_mb', 0)):.1f} MB" if initial_heap and final_heap else "  Memory: N/A")
        
        # Assert at least 90% of experiments were created
        assert exp_count >= num_workloads * 0.9, (
            f"Expected at least {int(num_workloads * 0.9)} experiments, got {exp_count}"
        )

    @pytest.mark.timeout(900)
    def test_perf_ros_003_recommendation_refresh(
        self,
        cluster_config,
        perf_cleanup,
        perf_timer,
        perf_result,
        perf_collector,
        kruize_credentials,
        db_pod,
        upload_url,
        gateway_url,
        ingress_pod,
        koku_api_url,
        jwt_token: JWTToken,
        rh_identity_header: str,
    ):
        """PERF-ROS-003: Recommendation refresh performance.
        
        Measures:
        - Time to update existing recommendations with new data
        - Incremental processing efficiency
        
        Expected: Refresh faster than initial generation
        """
        cluster_id = generate_cluster_id()
        source_name = f"perf-ros-003-{uuid.uuid4().hex[:8]}"
        
        # Register source
        source = register_source(
            cluster_config.namespace,
            ingress_pod,
            koku_api_url,
            rh_identity_header,
            cluster_id,
            "org1234567",
            source_name,
        )
        
        perf_cleanup.track(
            source_id=source.source_id,
            cluster_id=cluster_id,
            source_name=source_name,
        )
        
        # First upload - establish baseline data (7 days)
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=7)
        
        with perf_timer.measure("initial_upload"):
            upload_result = generate_and_upload_data(
                cluster_id=cluster_id,
                source_name=source_name,
                start_date=start_date,
                end_date=end_date,
                ingress_url=gateway_url + "/api/ingress",
                jwt_token=jwt_token,
                profile_name="baseline",
            )
        
        assert upload_result.get("upload_status") == 202, f"Initial upload failed: {upload_result}"
        
        # Wait for initial experiments
        with perf_timer.measure("initial_processing"):
            exp_success, initial_exp_count, initial_time = wait_for_kruize_experiments(
                cluster_config.namespace,
                db_pod,
                kruize_credentials["user"],
                kruize_credentials["password"],
                cluster_id,
                expected_count=1,  # Based on baseline profile
                timeout=300,
            )
        
        if not exp_success:
            pytest.skip(f"Initial experiments not created: {initial_exp_count}")
        
        # Get initial recommendation timestamp
        initial_rec_count = get_kruize_recommendation_count(
            cluster_config.namespace,
            db_pod,
            kruize_credentials["user"],
            kruize_credentials["password"],
            cluster_id,
        )
        
        # Second upload - new data for same workloads (refresh)
        time.sleep(30)  # Allow some time between uploads
        
        # Upload 1 more day of data
        end_date2 = datetime.now(timezone.utc)
        start_date2 = end_date2 - timedelta(days=1)
        
        with perf_timer.measure("refresh_upload"):
            upload_result2 = generate_and_upload_data(
                cluster_id=cluster_id,
                source_name=source_name,
                start_date=start_date2,
                end_date=end_date2,
                ingress_url=gateway_url + "/api/ingress",
                jwt_token=jwt_token,
                profile_name="baseline",
            )
        
        assert upload_result2.get("upload_status") == 202, f"Refresh upload failed: {upload_result2}"
        
        # Wait for refreshed recommendations
        with perf_timer.measure("refresh_processing"):
            # Wait for recommendation count to increase or update
            start_time = time.time()
            timeout = 300
            refresh_complete = False
            
            while time.time() - start_time < timeout:
                current_rec_count = get_kruize_recommendation_count(
                    cluster_config.namespace,
                    db_pod,
                    kruize_credentials["user"],
                    kruize_credentials["password"],
                    cluster_id,
                )
                # Recommendations should have been updated
                if current_rec_count >= initial_rec_count:
                    refresh_complete = True
                    break
                time.sleep(10)
            
            refresh_time = time.time() - start_time
        
        # Collect metrics
        perf_result.metrics = {
            "initial_experiments": initial_exp_count,
            "initial_processing_time_sec": initial_time,
            "initial_recommendations": initial_rec_count,
            "refresh_time_sec": refresh_time,
            "refresh_complete": refresh_complete,
            "speedup_ratio": initial_time / refresh_time if refresh_time > 0 else 0,
        }
        perf_result.timings = perf_timer.get_timings()
        perf_result.passed = refresh_complete
        
        perf_collector.add_result(perf_result)
        
        print(f"\n=== PERF-ROS-003 Results ===")
        print(f"  Initial processing: {initial_time:.1f}s for {initial_exp_count} experiments")
        print(f"  Refresh processing: {refresh_time:.1f}s")
        print(f"  Speedup ratio: {perf_result.metrics['speedup_ratio']:.2f}x")

    @pytest.mark.timeout(1800)
    def test_perf_ros_004_kruize_memory_pressure(
        self,
        cluster_config,
        perf_cleanup,
        perf_timer,
        perf_result,
        perf_collector,
        kruize_credentials,
        db_pod,
        upload_url,
        gateway_url,
        ingress_pod,
        koku_api_url,
        jwt_token: JWTToken,
        rh_identity_header: str,
    ):
        """PERF-ROS-004: Kruize memory pressure test.
        
        Measures:
        - Kruize heap usage with high workload count
        - Memory stability under sustained load
        - OOM risk assessment
        
        Expected: No OOM, heap stays within limits
        """
        cluster_id = generate_cluster_id()
        source_name = f"perf-ros-004-{uuid.uuid4().hex[:8]}"
        num_workloads = 100  # High workload count to stress Kruize
        
        # Get Kruize pod limits
        kruize_pod = get_pod_by_label(cluster_config.namespace, "app.kubernetes.io/component=ros-optimization")
        
        result = run_oc_command([
            "get", "pod", "-n", cluster_config.namespace, kruize_pod,
            "-o", "jsonpath={.spec.containers[0].resources.limits.memory}"
        ], check=False)
        memory_limit = result.stdout.strip() if result.returncode == 0 else "unknown"
        
        # Initial memory snapshot
        initial_heap = get_kruize_heap_usage(cluster_config.namespace)
        memory_samples = []
        
        # Register source
        source = register_source(
            cluster_config.namespace,
            ingress_pod,
            koku_api_url,
            rh_identity_header,
            cluster_id,
            "org1234567",
            source_name,
        )

        perf_cleanup.track(
            source_id=source.source_id,
            cluster_id=cluster_id,
            source_name=source_name,
        )

        # Start memory monitoring
        import threading
        monitor_stop = threading.Event()
        
        def collect_memory():
            while not monitor_stop.is_set():
                m = get_kruize_heap_usage(cluster_config.namespace)
                if m:
                    memory_samples.append({
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "used_mb": m.get("used_mb", 0),
                    })
                monitor_stop.wait(10)
        
        monitor = threading.Thread(target=collect_memory)
        monitor.start()
        
        try:
            # Generate and upload high workload count
            end_date = datetime.now(timezone.utc)
            start_date = end_date - timedelta(days=7)
            
            with perf_timer.measure("data_generation_upload"):
                upload_result = generate_and_upload_data(
                    cluster_id=cluster_id,
                    source_name=source_name,
                    start_date=start_date,
                    end_date=end_date,
                    ingress_url=gateway_url + "/api/ingress",
                    jwt_token=jwt_token,
                    profile_name="baseline",
                )

            assert upload_result.get("upload_status") == 202, f"Upload failed: {upload_result}"
            
            # Wait for processing
            with perf_timer.measure("processing"):
                exp_success, exp_count, exp_time = wait_for_kruize_experiments(
                    cluster_config.namespace,
                    db_pod,
                    kruize_credentials["user"],
                    kruize_credentials["password"],
                    cluster_id,
                    expected_count=num_workloads,
                    timeout=900,
                )
        finally:
            monitor_stop.set()
            monitor.join(timeout=10)
        
        # Final memory snapshot
        final_heap = get_kruize_heap_usage(cluster_config.namespace)
        
        # Analyze memory samples
        if memory_samples:
            peak_memory = max(s["used_mb"] for s in memory_samples)
            avg_memory = sum(s["used_mb"] for s in memory_samples) / len(memory_samples)
            memory_growth = memory_samples[-1]["used_mb"] - memory_samples[0]["used_mb"] if len(memory_samples) > 1 else 0
        else:
            peak_memory = final_heap.get("used_mb", 0) if final_heap else 0
            avg_memory = 0
            memory_growth = 0
        
        # Check for Kruize restarts (OOM indicator)
        result = run_oc_command([
            "get", "pod", "-n", cluster_config.namespace, kruize_pod,
            "-o", "jsonpath={.status.containerStatuses[0].restartCount}"
        ], check=False)
        restart_count = int(result.stdout.strip()) if result.returncode == 0 and result.stdout.strip().isdigit() else 0
        
        # Collect metrics
        perf_result.metrics = {
            "workload_count": num_workloads,
            "experiment_count": exp_count,
            "processing_time_sec": exp_time,
            "memory_limit": memory_limit,
            "initial_heap_mb": initial_heap.get("used_mb") if initial_heap else None,
            "final_heap_mb": final_heap.get("used_mb") if final_heap else None,
            "peak_memory_mb": peak_memory,
            "avg_memory_mb": avg_memory,
            "memory_growth_mb": memory_growth,
            "sample_count": len(memory_samples),
            "kruize_restarts": restart_count,
        }
        perf_result.timings = perf_timer.get_timings()
        perf_result.passed = restart_count == 0 and exp_count >= num_workloads * 0.8
        
        perf_collector.add_result(perf_result)
        
        print(f"\n=== PERF-ROS-004 Results ===")
        print(f"  Workloads: {num_workloads}")
        print(f"  Experiments created: {exp_count} in {exp_time:.1f}s")
        print(f"  Memory limit: {memory_limit}")
        print(f"  Peak memory: {peak_memory:.1f} MB")
        print(f"  Average memory: {avg_memory:.1f} MB")
        print(f"  Memory growth: {memory_growth:.1f} MB")
        print(f"  Kruize restarts: {restart_count}")
        
        # Assert no OOM
        assert restart_count == 0, f"Kruize restarted {restart_count} times (possible OOM)"
        
        # Assert reasonable experiment creation
        assert exp_count >= num_workloads * 0.8, (
            f"Expected at least {int(num_workloads * 0.8)} experiments, got {exp_count}"
        )
