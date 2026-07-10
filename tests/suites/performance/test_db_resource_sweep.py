"""
PostgreSQL Resource Sweep Tests (COST-7605 DB-1, DB-2).

Tests whether PostgreSQL CPU and memory allocations affect API query latency
and ingestion processing time. Patches the database StatefulSet with different
resource configurations, runs representative API queries, and captures
pg_stat metrics to identify diminishing-returns thresholds.

Test IDs:
- PERF-DB-001: CPU sweep (1000m → 2000m → 4000m)
- PERF-DB-002: Memory sweep (4Gi → 8Gi → 16Gi)
"""

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pytest
import requests

from conftest import (
    ClusterConfig,
    DatabaseConfig,
    KeycloakConfig,
    create_authenticated_session,
    obtain_jwt_token,
)
from utils import execute_db_query, get_pod_by_label, run_oc_command

from .data_classes import PerformanceResult
from .helpers import PerfResultCollector, PerfTimer
from .k8s_helpers import (
    calculate_percentiles,
    get_resource_spec,
    merge_resources,
    patch_resource_spec,
    restore_resource_spec,
)
from .profiles import ACTIVE_PROFILE as _ACTIVE_PROFILE, PROFILES


# =============================================================================
# pg_stat Metrics Collection
# =============================================================================


@dataclass
class PgStatSnapshot:
    """Point-in-time snapshot of PostgreSQL performance counters."""

    timestamp: float = 0
    # pg_stat_bgwriter
    buffers_checkpoint: int = 0
    buffers_clean: int = 0
    buffers_backend: int = 0
    # pg_stat_database (for the koku database)
    blks_hit: int = 0
    blks_read: int = 0
    xact_commit: int = 0
    xact_rollback: int = 0
    tup_returned: int = 0
    tup_fetched: int = 0
    deadlocks: int = 0
    # derived
    cache_hit_ratio: float = 0.0


def capture_pg_stats(
    namespace: str, db_pod: str, db_name: str, db_user: str
) -> PgStatSnapshot:
    """Capture a snapshot of PostgreSQL performance statistics."""
    snap = PgStatSnapshot(timestamp=time.time())

    # pg_stat_bgwriter
    bgwriter_query = (
        "SELECT buffers_checkpoint, buffers_clean, buffers_backend "
        "FROM pg_stat_bgwriter"
    )
    rows = execute_db_query(namespace, db_pod, db_name, db_user, bgwriter_query)
    if rows and rows[0]:
        parts = rows[0][0].split("|") if isinstance(rows[0], tuple) else str(rows[0]).split("|")
        if len(parts) >= 3:
            snap.buffers_checkpoint = _safe_int(parts[0])
            snap.buffers_clean = _safe_int(parts[1])
            snap.buffers_backend = _safe_int(parts[2])

    # pg_stat_database for the koku DB
    db_query = (
        f"SELECT blks_hit, blks_read, xact_commit, xact_rollback, "
        f"tup_returned, tup_fetched, deadlocks "
        f"FROM pg_stat_database WHERE datname = '{db_name}'"
    )
    rows = execute_db_query(namespace, db_pod, db_name, db_user, db_query)
    if rows and rows[0]:
        parts = rows[0][0].split("|") if isinstance(rows[0], tuple) else str(rows[0]).split("|")
        if len(parts) >= 7:
            snap.blks_hit = _safe_int(parts[0])
            snap.blks_read = _safe_int(parts[1])
            snap.xact_commit = _safe_int(parts[2])
            snap.xact_rollback = _safe_int(parts[3])
            snap.tup_returned = _safe_int(parts[4])
            snap.tup_fetched = _safe_int(parts[5])
            snap.deadlocks = _safe_int(parts[6])

    total_blocks = snap.blks_hit + snap.blks_read
    if total_blocks > 0:
        snap.cache_hit_ratio = round(snap.blks_hit / total_blocks, 4)

    return snap


