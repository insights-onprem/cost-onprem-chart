"""
E2E suite fixtures.

Provides shared fixtures for E2E tests:
- e2e_cluster_id: Unique cluster ID for test run
- e2e_test_data: Generated NISE test data
- registered_source: Source registration with cleanup
- koku_api_reads_url: Internal Koku API reads URL
- koku_api_writes_url: Internal Koku API writes URL
- rh_identity_header: Base64-encoded X-Rh-Identity header

All internal API calls use the dedicated test_runner_pod fixture from the root
conftest.py, ensuring isolation from application pods.

Database configuration is obtained from the database_config fixture which
dynamically detects the actual database name and user from the deployment.
"""

import json
import os
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pytest

from utils import (
    create_pod_session,
    create_rh_identity_header,
    exec_in_pod,
    get_pod_by_label,
)
from cleanup import full_cleanup
from e2e_helpers import (
    E2E_CLUSTER_PREFIX,
    NISEConfig,
    SourceRegistration,
    is_nise_available,
    install_nise,
    ensure_nise_available,
    generate_nise_data,
    generate_dynamic_static_report,
    get_koku_api_reads_url,
    get_koku_api_writes_url,
    register_source,
    delete_source,
)


# =============================================================================
# Cluster ID and Identity Fixtures
# =============================================================================


@pytest.fixture(scope="class")
def e2e_cluster_id() -> str:
    """Generate a unique cluster ID for this E2E test run.
    
    Format: e2e-pytest-{timestamp}-{uuid8}
    Example: e2e-pytest-1706745600-abc12345
    """
    return f"{E2E_CLUSTER_PREFIX}{int(time.time())}-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="class")
def rh_identity_header(org_id: str) -> str:
    """Create base64-encoded X-Rh-Identity header for internal API calls."""
    return create_rh_identity_header(org_id=org_id)


# =============================================================================
# Internal API URL Fixtures
# =============================================================================


@pytest.fixture(scope="class")
def koku_api_reads_url(cluster_config) -> str:
    """Internal Koku API reads URL for GET operations."""
    return get_koku_api_reads_url(
        cluster_config.helm_release_name,
        cluster_config.namespace,
    )


@pytest.fixture(scope="class")
def koku_api_writes_url(cluster_config) -> str:
    """Internal Koku API writes URL for POST/PUT/DELETE operations."""
    return get_koku_api_writes_url(
        cluster_config.helm_release_name,
        cluster_config.namespace,
    )


# =============================================================================
# Test Data Generation Fixtures
# =============================================================================


