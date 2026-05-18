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

from conftest import ClusterConfig, DatabaseConfig
from utils import run_oc_command, get_pod_by_label


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
    
    # Concurrent upload settings
    concurrent_upload_max: int = field(
        default_factory=lambda: int(os.environ.get("PERF_CONCURRENT_UPLOADS_MAX", "5"))
    )


def _create_perf_config() -> PerfTestConfig:
    """Create PerfTestConfig, working around frozen dataclass with default_factory."""
    return PerfTestConfig(
        soak_duration_hours=float(os.environ.get("SOAK_DURATION_HOURS", "1")),
        soak_upload_interval_minutes=int(os.environ.get("SOAK_UPLOAD_INTERVAL_MINUTES", "15")),
        soak_query_interval_minutes=int(os.environ.get("SOAK_QUERY_INTERVAL_MINUTES", "5")),
        soak_metrics_interval_seconds=int(os.environ.get("SOAK_METRICS_INTERVAL_SECONDS", "60")),
        ing_high_freq_duration_minutes=int(os.environ.get("PERF_ING_005_DURATION_MINUTES", "15")),
        ing_high_freq_interval_seconds=int(os.environ.get("PERF_ING_005_INTERVAL_SECONDS", "300")),
        scale_max_sources=int(os.environ.get("PERF_SCALE_002_MAX_SOURCES", "25")),
        scale_batch_size=int(os.environ.get("PERF_SCALE_002_BATCH_SIZE", "5")),
        api_crud_iterations=int(os.environ.get("PERF_API_003_ITERATIONS", "10")),
        timeout_provider_ready=int(os.environ.get("PERF_TIMEOUT_PROVIDER", "300")),
        timeout_summary_tables=int(os.environ.get("PERF_TIMEOUT_SUMMARY", "600")),
        timeout_kruize_experiments=int(os.environ.get("PERF_TIMEOUT_KRUIZE", "300")),
        timeout_kruize_recommendations=int(os.environ.get("PERF_TIMEOUT_RECOMMENDATIONS", "600")),
        memory_growth_daily_percent_max=float(os.environ.get("PERF_MEMORY_GROWTH_MAX", "5.0")),
        concurrent_upload_max=int(os.environ.get("PERF_CONCURRENT_UPLOADS_MAX", "5")),
    )


# Global config instance - import this in test files
PERF_CONFIG = _create_perf_config()


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
    storage_type: str = ""  # ODF, S4, hostpath, etc.
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
# Performance Profile Definitions (from Pau's Production Data)
# =============================================================================

PERFORMANCE_PROFILES = {
    "small": {
        "description": "37% of customers - Single cluster, 15 nodes, 200 cores",
        "clusters": 1,
        "nodes_per_cluster": 15,
        "cpu_cores_per_node": 13,  # ~200 total
        "memory_gib_per_node": 73,  # ~1.1 TB total
        "namespaces_per_cluster": 10,
        "pods_per_namespace": 5,
        "pvcs_per_cluster": 48,
        "cost_models": 1,
        "data_days": 30,
    },
    "medium": {
        "description": "35% of customers - 2 clusters, 49 nodes, 544 cores",
        "clusters": 2,
        "nodes_per_cluster": 25,  # ~49 total
        "cpu_cores_per_node": 11,  # ~544 total
        "memory_gib_per_node": 57,  # ~2.8 TB total
        "namespaces_per_cluster": 20,
        "pods_per_namespace": 8,
        "pvcs_per_cluster": 89,  # ~177 total
        "cost_models": 1,
        "data_days": 30,
    },
    "large": {
        "description": "21% of customers - 7 clusters, 133 nodes, 1964 cores",
        "clusters": 7,
        "nodes_per_cluster": 19,  # ~133 total
        "cpu_cores_per_node": 15,  # ~1964 total
        "memory_gib_per_node": 73,  # ~9.7 TB total
        "namespaces_per_cluster": 30,
        "pods_per_namespace": 10,
        "pvcs_per_cluster": 70,  # ~492 total
        "cost_models": 2,
        "data_days": 30,
    },
    "xlarge": {
        "description": "6% of customers - 23 clusters, 346 nodes, 6954 cores",
        "clusters": 23,
        "nodes_per_cluster": 15,  # ~346 total
        "cpu_cores_per_node": 20,  # ~6954 total
        "memory_gib_per_node": 140,  # ~48.5 TB total
        "namespaces_per_cluster": 40,
        "pods_per_namespace": 15,
        "pvcs_per_cluster": 55,  # ~1255 total
        "cost_models": 3,
        "data_days": 30,
    },
    "stress_p99": {
        "description": "P99 stress test - 33 clusters, 1072 nodes",
        "clusters": 33,
        "nodes_per_cluster": 32,  # ~1072 total
        "cpu_cores_per_node": 54,  # ~57424 total
        "memory_gib_per_node": 128,
        "namespaces_per_cluster": 50,
        "pods_per_namespace": 20,
        "pvcs_per_cluster": 185,  # ~6099 total
        "cost_models": 7,
        "data_days": 30,
    },
}


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
    
    return {"storage_type": "unknown", "storage_class": "unknown"}


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


def _parse_cpu(value: str) -> float:
    """Parse CPU value (e.g., '500m', '2') to cores."""
    if not value:
        return 0.0
    if value.endswith("m"):
        return float(value[:-1]) / 1000
    return float(value)


def _parse_memory_mib(value: str) -> float:
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


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def cluster_info(cluster_config: ClusterConfig) -> ClusterInfo:
    """Collect cluster information for performance context."""
    node_info = get_node_info(cluster_config.namespace)
    storage_info = get_storage_info(cluster_config.namespace)
    
    return ClusterInfo(
        ocp_version=get_ocp_version(cluster_config.namespace),
        node_count=node_info["node_count"],
        worker_node_count=node_info["worker_count"],
        total_cpu_cores=node_info["cpu"],
        total_memory_gib=node_info["memory_gib"],
        storage_class=storage_info["storage_class"],
        storage_type=storage_info["storage_type"],
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
    if performance_profile not in PERFORMANCE_PROFILES:
        pytest.skip(f"Unknown performance profile: {performance_profile}")
    return PERFORMANCE_PROFILES[performance_profile]


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
    """Get the directory for performance reports."""
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