def diff_pg_stats(before: PgStatSnapshot, after: PgStatSnapshot) -> Dict[str, Any]:
    """Compute the delta between two pg_stat snapshots."""
    delta_hit = after.blks_hit - before.blks_hit
    delta_read = after.blks_read - before.blks_read
    total = delta_hit + delta_read

    return {
        "duration_s": round(after.timestamp - before.timestamp, 1),
        "blks_hit_delta": delta_hit,
        "blks_read_delta": delta_read,
        "cache_hit_ratio": round(delta_hit / total, 4) if total > 0 else 1.0,
        "xact_commit_delta": after.xact_commit - before.xact_commit,
        "xact_rollback_delta": after.xact_rollback - before.xact_rollback,
        "tup_returned_delta": after.tup_returned - before.tup_returned,
        "tup_fetched_delta": after.tup_fetched - before.tup_fetched,
        "deadlocks_delta": after.deadlocks - before.deadlocks,
        "buffers_backend_delta": after.buffers_backend - before.buffers_backend,
    }


def get_db_cpu_utilization(namespace: str, db_pod: str) -> Optional[float]:
    """Read current CPU usage of the database pod in millicores."""
    result = run_oc_command(
        ["adm", "top", "pod", db_pod, "-n", namespace, "--no-headers"],
        check=False,
    )
    if result.returncode != 0:
        return None
    parts = result.stdout.split()
    if len(parts) >= 2:
        cpu_str = parts[1]
        if cpu_str.endswith("m"):
            return int(cpu_str[:-1])
        try:
            return int(cpu_str) * 1000
        except ValueError:
            pass
    return None


def get_db_shared_buffers(
    namespace: str, db_pod: str, db_name: str, db_user: str
) -> str:
    """Read the current shared_buffers setting."""
    rows = execute_db_query(
        namespace, db_pod, db_name, db_user, "SHOW shared_buffers"
    )
    if rows and rows[0]:
        return str(rows[0][0]).strip() if isinstance(rows[0], tuple) else str(rows[0]).strip()
    return "unknown"



# =============================================================================
# API Latency Measurement (lightweight, inline)
# =============================================================================


def measure_api_latencies(
    gateway_url: str,
    keycloak_config: KeycloakConfig,
    iterations: int = 10,
) -> Dict[str, Any]:
    """Run a standard set of API queries and return latency stats.

    Uses the same endpoints as the existing API latency tests but
    with fewer iterations for faster sweep runs.
    """
    session = create_authenticated_session(keycloak_config)
    base = gateway_url.rstrip("/")
    results = {}

    # API-001 equivalent: report baseline
    report_url = f"{base}/api/cost-management/v1/reports/openshift/costs/"
    report_latencies = _measure_endpoint(session, report_url, iterations)
    results["report_baseline"] = report_latencies

    # API-003 equivalent: group_by query (most CPU-sensitive)
    group_by_url = (
        f"{base}/api/cost-management/v1/reports/openshift/costs/"
        f"?group_by[project]=*&filter[time_scope_value]=-30"
    )
    group_by_latencies = _measure_endpoint(session, group_by_url, iterations)
    results["group_by_project"] = group_by_latencies

    # Cost models list
    cost_models_url = f"{base}/api/cost-management/v1/cost-models/"
    cost_model_latencies = _measure_endpoint(session, cost_models_url, iterations)
    results["cost_models_list"] = cost_model_latencies

    session.close()
    return results


def _measure_endpoint(
    session: requests.Session, url: str, iterations: int
) -> Dict[str, Any]:
    """Measure latency for a single endpoint over N iterations."""
    # Warmup
    for _ in range(2):
        try:
            session.get(url, timeout=60)
        except requests.RequestException:
            pass

    latencies = []
    errors = 0
    for _ in range(iterations):
        start = time.time()
        try:
            resp = session.get(url, timeout=60)
            latency = time.time() - start
            latencies.append(latency)
            if resp.status_code != 200:
                errors += 1
        except requests.RequestException:
            latencies.append(time.time() - start)
            errors += 1

    return calculate_percentiles(latencies, errors)




# =============================================================================
# Utilities
# =============================================================================


def _safe_int(s: str) -> int:
    try:
        return int(s.strip())
    except (ValueError, AttributeError):
        return 0


# =============================================================================
# Test Parametrization
# =============================================================================

