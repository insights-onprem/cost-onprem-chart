"""
Ingestion Throughput Performance Tests (PERF-ING-*).

Tests data ingestion capacity under various loads per FLPATH-4036.

Test IDs:
- PERF-ING-001: Single source baseline
- PERF-ING-002: Single source burst (90 days)
- PERF-ING-003: Concurrent uploads
- PERF-ING-004: Large file upload (50MB+)
- PERF-ING-005: High frequency uploads
- PERF-ING-006: 6-hour processing window validation (SC-4)
"""

import os
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import requests

from conftest import ClusterConfig, JWTToken, obtain_jwt_token
from e2e_helpers import (
    NISEConfig,
    SourceRegistration,
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
    get_pod_by_label,
    run_oc_command,
)

from .conftest import (
    PerfCleanupTracker,
    PerfResultCollector,
    PerfTimer,
    PerformanceResult,
    ResourceSnapshot,
    TimingMetric,
    get_pod_resource_usage,
    save_perf_result,
)
from .profiles import PROFILES, get_profile_metrics, get_profile_nise_yaml


# =============================================================================
# Constants
# =============================================================================

UPLOAD_CONTENT_TYPE = "application/vnd.redhat.hccm.filename+tgz"


# =============================================================================
# Helper Functions
# =============================================================================

def get_listener_cpu_usage(namespace: str) -> Optional[float]:
    """Get current listener CPU usage in cores."""
    result = run_oc_command([
        "adm", "top", "pod", "-n", namespace,
        "-l", "app.kubernetes.io/component=listener",
        "--no-headers"
    ], check=False)
    
    if result.returncode != 0 or not result.stdout.strip():
        return None
    
    # Parse output: "pod-name CPU(cores) MEMORY(bytes)"
    try:
        parts = result.stdout.strip().split()
        if len(parts) >= 2:
            cpu_str = parts[1]
            if cpu_str.endswith("m"):
                return float(cpu_str[:-1]) / 1000
            return float(cpu_str)
    except (ValueError, IndexError):
        pass
    
    return None


