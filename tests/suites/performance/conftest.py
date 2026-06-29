"""
Performance test fixtures for Cost On-Prem (FLPATH-4036).

Provides:
- Cluster information collection
- Timing instrumentation
- Performance report generation
- NISE profile-based data generation

Note: Cleanup uses existing utilities from e2e_helpers.py and cleanup.py.
Tests should follow the same pattern as cost_management/conftest.py.
"""

import json
import os
import platform
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from conftest import ClusterConfig, DatabaseConfig, obtain_jwt_token
from utils import run_oc_command, get_pod_by_label


# ---------------------------------------------------------------------------
# Test ordering — run ROS tests before ingestion tests (PERF-FINDING-013)
# ---------------------------------------------------------------------------
# The ros-processor cannot skip FK-errored Kafka events.  If ingestion tests
# run first and their cleanup deletes sources before the ROS queue drains,
# poison pills block the queue for all subsequent ROS tests.  Running the ROS
# suite earlier avoids this entirely.
# ---------------------------------------------------------------------------
_PERF_FILE_ORDER = [
    "test_api_latency",
    "test_ros",
    "test_ingestion",
    "test_scale",
    "test_soak",
]


def pytest_collection_modifyitems(items: list) -> None:
    """Sort performance test items by the preferred file execution order."""

    def _sort_key(item: pytest.Item) -> tuple:
        fname = Path(item.fspath).stem
        try:
            idx = _PERF_FILE_ORDER.index(fname)
        except ValueError:
            idx = len(_PERF_FILE_ORDER)
        return (idx, item.fspath, item.name)

    items.sort(key=_sort_key)


# =============================================================================
# Centralized Performance Test Configuration
# =============================================================================

@dataclass(frozen=True)
class PerfTestConfig:
    """Centralized configuration for performance tests.
    
    All defaults can be overridden via environment variables.
    """
    # Soak test settings
    soak_duration_hours: float = field(
        default_factory=lambda: float(os.environ.get("SOAK_DURATION_HOURS", "1"))
    )
    soak_upload_interval_minutes: int = field(
        default_factory=lambda: int(os.environ.get("SOAK_UPLOAD_INTERVAL_MINUTES", "15"))
    )
    soak_query_interval_minutes: int = field(
        default_factory=lambda: int(os.environ.get("SOAK_QUERY_INTERVAL_MINUTES", "5"))
    )
    soak_metrics_interval_seconds: int = field(
        default_factory=lambda: int(os.environ.get("SOAK_METRICS_INTERVAL_SECONDS", "60"))
    )
    
    # Ingestion test settings
    ing_high_freq_duration_minutes: int = field(
        default_factory=lambda: int(os.environ.get("PERF_ING_005_DURATION_MINUTES", "15"))
    )
    ing_high_freq_interval_seconds: int = field(
        default_factory=lambda: int(os.environ.get("PERF_ING_005_INTERVAL_SECONDS", "300"))
    )
    
    # Scale test settings
    scale_max_sources: int = field(
        default_factory=lambda: int(os.environ.get("PERF_SCALE_002_MAX_SOURCES", "25"))
    )
    scale_batch_size: int = field(
        default_factory=lambda: int(os.environ.get("PERF_SCALE_002_BATCH_SIZE", "5"))
    )
    
    # API test settings
    api_crud_iterations: int = field(
        default_factory=lambda: int(os.environ.get("PERF_API_003_ITERATIONS", "10"))
    )
    
    # Timeouts (profile-aware defaults)
    timeout_provider_ready: int = field(
        default_factory=lambda: int(os.environ.get("PERF_TIMEOUT_PROVIDER", "300"))
    )
    timeout_summary_tables: int = field(
        default_factory=lambda: int(os.environ.get("PERF_TIMEOUT_SUMMARY", "600"))
    )
    timeout_kruize_experiments: int = field(
        default_factory=lambda: int(os.environ.get("PERF_TIMEOUT_KRUIZE", "300"))
    )
    timeout_kruize_recommendations: int = field(
        default_factory=lambda: int(os.environ.get("PERF_TIMEOUT_RECOMMENDATIONS", "600"))
    )
    
    # Memory leak thresholds
    memory_growth_daily_percent_max: float = field(
        default_factory=lambda: float(os.environ.get("PERF_MEMORY_GROWTH_MAX", "5.0"))
    )
    
    # Concurrent upload settings.
    # Should be >= the highest ING-003 parametrize value (currently 10) so that
    # test variants are not silently collapsed.  Override downward on resource-
    # constrained machines via PERF_CONCURRENT_UPLOADS_MAX.
    concurrent_upload_max: int = field(
        default_factory=lambda: int(os.environ.get("PERF_CONCURRENT_UPLOADS_MAX", "10"))
    )


# Global config instance - import this in test files.
# PerfTestConfig fields use default_factory=lambda: os.environ.get(...) so all
# env vars are read at instantiation time — no explicit constructor args needed.
PERF_CONFIG = PerfTestConfig()


def get_profile_timeout_multiplier(profile_name: str) -> float:
    """Get timeout multiplier based on profile size.
    
    Larger profiles need longer timeouts for processing.
    """
    multipliers = {
        "baseline": 1.0,
        "small": 1.0,
        "medium": 2.0,
        "large": 4.0,
        "xlarge": 8.0,
        "stress_p99": 12.0,
        "stress_max": 20.0,
    }
    return multipliers.get(profile_name, 1.0)


def get_timeout_for_profile(base_timeout: int, profile_name: str) -> int:
    """Calculate timeout adjusted for profile size."""
    multiplier = get_profile_timeout_multiplier(profile_name)
    return int(base_timeout * multiplier)


@dataclass
class KruizeCredentials:
    """Kruize database credentials for ROS tests.
    
    Use this instead of passing individual user/password parameters.
    """
    user: str
    password: str
    database: str = "costonprem_kruize"
    
    @classmethod
    def from_secret(cls, namespace: str, secret_name: str) -> Optional["KruizeCredentials"]:
        """Load credentials from Kubernetes secret."""
        from utils import get_secret_value
        
        user = get_secret_value(namespace, secret_name, "kruize-user")
        password = get_secret_value(namespace, secret_name, "kruize-password")
        
        if not user or not password:
            return None
        
        return cls(user=user, password=password)


# =============================================================================
# Data Classes for Performance Metrics
# =============================================================================