# CPU sweep: request/limit pairs
_DB_CPU_LEVELS: dict = {
    "baseline": [
        pytest.param(
            {"requests": {"cpu": "100m"}, "limits": {"cpu": "500m"}},
            id="500m-default",
        ),
    ],
    "small": [
        pytest.param(
            {"requests": {"cpu": "100m"}, "limits": {"cpu": "500m"}},
            id="500m-default",
        ),
        pytest.param(
            {"requests": {"cpu": "500m"}, "limits": {"cpu": "2000m"}},
            id="2000m-medium",
        ),
    ],
    "medium": [
        pytest.param(
            {"requests": {"cpu": "500m"}, "limits": {"cpu": "2000m"}},
            id="2000m-medium",
        ),
        pytest.param(
            {"requests": {"cpu": "1000m"}, "limits": {"cpu": "4000m"}},
            id="4000m-large",
        ),
        pytest.param(
            {"requests": {"cpu": "2000m"}, "limits": {"cpu": "8000m"}},
            id="8000m-xlarge",
        ),
    ],
    "large": [
        pytest.param(
            {"requests": {"cpu": "500m"}, "limits": {"cpu": "2000m"}},
            id="2000m-medium",
        ),
        pytest.param(
            {"requests": {"cpu": "1000m"}, "limits": {"cpu": "4000m"}},
            id="4000m-large",
        ),
        pytest.param(
            {"requests": {"cpu": "2000m"}, "limits": {"cpu": "8000m"}},
            id="8000m-xlarge",
        ),
    ],
}

# Memory sweep: request/limit pairs
_DB_MEM_LEVELS: dict = {
    "baseline": [
        pytest.param(
            {"requests": {"memory": "256Mi"}, "limits": {"memory": "512Mi"}},
            id="512Mi-default",
        ),
    ],
    "small": [
        pytest.param(
            {"requests": {"memory": "256Mi"}, "limits": {"memory": "512Mi"}},
            id="512Mi-default",
        ),
        pytest.param(
            {"requests": {"memory": "2Gi"}, "limits": {"memory": "4Gi"}},
            id="4Gi-small",
        ),
    ],
    "medium": [
        pytest.param(
            {"requests": {"memory": "2Gi"}, "limits": {"memory": "4Gi"}},
            id="4Gi-medium",
        ),
        pytest.param(
            {"requests": {"memory": "4Gi"}, "limits": {"memory": "8Gi"}},
            id="8Gi-large",
        ),
        pytest.param(
            {"requests": {"memory": "8Gi"}, "limits": {"memory": "16Gi"}},
            id="16Gi-xlarge",
        ),
    ],
    "large": [
        pytest.param(
            {"requests": {"memory": "2Gi"}, "limits": {"memory": "4Gi"}},
            id="4Gi-medium",
        ),
        pytest.param(
            {"requests": {"memory": "4Gi"}, "limits": {"memory": "8Gi"}},
            id="8Gi-large",
        ),
        pytest.param(
            {"requests": {"memory": "8Gi"}, "limits": {"memory": "16Gi"}},
            id="16Gi-xlarge",
        ),
    ],
}


def _get_levels(table: dict) -> list:
    profile = _ACTIVE_PROFILE if _ACTIVE_PROFILE in table else "medium"
    return table.get(profile, table["medium"])


# =============================================================================
# Tests
# =============================================================================