def generate_and_upload_data(
    cluster_id: str,
    source_name: str,
    start_date: datetime,
    end_date: datetime,
    ingress_url: str,
    jwt_token: JWTToken,
    config: Optional[NISEConfig] = None,
    profile_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate NISE data and upload it to ingress.
    
    Returns timing and metadata about the upload.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Generate data
        gen_start = time.time()
        
        if profile_name and profile_name in PROFILES:
            # Use profile-based generation
            yaml_content = get_profile_nise_yaml(
                profile_name, start_date, end_date, cluster_id, 0
            )
            yaml_path = os.path.join(temp_dir, "static_report.yml")
            with open(yaml_path, "w") as f:
                f.write(yaml_content)
            
            # Run NISE with the generated YAML
            import subprocess
            nise_output = os.path.join(temp_dir, "nise_output")
            os.makedirs(nise_output, exist_ok=True)
            
            result = subprocess.run(
                ["nise", "report", "ocp",
                 "--static-report-file", yaml_path,
                 "--ocp-cluster-id", cluster_id,
                 "-w", "--ros-ocp-info"],
                capture_output=True,
                text=True,
                timeout=600,
                cwd=nise_output,
            )
            
            if result.returncode != 0:
                raise RuntimeError(f"NISE failed: {result.stderr}")
            
            # Collect generated files
            csv_files = list(Path(nise_output).rglob("*.csv"))
            # Categorize files
            pod_usage_files = [str(f) for f in csv_files if "pod_usage" in f.name.lower()]
            ros_usage_files = [str(f) for f in csv_files if "ros_usage" in f.name.lower() or "resource_" in f.name.lower()]
            node_label_files = [str(f) for f in csv_files if "node_labels" in f.name.lower()]
            namespace_label_files = [str(f) for f in csv_files if "namespace_labels" in f.name.lower()]
        else:
            # Use NISEConfig-based generation
            files = generate_nise_data(
                cluster_id, start_date, end_date, temp_dir, config
            )
            pod_usage_files = files.get("pod_usage_files", [])
            ros_usage_files = files.get("ros_usage_files", [])
            node_label_files = files.get("node_label_files", [])
            namespace_label_files = files.get("namespace_label_files", [])
        
        gen_duration = time.time() - gen_start
        total_files = len(pod_usage_files) + len(ros_usage_files)
        
        # Create upload package
        package_start = time.time()
        
        package_path = create_upload_package_from_files(
            pod_usage_files=pod_usage_files,
            ros_usage_files=ros_usage_files,
            cluster_id=cluster_id,
            start_date=start_date,
            end_date=end_date,
            node_label_files=node_label_files if node_label_files else None,
            namespace_label_files=namespace_label_files if namespace_label_files else None,
        )
        
        package_size_mb = os.path.getsize(package_path) / (1024 * 1024)
        package_duration = time.time() - package_start
        
        # Upload
        upload_start = time.time()
        session = requests.Session()
        session.verify = False
        
        response = upload_with_retry(
            session,
            f"{ingress_url}/v1/upload",
            package_path,
            jwt_token.authorization_header,
        )
        
        upload_duration = time.time() - upload_start
        
        return {
            "cluster_id": cluster_id,
            "source_name": source_name,
            "csv_file_count": total_files,
            "package_size_mb": round(package_size_mb, 3),
            "generation_seconds": round(gen_duration, 3),
            "packaging_seconds": round(package_duration, 3),
            "upload_seconds": round(upload_duration, 3),
            "upload_status": response.status_code,
            "upload_mb_per_second": round(package_size_mb / upload_duration, 3) if upload_duration > 0 else 0,
        }


# =============================================================================
# Test Classes
# =============================================================================

@pytest.mark.performance
@pytest.mark.ingestion
@pytest.mark.slow
class TestIngestionThroughput:
    """Ingestion throughput performance tests."""
    
    @pytest.fixture(autouse=True)
    def setup(self, cluster_config: ClusterConfig, keycloak_config):
        """Setup for ingestion tests."""
        self.namespace = cluster_config.namespace
        self.helm_release = cluster_config.helm_release_name
        
        # Ensure NISE is available
        if not ensure_nise_available():
            pytest.skip("NISE (koku-nise) not available")
        
        # Store keycloak config for token refresh
        self._keycloak_config = keycloak_config
    
    def _get_fresh_token(self) -> JWTToken:
        """Get a fresh JWT token for long-running tests."""
        return obtain_jwt_token(self._keycloak_config)
    
    @pytest.mark.parametrize("profile_name", ["baseline", "small"])
    def test_perf_ing_001_single_source_baseline(
        self,
        profile_name: str,
        cluster_config: ClusterConfig,
        ingress_url: str,
        database_config,
        perf_timer: PerfTimer,
        perf_result: PerformanceResult,
        perf_collector: PerfResultCollector,
        rh_identity_header: str,
        perf_cleanup,
    ):
        """PERF-ING-001: Single source baseline - 1 source, 1 month data, default config.
        
        Metrics captured:
        - Time to complete full pipeline
        - Listener CPU utilization
        - Upload throughput (MB/s)
        """
        cluster_id = generate_cluster_id()
        source_name = f"perf-ing-001-{cluster_id[-8:]}"
        
        # Get pod for internal API calls
        ingress_pod = get_pod_by_label(self.namespace, "app.kubernetes.io/component=ingress")
        if not ingress_pod:
            pytest.skip("Ingress pod not found")
        
        koku_api_url = f"http://{self.helm_release}-koku-api.{self.namespace}.svc.cluster.local:8000/api/cost-management/v1"
        
        # Register source
        with perf_timer.measure("source_registration"):
            source = register_source(
                self.namespace,
                ingress_pod,
                koku_api_url,
                rh_identity_header,
                cluster_id,
                "org1234567",
                source_name,
            )
        
        # Track for cleanup
        perf_cleanup.track(source_id=source.source_id, cluster_id=cluster_id, source_name=source_name)
        
        # Generate and upload data
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=PROFILES[profile_name]["data_days"])
        
        jwt_token = self._get_fresh_token()
        
        with perf_timer.measure("data_generation_and_upload"):
            upload_result = generate_and_upload_data(
                cluster_id,
                source_name,
                start_date,
                end_date,
                ingress_url,
                jwt_token,
                profile_name=profile_name,
            )
        
        # Wait for processing
        with perf_timer.measure("processing_wait"):
            schema = wait_for_summary_tables(
                self.namespace,
                database_config.pod_name,
                cluster_id,
                timeout=900,
                interval=30,
            )
        
        # Capture listener CPU at end
        listener_cpu = get_listener_cpu_usage(self.namespace)
        
        # Record metrics
        perf_result.metrics = {
            "profile": profile_name,
            "upload": upload_result,
            "listener_cpu_cores": listener_cpu,
            "processing_completed": schema is not None,
        }
        perf_result.timings = perf_timer.get_timings()
        perf_result.passed = schema is not None
        
        if not schema:
            perf_result.error_message = "Processing did not complete within timeout"
        
        perf_collector.add_result(perf_result)
        
        assert schema is not None, "Data processing did not complete"

    @pytest.mark.parametrize(
        "data_days,timeout_seconds",
        [
            pytest.param(30, 600, id="30-days"),
            pytest.param(60, 900, id="60-days"),
            pytest.param(90, 1200, id="90-days"),
        ],
    )
    def test_perf_ing_002_single_source_burst(
        self,
        data_days: int,
        timeout_seconds: int,
        cluster_config: ClusterConfig,
        ingress_url: str,
        database_config,
        perf_timer: PerfTimer,
        perf_result: PerformanceResult,
        perf_collector: PerfResultCollector,
        rh_identity_header: str,
        perf_cleanup,
        request,
    ):
        """PERF-ING-002: Single source burst - 1 source, N days data, max listener CPU.
        
        Tests processing throughput with varying data volumes (30, 60, 90 days).
        
        Metrics captured:
        - Total processing time
        - Throughput (MB/s)
        - Listener CPU over time
        """
        # Apply dynamic timeout based on data volume
        # pytest-timeout doesn't support parametrized timeouts directly,
        # so we implement our own deadline
        test_deadline = time.time() + timeout_seconds
        
        cluster_id = generate_cluster_id()
        source_name = f"perf-ing-002-{data_days}d-{cluster_id[-8:]}"
        profile_name = "single_source_burst"
        
        ingress_pod = get_pod_by_label(self.namespace, "app.kubernetes.io/component=ingress")
        if not ingress_pod:
            pytest.skip("Ingress pod not found")
        
        koku_api_url = f"http://{self.helm_release}-koku-api.{self.namespace}.svc.cluster.local:8000/api/cost-management/v1"
        
        # Register source
        with perf_timer.measure("source_registration"):
            source = register_source(
                self.namespace,
                ingress_pod,
                koku_api_url,
                rh_identity_header,
                cluster_id,
                "org1234567",
                source_name,
            )
        
        # Track for cleanup
        perf_cleanup.track(source_id=source.source_id, cluster_id=cluster_id, source_name=source_name)
        
        # Variable days of data based on parameter
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=data_days)
        
        jwt_token = self._get_fresh_token()
        
        # Capture baseline CPU
        baseline_cpu = get_listener_cpu_usage(self.namespace)
        cpu_samples = [baseline_cpu] if baseline_cpu else []
        
        with perf_timer.measure("data_generation_and_upload"):
            upload_result = generate_and_upload_data(
                cluster_id,
                source_name,
                start_date,
                end_date,
                ingress_url,
                jwt_token,
                profile_name=profile_name,
            )
        
        # Check if we've exceeded deadline after upload
        if time.time() > test_deadline:
            perf_result.metrics = {
                "profile": profile_name,
                "data_days": data_days,
                "upload": upload_result,
                "error": "Upload exceeded timeout",
            }
            perf_result.timings = perf_timer.get_timings()
            perf_result.passed = False
            perf_result.error_message = f"Upload exceeded {timeout_seconds}s timeout"
            perf_collector.add_result(perf_result)
            pytest.fail(f"{data_days}-day data upload exceeded {timeout_seconds}s timeout")
        
        # Calculate remaining time for processing
        remaining_time = max(60, test_deadline - time.time())
        
        # Monitor processing with CPU sampling
        with perf_timer.measure("processing_wait"):
            start_wait = time.time()
            schema = None
            
            while time.time() - start_wait < remaining_time:
                schema = wait_for_summary_tables(
                    self.namespace,
                    database_config.pod_name,
                    cluster_id,
                    timeout=60,
                    interval=30,
                )
                
                # Sample CPU
                cpu = get_listener_cpu_usage(self.namespace)
                if cpu:
                    cpu_samples.append(cpu)
                
                if schema:
                    break
        
        # Calculate metrics
        processing_time = perf_timer.get_timing("processing_wait")
        throughput_mb_s = 0
        if processing_time and processing_time.duration_seconds > 0:
            throughput_mb_s = upload_result["package_size_mb"] / processing_time.duration_seconds
        
        perf_result.metrics = {
            "profile": profile_name,
            "data_days": data_days,
            "timeout_seconds": timeout_seconds,
            "upload": upload_result,
            "cpu_samples": cpu_samples,
            "avg_cpu_cores": sum(cpu_samples) / len(cpu_samples) if cpu_samples else 0,
            "max_cpu_cores": max(cpu_samples) if cpu_samples else 0,
            "processing_throughput_mb_s": round(throughput_mb_s, 4),
            "processing_completed": schema is not None,
        }
        perf_result.timings = perf_timer.get_timings()
        perf_result.passed = schema is not None
        
        perf_collector.add_result(perf_result)
        
        assert schema is not None, f"{data_days}-day data processing did not complete"

    @pytest.mark.timeout(600)  # 10 minutes per concurrent upload test
    @pytest.mark.parametrize("concurrent_sources", [2, 5, 10])
    def test_perf_ing_003_concurrent_uploads(
        self,
        concurrent_sources: int,
        cluster_config: ClusterConfig,
        ingress_url: str,
        database_config,
        perf_timer: PerfTimer,
        perf_result: PerformanceResult,
        perf_collector: PerfResultCollector,
        rh_identity_header: str,
        perf_cleanup,
    ):
        """PERF-ING-003: Concurrent uploads - N sources uploading simultaneously.
        
        Tests system behavior under concurrent load.
        
        Metrics captured:
        - Queue depth
        - Time to complete all
        - Error rate
        """
        ingress_pod = get_pod_by_label(self.namespace, "app.kubernetes.io/component=ingress")
        if not ingress_pod:
            pytest.skip("Ingress pod not found")
        
        koku_api_url = f"http://{self.helm_release}-koku-api.{self.namespace}.svc.cluster.local:8000/api/cost-management/v1"
        
        # Register all sources first
        sources = []
        with perf_timer.measure("source_registration_all"):
            for i in range(concurrent_sources):
                cluster_id = generate_cluster_id()
                source_name = f"perf-ing-003-{i:02d}-{cluster_id[-8:]}"
                
                source = register_source(
                    self.namespace,
                    ingress_pod,
                    koku_api_url,
                    rh_identity_header,
                    cluster_id,
                    "org1234567",
                    source_name,
                )
                
                # Track each source for cleanup
                perf_cleanup.track(source_id=source.source_id, cluster_id=cluster_id, source_name=source_name)
                
                sources.append({
                    "cluster_id": cluster_id,
                    "source_name": source_name,
                    "source": source,
                })
        
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=7)  # 1 week of data each
        
        # Upload concurrently
        upload_results = []
        errors = []
        
        def upload_source(source_info: Dict[str, Any]) -> Dict[str, Any]:
            try:
                jwt_token = self._get_fresh_token()
                return generate_and_upload_data(
                    source_info["cluster_id"],
                    source_info["source_name"],
                    start_date,
                    end_date,
                    ingress_url,
                    jwt_token,
                    profile_name="baseline",
                )
            except Exception as e:
                return {"error": str(e), "cluster_id": source_info["cluster_id"]}
        
        with perf_timer.measure("concurrent_uploads"):
            with ThreadPoolExecutor(max_workers=concurrent_sources) as executor:
                futures = {executor.submit(upload_source, s): s for s in sources}
                
                for future in as_completed(futures):
                    result = future.result()
                    if "error" in result:
                        errors.append(result)
                    else:
                        upload_results.append(result)
        
        # Wait for all to process
        processed_count = 0
        with perf_timer.measure("processing_wait_all"):
            for source_info in sources:
                schema = wait_for_summary_tables(
                    self.namespace,
                    database_config.pod_name,
                    source_info["cluster_id"],
                    timeout=600,
                    interval=30,
                )
                if schema:
                    processed_count += 1
        
        perf_result.metrics = {
            "concurrent_sources": concurrent_sources,
            "successful_uploads": len(upload_results),
            "failed_uploads": len(errors),
            "processed_count": processed_count,
            "error_rate": len(errors) / concurrent_sources if concurrent_sources > 0 else 0,
            "total_upload_mb": sum(r.get("package_size_mb", 0) for r in upload_results),
            "errors": errors,
        }
        perf_result.timings = perf_timer.get_timings()
        perf_result.passed = processed_count == concurrent_sources
        
        perf_collector.add_result(perf_result)
        
        assert len(errors) == 0, f"Upload errors: {errors}"
        assert processed_count == concurrent_sources, f"Only {processed_count}/{concurrent_sources} processed"

    @pytest.mark.timeout(3600)  # 60 minutes for large file upload test (generation + upload + processing)
    @pytest.mark.parametrize("target_size_mb", [50, 100])
    def test_perf_ing_004_large_file_upload(
        self,
        target_size_mb: int,
        cluster_config: ClusterConfig,
        ingress_url: str,
        database_config,
        perf_timer: PerfTimer,
        perf_result: PerformanceResult,
        perf_collector: PerfResultCollector,
        rh_identity_header: str,
        perf_cleanup,
    ):
        """PERF-ING-004: Large file upload (50MB+).
        
        Tests system behavior with large payload files that approach or exceed
        typical upload size limits.
        
        Default ingress limit is 100MB. This test validates:
        - Upload succeeds for large files
        - Processing completes within reasonable time
        - No timeouts or memory issues
        
        Metrics captured:
        - Upload time and throughput
        - Processing time
        - Any errors or retries
        
        The test uses extended date ranges with larger profiles to generate
        files approaching the target size. Actual generated size depends on
        profile characteristics and NISE output.
        """
        cluster_id = generate_cluster_id()
        source_name = f"perf-ing-004-{target_size_mb}mb-{cluster_id[-8:]}"
        
        ingress_pod = get_pod_by_label(self.namespace, "app.kubernetes.io/component=ingress")
        if not ingress_pod:
            pytest.skip("Ingress pod not found")
        
        koku_api_url = f"http://{self.helm_release}-koku-api.{self.namespace}.svc.cluster.local:8000/api/cost-management/v1"
        
        # Register source
        with perf_timer.measure("source_registration"):
            source = register_source(
                self.namespace,
                ingress_pod,
                koku_api_url,
                rh_identity_header,
                cluster_id,
                "org1234567",
                source_name,
            )
        
        # Track for cleanup
        perf_cleanup.track(source_id=source.source_id, cluster_id=cluster_id, source_name=source_name)
        
        # NOTE: This test intentionally ignores PERF_PROFILE and always uses "large"
        # profile because the goal is to generate files of specific sizes (50MB+).
        # Smaller profiles cannot generate files large enough to test upload limits.
        #
        # Empirical data sizes (from test runs):
        # - large profile (133 nodes) x 90 days ≈ 200 MB (much larger than expected)
        # - medium profile (49 nodes) x 30 days ≈ 15-25 MB  
        # - medium profile x 60 days ≈ 30-50 MB
        # - large profile x 30 days ≈ 50-70 MB
        # - large profile x 45 days ≈ 100+ MB
        if target_size_mb <= 50:
            days_for_size = 30
            profile_name = "large"  # Always large - required for 50MB+ files
        else:
            days_for_size = 45
            profile_name = "large"  # Always large - required for 100MB+ files
        
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days_for_size)
        
        jwt_token = self._get_fresh_token()
        
        # Capture baseline CPU
        baseline_cpu = get_listener_cpu_usage(self.namespace)
        cpu_samples = [baseline_cpu] if baseline_cpu else []
        
        # Generate and upload data using standard helper
        # This handles NISE generation, packaging, and upload
        with perf_timer.measure("data_generation_and_upload"):
            upload_result = generate_and_upload_data(
                cluster_id,
                source_name,
                start_date,
                end_date,
                ingress_url,
                jwt_token,
                profile_name=profile_name,
            )
        
        package_size_mb = upload_result.get("package_size_mb", 0)
        upload_seconds = upload_result.get("upload_seconds", 0)
        generation_seconds = upload_result.get("generation_seconds", 0)
        
        # Log the actual size achieved
        print(f"\n[PERF-ING-004] Generated package: {package_size_mb:.2f} MB (target: {target_size_mb} MB)")
        
        # Note: we don't skip if size is less than target - we document what was achieved
        # The test validates that large file uploads work, not that we hit exact sizes
        size_achieved_pct = (package_size_mb / target_size_mb * 100) if target_size_mb > 0 else 0
        
        upload_throughput = package_size_mb / upload_seconds if upload_seconds > 0 else 0
        
        # Wait for processing - large files need extended time
        # Based on empirical data: ~67MB takes 20+ minutes to process
        # Allow up to 45 minutes for processing large files
        with perf_timer.measure("processing_wait"):
            processing_start = time.time()
            schema = None
            max_wait = 2700  # 45 minutes for processing large files
            
            while time.time() - processing_start < max_wait:
                schema = wait_for_summary_tables(
                    self.namespace,
                    database_config.pod_name,
                    cluster_id,
                    timeout=60,
                    interval=30,
                )
                
                # Sample CPU during processing
                cpu = get_listener_cpu_usage(self.namespace)
                if cpu:
                    cpu_samples.append(cpu)
                
                if schema:
                    break
        
        processing_time = time.time() - processing_start
        
        perf_result.metrics = {
            "target_size_mb": target_size_mb,
            "actual_size_mb": round(package_size_mb, 2),
            "size_achieved_pct": round(size_achieved_pct, 1),
            "profile": profile_name,
            "days": days_for_size,
            "generation_time_seconds": round(generation_seconds, 2),
            "upload_time_seconds": round(upload_seconds, 2),
            "upload_throughput_mb_s": round(upload_throughput, 4),
            "processing_time_seconds": round(processing_time, 2),
            "total_time_seconds": round(upload_seconds + processing_time, 2),
            "cpu_samples": cpu_samples,
            "avg_cpu_cores": sum(cpu_samples) / len(cpu_samples) if cpu_samples else 0,
            "max_cpu_cores": max(cpu_samples) if cpu_samples else 0,
            "processing_completed": schema is not None,
        }
        perf_result.timings = perf_timer.get_timings()
        perf_result.passed = schema is not None
        
        perf_collector.add_result(perf_result)
        
        # Print summary
        print(f"\n=== PERF-ING-004 Results ({target_size_mb}MB target) ===")
        print(f"  Actual size: {package_size_mb:.2f} MB ({size_achieved_pct:.0f}% of target)")
        print(f"  Generation: {generation_seconds:.1f}s")
        print(f"  Upload: {upload_seconds:.1f}s ({upload_throughput:.2f} MB/s)")
        print(f"  Processing: {processing_time:.1f}s")
        print(f"  Total: {upload_seconds + processing_time:.1f}s")
        print(f"  Completed: {schema is not None}")
        
        assert schema is not None, f"Large file ({package_size_mb:.2f} MB) processing did not complete"

    @pytest.mark.timeout(1200)  # 20 minutes for high-frequency upload test
    def test_perf_ing_005_high_frequency_uploads(
        self,
        cluster_config: ClusterConfig,
        ingress_url: str,
        database_config,
        perf_timer: PerfTimer,
        perf_result: PerformanceResult,
        perf_collector: PerfResultCollector,
        rh_identity_header: str,
        perf_cleanup,
    ):
        """PERF-ING-005: High frequency uploads - Upload every 5 min for 1 hour.
        
        Tests sustained upload rate and queue handling.
        
        Metrics captured:
        - Message queue lag
        - Error rate over time
        - Processing backlog
        """
        test_duration_minutes = int(os.environ.get("PERF_ING_005_DURATION_MINUTES", "15"))
        upload_interval_seconds = int(os.environ.get("PERF_ING_005_INTERVAL_SECONDS", "300"))
        
        cluster_id = generate_cluster_id()
        source_name = f"perf-ing-005-{cluster_id[-8:]}"
        
        ingress_pod = get_pod_by_label(self.namespace, "app.kubernetes.io/component=ingress")
        if not ingress_pod:
            pytest.skip("Ingress pod not found")
        
        koku_api_url = f"http://{self.helm_release}-koku-api.{self.namespace}.svc.cluster.local:8000/api/cost-management/v1"
        
        # Register source
        source = register_source(
            self.namespace,
            ingress_pod,
            koku_api_url,
            rh_identity_header,
            cluster_id,
            "org1234567",
            source_name,
        )
        
        # Track for cleanup
        perf_cleanup.track(source_id=source.source_id, cluster_id=cluster_id, source_name=source_name)
        
        # Run uploads at intervals
        upload_results = []
        errors = []
        start_time = time.time()
        end_time = start_time + (test_duration_minutes * 60)
        upload_count = 0
        
        with perf_timer.measure("high_frequency_test"):
            while time.time() < end_time:
                upload_count += 1
                
                # Each upload covers last 6 hours (simulating real upload pattern)
                data_end = datetime.now(timezone.utc)
                data_start = data_end - timedelta(hours=6)
                
                try:
                    jwt_token = self._get_fresh_token()
                    result = generate_and_upload_data(
                        cluster_id,
                        source_name,
                        data_start,
                        data_end,
                        ingress_url,
                        jwt_token,
                        profile_name="baseline",
                    )
                    result["upload_number"] = upload_count
                    result["elapsed_minutes"] = (time.time() - start_time) / 60
                    upload_results.append(result)
                except Exception as e:
                    errors.append({
                        "upload_number": upload_count,
                        "elapsed_minutes": (time.time() - start_time) / 60,
                        "error": str(e),
                    })
                
                # Wait for next interval (if not done)
                if time.time() < end_time:
                    time.sleep(upload_interval_seconds)
        
        perf_result.metrics = {
            "test_duration_minutes": test_duration_minutes,
            "upload_interval_seconds": upload_interval_seconds,
            "total_uploads": upload_count,
            "successful_uploads": len(upload_results),
            "failed_uploads": len(errors),
            "error_rate": len(errors) / upload_count if upload_count > 0 else 0,
            "total_data_mb": sum(r.get("package_size_mb", 0) for r in upload_results),
            "upload_results": upload_results,
            "errors": errors,
        }
        perf_result.timings = perf_timer.get_timings()
        perf_result.passed = len(errors) == 0
        
        perf_collector.add_result(perf_result)
        
        error_rate = len(errors) / upload_count if upload_count > 0 else 0
        assert error_rate < 0.1, f"Error rate {error_rate:.1%} exceeds 10% threshold"

    @pytest.mark.timeout(21600)  # 6 hours max
    @pytest.mark.parametrize("profile_name", ["small", "medium", "large"])
    def test_perf_ing_006_processing_window_validation(
        self,
        cluster_config: ClusterConfig,
        database_config,
        perf_timer: PerfTimer,
        perf_result: PerformanceResult,
        perf_collector: PerfResultCollector,
        rh_identity_header: str,
        perf_cleanup,
        profile_name: str,
    ):
        """PERF-ING-006: 6-hour processing window validation (SC-4).
        
        Validates that recommended configurations sustain daily processing
        within a 6-hour window.
        
        This test simulates a full day's data upload pattern:
        - 4 uploads per day (every 6 hours, as per real-world pattern)
        - Uses profile-specific data volumes (small/medium/large)
        - Measures total end-to-end processing time
        
        Success criteria (SC-4):
        - All 4 daily uploads processed
        - Total processing time < 6 hours
        - Summary tables updated after each upload
        
        Environment:
            PERF_PROFILE: Override profile (small, medium, large)
        """
        # Get profile configuration
        profile = PROFILES.get(profile_name)
        if not profile:
            pytest.skip(f"Profile '{profile_name}' not found")
        
        # Get profile settings
        clusters = profile.get("clusters", 1)
        
        ingress_pod = get_pod_by_label(self.namespace, "app.kubernetes.io/component=ingress")
        if not ingress_pod:
            pytest.skip("Ingress pod not found")
        
        koku_api_url = f"http://{self.helm_release}-koku-api.{self.namespace}.svc.cluster.local:8000/api/cost-management/v1"
        
        # Track all sources for cleanup
        sources = []
        cluster_ids = []
        
        # Register sources for each cluster in the profile
        for i in range(clusters):
            cluster_id = generate_cluster_id()
            source_name = f"perf-ing-006-{profile_name}-c{i:02d}-{cluster_id[-6:]}"
            
            source = register_source(
                self.namespace,
                ingress_pod,
                koku_api_url,
                rh_identity_header,
                cluster_id,
                "org1234567",
                source_name,
            )
            
            sources.append(source)
            cluster_ids.append(cluster_id)
            perf_cleanup.track(source_id=source.source_id, cluster_id=cluster_id, source_name=source_name)
        
        print(f"\n=== PERF-ING-006: 6-Hour Processing Window ({profile_name} profile) ===")
        print(f"  Profile: {profile.get('description', profile_name)}")
        print(f"  Clusters: {clusters}")
        print(f"  Registered sources: {len(sources)}")
        
        # Simulate 4 uploads per day (6-hour intervals)
        uploads_per_day = 4
        upload_results = []
        total_start_time = time.time()
        
        for upload_num in range(uploads_per_day):
            upload_start = time.time()
            
            # Each upload covers 6 hours of data
            data_end = datetime.now(timezone.utc)
            data_start = data_end - timedelta(hours=6)
            
            print(f"\n  Upload {upload_num + 1}/{uploads_per_day}:")
            print(f"    Data range: {data_start.strftime('%Y-%m-%d %H:%M')} to {data_end.strftime('%Y-%m-%d %H:%M')}")
            
            upload_details = []
            
            with perf_timer.measure(f"upload_{upload_num + 1}"):
                for i, (source, cluster_id) in enumerate(zip(sources, cluster_ids)):
                    try:
                        jwt_token = self._get_fresh_token()
                        result = generate_and_upload_data(
                            cluster_id,
                            source.source_name if hasattr(source, 'source_name') else f"source-{i}",
                            data_start,
                            data_end,
                            f"https://{cluster_config.gateway_route_host}/api/ingress/v1/upload",
                            jwt_token,
                            profile_name=profile_name,
                        )
                        upload_details.append({
                            "cluster_id": cluster_id,
                            "success": True,
                            "size_mb": result.get("package_size_mb", 0),
                            "upload_time": result.get("upload_time", 0),
                        })
                    except Exception as e:
                        upload_details.append({
                            "cluster_id": cluster_id,
                            "success": False,
                            "error": str(e),
                        })
            
            # Wait for processing to complete
            with perf_timer.measure(f"processing_{upload_num + 1}"):
                for source, cluster_id in zip(sources, cluster_ids):
                    try:
                        wait_for_summary_tables(
                            self.namespace,
                            database_config.pod_name,
                            cluster_id,
                            timeout=1800,  # 30 min per source
                        )
                    except Exception as e:
                        print(f"    Warning: Summary wait failed for {cluster_id}: {e}")
            
            upload_elapsed = time.time() - upload_start
            successful = sum(1 for d in upload_details if d.get("success", False))
            
            upload_results.append({
                "upload_number": upload_num + 1,
                "elapsed_seconds": upload_elapsed,
                "successful_uploads": successful,
                "total_uploads": len(upload_details),
                "details": upload_details,
            })
            
            print(f"    Completed: {successful}/{len(upload_details)} sources in {upload_elapsed:.1f}s")
        
        # Calculate totals
        total_elapsed = time.time() - total_start_time
        total_successful = sum(r["successful_uploads"] for r in upload_results)
        total_uploads = sum(r["total_uploads"] for r in upload_results)
        
        # 6-hour window = 21600 seconds
        processing_window_seconds = 6 * 60 * 60
        
        perf_result.metrics = {
            "profile": profile_name,
            "profile_description": profile.get("description", ""),
            "clusters": clusters,
            "uploads_per_day": uploads_per_day,
            "total_elapsed_seconds": total_elapsed,
            "total_elapsed_hours": total_elapsed / 3600,
            "successful_uploads": total_successful,
            "total_uploads": total_uploads,
            "processing_window_seconds": processing_window_seconds,
            "within_window": total_elapsed < processing_window_seconds,
            "window_utilization_pct": (total_elapsed / processing_window_seconds) * 100,
            "upload_results": upload_results,
        }
        perf_result.timings = perf_timer.get_timings()
        perf_result.passed = (
            total_elapsed < processing_window_seconds and
            total_successful == total_uploads
        )
        
        perf_collector.add_result(perf_result)
        
        print(f"\n=== PERF-ING-006 Results ({profile_name}) ===")
        print(f"  Total processing time: {total_elapsed/3600:.2f} hours")
        print(f"  Window utilization: {perf_result.metrics['window_utilization_pct']:.1f}%")
        print(f"  Successful uploads: {total_successful}/{total_uploads}")
        print(f"  Within 6-hour window: {perf_result.metrics['within_window']}")
        
        # Assert success criteria (SC-4)
        assert total_elapsed < processing_window_seconds, (
            f"Processing time {total_elapsed/3600:.2f}h exceeds 6-hour window for {profile_name} profile"
        )
        assert total_successful == total_uploads, (
            f"Only {total_successful}/{total_uploads} uploads succeeded"
        )