@pytest.fixture(scope="class")
def e2e_test_data(e2e_cluster_id: str):
    """Generate test data for E2E validation using NISE.
    
    By default, uses NISE to generate proper OCP cost data format.
    Set E2E_USE_SIMPLE_DATA=true to use simplified format (may not populate summary tables).
    
    Returns dict with:
        - csv_content: CSV content for upload
        - csv_files: List of generated CSV file paths
        - pod_usage_files: Pod usage CSV files (for Koku)
        - ros_usage_files: ROS usage CSV files (for ROS processor)
        - node_label_files: Node label CSV files
        - namespace_label_files: Namespace label CSV files
        - cluster_id: The cluster ID
        - start_date: Start date for the data
        - end_date: End date for the data
        - generator: "nise" or "simple"
        - temp_dir: Temporary directory (caller should clean up)
    """
    use_simple = os.environ.get("E2E_USE_SIMPLE_DATA", "false").lower() == "true"
    
    if use_simple:
        print("\n  ⚠️  Using SIMPLE data format (E2E_USE_SIMPLE_DATA=true)")
        print("     Warning: Summary tables may not be populated with this format")
        return _generate_simple_data(e2e_cluster_id)
    
    # Try NISE first
    if not is_nise_available():
        print("\n  NISE not found, attempting to install...")
        if not install_nise():
            print("  ⚠️  NISE installation failed, falling back to simple data")
            data = _generate_simple_data(e2e_cluster_id)
            data["nise_install_failed"] = True
            return data
    
    # Generate NISE data
    print(f"\n  Generating OCP data with NISE for cluster: {e2e_cluster_id}")
    
    now = datetime.utcnow()
    # Use current date range - this is CRITICAL for Koku to process summaries
    start_date = now - timedelta(days=1)
    end_date = now + timedelta(days=1)
    
    # Create temp directory for NISE output
    temp_dir = tempfile.mkdtemp(prefix="e2e-nise-")
    
    try:
        nise_files = generate_nise_data(
            cluster_id=e2e_cluster_id,
            start_date=start_date,
            end_date=end_date,
            output_dir=temp_dir,
            include_ros=True,
        )
        
        pod_usage_count = len(nise_files.get('pod_usage_files', []))
        total_count = len(nise_files.get('all_files', []))
        print(f"  ✅ NISE generated {total_count} CSV files ({pod_usage_count} pod_usage)")
        
        # Read the pod_usage CSV file for upload (required for summary tables)
        pod_usage_files = nise_files.get("pod_usage_files", [])
        csv_files = nise_files.get("all_files", [])
        
        csv_content = None
        if pod_usage_files:
            with open(pod_usage_files[0], "r") as f:
                csv_content = f.read()
            print(f"  📄 Using pod_usage file: {Path(pod_usage_files[0]).name}")
        elif csv_files:
            with open(csv_files[0], "r") as f:
                csv_content = f.read()
            print(f"  ⚠️  No pod_usage files, using: {Path(csv_files[0]).name}")
        else:
            print("  ⚠️  NISE generated no CSV files, falling back to simple data")
            shutil.rmtree(temp_dir, ignore_errors=True)
            return _generate_simple_data(e2e_cluster_id)
        
        return {
            "csv_content": csv_content,
            "csv_files": csv_files,
            "pod_usage_files": pod_usage_files,
            "ros_usage_files": nise_files.get("ros_usage_files", []),
            "node_label_files": nise_files.get("node_label_files", []),
            "namespace_label_files": nise_files.get("namespace_label_files", []),
            "cluster_id": e2e_cluster_id,
            "start_date": start_date,
            "end_date": end_date,
            "generator": "nise",
            "temp_dir": temp_dir,
        }
        
    except Exception as e:
        print(f"  ⚠️  NISE generation failed: {e}")
        print("     Falling back to simple data format")
        shutil.rmtree(temp_dir, ignore_errors=True)
        data = _generate_simple_data(e2e_cluster_id)
        data["nise_error"] = str(e)
        return data