@pytest.mark.performance
@pytest.mark.db_sweep
class TestPostgresCPUSweep:
    """PERF-DB-001: PostgreSQL CPU sweep.

    Patches the database StatefulSet with different CPU allocations,
    runs API queries, and captures pg_stat metrics to find the
    diminishing-returns threshold.
    """

    @pytest.fixture(autouse=True)
    def setup(self, cluster_config: ClusterConfig, keycloak_config: KeycloakConfig):
        self.namespace = cluster_config.namespace
        self.helm_release = cluster_config.helm_release_name
        self._keycloak_config = keycloak_config
        self._sts_name = f"{self.helm_release}-database"

    @pytest.mark.parametrize("cpu_resources", _get_levels(_DB_CPU_LEVELS))
    def test_perf_db_001_cpu_sweep(
        self,
        cpu_resources: dict,
        gateway_url: str,
        database_config: DatabaseConfig,
        perf_timer: PerfTimer,
        perf_result: PerformanceResult,
        perf_collector: PerfResultCollector,
    ):
        """Measure API latency and pg_stat metrics at a given CPU allocation."""
        cpu_limit = cpu_resources["limits"]["cpu"]
        profile_name = _ACTIVE_PROFILE if _ACTIVE_PROFILE in PROFILES else "medium"

        print(f"\n{'='*70}")
        print(f"PERF-DB-001: Database CPU = {cpu_limit} (profile: {profile_name})")
        print(f"{'='*70}")

        original_resources = get_resource_spec(self.namespace, "statefulset", self._sts_name)
        print(f"Original DB resources: {original_resources}")

        # Merge: keep existing memory, override CPU only
        target_resources = merge_resources(original_resources, cpu_resources)
        is_modified = target_resources != original_resources

        try:
            if is_modified:
                with perf_timer.measure("db_resource_patch"):
                    success = patch_resource_spec(
                        self.namespace, "statefulset", self._sts_name, target_resources
                    )
                    if not success:
                        pytest.skip(f"Could not patch database to CPU {cpu_limit}")

            # Capture pre-test pg_stat
            pg_before = capture_pg_stats(
                self.namespace,
                database_config.pod_name,
                database_config.database,
                database_config.user,
            )
            shared_buffers = get_db_shared_buffers(
                self.namespace,
                database_config.pod_name,
                database_config.database,
                database_config.user,
            )
            print(f"shared_buffers = {shared_buffers}")

            # Sample DB CPU before queries
            cpu_before = get_db_cpu_utilization(self.namespace, database_config.pod_name)

            # Run API latency measurements
            with perf_timer.measure("api_latency_measurement"):
                api_results = measure_api_latencies(
                    gateway_url, self._keycloak_config, iterations=20
                )

            # Sample DB CPU during/after queries
            cpu_after = get_db_cpu_utilization(self.namespace, database_config.pod_name)

            # Capture post-test pg_stat
            pg_after = capture_pg_stats(
                self.namespace,
                database_config.pod_name,
                database_config.database,
                database_config.user,
            )

            pg_delta = diff_pg_stats(pg_before, pg_after)

        finally:
            if is_modified:
                print(f"\nRestoring DB resources to original...")
                restore_resource_spec(
                    self.namespace, "statefulset", self._sts_name, original_resources
                )

        # Build result
        perf_result.test_id = f"PERF-DB-001-{cpu_limit}"
        perf_result.metrics = {
            "cpu_limit": cpu_limit,
            "cpu_request": cpu_resources["requests"]["cpu"],
            "profile": profile_name,
            "shared_buffers": shared_buffers,
            "api_latencies": api_results,
            "pg_stat_delta": pg_delta,
            "db_cpu_millicores": {
                "before_queries": cpu_before,
                "after_queries": cpu_after,
            },
        }
        perf_result.timings = perf_timer.get_timings()
        perf_result.passed = True
        perf_collector.add_result(perf_result)

        # Print summary
        print(f"\n{'='*70}")
        print(f"PERF-DB-001 SUMMARY — CPU {cpu_limit}")
        print(f"  Cache hit ratio:     {pg_delta['cache_hit_ratio']:.4f}")
        print(f"  Transactions:        {pg_delta['xact_commit_delta']} commit, "
              f"{pg_delta['xact_rollback_delta']} rollback")
        print(f"  Report baseline p95: {api_results['report_baseline']['p95']:.4f}s")
        print(f"  Group-by p95:        {api_results['group_by_project']['p95']:.4f}s")
        print(f"  DB CPU:              {cpu_before}m → {cpu_after}m")
        print(f"{'='*70}")