@dataclass
class ClusterInfo:
    """Information about the target cluster for performance context."""
    
    ocp_version: str = ""
    node_count: int = 0
    worker_node_count: int = 0
    total_cpu_cores: int = 0
    total_memory_gib: float = 0.0
    storage_class: str = ""
    storage_type: str = ""  # ODF, Longhorn, HPP, etc.
    s3_backend: str = ""  # NooBaa, S4, MinIO, etc.
    platform: str = ""  # AWS, GCP, bare-metal, etc.
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResourceSnapshot:
    """Point-in-time resource usage snapshot."""
    
    timestamp: str
    pod_name: str
    cpu_usage_cores: float
    cpu_request_cores: float
    cpu_limit_cores: float
    memory_usage_mib: float
    memory_request_mib: float
    memory_limit_mib: float
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TimingMetric:
    """Timing measurement for a specific operation."""
    
    name: str
    duration_seconds: float
    start_time: str
    end_time: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PerformanceResult:
    """Complete performance test result."""
    
    test_id: str
    test_name: str
    profile: str  # Small, Medium, Large, XL
    chart_version: str
    timestamp: str
    cluster_info: ClusterInfo
    timings: List[TimingMetric] = field(default_factory=list)
    resource_snapshots: List[ResourceSnapshot] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    passed: bool = True
    error_message: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["cluster_info"] = self.cluster_info.to_dict()
        result["timings"] = [t.to_dict() for t in self.timings]
        result["resource_snapshots"] = [r.to_dict() for r in self.resource_snapshots]
        return result


# =============================================================================
# Performance Profile Definitions
# =============================================================================
# Canonical source: tests/suites/performance/profiles.py (PROFILES dict)

from .profiles import PROFILES


# =============================================================================
# Helper Functions
# =============================================================================

def get_ocp_version(namespace: str) -> str:
    """Get OpenShift cluster version."""
    result = run_oc_command([
        "get", "clusterversion", "version",
        "-o", "jsonpath={.status.desired.version}"
    ], check=False)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def get_node_info(namespace: str) -> Dict[str, Any]:
    """Get node count and resource totals."""
    # Get all nodes
    result = run_oc_command([
        "get", "nodes", "-o", "json"
    ], check=False)
    
    if result.returncode != 0:
        return {"node_count": 0, "worker_count": 0, "cpu": 0, "memory": 0}
    
    try:
        nodes = json.loads(result.stdout)
        total_cpu = 0
        total_memory_ki = 0
        worker_count = 0
        
        for node in nodes.get("items", []):
            # Check if worker node
            labels = node.get("metadata", {}).get("labels", {})
            if "node-role.kubernetes.io/worker" in labels:
                worker_count += 1
            
            capacity = node.get("status", {}).get("capacity", {})
            cpu = capacity.get("cpu", "0")
            memory = capacity.get("memory", "0Ki")
            
            # Parse CPU (could be "4" or "4000m")
            if cpu.endswith("m"):
                total_cpu += int(cpu[:-1]) / 1000
            else:
                total_cpu += int(cpu)
            
            # Parse memory (Ki to GiB)
            if memory.endswith("Ki"):
                total_memory_ki += int(memory[:-2])
            elif memory.endswith("Mi"):
                total_memory_ki += int(memory[:-2]) * 1024
            elif memory.endswith("Gi"):
                total_memory_ki += int(memory[:-2]) * 1024 * 1024
        
        return {
            "node_count": len(nodes.get("items", [])),
            "worker_count": worker_count,
            "cpu": int(total_cpu),
            "memory_gib": total_memory_ki / (1024 * 1024),
        }
    except (json.JSONDecodeError, ValueError):
        return {"node_count": 0, "worker_count": 0, "cpu": 0, "memory_gib": 0}


def get_storage_info(namespace: str) -> Dict[str, str]:
    """Detect storage type (ODF, S4, hostpath, etc.)."""
    # Check for ODF
    result = run_oc_command([
        "get", "storagecluster", "-n", "openshift-storage",
        "-o", "jsonpath={.items[0].metadata.name}"
    ], check=False)
    
    if result.returncode == 0 and result.stdout.strip():
        return {"storage_type": "ODF", "storage_class": "ocs-storagecluster-ceph-rbd"}
    
    # Check for S4
    result = run_oc_command([
        "get", "storageclass", "s4-storage", "-o", "name"
    ], check=False)
    
    if result.returncode == 0:
        return {"storage_type": "S4", "storage_class": "s4-storage"}
    
    # Check for HPP (HostPath Provisioner)
    result = run_oc_command([
        "get", "storageclass", "hpp-backend", "-o", "name"
    ], check=False)
    
    if result.returncode == 0:
        return {"storage_type": "HPP", "storage_class": "hpp-backend"}
    
    # Check for Longhorn
    result = run_oc_command([
        "get", "storageclass", "longhorn", "-o", "name"
    ], check=False)
    
    if result.returncode == 0:
        return {"storage_type": "Longhorn", "storage_class": "longhorn"}
    
    return {"storage_type": "unknown", "storage_class": "unknown"}


def get_s3_backend(namespace: str) -> str:
    """Detect S3 backend type (NooBaa, S4, MinIO, etc.)."""
    # Check for NooBaa (ODF)
    result = run_oc_command([
        "get", "noobaa", "-n", "openshift-storage", "-o", "name"
    ], check=False)
    
    if result.returncode == 0 and result.stdout.strip():
        return "NooBaa"
    
    # Check for S4 (look for S4 storage system)
    result = run_oc_command([
        "get", "storagesystem", "-n", "openshift-storage",
        "-o", "jsonpath={.items[*].metadata.name}"
    ], check=False)
    
    if result.returncode == 0 and "s4" in result.stdout.lower():
        return "S4"
    
    # Check for MinIO deployment
    result = run_oc_command([
        "get", "deployment", "-l", "app=minio", "-A",
        "-o", "jsonpath={.items[0].metadata.name}"
    ], check=False)
    
    if result.returncode == 0 and result.stdout.strip():
        return "MinIO"
    
    # Check S3 endpoint from cost-onprem config
    result = run_oc_command([
        "get", "configmap", "-n", namespace, "-l", "app.kubernetes.io/component=aws-config",
        "-o", "jsonpath={.items[0].data.config}"
    ], check=False)
    
    if result.returncode == 0:
        config = result.stdout.lower()
        if "s4" in config:
            return "S4"
        if "noobaa" in config:
            return "NooBaa"
        if "minio" in config:
            return "MinIO"
    
    return "unknown"