def _generate_simple_data(cluster_id: str) -> dict:
    """Generate simple OCP data (legacy format - may not populate summary tables).
    
    WARNING: This format may not be fully processed by Koku. Use NISE for
    complete E2E validation.
    """
    now = datetime.utcnow()
    
    # Generate 4 intervals of data
    intervals = []
    for i in range(4):
        start = now - timedelta(minutes=75 - (i * 15))
        end = now - timedelta(minutes=60 - (i * 15))
        intervals.append((start, end))
    
    # CSV header matching OCP ROS format
    header = (
        "report_period_start,report_period_end,interval_start,interval_end,"
        "container_name,pod,owner_name,owner_kind,workload,workload_type,"
        "namespace,image_name,node,resource_id,"
        "cpu_request_container_avg,cpu_request_container_sum,"
        "cpu_limit_container_avg,cpu_limit_container_sum,"
        "cpu_usage_container_avg,cpu_usage_container_min,cpu_usage_container_max,cpu_usage_container_sum,"
        "cpu_throttle_container_avg,cpu_throttle_container_max,cpu_throttle_container_sum,"
        "memory_request_container_avg,memory_request_container_sum,"
        "memory_limit_container_avg,memory_limit_container_sum,"
        "memory_usage_container_avg,memory_usage_container_min,memory_usage_container_max,memory_usage_container_sum,"
        "memory_rss_usage_container_avg,memory_rss_usage_container_min,memory_rss_usage_container_max,memory_rss_usage_container_sum"
    )
    
    rows = [header]
    date_str = now.strftime("%Y-%m-%d")
    
    cpu_usages = [0.247832, 0.265423, 0.289567, 0.234567]
    mem_usages = [413587266, 427891456, 445678901, 398765432]
    
    for i, (start, end) in enumerate(intervals):
        start_str = start.strftime("%Y-%m-%d %H:%M:%S -0000 UTC")
        end_str = end.strftime("%Y-%m-%d %H:%M:%S -0000 UTC")
        cpu = cpu_usages[i]
        mem = mem_usages[i]
        
        row = (
            f"{date_str},{date_str},{start_str},{end_str},"
            f"test-container,test-pod-{cluster_id[-8:]},test-deployment,Deployment,test-workload,deployment,"
            f"test-namespace,quay.io/test/image:latest,worker-node-1,resource-{cluster_id[-8:]},"
            f"0.5,0.5,1.0,1.0,"
            f"{cpu},{cpu*0.75},{cpu*1.3},{cpu},"
            f"0.001,0.002,0.001,"
            f"536870912,536870912,1073741824,1073741824,"
            f"{mem},{mem*0.99},{mem*1.02},{mem},"
            f"{mem*0.95},{mem*0.94},{mem*0.96},{mem*0.95}"
        )
        rows.append(row)
    
    start_date = now - timedelta(days=1)
    end_date = now
    
    return {
        "csv_content": "\n".join(rows),
        "csv_files": [],
        "pod_usage_files": [],
        "ros_usage_files": [],
        "node_label_files": [],
        "namespace_label_files": [],
        "cluster_id": cluster_id,
        "start_date": start_date,
        "end_date": end_date,
        "generator": "simple",
        "warning": (
            "Simple data format may not populate Koku summary tables. "
            "Use NISE for complete E2E validation."
        ),
    }


# =============================================================================
# Source Registration Fixture
# =============================================================================


