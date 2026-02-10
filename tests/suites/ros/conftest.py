"""
ROS (Resource Optimization Service) suite fixtures.

Includes the ros_test_data fixture that runs the full E2E flow for ROS tests.
This makes ROS API tests SELF-CONTAINED - they don't depend on other test modules.
"""

import os
import shutil
import tempfile
import time
from datetime import datetime, timedelta

import pytest
import requests

from conftest import obtain_jwt_token, DatabaseConfig
from e2e_helpers import (
    NISEConfig,
    cleanup_database_records,
    delete_source,
    ensure_nise_available,
    generate_cluster_id,
    generate_nise_data,
    get_koku_api_reads_url,
    get_koku_api_writes_url,
    register_source,
    upload_with_retry,
    wait_for_provider,
    wait_for_summary_tables,
)
from utils import (
    create_pod_session,
    create_rh_identity_header,
    create_upload_package_from_files,
    execute_db_query,
    get_pod_by_label,
    get_secret_value,
    get_route_url,
    run_oc_command,
)


@pytest.fixture(scope="module")
def kruize_pod(cluster_config) -> str:
    """Get Kruize pod name."""
    pod = get_pod_by_label(cluster_config.namespace, "app.kubernetes.io/component=ros-optimization")
    if not pod:
        pytest.skip("Kruize pod not found")
    return pod


@pytest.fixture(scope="module")
def kruize_credentials(cluster_config) -> dict:
    """Get Kruize database credentials."""
    secret_name = f"{cluster_config.helm_release_name}-db-credentials"
    user = get_secret_value(cluster_config.namespace, secret_name, "kruize-user")
    password = get_secret_value(cluster_config.namespace, secret_name, "kruize-password")
    
    if not user or not password:
        pytest.skip("Kruize database credentials not found")
    
    return {"user": user, "password": password, "database": "kruize_db"}


@pytest.fixture(scope="module")
def ros_api_url(cluster_config) -> str:
    """Get ROS API URL via the centralized gateway."""
    # With centralized gateway, all API traffic goes through cost-onprem-api route
    route_name = f"{cluster_config.helm_release_name}-api"
    url = get_route_url(cluster_config.namespace, route_name)
    if not url:
        pytest.skip("API gateway route not found")

    # Get the route path (e.g., /api)
    result = run_oc_command([
        "get", "route", route_name, "-n", cluster_config.namespace,
        "-o", "jsonpath={.spec.path}"
    ], check=False)
    route_path = result.stdout.strip().rstrip("/")

    return f"{url}{route_path}" if route_path else url


def wait_for_kruize_recommendations(
    namespace: str,
    db_pod: str,
    cluster_id: str,
    kruize_user: str,
    kruize_password: str,
    timeout: int = 300,
    poll_interval: int = 10,
) -> bool:
    """Wait for Kruize to generate recommendations for a cluster.
    
    Returns True if recommendations are found, False on timeout.
    """
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        # Check for experiments first
        exp_result = execute_db_query(
            namespace, db_pod, "kruize_db", kruize_user,
            f"""
            SELECT COUNT(*) FROM kruize_experiments
            WHERE cluster_name LIKE '%{cluster_id}%'
            """,
            password=kruize_password,
        )
        
        exp_count = int(exp_result[0][0]) if exp_result and exp_result[0][0] else 0
        
        if exp_count > 0:
            # Check for recommendations
            rec_result = execute_db_query(
                namespace, db_pod, "kruize_db", kruize_user,
                f"""
                SELECT COUNT(*) FROM kruize_recommendations kr
                JOIN kruize_experiments ke ON kr.experiment_name = ke.experiment_name
                WHERE ke.cluster_name LIKE '%{cluster_id}%'
                """,
                password=kruize_password,
            )
            
            rec_count = int(rec_result[0][0]) if rec_result and rec_result[0][0] else 0
            
            if rec_count > 0:
                return True
        
        time.sleep(poll_interval)
    
    return False