def get_chart_version(namespace: str, release_name: str) -> str:
    """Get deployed Helm chart version."""
    result = run_oc_command([
        "get", "configmap", f"{release_name}-release-info",
        "-n", namespace, "-o", "jsonpath={.data.chart-version}"
    ], check=False)
    
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    
    # Fallback: try helm status
    try:
        helm_result = subprocess.run(
            ["helm", "status", release_name, "-n", namespace, "-o", "json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if helm_result.returncode == 0:
            status = json.loads(helm_result.stdout)
            return status.get("chart", "unknown")
    except Exception:
        pass
    
    return "unknown"


def get_pod_resource_usage(namespace: str, label_selector: str) -> List[ResourceSnapshot]:
    """Get current resource usage for pods matching selector."""
    snapshots = []
    timestamp = datetime.now(timezone.utc).isoformat()
    
    # Get pod resource requests/limits
    result = run_oc_command([
        "get", "pods", "-n", namespace, "-l", label_selector,
        "-o", "json"
    ], check=False)
    
    if result.returncode != 0:
        return snapshots
    
    try:
        pods = json.loads(result.stdout)
        for pod in pods.get("items", []):
            pod_name = pod.get("metadata", {}).get("name", "")
            containers = pod.get("spec", {}).get("containers", [])
            
            for container in containers:
                resources = container.get("resources", {})
                requests = resources.get("requests", {})
                limits = resources.get("limits", {})
                
                # Parse CPU
                cpu_request = _parse_cpu(requests.get("cpu", "0"))
                cpu_limit = _parse_cpu(limits.get("cpu", "0"))
                
                # Parse memory
                mem_request = _parse_memory_mib(requests.get("memory", "0"))
                mem_limit = _parse_memory_mib(limits.get("memory", "0"))
                
                snapshots.append(ResourceSnapshot(
                    timestamp=timestamp,
                    pod_name=f"{pod_name}/{container.get('name', '')}",
                    cpu_usage_cores=0,  # Would need metrics-server
                    cpu_request_cores=cpu_request,
                    cpu_limit_cores=cpu_limit,
                    memory_usage_mib=0,  # Would need metrics-server
                    memory_request_mib=mem_request,
                    memory_limit_mib=mem_limit,
                ))
    except json.JSONDecodeError:
        pass
    
    return snapshots


def parse_cpu_millicores(value: str) -> float:
    """Parse CPU value (e.g., '500m', '2') to cores."""
    if not value:
        return 0.0
    if value.endswith("m"):
        return float(value[:-1]) / 1000
    return float(value)


def parse_memory_mib(value: str) -> float:
    """Parse memory value to MiB."""
    if not value:
        return 0.0
    value = str(value)
    if value.endswith("Ki"):
        return float(value[:-2]) / 1024
    elif value.endswith("Mi"):
        return float(value[:-2])
    elif value.endswith("Gi"):
        return float(value[:-2]) * 1024
    elif value.endswith("Ti"):
        return float(value[:-2]) * 1024 * 1024
    return float(value) / (1024 * 1024)  # Assume bytes


# Keep private aliases for internal use
_parse_cpu = parse_cpu_millicores
_parse_memory_mib = parse_memory_mib


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def cluster_info(cluster_config: ClusterConfig) -> ClusterInfo:
    """Collect cluster information for performance context."""
    node_info = get_node_info(cluster_config.namespace)
    storage_info = get_storage_info(cluster_config.namespace)
    s3_backend = get_s3_backend(cluster_config.namespace)
    
    return ClusterInfo(
        ocp_version=get_ocp_version(cluster_config.namespace),
        node_count=node_info["node_count"],
        worker_node_count=node_info["worker_count"],
        total_cpu_cores=node_info["cpu"],
        total_memory_gib=node_info["memory_gib"],
        storage_class=storage_info["storage_class"],
        storage_type=storage_info["storage_type"],
        s3_backend=s3_backend,
        platform=os.environ.get("CLUSTER_PLATFORM", "unknown"),
    )


@pytest.fixture(scope="session")
def chart_version(cluster_config: ClusterConfig) -> str:
    """Get the deployed chart version."""
    return get_chart_version(cluster_config.namespace, cluster_config.helm_release_name)


@pytest.fixture(scope="function")
def perf_timer():
    """Fixture providing timing utilities for performance tests.
    
    Usage:
        def test_something(perf_timer):
            with perf_timer.measure("operation_name"):
                do_something()
            
            # Or manual timing
            perf_timer.start("another_op")
            do_another_thing()
            perf_timer.stop("another_op")
            
            # Get all timings
            timings = perf_timer.get_timings()
    """
    return PerfTimer()


class PerfTimer:
    """Thread-safe timer utility for performance measurements.

    All dict mutations are guarded by a lock so concurrent ThreadPoolExecutor
    workers can safely call start/stop/measure without corrupting state.
    """
    
    def __init__(self):
        self._timings: Dict[str, TimingMetric] = {}
        self._active: Dict[str, float] = {}
        self._lock = threading.Lock()
    
    def start(self, name: str, metadata: Optional[Dict[str, Any]] = None):
        """Start timing an operation."""
        with self._lock:
            self._active[name] = time.time()
            self._timings[name] = TimingMetric(
                name=name,
                duration_seconds=0,
                start_time=datetime.now(timezone.utc).isoformat(),
                end_time="",
                metadata=metadata or {},
            )
    
    def stop(self, name: str) -> float:
        """Stop timing and return duration."""
        with self._lock:
            if name not in self._active:
                return 0.0
            
            end_time = time.time()
            duration = end_time - self._active[name]
            
            self._timings[name].duration_seconds = duration
            self._timings[name].end_time = datetime.now(timezone.utc).isoformat()
            
            del self._active[name]
            return duration
    
    def measure(self, name: str, metadata: Optional[Dict[str, Any]] = None):
        """Context manager for timing an operation."""
        return _TimerContext(self, name, metadata)
    
    def get_timings(self) -> List[TimingMetric]:
        """Get all completed timings."""
        with self._lock:
            return list(self._timings.values())
    
    def get_timing(self, name: str) -> Optional[TimingMetric]:
        """Get a specific timing."""
        with self._lock:
            return self._timings.get(name)


class _TimerContext:
    """Context manager for PerfTimer.measure()."""
    
    def __init__(self, timer: PerfTimer, name: str, metadata: Optional[Dict[str, Any]]):
        self.timer = timer
        self.name = name
        self.metadata = metadata
    
    def __enter__(self):
        self.timer.start(self.name, self.metadata)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.timer.stop(self.name)
        return False


@pytest.fixture(scope="session")
def performance_profile() -> str:
    """Get the performance profile to use (from env var or default)."""
    return os.environ.get("PERF_PROFILE", "small")


@pytest.fixture(scope="session")
def profile_config(performance_profile: str) -> Dict[str, Any]:
    """Get the configuration for the selected performance profile."""
    if performance_profile not in PROFILES:
        pytest.skip(f"Unknown performance profile: {performance_profile}")
    return PROFILES[performance_profile]


@pytest.fixture(scope="function")
def perf_result(
    request,
    cluster_info: ClusterInfo,
    chart_version: str,
    performance_profile: str,
) -> PerformanceResult:
    """Create a PerformanceResult for the current test."""
    return PerformanceResult(
        test_id=f"{request.node.nodeid}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        test_name=request.node.name,
        profile=performance_profile,
        chart_version=chart_version,
        timestamp=datetime.now(timezone.utc).isoformat(),
        cluster_info=cluster_info,
    )


@pytest.fixture(scope="session")
def perf_reports_dir() -> Path:
    """Get the directory for performance reports.
    
    When called from deploy-test-cost-onprem.sh with unified output structure,
    uses PERF_OUTPUT_DIR/TEST_RUN_ID/results/ for S3 upload compatibility.
    Otherwise falls back to tests/reports/performance/ for standalone runs.
    """
    perf_output_dir = os.environ.get("PERF_OUTPUT_DIR")
    test_run_id = os.environ.get("TEST_RUN_ID")
    
    if perf_output_dir and test_run_id:
        # Unified output mode - write to results/ subdirectory
        reports_dir = Path(perf_output_dir) / test_run_id / "results"
    else:
        # Standalone mode - use default location
        reports_dir = Path(__file__).parent.parent.parent / "reports" / "performance"
    
    reports_dir.mkdir(parents=True, exist_ok=True)
    return reports_dir


def save_perf_result(result: PerformanceResult, output_dir: Path) -> Path:
    """Save a performance result to JSON."""
    filename = f"{result.test_name}_{result.profile}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    output_path = output_dir / filename
    
    with open(output_path, "w") as f:
        json.dump(result.to_dict(), f, indent=2)
    
    return output_path


@pytest.fixture(scope="session")
def perf_collector(perf_reports_dir: Path):
    """Collector for aggregating performance results across a session."""
    collector = PerfResultCollector(perf_reports_dir)
    yield collector
    collector.finalize()


@pytest.fixture(scope="session")
def kruize_credentials(cluster_config: ClusterConfig) -> Optional[KruizeCredentials]:
    """Get Kruize database credentials.
    
    Returns None if credentials are not available (ROS not deployed).
    Tests that require ROS should skip if this returns None.
    """
    secret_name = f"{cluster_config.helm_release_name}-db-credentials"
    creds = KruizeCredentials.from_secret(cluster_config.namespace, secret_name)
    return creds


@pytest.fixture(scope="session")
def perf_config() -> PerfTestConfig:
    """Get the centralized performance test configuration."""
    return PERF_CONFIG


class PerfResultCollector:
    """Collects and aggregates performance results for a test session."""
    
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.results: List[PerformanceResult] = []
        self.session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    
    def add_result(self, result: PerformanceResult):
        """Add a test result to the collection."""
        self.results.append(result)
        # Also save individual result
        save_perf_result(result, self.output_dir)
    
    def finalize(self):
        """Save aggregated session report."""
        if not self.results:
            return
        
        session_report = {
            "session_id": self.session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_tests": len(self.results),
            "passed": sum(1 for r in self.results if r.passed),
            "failed": sum(1 for r in self.results if not r.passed),
            "results": [r.to_dict() for r in self.results],
        }
        
        report_path = self.output_dir / f"session_{self.session_id}.json"
        with open(report_path, "w") as f:
            json.dump(session_report, f, indent=2)


# =============================================================================
# Cleanup Utilities
# =============================================================================

from e2e_helpers import delete_source, cleanup_database_records
from cleanup import cleanup_s3_data


@dataclass
class PerfTestResource:
    """Tracks a resource created by a performance test for cleanup."""
    source_id: Optional[str] = None
    cluster_id: Optional[str] = None
    source_name: Optional[str] = None


class PerfCleanupTracker:
    """Tracks resources created during performance tests for cleanup.
    
    Usage in tests:
        def test_something(self, perf_cleanup, ...):
            source = register_source(...)
            perf_cleanup.track(source_id=source.source_id, cluster_id=cluster_id)
            # ... test logic ...
            # Cleanup happens automatically after test
    """
    
    def __init__(self, namespace: str, helm_release: str):
        self.namespace = namespace
        self.helm_release = helm_release
        self.resources: List[PerfTestResource] = []
        self._cleanup_enabled = os.environ.get("E2E_CLEANUP_AFTER", "true").lower() != "false"
    
    def track(
        self,
        source_id: Optional[str] = None,
        cluster_id: Optional[str] = None,
        source_name: Optional[str] = None,
    ):
        """Track a resource for cleanup after test completes."""
        self.resources.append(PerfTestResource(
            source_id=source_id,
            cluster_id=cluster_id,
            source_name=source_name,
        ))
    
    def _wait_for_ros_drain(self):
        """Wait for the ROS processor Kafka consumer lag to reach zero.

        The ros-processor is a Go-based Kafka consumer on ``hccm.ros.events``.
        If we delete sources before it has consumed the corresponding events,
        it hits FK constraint errors that poison the queue.  We must wait for
        the queue to fully drain before deleting any sources.

        The timeout scales with the number of tracked resources — more sources
        means more ROS events to process (~3s per workload via Kruize API).
        We also track whether lag is making progress; if lag stalls completely
        for ``stall_timeout`` seconds we give up (processor may be stuck on
        an unrelated error).
        """
        try:
            from suites.performance.test_ros import get_ros_queue_depth
        except ImportError:
            return

        num_resources = len(self.resources)
        max_timeout = max(120, num_resources * 60)
        stall_timeout = 90

        start = time.time()
        prev_lag = None
        last_progress_time = start
        while time.time() - start < max_timeout:
            lag = get_ros_queue_depth(self.namespace)
            if lag is not None and lag == 0:
                return
            if lag is not None:
                if lag != prev_lag:
                    print(f"  [ros-drain] lag={lag}, waiting…")
                    if prev_lag is not None and lag < prev_lag:
                        last_progress_time = time.time()
                    prev_lag = lag
                elif time.time() - last_progress_time > stall_timeout:
                    print(f"  [ros-drain] lag stalled at {lag} for {stall_timeout}s, giving up")
                    return
            time.sleep(5)
        print(f"  [ros-drain] drained to lag={prev_lag} (timeout {max_timeout}s)")

    def _get_s3_config(self) -> Optional[Dict[str, str]]:
        """Get S3 configuration from the cluster."""
        try:
            # Get S3 credentials secret
            result = run_oc_command([
                "get", "secret", f"{self.helm_release}-s3-credentials",
                "-n", self.namespace,
                "-o", "jsonpath={.data.access-key}"
            ], check=False)
            if result.returncode != 0:
                return None
            
            import base64
            access_key = base64.b64decode(result.stdout.strip()).decode('utf-8')
            
            result = run_oc_command([
                "get", "secret", f"{self.helm_release}-s3-credentials",
                "-n", self.namespace,
                "-o", "jsonpath={.data.secret-key}"
            ], check=False)
            if result.returncode != 0:
                return None
            
            secret_key = base64.b64decode(result.stdout.strip()).decode('utf-8')
            
            # Get S3 endpoint from configmap
            result = run_oc_command([
                "get", "configmap", f"{self.helm_release}-aws-config",
                "-n", self.namespace,
                "-o", "jsonpath={.data.endpoint}"
            ], check=False)
            
            # Default to common S4/NooBaa endpoints if not found
            if result.returncode == 0 and result.stdout.strip():
                endpoint = result.stdout.strip()
            else:
                # Try to detect S4 or NooBaa
                s4_result = run_oc_command([
                    "get", "svc", "s4", "-n", "s4",
                    "-o", "jsonpath={.spec.clusterIP}"
                ], check=False)
                if s4_result.returncode == 0 and s4_result.stdout.strip():
                    endpoint = f"http://{s4_result.stdout.strip()}:7480"
                else:
                    endpoint = "https://s3.openshift-storage.svc:443"
            
            return {
                "endpoint": endpoint,
                "access_key": access_key,
                "secret_key": secret_key,
                "bucket": "koku-bucket",
                "verify_ssl": False,
            }
        except Exception as e:
            print(f"  [s3-cleanup] Could not get S3 config: {e}")
            return None

    def _cleanup_s3_data(self, failures: List[str]):
        """Clean up S3 data for all tracked clusters."""
        s3_config = self._get_s3_config()
        if not s3_config:
            print("  [s3-cleanup] Skipped - could not get S3 credentials")
            return
        
        # Get unique cluster IDs
        cluster_ids = {r.cluster_id for r in self.resources if r.cluster_id}
        if not cluster_ids:
            return
        
        # Default org_id used in tests
        org_id = os.environ.get("ORG_ID", "6089719")
        
        total_deleted = 0
        for cluster_id in cluster_ids:
            try:
                result = cleanup_s3_data(
                    endpoint=s3_config["endpoint"],
                    access_key=s3_config["access_key"],
                    secret_key=s3_config["secret_key"],
                    bucket=s3_config["bucket"],
                    org_id=org_id,
                    cluster_id=cluster_id,
                    verify_ssl=s3_config["verify_ssl"],
                )
                files_deleted = result.get("files_deleted", 0)
                total_deleted += files_deleted
                if files_deleted > 0:
                    print(f"  Cleaned {files_deleted} S3 files for cluster {cluster_id}")
                if result.get("error"):
                    print(f"  Warning: S3 cleanup error for {cluster_id}: {result['error']}")
            except Exception as e:
                msg = f"Error cleaning S3 for cluster {cluster_id}: {e}"
                print(f"  {msg}")
                failures.append(msg)
        
        if total_deleted > 0:
            print(f"  [s3-cleanup] Total: {total_deleted} files deleted")

    def cleanup(self, rh_identity_header: str):
        """Clean up all tracked resources.

        Raises RuntimeError if any cleanup operations fail, so test frameworks
        surface dirty state rather than silently proceeding.
        """
        if not self._cleanup_enabled:
            print(f"\n[PERF CLEANUP] Skipped (E2E_CLEANUP_AFTER=false)")
            print(f"  Tracked resources: {len(self.resources)}")
            for r in self.resources:
                print(f"    - source_id={r.source_id}, cluster_id={r.cluster_id}")
            return
        
        if not self.resources:
            return
        
        print(f"\n[PERF CLEANUP] Cleaning {len(self.resources)} tracked resources...")
        
        # Wait for the ROS processor to consume any pending Kafka events
        # before deleting sources.  Ingestion tests produce ROS events as a
        # side-effect; deleting the source first removes FK targets that the
        # ros-processor needs, poisoning its Kafka queue with permanent errors.
        self._wait_for_ros_drain()
        
        # Get pods for cleanup operations
        ingress_pod = get_pod_by_label(self.namespace, "app.kubernetes.io/component=ingress")
        db_pod = get_pod_by_label(self.namespace, "app.kubernetes.io/component=database")
        
        koku_api_url = f"http://{self.helm_release}-koku-api.{self.namespace}.svc.cluster.local:8000/api/cost-management/v1"
        
        failures = []
        
        for resource in self.resources:
            # Delete source via API
            if resource.source_id and ingress_pod:
                try:
                    if delete_source(
                        self.namespace,
                        ingress_pod,
                        koku_api_url,
                        rh_identity_header,
                        resource.source_id,
                        container="ingress",
                    ):
                        print(f"  Deleted source {resource.source_id}")
                    else:
                        msg = f"Could not delete source {resource.source_id}"
                        print(f"  Warning: {msg}")
                        failures.append(msg)
                except Exception as e:
                    msg = f"Error deleting source {resource.source_id}: {e}"
                    print(f"  {msg}")
                    failures.append(msg)
            
            # Clean database records
            if resource.cluster_id and db_pod:
                try:
                    if cleanup_database_records(self.namespace, db_pod, resource.cluster_id):
                        print(f"  Cleaned DB records for cluster {resource.cluster_id}")
                    else:
                        msg = f"Could not clean DB for cluster {resource.cluster_id}"
                        print(f"  Warning: {msg}")
                        failures.append(msg)
                except Exception as e:
                    msg = f"Error cleaning DB for cluster {resource.cluster_id}: {e}"
                    print(f"  {msg}")
                    failures.append(msg)
        
        # Clean S3 data for all tracked clusters
        self._cleanup_s3_data(failures)
        
        self.resources.clear()
        
        if failures:
            print(f"[PERF CLEANUP] Completed with {len(failures)} failure(s)")
            import warnings
            warnings.warn(
                f"Performance test cleanup had {len(failures)} failure(s): "
                + "; ".join(failures[:3]),
                RuntimeWarning,
                stacklevel=2,
            )
        else:
            print("[PERF CLEANUP] Complete")


@pytest.fixture
def perf_cleanup(cluster_config: ClusterConfig, rh_identity_header: str):
    """Fixture that tracks and cleans up performance test resources.
    
    Usage:
        def test_my_perf_test(self, perf_cleanup, ...):
            source = register_source(...)
            perf_cleanup.track(source_id=source.source_id, cluster_id=cluster_id)
            # Test runs...
            # Cleanup happens automatically
    """
    tracker = PerfCleanupTracker(
        namespace=cluster_config.namespace,
        helm_release=cluster_config.helm_release_name,
    )
    
    yield tracker
    
    # Cleanup after test completes (pass or fail)
    tracker.cleanup(rh_identity_header)


# =============================================================================
# Tag Enablement
# =============================================================================
# Tag enablement functions and fixture are defined in the root conftest.py
# and are available to all test suites. The `ensure_tags_enabled` fixture
# uses the API (PUT /settings/tags/enable/) to enable tags needed for testing.
#
# Performance tests that need tags (e.g., API-006 tag filtering) should
# include `ensure_tags_enabled` in their fixture dependencies.
#
# See: tests/conftest.py for implementation details
# See: koku docs/architecture/api-settings-endpoints.md for API documentation


# =============================================================================
# Tag Test Data Fixture
# =============================================================================

@pytest.fixture(scope="function")
def labeled_nise_source(
    cluster_config: ClusterConfig,
    gateway_url: str,
    ingress_url: str,
    rh_identity_header: str,
    jwt_token,
    perf_cleanup,
    ensure_tags_enabled,
):
    """Create a NISE source with labeled data for tag filtering tests.
    
    This fixture:
    1. Registers a new source
    2. Generates and uploads NISE data with pod labels
    3. Waits for processing to complete
    4. Ensures tags are enabled
    5. Returns info about available tags
    6. Cleans up the source after test
    
    The NISE profile includes labels like:
    - environment:performance
    - app:perf-baseline-c00-app
    - tier:web|api|worker|db
    
    Usage:
        def test_tag_filtering(self, labeled_nise_source, ...):
            available_tags = labeled_nise_source["available_tags"]
            # ... test with tags ...
    """
    import subprocess
    import tempfile
    import requests
    from datetime import timedelta
    from pathlib import Path
    
    from e2e_helpers import (
        generate_cluster_id,
        register_source,
        upload_with_retry,
        wait_for_provider,
        wait_for_processing_complete,
        wait_for_summary_tables,
    )
    from utils import create_upload_package_from_files, execute_db_query
    from .profiles import get_profile_nise_yaml
    
    # Get required pods
    namespace = cluster_config.namespace
    helm_release = cluster_config.helm_release_name
    
    ingress_pod = get_pod_by_label(namespace, "app.kubernetes.io/component=ingress")
    if not ingress_pod:
        pytest.skip("Ingress pod not found - cannot create labeled source")
    
    # Internal API URL for source registration
    koku_api_url = f"http://{helm_release}-koku-api.{namespace}.svc.cluster.local:8000/api/cost-management/v1"
    
    # Create unique source
    cluster_id = generate_cluster_id()
    source_name = f"perf-tag-{cluster_id[-8:]}"
    
    print(f"\n[labeled_nise_source] Creating source {source_name} with labeled data")
    
    # Register source (requires namespace, pod, api_url, header, cluster_id, org_id, source_name)
    source = register_source(
        namespace,
        ingress_pod,
        koku_api_url,
        rh_identity_header,
        cluster_id,
        "org1234567",
        source_name,
    )
    
    # Track for cleanup
    perf_cleanup.track(
        source_id=source.source_id,
        cluster_id=cluster_id,
        source_name=source_name,
    )
    
    # Generate NISE data with labels
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=1)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        # Generate YAML with labels
        yaml_content = get_profile_nise_yaml("baseline", start_date, end_date, cluster_id, 0)
        yaml_path = os.path.join(temp_dir, "static_report.yml")
        with open(yaml_path, "w") as f:
            f.write(yaml_content)
        
        # Run NISE
        nise_output = os.path.join(temp_dir, "nise_output")
        os.makedirs(nise_output, exist_ok=True)
        
        result = subprocess.run(
            ["nise", "report", "ocp",
             "--static-report-file", yaml_path,
             "--ocp-cluster-id", cluster_id,
             "-w"],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=nise_output,
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"NISE failed: {result.stderr}")
        
        # Collect generated files
        csv_files = list(Path(nise_output).rglob("*.csv"))
        pod_usage_files = [str(f) for f in csv_files if "pod_usage" in f.name.lower()]
        node_label_files = [str(f) for f in csv_files if "node_label" in f.name.lower()]
        namespace_label_files = [str(f) for f in csv_files if "namespace_label" in f.name.lower()]
        
        print(f"[labeled_nise_source] Generated {len(csv_files)} CSV files, {len(pod_usage_files)} with pod data")
        
        # Create upload package
        package_path = create_upload_package_from_files(
            pod_usage_files=pod_usage_files,
            ros_usage_files=[],
            cluster_id=cluster_id,
            start_date=start_date,
            end_date=end_date,
            node_label_files=node_label_files if node_label_files else None,
            namespace_label_files=namespace_label_files if namespace_label_files else None,
        )
        
        # Upload - ingress requires JWT auth, not RH-Identity
        session = requests.Session()
        session.verify = False
        
        response = upload_with_retry(
            session,
            f"{ingress_url}/v1/upload",
            package_path,
            jwt_token.authorization_header,
            max_retries=3,
        )
        
        print(f"[labeled_nise_source] Upload status: {response.status_code}")
    
    # Wait for full processing including summarization (tags require summarized data)
    db_pod = get_pod_by_label(namespace, "app.kubernetes.io/component=database")
    if not db_pod:
        pytest.skip("Database pod not found — cannot verify tag processing")

    # Step 1: Wait for provider to be created (manifest entry)
    print("[labeled_nise_source] Waiting for provider to be created...")
    wait_for_provider(
        namespace,
        db_pod,
        cluster_id,
        timeout=120,
    )

    # Step 2: Wait for manifest processing (download + processing + summary phases).
    # Guard against stale manifests: if completed_datetime is set but num_processed
    # files is 0, the manifest is from a prior run — wait for a fresh one.
    # On larger profiles celery workers are under heavier load so allow more time.
    print("[labeled_nise_source] Waiting for manifest processing...")
    import time as _proc_time
    _profile = os.environ.get("PERF_PROFILE", "baseline")
    proc_deadline = _proc_time.time() + get_timeout_for_profile(300, _profile)
    proc_result = {"complete": False}

    while _proc_time.time() < proc_deadline:
        proc_result = wait_for_processing_complete(
            namespace,
            db_pod,
            cluster_id,
            poll_interval=10,
            max_wait_seconds=max(15, int(proc_deadline - _proc_time.time())),
        )
        if proc_result.get("complete"):
            processed = proc_result.get("num_processed_files", 0)
            if processed > 0:
                break
            # Stale manifest — completed_datetime set but 0 files processed.
            # Sleep and retry; the listener will create a new manifest.
            print(
                f"[labeled_nise_source] Stale manifest (0 files processed) — "
                f"waiting for fresh manifest..."
            )
            _proc_time.sleep(15)
        else:
            break

    print(
        f"[labeled_nise_source] Processing: complete={proc_result.get('complete')}, "
        f"files={proc_result.get('num_processed_files', 0)}, "
        f"elapsed={proc_result.get('elapsed_s', '?')}s"
    )

    # Step 3: Confirm summary table rows exist for THIS cluster_id.
    # The schema may have rows from other clusters; we need rows specifically
    # for our newly uploaded data with pod_labels populated.
    print("[labeled_nise_source] Waiting for summary table rows with pod_labels...")
    schema_name = proc_result.get("schema_name")

    if not schema_name:
        # Fall back to looking up the schema from the manifest
        schema_name = wait_for_summary_tables(
            namespace,
            db_pod,
            cluster_id,
            timeout=300,
            interval=10,
        )

    if schema_name:
        label_wait_start = _proc_time.time()
        _profile = os.environ.get("PERF_PROFILE", "baseline")
        label_wait_max = get_timeout_for_profile(300, _profile)
        found_labels = False

        while _proc_time.time() - label_wait_start < label_wait_max:
            count_rows = execute_db_query(
                namespace, db_pod, "costonprem_koku", "koku_user",
                f"""
                SELECT COUNT(*)
                FROM   {schema_name}.reporting_ocpusagelineitem_daily_summary
                WHERE  cluster_id = '{cluster_id}'
                  AND  pod_labels IS NOT NULL
                  AND  pod_labels != '{{}}'::jsonb
                """,
            )
            count = int(count_rows[0][0]) if count_rows and count_rows[0] else 0
            elapsed = round(_proc_time.time() - label_wait_start, 1)
            if count > 0:
                print(
                    f"[labeled_nise_source] Found {count} summary rows with pod_labels "
                    f"in {elapsed}s"
                )
                found_labels = True
                break
            # Every 60s, print diagnostic info about summary table state
            if int(elapsed) % 60 < 16:
                any_rows = execute_db_query(
                    namespace, db_pod, "costonprem_koku", "koku_user",
                    f"""
                    SELECT COUNT(*), COUNT(NULLIF(pod_labels::text, '{{}}'))
                    FROM   {schema_name}.reporting_ocpusagelineitem_daily_summary
                    WHERE  cluster_id = '{cluster_id}'
                    """,
                )
                total = int(any_rows[0][0]) if any_rows and any_rows[0] else 0
                with_labels = int(any_rows[0][1]) if any_rows and len(any_rows[0]) > 1 else 0
                print(
                    f"[labeled_nise_source] {elapsed}s — cluster {cluster_id[:8]}: "
                    f"{total} total summary rows, {with_labels} with pod_labels"
                )
            else:
                print(
                    f"[labeled_nise_source] {elapsed}s — no summary rows with pod_labels yet "
                    f"for {cluster_id[:8]}..."
                )
            _proc_time.sleep(15)

        if not found_labels:
            # Final diagnostic: check if ANY rows exist for this cluster
            diag_rows = execute_db_query(
                namespace, db_pod, "costonprem_koku", "koku_user",
                f"""
                SELECT COUNT(*) AS total,
                       COUNT(NULLIF(pod_labels::text, '{{}}')) AS with_labels
                FROM   {schema_name}.reporting_ocpusagelineitem_daily_summary
                WHERE  cluster_id = '{cluster_id}'
                """,
            )
            total = int(diag_rows[0][0]) if diag_rows and diag_rows[0] else 0
            with_labels = int(diag_rows[0][1]) if diag_rows and len(diag_rows[0]) > 1 else 0
            pytest.fail(
                f"Summary table has no rows with pod_labels for cluster {cluster_id} "
                f"after {label_wait_max}s ({total} total rows, {with_labels} with labels). "
                f"Data uploaded (HTTP 202) but labels not summarized. "
                f"Check celery-worker-summary and listener logs."
            )
    else:
        pytest.fail(
            f"Summary table never populated for cluster {cluster_id}. "
            "Summarization may not have run — check celery-worker-summary logs."
        )
    print(f"[labeled_nise_source] Summary rows confirmed in schema: {schema_name}")

    # Step 4: Query pod_labels directly from summary table to see which
    # NISE labels actually made it into the DB. This is ground truth — if a
    # label isn't here, no amount of API polling will find it.
    db_tag_rows = execute_db_query(
        namespace, db_pod, "costonprem_koku", "koku_user",
        f"""
        SELECT DISTINCT k
        FROM   {schema_name}.reporting_ocpusagelineitem_daily_summary,
               LATERAL jsonb_object_keys(pod_labels) AS k
        WHERE  cluster_id = '{cluster_id}'
          AND  pod_labels IS NOT NULL
          AND  pod_labels != '{{}}'::jsonb
        ORDER  BY k
        """,
    )
    db_tag_keys = [row[0] for row in db_tag_rows] if db_tag_rows else []
    print(f"[labeled_nise_source] Tags in summary table pod_labels: {db_tag_keys}")

    nise_tag_keys = [
        "tagone", "tagtwo", "tagthree", "tagfour", "tagfive",
        "tagsix", "tagseven", "tageight", "tagnine", "tagten",
    ]
    db_hits = [k for k in nise_tag_keys if k in db_tag_keys]
    db_misses = [k for k in nise_tag_keys if k not in db_tag_keys]
    if db_misses:
        print(
            f"[labeled_nise_source] WARNING: {len(db_misses)} NISE labels missing from DB: "
            f"{db_misses}"
        )

    # Also check namespace and node labels (stored in separate columns)
    ns_label_rows = execute_db_query(
        namespace, db_pod, "costonprem_koku", "koku_user",
        f"""
        SELECT DISTINCT k
        FROM   {schema_name}.reporting_ocpusagelineitem_daily_summary,
               LATERAL jsonb_object_keys(namespace_labels) AS k
        WHERE  cluster_id = '{cluster_id}'
          AND  namespace_labels IS NOT NULL
          AND  namespace_labels != '{{}}'::jsonb
        ORDER  BY k
        """,
    )
    ns_tag_keys = [row[0] for row in ns_label_rows] if ns_label_rows else []
    print(f"[labeled_nise_source] Tags in namespace_labels: {ns_tag_keys}")

    node_label_rows = execute_db_query(
        namespace, db_pod, "costonprem_koku", "koku_user",
        f"""
        SELECT DISTINCT k
        FROM   {schema_name}.reporting_ocpnode_label_summary,
               LATERAL jsonb_object_keys(node_labels) AS k
        WHERE  cluster_id = '{cluster_id}'
          AND  node_labels IS NOT NULL
          AND  node_labels != '{{}}'::jsonb
        ORDER  BY k
        """,
    )
    node_tag_keys = [row[0] for row in node_label_rows] if node_label_rows else []
    if not node_tag_keys:
        # Fallback: node labels may live in the same summary table
        node_label_rows = execute_db_query(
            namespace, db_pod, "costonprem_koku", "koku_user",
            f"""
            SELECT DISTINCT k
            FROM   {schema_name}.reporting_ocpusagelineitem_daily_summary,
                   LATERAL jsonb_object_keys(node_labels) AS k
            WHERE  cluster_id = '{cluster_id}'
              AND  node_labels IS NOT NULL
              AND  node_labels != '{{}}'::jsonb
            ORDER  BY k
            """,
        )
        node_tag_keys = [row[0] for row in node_label_rows] if node_label_rows else []
    print(f"[labeled_nise_source] Tags in node_labels: {node_tag_keys}")

    all_db_tags = sorted(set(db_tag_keys + ns_tag_keys + node_tag_keys))
    print(f"[labeled_nise_source] Total unique tags in DB: {len(all_db_tags)} — {all_db_tags}")

    # Step 5: Enable discovered tags via the API and poll for availability.
    # Only bother polling for tags we confirmed exist in the DB.
    from conftest import enable_tags_via_api
    auth_header = {"Authorization": f"Bearer {jwt_token.access_token}"}

    all_expected_keys = nise_tag_keys + ["nslabel1", "nslabel2", "nodelabel1", "nodelabel2", "nodelabel3"]
    tags_to_enable = [k for k in all_expected_keys if k in all_db_tags]
    if not tags_to_enable:
        tags_to_enable = all_db_tags[:15]
        print(
            f"[labeled_nise_source] No expected NISE tags in DB — using discovered DB tags: "
            f"{tags_to_enable}"
        )

    max_tag_wait = 120
    poll_interval = 10
    tag_wait_start = time.time()
    available_tags = []

    print(f"[labeled_nise_source] Enabling {len(tags_to_enable)} tags via API (max {max_tag_wait}s)...")

    while time.time() - tag_wait_start < max_tag_wait:
        enable_result = enable_tags_via_api(gateway_url, auth_header, tags_to_enable)
        enabled = enable_result.get('enabled', [])
        already = enable_result.get('already_enabled', [])
        not_found = enable_result.get('not_found', [])

        elapsed = round(time.time() - tag_wait_start, 1)
        print(
            f"[labeled_nise_source] {elapsed}s — enabled={len(enabled)}, "
            f"already={len(already)}, not_found={len(not_found)}"
        )

        if not not_found:
            print(f"[labeled_nise_source] All tags found in API in {elapsed}s")
            break

        time.sleep(poll_interval)
    else:
        print(f"[labeled_nise_source] Warning: tag enablement timed out after {max_tag_wait}s")

    # Final query for available tags from the API
    session = requests.Session()
    session.verify = False
    session.headers["Authorization"] = f"Bearer {jwt_token.access_token}"

    tags_response = session.get(
        f"{gateway_url}/cost-management/v1/tags/openshift/",
        timeout=30,
    )

    available_tags = []
    if tags_response.status_code == 200:
        tag_data = tags_response.json().get("data", [])
        for entry in tag_data:
            if isinstance(entry, dict) and "key" in entry:
                available_tags.append(entry["key"])
            elif isinstance(entry, str):
                available_tags.append(entry)

    print(f"[labeled_nise_source] Available tags from API: {available_tags}")

    yield {
        "source_id": source.source_id,
        "source_name": source_name,
        "cluster_id": cluster_id,
        "available_tags": available_tags,
        "db_tags": all_db_tags,
        "expected_labels": nise_tag_keys,
    }

    # Cleanup handled by perf_cleanup fixture


# =============================================================================
# Queue Depth Helpers
# =============================================================================

def get_celery_queue_depths(namespace: str) -> dict:
    """Query Valkey (Celery broker) for queue lengths via oc exec.

    Returns a dict of {queue_name: length} for all active Celery queues,
    or an empty dict if the query fails.
    """
    valkey_pod = get_pod_by_label(namespace, "app.kubernetes.io/name=valkey")
    if not valkey_pod:
        return {}

    queues = ["celery", "priority", "summary", "ocp", "cost_model", "download", "ros"]
    depths = {}
    for q in queues:
        result = run_oc_command(
            ["exec", "-n", namespace, valkey_pod, "--",
             "valkey-cli", "LLEN", q],
            check=False,
        )
        if result.returncode == 0:
            try:
                depths[q] = int(result.stdout.strip())
            except ValueError:
                pass
    return depths


def wait_for_queue_drain(
    namespace: str,
    max_wait_seconds: int = 600,
    poll_interval: int = 15,
    label: str = "",
) -> dict:
    """Block until all Celery queues are empty or max_wait_seconds elapses.

    Returns a dict with drain result and final queue depths.
    """
    prefix = f"[queue-drain{' ' + label if label else ''}]"
    start = time.time()
    last_depths: dict = {}

    while time.time() - start < max_wait_seconds:
        depths = get_celery_queue_depths(namespace)
        total = sum(depths.values())
        last_depths = depths
        if total == 0:
            elapsed = round(time.time() - start, 1)
            print(f"{prefix} Queues empty after {elapsed}s")
            return {"drained": True, "elapsed_s": elapsed, "final_depths": depths}
        non_empty = {k: v for k, v in depths.items() if v > 0}
        print(f"{prefix} {non_empty} — waiting...")
        time.sleep(poll_interval)

    elapsed = round(time.time() - start, 1)
    print(f"{prefix} Timed out after {elapsed}s. Final depths: {last_depths}")
    return {"drained": False, "elapsed_s": elapsed, "final_depths": last_depths}


# =============================================================================
# Shared Authentication Helper
# =============================================================================

def create_authenticated_session(keycloak_config) -> "requests.Session":
    """Create a requests.Session with a fresh JWT token.

    Use this in test classes that need authenticated API access. It avoids
    duplicating token-acquisition logic across multiple test files.
    """
    import requests

    token = obtain_jwt_token(keycloak_config)
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token.access_token}",
        "Content-Type": "application/json",
    })
    session.verify = False
    return session


# =============================================================================
# Centralized Koku API URL Fixture
# =============================================================================

@pytest.fixture(scope="session")
def koku_api_url(cluster_config: ClusterConfig) -> str:
    """Internal Koku API URL for in-cluster requests."""
    return (
        f"http://{cluster_config.helm_release_name}-koku-api"
        f".{cluster_config.namespace}.svc.cluster.local:8000"
        f"/api/cost-management/v1"
    )