@pytest.mark.performance
@pytest.mark.db_sweep
class TestPostgresMemorySweep:
    """PERF-DB-002: PostgreSQL memory sweep.

    Patches the database StatefulSet with different memory allocations,
    runs API queries, and measures buffer cache hit ratio to find
    the point of diminishing returns.
    """

    @pytest.fixture(autouse=True)
    def setup(self, cluster_config: ClusterConfig, keycloak_config: KeycloakConfig):
        self.namespace = cluster_config.namespace
        self.helm_release = cluster_config.helm_release_name
        self._keycloak_config = keycloak_config
        self._sts_name = f"{self.helm_release}-database"

    @pytest.mark.parametrize("mem_resources", _get_levels(_DB_MEM_LEVELS))
    def test_perf_db_002_memory_sweep(
        self,
        mem_resources: dict,
        gateway_url: str,
        database_config: DatabaseConfig,
        perf_timer: PerfTimer,
        perf_result: PerformanceResult,
        perf_collector: PerfResultCollector,
    ):
        """Measure API latency and buffer cache hit ratio at a given memory allocation."""
        mem_limit = mem_resources["limits"]["memory"]
        profile_name = _ACTIVE_PROFILE if _ACTIVE_PROFILE in PROFILES else "medium"

        print(f"\n{'='*70}")
        print(f"PERF-DB-002: Database memory = {mem_limit} (profile: {profile_name})")
        print(f"{'='*70}")

        original_resources = get_resource_spec(self.namespace, "statefulset", self._sts_name)
        print(f"Original DB resources: {original_resources}")

        # Merge: keep existing CPU, override memory only
        target_resources = merge_resources(original_resources, mem_resources)
        is_modified = target_resources != original_resources

        try:
            if is_modified:
                with perf_timer.measure("db_resource_patch"):
                    success = patch_resource_spec(
                        self.namespace, "statefulset", self._sts_name, target_resources
                    )
                    if not success:
                        pytest.skip(f"Could not patch database to memory {mem_limit}")

            # Refresh database_config pod name (new pod after restart)
            db_pod = _find_db_pod(self.namespace)
            if not db_pod:
                pytest.skip("Database pod not found after patch")

            shared_buffers = get_db_shared_buffers(
                self.namespace, db_pod,
                database_config.database, database_config.user,
            )
            print(f"shared_buffers = {shared_buffers}")

            pg_before = capture_pg_stats(
                self.namespace, db_pod,
                database_config.database, database_config.user,
            )

            with perf_timer.measure("api_latency_measurement"):
                api_results = measure_api_latencies(
                    gateway_url, self._keycloak_config, iterations=20
                )

            pg_after = capture_pg_stats(
                self.namespace, db_pod,
                database_config.database, database_config.user,
            )
            pg_delta = diff_pg_stats(pg_before, pg_after)

        finally:
            if is_modified:
                print(f"\nRestoring DB resources to original...")
                restore_resource_spec(
                    self.namespace, "statefulset", self._sts_name, original_resources
                )

        perf_result.test_id = f"PERF-DB-002-{mem_limit}"
        perf_result.metrics = {
            "memory_limit": mem_limit,
            "memory_request": mem_resources["requests"]["memory"],
            "profile": profile_name,
            "shared_buffers": shared_buffers,
            "api_latencies": api_results,
            "pg_stat_delta": pg_delta,
        }
        perf_result.timings = perf_timer.get_timings()
        perf_result.passed = True
        perf_collector.add_result(perf_result)

        print(f"\n{'='*70}")
        print(f"PERF-DB-002 SUMMARY — Memory {mem_limit}")
        print(f"  Cache hit ratio:     {pg_delta['cache_hit_ratio']:.4f}")
        print(f"  Blocks hit/read:     {pg_delta['blks_hit_delta']}/{pg_delta['blks_read_delta']}")
        print(f"  shared_buffers:      {shared_buffers}")
        print(f"  Report baseline p95: {api_results['report_baseline']['p95']:.4f}s")
        print(f"  Group-by p95:        {api_results['group_by_project']['p95']:.4f}s")
        print(f"{'='*70}")


# =============================================================================
# Helpers
# =============================================================================


def _find_db_pod(namespace: str) -> Optional[str]:
    """Find the current running database pod name."""
    return get_pod_by_label(namespace, "app.kubernetes.io/component=database")