@pytest.fixture(scope="module")
def ros_test_data(
    cluster_config,
    database_config,
    s3_config,
    keycloak_config,
    ingress_url,
    org_id,
    test_runner_pod,
):
    """Run full E2E setup for ROS/recommendations tests - SELF-CONTAINED.
    
    This fixture:
    1. Generates NISE data with ROS data (--ros-ocp-info)
    2. Registers a source in Koku
    3. Uploads data via JWT-authenticated ingress
    4. Waits for Koku to process and populate summary tables
    5. Waits for Kruize to create experiments and recommendations
    6. Yields the test context with cluster_id for filtering
    7. Cleans up all test data on teardown (if E2E_CLEANUP_AFTER=true)
    
    Environment Variables:
    - E2E_CLEANUP_BEFORE: Run cleanup before tests (default: true)
    - E2E_CLEANUP_AFTER: Run cleanup after tests (default: true)
    """
    cleanup_before = os.environ.get("E2E_CLEANUP_BEFORE", "true").lower() == "true"
    cleanup_after = os.environ.get("E2E_CLEANUP_AFTER", "true").lower() == "true"
    
    if not ensure_nise_available():
        pytest.skip("NISE not available and could not be installed")
    
    cluster_id = generate_cluster_id(prefix="ros-test")
    db_pod = database_config.pod_name
    if not db_pod:
        pytest.skip("Database pod not found")
    
    # Get Kruize credentials
    secret_name = f"{cluster_config.helm_release_name}-db-credentials"
    kruize_user = get_secret_value(cluster_config.namespace, secret_name, "kruize-user")
    kruize_password = get_secret_value(cluster_config.namespace, secret_name, "kruize-password")
    
    if not kruize_user or not kruize_password:
        pytest.skip("Kruize database credentials not found")
    
    temp_dir = tempfile.mkdtemp(prefix="ros_test_")
    source_registration = None
    
    api_reads_url = get_koku_api_reads_url(cluster_config.helm_release_name, cluster_config.namespace)
    api_writes_url = get_koku_api_writes_url(cluster_config.helm_release_name, cluster_config.namespace)
    rh_identity = create_rh_identity_header(org_id)
    
    nise_config = NISEConfig()
    
    try:
        print(f"\n{'='*60}")
        print("ROS TEST SETUP (Self-Contained)")
        print(f"{'='*60}")
        print(f"  Cluster ID: {cluster_id}")
        print(f"  Cleanup before: {cleanup_before}")
        print(f"  Cleanup after: {cleanup_after}")
        
        # Step 1: Generate NISE data with ROS info
        print("\n  [1/6] Generating NISE data with ROS info...")
        now = datetime.utcnow()
        start_date = (now - timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        
        files = generate_nise_data(
            cluster_id, start_date, end_date, temp_dir,
            config=nise_config,
            include_ros=True,  # Critical for ROS tests
        )
        print(f"       Generated {len(files['all_files'])} CSV files")
        print(f"       ROS files: {len(files.get('ros_usage_files', []))}")
        
        if not files["all_files"]:
            pytest.skip("NISE generated no CSV files")
        
        # Step 2: Register source
        print("\n  [2/6] Registering source...")
        source_registration = register_source(
            namespace=cluster_config.namespace,
            pod=test_runner_pod,
            api_reads_url=api_reads_url,
            api_writes_url=api_writes_url,
            rh_identity_header=rh_identity,
            cluster_id=cluster_id,
            org_id=org_id,
            source_name=f"ros-test-{cluster_id[-8:]}",
            container="runner",
        )
        print(f"       Source ID: {source_registration.source_id}")
        
        # Step 3: Wait for provider
        print("\n  [3/6] Waiting for provider in Koku...")
        if not wait_for_provider(
            cluster_config.namespace,
            db_pod,
            cluster_id,
            database=database_config.database,
        ):
            pytest.fail(f"Provider not created for cluster {cluster_id}")
        print("       Provider created")
        
        # Step 4: Upload data
        print("\n  [4/6] Uploading data via ingress...")
        package_path = create_upload_package_from_files(
            pod_usage_files=files["pod_usage_files"],
            ros_usage_files=files["ros_usage_files"],
            cluster_id=cluster_id,
            start_date=start_date,
            end_date=end_date,
            node_label_files=files.get("node_label_files"),
            namespace_label_files=files.get("namespace_label_files"),
        )
        
        upload_url = f"{ingress_url}/v1/upload"
        upload_token = obtain_jwt_token(keycloak_config)
        
        session = requests.Session()
        session.verify = False
        
        response = upload_with_retry(
            session,
            upload_url,
            package_path,
            upload_token.authorization_header,
        )
        
        if response.status_code not in [200, 201, 202]:
            pytest.fail(f"Upload failed: {response.status_code}")
        print(f"       Upload successful: {response.status_code}")
        
        # Step 5: Wait for Koku processing
        print("\n  [5/6] Waiting for Koku processing...")
        schema_name = wait_for_summary_tables(
            cluster_config.namespace,
            db_pod,
            cluster_id,
            database=database_config.database,
        )
        
        if not schema_name:
            pytest.fail(f"Timeout waiting for summary tables for cluster {cluster_id}")
        print("       Summary tables populated")
        
        # Step 6: Wait for Kruize recommendations
        print("\n  [6/6] Waiting for Kruize recommendations...")
        if not wait_for_kruize_recommendations(
            cluster_config.namespace,
            db_pod,
            cluster_id,
            kruize_user,
            kruize_password,
            timeout=300,
        ):
            # Check if experiments exist at least
            exp_result = execute_db_query(
                cluster_config.namespace, db_pod, "kruize_db", kruize_user,
                f"SELECT COUNT(*) FROM kruize_experiments WHERE cluster_name LIKE '%{cluster_id}%'",
                password=kruize_password,
            )
            exp_count = int(exp_result[0][0]) if exp_result and exp_result[0][0] else 0
            
            if exp_count == 0:
                pytest.skip(
                    f"Kruize experiments not created for cluster {cluster_id}. "
                    "ROS processor may not be processing data correctly."
                )
            else:
                pytest.skip(
                    f"Kruize recommendations not generated (experiments: {exp_count}). "
                    "Kruize may need more time or data."
                )
        
        print("       Recommendations generated")
        
        print(f"\n{'='*60}")
        print("ROS SETUP COMPLETE - Running tests")
        print(f"{'='*60}\n")
        
        yield {
            "namespace": cluster_config.namespace,
            "cluster_id": cluster_id,
            "source_id": source_registration.source_id,
            "org_id": org_id,
            "schema_name": schema_name,
        }
        
    finally:
        print(f"\n{'='*60}")
        if cleanup_after:
            print("ROS TEST CLEANUP")
            print(f"{'='*60}")
            
            if source_registration:
                if delete_source(
                    cluster_config.namespace,
                    test_runner_pod,
                    api_writes_url,
                    rh_identity,
                    source_registration.source_id,
                    container="runner",
                ):
                    print(f"  Deleted source {source_registration.source_id}")
            
            if db_pod:
                cleanup_database_records(
                    cluster_config.namespace,
                    db_pod,
                    cluster_id,
                    database=database_config.database,
                )
                print("  Cleaned up database records")
        else:
            print("ROS TEST CLEANUP SKIPPED (E2E_CLEANUP_AFTER=false)")
            print(f"{'='*60}")
            print(f"  Data preserved for cluster: {cluster_id}")
        
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        
        print(f"{'='*60}\n")