@pytest.fixture(scope="class")
def registered_source(
    cluster_config,
    database_config,
    org_id: str,
    e2e_cluster_id: str,
    s3_config,
    koku_api_reads_url: str,
    koku_api_writes_url: str,
    test_runner_pod: str,
    rh_identity_header: str,
):
    """Register a source for E2E testing with cleanup before and after.
    
    Uses the dedicated test_runner_pod for all internal API calls, ensuring
    isolation from application pods.
    
    Database configuration is obtained from the database_config fixture which
    dynamically detects the actual database name and user from the deployment.
    
    Cleanup includes:
      - S3 data files from previous runs
      - Database processing records
      - Optionally Valkey cache and listener restart (if E2E_RESTART_SERVICES=1)
    
    Yields dict with:
        - source_id: ID of the registered source
        - source_name: Name of the source
        - cluster_id: Cluster ID
        - org_id: Organization ID
        - test_runner_pod: Pod name for curl commands
        - koku_api_reads_url: Internal API reads URL
        - koku_api_writes_url: Internal API writes URL
        - rh_identity_header: Base64-encoded identity header
        - db_pod: Database pod name
        - database: Database name
        - db_user: Database user
        - s3_config_dict: S3 configuration dict
    """
    # Check cleanup settings
    cleanup_before = os.environ.get("E2E_CLEANUP_BEFORE", "true").lower() == "true"
    cleanup_after = os.environ.get("E2E_CLEANUP_AFTER", "true").lower() == "true"
    restart_services = os.environ.get("E2E_RESTART_SERVICES", "false").lower() == "true"
    
    # Get database pod from database_config fixture
    db_pod = database_config.pod_name
    
    # Prepare S3 config dict for cleanup
    s3_config_dict = None
    if s3_config:
        s3_config_dict = {
            "endpoint": s3_config.endpoint,
            "access_key": s3_config.access_key,
            "secret_key": s3_config.secret_key,
            "bucket": s3_config.bucket,
            "verify_ssl": s3_config.verify_ssl,
        }
    
    # Pre-test cleanup
    if cleanup_before and db_pod:
        print("\n" + "=" * 60)
        print("PRE-TEST CLEANUP")
        print("=" * 60)
        full_cleanup(
            namespace=cluster_config.namespace,
            db_pod=db_pod,
            org_id=org_id,
            s3_config=s3_config_dict,
            cluster_id=None,  # Clean all clusters for this org
            restart_services=restart_services,
            verbose=True,
            database=database_config.database,
            db_user=database_config.user,
        )
    
    # Delete any existing e2e sources using test_runner_pod
    print(f"  🔍 Checking for existing e2e sources...")
    session = create_pod_session(
        namespace=cluster_config.namespace,
        pod=test_runner_pod,
        container="runner",
        headers={
            "X-Rh-Identity": rh_identity_header,
            "Content-Type": "application/json",
        },
    )
    
    try:
        response = session.get(f"{koku_api_reads_url}/sources")
        if response.ok:
            existing_sources = response.json()
            for existing in existing_sources.get("data", []):
                existing_name = existing.get("name", "")
                existing_id = existing.get("id")
                if existing_id and existing_name.startswith("e2e-source-"):
                    print(f"     🗑️  Deleting existing source '{existing_name}' (id={existing_id})...")
                    session.delete(f"{koku_api_writes_url}/sources/{existing_id}")
                    time.sleep(2)
    except Exception:
        pass
    
    # Register the source using test_runner_pod
    print(f"  📝 Registering source for cluster: {e2e_cluster_id}")
    registration = register_source(
        namespace=cluster_config.namespace,
        pod=test_runner_pod,
        api_reads_url=koku_api_reads_url,
        api_writes_url=koku_api_writes_url,
        rh_identity_header=rh_identity_header,
        cluster_id=e2e_cluster_id,
        org_id=org_id,
        container="runner",
    )
    
    print(f"     ✅ Source created: {registration.source_name} (id={registration.source_id})")
    
    yield {
        "source_id": registration.source_id,
        "source_name": registration.source_name,
        "cluster_id": e2e_cluster_id,
        "org_id": org_id,
        "test_runner_pod": test_runner_pod,
        "koku_api_reads_url": koku_api_reads_url,
        "koku_api_writes_url": koku_api_writes_url,
        "rh_identity_header": rh_identity_header,
        "db_pod": db_pod,
        "database": database_config.database,
        "db_user": database_config.user,
        "s3_config_dict": s3_config_dict,
    }
    
    # Post-test cleanup
    if cleanup_after:
        print("\n" + "=" * 60)
        print("POST-TEST CLEANUP")
        print("=" * 60)
        
        # Delete the source using test_runner_pod
        print("  🗑️  Deleting test source...")
        delete_source(
            namespace=cluster_config.namespace,
            pod=test_runner_pod,
            api_writes_url=koku_api_writes_url,
            rh_identity_header=rh_identity_header,
            source_id=registration.source_id,
            container="runner",
        )
        print(f"     ✅ Deleted source {registration.source_id}")
        
        # Full cleanup
        if db_pod:
            full_cleanup(
                namespace=cluster_config.namespace,
                db_pod=db_pod,
                org_id=org_id,
                s3_config=s3_config_dict,
                cluster_id=e2e_cluster_id,
                restart_services=False,
                verbose=True,
                database=database_config.database,
                db_user=database_config.user,
            )
    else:
        print("\n" + "=" * 60)
        print("POST-TEST CLEANUP SKIPPED (E2E_CLEANUP_AFTER=false)")
        print("=" * 60)
        print(f"  Data preserved for cluster: {e2e_cluster_id}")
        print(f"  Source ID: {registration.source_id}")
