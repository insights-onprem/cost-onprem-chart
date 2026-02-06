"""
Complete end-to-end data flow tests.

These tests validate the entire pipeline:
  Generated Data → Upload via Ingress (JWT) → Koku Processing → ROS/Kruize

This is the canonical E2E test that covers the full production data flow.

Data Generation Options:
  - NISE (default): Uses koku-nise to generate proper OCP cost data format
  - Simple (fallback): Uses simplified CSV format (may not populate summary tables)

Environment Variables:
  - E2E_USE_SIMPLE_DATA=true: Use simple CSV format instead of NISE
  - E2E_NISE_STATIC_REPORT: Path to custom NISE static report file
  - E2E_CLEANUP_BEFORE=true/false: Run cleanup before tests (default: true)
  - E2E_CLEANUP_AFTER=true/false: Run cleanup after tests (default: true)
  - E2E_RESTART_SERVICES=true: Restart Valkey/listener during cleanup (slower but thorough)

Test Steps:
  1. Source Registration - Register OCP source via Koku Sources API
  2. Provider Creation - Verify provider created in Koku database
  3. Data Upload - Upload test data via JWT-authenticated ingress
  4. Manifest Creation - Verify manifest created in Koku database
  5. File Processing - Verify files processed by MASU
  6. Summary Tables - Verify summary tables populated
  7. Kruize Experiments - Verify Kruize experiments created
  8. Recommendations - Verify recommendations generated
  9. API Access - Verify recommendations accessible via API
"""

import os
import shutil

import pytest
import requests

from utils import (
    create_upload_package,
    create_upload_package_from_files,
    execute_db_query,
    get_pod_by_label,
    get_secret_value,
    wait_for_condition,
    run_oc_command,
)
from conftest import obtain_jwt_token


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.slow
class TestCompleteDataFlow:
    """
    End-to-end test of the complete data flow.
    
    This test class validates the FULL production pipeline:
    
    1. Source Registration:
       - Register OCP source via Koku Sources API
       - Verify provider created in Koku database
    
    2. Data Upload (via Ingress):
       - Generate test CSV data with realistic metrics
       - Package into tar.gz with manifest
       - Upload via JWT-authenticated ingress endpoint
       - Verify ingress stores in S3 and publishes to Kafka
    
    3. Koku Processing:
       - Koku Listener consumes from platform.upload.announce
       - MASU processes cost data from S3
       - Manifest and file status tracked in database
       - Summary tables populated with aggregated data
    
    4. ROS Pipeline:
       - Koku copies ROS data to ros-data bucket
       - Koku emits events to hccm.ros.events topic
       - ROS Processor consumes and sends to Kruize
    
    5. Recommendations:
       - Kruize generates optimization recommendations
       - Recommendations accessible via JWT-authenticated API
    """

    @pytest.fixture(scope="class")
    def e2e_cluster_id(self) -> str:
        """Generate a unique cluster ID for this E2E test run."""
        import uuid
        return f"e2e-pytest-{int(time.time())}-{uuid.uuid4().hex[:8]}"

    @pytest.fixture(scope="class")
    def e2e_test_data(self, e2e_cluster_id: str) -> dict:
        """Generate test data for E2E validation.
        
        By default, uses NISE to generate proper OCP cost data format.
        Set E2E_USE_SIMPLE_DATA=true to use simplified format (may not populate summary tables).
        """
        use_simple = os.environ.get("E2E_USE_SIMPLE_DATA", "false").lower() == "true"
        
        if use_simple:
            print("\n  ⚠️  Using SIMPLE data format (E2E_USE_SIMPLE_DATA=true)")
            print("     Warning: Summary tables may not be populated with this format")
            return generate_simple_ocp_data(e2e_cluster_id)
        
        # Try NISE first
        if not is_nise_available():
            print("\n  NISE not found, attempting to install...")
            if not install_nise():
                print("  ⚠️  NISE installation failed, falling back to simple data")
                print("     Warning: Summary tables may not be populated with this format")
                data = generate_simple_ocp_data(e2e_cluster_id)
                data["nise_install_failed"] = True
                return data
        
        # Generate NISE data
        print(f"\n  Generating OCP data with NISE for cluster: {e2e_cluster_id}")
        
        now = datetime.utcnow()
        # Use current date range - this is CRITICAL for Koku to process summaries
        # Data must be in the current billing period
        start_date = now - timedelta(days=1)
        end_date = now + timedelta(days=1)
        
        # Create temp directory for NISE output
        temp_dir = tempfile.mkdtemp(prefix="e2e-nise-")
        
        # Check for custom static report (only use if explicitly set)
        # NOTE: Do NOT use the default static report file as it has hardcoded dates
        # that will cause "missing start or end dates" errors in Koku
        static_report = os.environ.get("E2E_NISE_STATIC_REPORT")
        
        try:
            nise_data = generate_nise_ocp_data(
                cluster_id=e2e_cluster_id,
                start_date=start_date,
                end_date=end_date,
                output_dir=temp_dir,
                static_report_file=static_report,  # Will generate dynamic report if None
            )
            
            pod_usage_count = len(nise_data.get('pod_usage_files', []))
            total_count = len(nise_data.get('csv_files', []))
            print(f"  ✅ NISE generated {total_count} CSV files ({pod_usage_count} pod_usage)")
            
            # Read the pod_usage CSV file for upload (required for summary tables)
            pod_usage_files = nise_data.get("pod_usage_files", [])
            csv_files = nise_data.get("csv_files", [])
            
            if pod_usage_files:
                # Prefer pod_usage files - these are required for summary tables
                with open(pod_usage_files[0], "r") as f:
                    csv_content = f.read()
                nise_data["csv_content"] = csv_content
                print(f"  📄 Using pod_usage file: {Path(pod_usage_files[0]).name}")
            elif csv_files:
                # Fall back to any CSV file
                with open(csv_files[0], "r") as f:
                    csv_content = f.read()
                nise_data["csv_content"] = csv_content
                print(f"  ⚠️  No pod_usage files, using: {Path(csv_files[0]).name}")
            else:
                # No CSV files generated - fall back to simple
                print("  ⚠️  NISE generated no CSV files, falling back to simple data")
                shutil.rmtree(temp_dir, ignore_errors=True)
                return generate_simple_ocp_data(e2e_cluster_id)
            
            nise_data["temp_dir"] = temp_dir
            return nise_data
            
        except Exception as e:
            print(f"  ⚠️  NISE generation failed: {e}")
            print("     Falling back to simple data format")
            shutil.rmtree(temp_dir, ignore_errors=True)
            data = generate_simple_ocp_data(e2e_cluster_id)
            data["nise_error"] = str(e)
            return data

    @pytest.fixture(scope="class")
    def registered_source(
        self,
        cluster_config,
        org_id: str,
        e2e_cluster_id: str,
        s3_config,
        koku_api_url: str,
        ingress_pod: str,
        rh_identity_header: str,
    ):
        """Register a source for E2E testing with cleanup before and after.
        
        Cleanup includes:
          - S3 data files from previous runs
          - Database processing records
          - Optionally Valkey cache and listener restart (if E2E_RESTART_SERVICES=1)
        """
        from utils import exec_in_pod, get_pod_by_label
        
        # Check cleanup settings
        cleanup_before = os.environ.get("E2E_CLEANUP_BEFORE", "true").lower() == "true"
        cleanup_after = os.environ.get("E2E_CLEANUP_AFTER", "true").lower() == "true"
        restart_services = os.environ.get("E2E_RESTART_SERVICES", "false").lower() == "true"
        
        # Get database pod for cleanup
        db_pod = get_pod_by_label(
            cluster_config.namespace,
            "app.kubernetes.io/component=database"
        )
        
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
            )
        
        # Get source type ID from Koku (GET - use reads)
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-w", "\n__HTTP_CODE__:%{http_code}",
                f"{koku_api_url}/source_types",
                "-H", "Content-Type: application/json",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )
        
        if not result:
            pytest.fail(
                f"Could not get source types - exec_in_pod returned None. "
                f"ingress_pod={ingress_pod}, url={koku_api_url}/source_types"
            )
        
        # Parse response and status code
        if "__HTTP_CODE__:" in result:
            body, http_code = result.rsplit("__HTTP_CODE__:", 1)
            result = body.strip()
            http_code = http_code.strip()
            if http_code != "200":
                pytest.fail(
                    f"Source types request failed with HTTP {http_code}. "
                    f"Response: {result[:500]}"
                )
        
        if not result:
            pytest.fail("Source types returned empty response")
        
        source_types = json.loads(result)
        ocp_type_id = None
        for st in source_types.get("data", []):
            if st.get("name") == "openshift":
                ocp_type_id = st.get("id")
                break
        
        if not ocp_type_id:
            pytest.skip("OpenShift source type not found")
        
        # Get application type ID from Koku (GET - use reads)
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", f"{koku_api_url}/application_types",
                "-H", "Content-Type: application/json",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )
        
        app_types = json.loads(result)
        cost_mgmt_app_id = None
        for at in app_types.get("data", []):
            if at.get("name") == "/insights/platform/cost-management":
                cost_mgmt_app_id = at.get("id")
                break
        
        # Create source with unique name
        source_name = f"e2e-source-{e2e_cluster_id[-8:]}"
        
        # Check for existing e2e sources and delete them
        print(f"  🔍 Checking for existing e2e sources...")
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", f"{koku_api_url}/sources",
                "-H", "Content-Type: application/json",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )
        
        if result:
            try:
                existing_sources = json.loads(result)
                for existing in existing_sources.get("data", []):
                    existing_name = existing.get("name", "")
                    existing_id = existing.get("id")
                    # Delete any e2e test sources (DELETE - use writes)
                    if existing_id and existing_name.startswith("e2e-source-"):
                        print(f"     🗑️  Deleting existing source '{existing_name}' (id={existing_id})...")
                        exec_in_pod(
                            cluster_config.namespace,
                            ingress_pod,
                            [
                                "curl", "-s", "-X", "DELETE",
                                f"{koku_api_url}/sources/{existing_id}",
                                "-H", f"X-Rh-Identity: {rh_identity_header}",
                            ],
                            container="ingress",
                        )
                        time.sleep(2)  # Brief pause for deletion to propagate
            except (json.JSONDecodeError, TypeError):
                pass  # No existing sources or error in response
        
        # Create the new source with retry logic
        # On first run for a new org, the tenant schema creation can be slow
        # which may cause the first request to fail or timeout
        payload = json.dumps({
            "name": source_name,
            "source_type_id": ocp_type_id,
            "source_ref": e2e_cluster_id,
        })
        
        print(f"  📝 Creating source: {source_name}")
        print(f"     Cluster ID: {e2e_cluster_id}")
        print(f"     Source Type ID: {ocp_type_id}")
        
        # Retry logic for source creation
        # First request may fail due to tenant schema creation (slow operation)
        max_retries = 5
        retry_delay = 5  # seconds
        source_id = None
        last_error = None
        
        for attempt in range(max_retries):
            if attempt > 0:
                print(f"     ⏳ Retry {attempt}/{max_retries - 1} after {retry_delay}s delay...")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30)  # Exponential backoff, max 30s
            
            # POST /sources - use writes
            result = exec_in_pod(
                cluster_config.namespace,
                ingress_pod,
                [
                    "curl", "-s", "-w", "\n__HTTP_CODE__:%{http_code}", "-X", "POST",
                    f"{koku_api_url}/sources",
                    "-H", "Content-Type: application/json",
                    "-H", f"X-Rh-Identity: {rh_identity_header}",
                    "-d", payload,
                ],
                container="ingress",
                timeout=120,  # Longer timeout for first request (schema creation)
            )
            
            if not result:
                last_error = "exec_in_pod returned None (curl failed or timed out)"
                print(f"     ⚠️  Attempt {attempt + 1} failed: {last_error}")
                continue
            
            # Parse response and status code
            http_code = None
            if "__HTTP_CODE__:" in result:
                body, http_code = result.rsplit("__HTTP_CODE__:", 1)
                result = body.strip()
                http_code = http_code.strip()
            
            if http_code and http_code not in ("200", "201"):
                last_error = f"HTTP {http_code}: {result[:200]}"
                print(f"     ⚠️  Attempt {attempt + 1} failed: {last_error}")
                # 5xx errors might be transient, retry
                if http_code.startswith("5"):
                    continue
                # 4xx errors (except 409 conflict) are not retryable
                if http_code != "409":
                    break
                # 409 might mean source already exists, try to get it
                continue
            
            try:
                source_data = json.loads(result)
                source_id = source_data.get("id")
                if source_id:
                    print(f"     ✅ Source created successfully (id={source_id})")
                    break
                else:
                    last_error = f"No 'id' in response: {result[:200]}"
                    print(f"     ⚠️  Attempt {attempt + 1} failed: {last_error}")
            except json.JSONDecodeError as e:
                last_error = f"Invalid JSON: {result[:200]} - {e}"
                print(f"     ⚠️  Attempt {attempt + 1} failed: {last_error}")
        
        if not source_id:
            # Get debug info before failing
            error_result = exec_in_pod(
                cluster_config.namespace,
                ingress_pod,
                [
                    "curl", "-s", "-w", "\nHTTP_CODE:%{http_code}",
                    f"{koku_api_url}/sources",
                    "-H", "Content-Type: application/json",
                    "-H", f"X-Rh-Identity: {rh_identity_header}",
                ],
                container="ingress",
            )
            pytest.fail(
                f"Source creation failed after {max_retries} attempts. "
                f"Last error: {last_error}. "
                f"ingress_pod={ingress_pod}, url={koku_api_url}/sources, "
                f"Debug info: {error_result}"
            )
        
        # Create application via Koku API (POST - use writes)
        if cost_mgmt_app_id:
            app_payload = json.dumps({
                "source_id": source_id,
                "application_type_id": cost_mgmt_app_id,
            })
            
            exec_in_pod(
                cluster_config.namespace,
                ingress_pod,
                [
                    "curl", "-s", "-X", "POST",
                    f"{koku_api_url}/applications",
                    "-H", "Content-Type: application/json",
                    "-H", f"X-Rh-Identity: {rh_identity_header}",
                    "-d", app_payload,
                ],
                container="ingress",
            )
        
        yield {
            "source_id": source_id,
            "source_name": source_name,
            "cluster_id": e2e_cluster_id,
            "org_id": org_id,
            "ingress_pod": ingress_pod,
            "koku_api_url": koku_api_url,
            "rh_identity_header": rh_identity_header,
            "db_pod": db_pod,
            "s3_config_dict": s3_config_dict,
        }
        
        # Post-test cleanup (only if enabled)
        if cleanup_after:
            print("\n" + "=" * 60)
            print("POST-TEST CLEANUP")
            print("=" * 60)
            
            # Delete the source via Koku API (DELETE - use writes)
            print("  🗑️  Deleting test source...")
            exec_in_pod(
                cluster_config.namespace,
                ingress_pod,
                [
                    "curl", "-s", "-X", "DELETE",
                    f"{koku_api_url}/sources/{source_id}",
                    "-H", f"X-Rh-Identity: {rh_identity_header}",
                ],
                container="ingress",
            )
            print(f"     ✅ Deleted source {source_id}")
            
            # Full cleanup
            if db_pod:
                full_cleanup(
                    namespace=cluster_config.namespace,
                    db_pod=db_pod,
                    org_id=org_id,
                    s3_config=s3_config_dict,
                    cluster_id=e2e_cluster_id,  # Only clean this test's cluster
                    restart_services=False,  # Don't restart services after tests
                    verbose=True,
                )
        else:
            print("\n" + "=" * 60)
            print("POST-TEST CLEANUP SKIPPED (E2E_CLEANUP_AFTER=false)")
            print("=" * 60)
            print(f"  Data preserved for cluster: {e2e_cluster_id}")
            print(f"  Source ID: {source_id}")

    # =========================================================================
    # Test Steps - Ordered to validate the complete pipeline
    # =========================================================================

    def test_01_source_registered(self, registered_source):
        """Step 1: Verify source was registered successfully."""
        assert registered_source["source_id"], "Source ID not set"
        assert registered_source["cluster_id"], "Cluster ID not set"

    def test_02_provider_created_in_koku(
        self, cluster_config, database_config, registered_source
    ):
        """Step 2: Verify provider was created in Koku database via Kafka."""
        db_pod = get_pod_by_label(
            cluster_config.namespace,
            "app.kubernetes.io/component=database"
        )
        if not db_pod:
            pytest.skip("Database pod not found")
        
        cluster_id = registered_source["cluster_id"]
        
        def check_provider():
            result = execute_db_query(
                cluster_config.namespace,
                db_pod,
                database_config.database,
                database_config.user,
                f"""
                SELECT COUNT(*) FROM api_provider p
                JOIN api_providerauthentication a ON p.authentication_id = a.id
                WHERE a.credentials->>'cluster_id' = '{cluster_id}'
                   OR p.additional_context->>'cluster_id' = '{cluster_id}'
                """,
            )
            return result is not None and int(result[0][0]) > 0
        
        success = wait_for_condition(
            check_provider,
            timeout=180,
            interval=10,
            description="provider creation via Kafka",
        )
        
        assert success, f"Provider not created for cluster {cluster_id}"

    def test_03_upload_data_via_ingress(
        self,
        cluster_config,
        ingress_url: str,
        jwt_token,
        e2e_test_data: dict,
        registered_source,
        http_session: requests.Session,
    ):
        """Step 3: Upload test data via JWT-authenticated ingress."""
        cluster_id = registered_source["cluster_id"]
        
        # Get date range from NISE data or use defaults
        start_date = e2e_test_data.get("start_date")
        end_date = e2e_test_data.get("end_date")
        
        # Check if we have NISE-generated files with separate ROS data
        pod_usage_files = e2e_test_data.get("pod_usage_files", [])
        ros_usage_files = e2e_test_data.get("ros_usage_files", [])
        node_label_files = e2e_test_data.get("node_label_files", [])
        namespace_label_files = e2e_test_data.get("namespace_label_files", [])
        
        if pod_usage_files and ros_usage_files:
            # Use NISE files with proper separation of cost and ROS data
            print(f"  📦 Creating package with {len(pod_usage_files)} pod_usage + {len(ros_usage_files)} ros_usage files")
            if node_label_files:
                print(f"     + {len(node_label_files)} node_label files")
            if namespace_label_files:
                print(f"     + {len(namespace_label_files)} namespace_label files")
            tar_path = create_upload_package_from_files(
                pod_usage_files,
                ros_usage_files,
                cluster_id,
                start_date=start_date,
                end_date=end_date,
                node_label_files=node_label_files if node_label_files else None,
                namespace_label_files=namespace_label_files if namespace_label_files else None,
            )
        else:
            # Fall back to simple CSV content
            tar_path = create_upload_package(
                e2e_test_data["csv_content"],
                cluster_id,
                start_date=start_date,
                end_date=end_date,
            )
        
        try:
            with open(tar_path, "rb") as f:
                response = http_session.post(
                    f"{ingress_url}/v1/upload",
                    files={
                        "file": (
                            "cost-mgmt.tar.gz",
                            f,
                            "application/vnd.redhat.hccm.filename+tgz",
                        )
                    },
                    headers=jwt_token.authorization_header,
                    timeout=60,
                )
            
            if response.status_code == 503:
                pytest.skip("Ingress service returning 503 - pods may not be ready")
            
            # Ingress returns 202 Accepted when file is queued for processing
            assert response.status_code == 202, (
                f"Expected 202 Accepted, got {response.status_code}: {response.text}"
            )
        finally:
            # Clean up temp files
            tar_dir = os.path.dirname(tar_path)
            if os.path.exists(tar_path):
                os.unlink(tar_path)
            if os.path.exists(tar_dir):
                shutil.rmtree(tar_dir, ignore_errors=True)
            
            # Clean up NISE temp directory if present
            nise_temp_dir = e2e_test_data.get("temp_dir")
            if nise_temp_dir and os.path.exists(nise_temp_dir):
                shutil.rmtree(nise_temp_dir, ignore_errors=True)

    def test_04_manifest_created_in_koku(
        self, cluster_config, database_config, registered_source
    ):
        """Step 4: Verify manifest was created in Koku database."""
        db_pod = get_pod_by_label(
            cluster_config.namespace,
            "app.kubernetes.io/component=database"
        )
        if not db_pod:
            pytest.skip("Database pod not found")
        
        cluster_id = registered_source["cluster_id"]
        
        def check_manifest():
            result = execute_db_query(
                cluster_config.namespace,
                db_pod,
                database_config.database,
                database_config.user,
                f"""
                SELECT COUNT(*) FROM reporting_common_costusagereportmanifest
                WHERE cluster_id = '{cluster_id}'
                """,
            )
            return result is not None and int(result[0][0]) > 0
        
        success = wait_for_condition(
            check_manifest,
            timeout=300,
            interval=15,
            description="manifest creation",
        )
        
        assert success, f"Manifest not created for cluster {cluster_id}"
        
        # Validate manifest has required fields
        manifest_result = execute_db_query(
            cluster_config.namespace,
            db_pod,
            database_config.database,
            database_config.user,
            f"""
            SELECT 
                m.id,
                m.assembly_id,
                m.cluster_id,
                m.num_total_files,
                m.creation_datetime
            FROM reporting_common_costusagereportmanifest m
            WHERE m.cluster_id = '{cluster_id}'
            ORDER BY m.creation_datetime DESC
            LIMIT 1
            """,
        )
        
        assert manifest_result and manifest_result[0], "Manifest query returned no results"
        manifest = manifest_result[0]
        
        assert manifest[0] is not None, "Manifest missing ID"
        assert manifest[1] is not None, "Manifest missing assembly_id"
        assert manifest[2] == cluster_id, f"Manifest cluster_id mismatch: {manifest[2]}"
        assert manifest[3] is not None and int(manifest[3]) > 0, "Manifest has no files"
        assert manifest[4] is not None, "Manifest missing creation_datetime"
        
        print(f"  ✅ Manifest {manifest[0]} created with {manifest[3]} files")

    def test_05_files_processed_by_masu(
        self, cluster_config, database_config, registered_source
    ):
        """Step 5: Verify uploaded files were processed by MASU with proper status."""
        db_pod = get_pod_by_label(
            cluster_config.namespace,
            "app.kubernetes.io/component=database"
        )
        if not db_pod:
            pytest.skip("Database pod not found")
        
        cluster_id = registered_source["cluster_id"]
        
        # File processing status codes (from Koku)
        FILE_STATUS_PENDING = 0
        FILE_STATUS_SUCCESS = 1
        FILE_STATUS_FAILED = 2
        
        def check_processing():
            result = execute_db_query(
                cluster_config.namespace,
                db_pod,
                database_config.database,
                database_config.user,
                f"""
                SELECT s.status
                FROM reporting_common_costusagereportmanifest m
                JOIN reporting_common_costusagereportstatus s ON s.manifest_id = m.id
                WHERE m.cluster_id = '{cluster_id}'
                ORDER BY m.creation_datetime DESC
                LIMIT 1
                """,
            )
            # Status 1 = SUCCESS
            return result is not None and len(result) > 0 and str(result[0][0]) == "1"
        
        success = wait_for_condition(
            check_processing,
            timeout=600,
            interval=30,
            description="file processing by MASU",
        )
        
        assert success, "File processing not completed"
        
        # Get file status details
        file_status_result = execute_db_query(
            cluster_config.namespace,
            db_pod,
            database_config.database,
            database_config.user,
            f"""
            SELECT 
                s.report_name,
                s.status,
                s.failed_status,
                s.completed_datetime
            FROM reporting_common_costusagereportmanifest m
            JOIN reporting_common_costusagereportstatus s ON s.manifest_id = m.id
            WHERE m.cluster_id = '{cluster_id}'
            ORDER BY m.creation_datetime DESC
            """,
        )
        
        if file_status_result:
            failed_files = []
            missing_completion = []
            
            for row in file_status_result:
                report_name, status, failed_status, completed_datetime = row
                status_int = int(status) if status is not None else None
                
                if status_int == FILE_STATUS_FAILED:
                    failed_files.append(report_name)
                elif status_int == FILE_STATUS_SUCCESS and completed_datetime is None:
                    missing_completion.append(report_name)
            
            if failed_files:
                print(f"  ⚠️  {len(failed_files)} file(s) failed: {failed_files[:3]}")
            
            if missing_completion:
                print(f"  ⚠️  {len(missing_completion)} successful file(s) missing completion time")
            
            successful = sum(1 for row in file_status_result if row[1] and int(row[1]) == FILE_STATUS_SUCCESS)
            print(f"  ✅ {successful}/{len(file_status_result)} files processed successfully")

    @pytest.mark.timeout(900)  # 15 minutes for summary tables
    def test_06_summary_tables_populated(
        self, cluster_config, database_config, registered_source, e2e_test_data: dict
    ):
        """Step 6: Verify Koku summary tables are populated with correct data.
        
        IMPORTANT: Summary table population requires proper OCP data format.
        If using simple data (E2E_USE_SIMPLE_DATA=true), this test may fail.
        """
        data_generator = e2e_test_data.get("generator", "unknown")
        if data_generator == "simple":
            pytest.skip(
                "Summary table population requires NISE-generated data. "
                "Run with E2E_USE_SIMPLE_DATA=false to use NISE."
            )
        
        if e2e_test_data.get("nise_install_failed"):
            pytest.skip("NISE installation failed - cannot validate summary tables.")
        
        if e2e_test_data.get("nise_error"):
            pytest.skip(f"NISE data generation failed: {e2e_test_data['nise_error']}")
        
        db_pod = get_pod_by_label(
            cluster_config.namespace,
            "app.kubernetes.io/component=database"
        )
        if not db_pod:
            pytest.skip("Database pod not found")
        
        cluster_id = registered_source["cluster_id"]
        
        # Get tenant schema
        schema_result = execute_db_query(
            cluster_config.namespace,
            db_pod,
            database_config.database,
            database_config.user,
            f"""
            SELECT c.schema_name
            FROM reporting_common_costusagereportmanifest m
            JOIN api_provider p ON m.provider_id = p.uuid
            JOIN api_customer c ON p.customer_id = c.id
            WHERE m.cluster_id = '{cluster_id}'
            LIMIT 1
            """,
        )
        
        if not schema_result or not schema_result[0][0]:
            manifest_check = execute_db_query(
                cluster_config.namespace,
                db_pod,
                database_config.database,
                database_config.user,
                f"""
                SELECT m.id, m.provider_id, m.num_total_files, m.num_processed_files
                FROM reporting_common_costusagereportmanifest m
                WHERE m.cluster_id = '{cluster_id}'
                ORDER BY m.creation_datetime DESC
                LIMIT 1
                """,
            )
            
            if manifest_check and manifest_check[0]:
                manifest_info = manifest_check[0]
                assert False, (
                    f"Manifest found (id={manifest_info[0]}) but not linked to provider. "
                    f"Provider ID: {manifest_info[1]}, "
                    f"Files: {manifest_info[3]}/{manifest_info[2]} processed."
                )
            else:
                assert False, f"No manifest found for cluster_id '{cluster_id}'."
        
        schema_name = schema_result[0][0].strip()
        
        def check_summary():
            result = execute_db_query(
                cluster_config.namespace,
                db_pod,
                database_config.database,
                database_config.user,
                f"""
                SELECT COUNT(*),
                       COALESCE(SUM(pod_request_cpu_core_hours), 0),
                       COALESCE(SUM(pod_request_memory_gigabyte_hours), 0)
                FROM {schema_name}.reporting_ocpusagelineitem_daily_summary
                WHERE cluster_id = '{cluster_id}'
                """,
            )
            return result is not None and int(result[0][0]) > 0
        
        success = wait_for_condition(
            check_summary,
            timeout=840,
            interval=30,
            description="summary table population",
        )
        
        if not success:
            file_status = execute_db_query(
                cluster_config.namespace,
                db_pod,
                database_config.database,
                database_config.user,
                f"""
                SELECT rf.report_name, rf.completed_datetime, rf.status
                FROM reporting_common_costusagereportmanifest m
                JOIN reporting_common_costusagereportstatus rf ON m.id = rf.manifest_id
                WHERE m.cluster_id = '{cluster_id}'
                ORDER BY rf.completed_datetime DESC
                LIMIT 5
                """,
            )
            
            file_info = ""
            if file_status:
                file_info = "\n  Processed files:\n"
                for row in file_status:
                    file_info += f"    - {row[0]}: status={row[2]}, completed={row[1]}\n"
            
            assert False, (
                f"Summary tables not populated for cluster '{cluster_id}'.\n"
                f"Schema: {schema_name}\n{file_info}"
            )
        
        # Get summary data stats
        summary_stats = execute_db_query(
            cluster_config.namespace,
            db_pod,
            database_config.database,
            database_config.user,
            f"""
            SELECT 
                COUNT(*) as row_count,
                COALESCE(SUM(pod_request_cpu_core_hours), 0) as cpu_hours,
                COALESCE(SUM(pod_request_memory_gigabyte_hours), 0) as mem_gb_hours
            FROM {schema_name}.reporting_ocpusagelineitem_daily_summary
            WHERE cluster_id = '{cluster_id}'
            """,
        )
        
        if summary_stats and summary_stats[0]:
            row_count, cpu_hours, mem_gb_hours = summary_stats[0]
            print(f"  ✅ Summary tables populated: {row_count} rows, {float(cpu_hours):.2f} CPU-hours, {float(mem_gb_hours):.2f} GB-hours")

    @pytest.mark.timeout(300)  # 5 minutes for Kruize experiments
    def test_07_kruize_experiments_created(
        self, cluster_config, database_config, registered_source, e2e_test_data: dict
    ):
        """Step 7: Verify Kruize experiments were created from ROS events."""
        data_generator = e2e_test_data.get("generator", "unknown")
        if data_generator == "simple":
            pytest.skip("Kruize experiments require NISE-generated data.")
        
        db_pod = get_pod_by_label(
            cluster_config.namespace,
            "app.kubernetes.io/component=database"
        )
        if not db_pod:
            pytest.skip("Database pod not found")
        
        secret_name = f"{cluster_config.helm_release_name}-db-credentials"
        kruize_user = get_secret_value(cluster_config.namespace, secret_name, "kruize-user")
        kruize_password = get_secret_value(cluster_config.namespace, secret_name, "kruize-password")
        
        if not kruize_user:
            pytest.skip("Kruize credentials not found - ROS may not be deployed")
        
        cluster_id = registered_source["cluster_id"]
        
        def check_experiments():
            result = execute_db_query(
                cluster_config.namespace,
                db_pod,
                "kruize_db",
                kruize_user,
                f"""
                SELECT COUNT(*) FROM kruize_experiments
                WHERE cluster_name LIKE '%{cluster_id}%'
                """,
                password=kruize_password,
            )
            return result is not None and int(result[0][0]) > 0
        
        success = wait_for_condition(
            check_experiments,
            timeout=240,
            interval=20,
            description="Kruize experiment creation",
        )
        
        if not success:
            ros_events_check = None
            try:
                result = run_oc_command([
                    "exec", "-n", cluster_config.namespace,
                    "kafka-cluster-kafka-0", "--",
                    "bin/kafka-console-consumer.sh",
                    "--bootstrap-server", "localhost:9092",
                    "--topic", "hccm.ros.events",
                    "--from-beginning",
                    "--max-messages", "1",
                    "--timeout-ms", "5000",
                ], check=False)
                ros_events_check = "ROS events topic has messages" if result.returncode == 0 else "No messages in ROS events topic"
            except Exception:
                ros_events_check = "Could not check ROS events topic"
            
            assert False, (
                f"Kruize experiments not created for cluster '{cluster_id}'.\n"
                f"ROS Events: {ros_events_check}"
            )

    @pytest.mark.timeout(300)  # 5 minutes for recommendations
    def test_08_recommendations_generated(
        self, cluster_config, database_config, registered_source, e2e_test_data: dict
    ):
        """Step 8: Verify recommendations were generated by Kruize."""
        data_generator = e2e_test_data.get("generator", "unknown")
        if data_generator == "simple":
            pytest.skip("Recommendation generation requires NISE-generated data.")
        
        db_pod = get_pod_by_label(
            cluster_config.namespace,
            "app.kubernetes.io/component=database"
        )
        if not db_pod:
            pytest.skip("Database pod not found")
        
        secret_name = f"{cluster_config.helm_release_name}-db-credentials"
        kruize_user = get_secret_value(cluster_config.namespace, secret_name, "kruize-user")
        kruize_password = get_secret_value(cluster_config.namespace, secret_name, "kruize-password")
        
        if not kruize_user:
            pytest.skip("Kruize credentials not found - ROS may not be deployed")
        
        cluster_id = registered_source["cluster_id"]
        
        # First check if experiments exist
        experiment_result = execute_db_query(
            cluster_config.namespace,
            db_pod,
            "kruize_db",
            kruize_user,
            f"""
            SELECT COUNT(*) FROM kruize_experiments
            WHERE cluster_name LIKE '%{cluster_id}%'
            """,
            password=kruize_password,
        )
        
        experiment_count = int(experiment_result[0][0]) if experiment_result else 0
        if experiment_count == 0:
            pytest.skip(
                f"No Kruize experiments found for cluster '{cluster_id}'. "
                "test_07 must pass before recommendations can be generated."
            )
        
        def check_recommendations():
            result = execute_db_query(
                cluster_config.namespace,
                db_pod,
                "kruize_db",
                kruize_user,
                f"""
                SELECT COUNT(*) FROM kruize_recommendations
                WHERE cluster_name LIKE '%{cluster_id}%'
                """,
                password=kruize_password,
            )
            return result is not None and int(result[0][0]) > 0
        
        success = wait_for_condition(
            check_recommendations,
            timeout=240,
            interval=20,
            description="recommendation generation",
        )
        
        if not success:
            exp_details = execute_db_query(
                cluster_config.namespace,
                db_pod,
                "kruize_db",
                kruize_user,
                f"""
                SELECT experiment_name, status, created_at
                FROM kruize_experiments
                WHERE cluster_name LIKE '%{cluster_id}%'
                ORDER BY created_at DESC
                LIMIT 3
                """,
                password=kruize_password,
            )
            
            exp_info = ""
            if exp_details:
                exp_info = "\n  Experiments found:\n"
                for row in exp_details:
                    exp_info += f"    - {row[0]}: status={row[1]}, created={row[2]}\n"
            
            assert False, (
                f"Recommendations not generated for cluster '{cluster_id}'.\n"
                f"Experiments found: {experiment_count}\n{exp_info}"
            )

    def test_09_recommendations_accessible_via_api(
        self,
        gateway_url: str,
        keycloak_config,
        http_session: requests.Session,
    ):
        """Step 9: Verify recommendations are accessible via JWT-authenticated API."""
        # Get a fresh JWT token
        token_response = http_session.post(
            keycloak_config.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": keycloak_config.client_id,
                "client_secret": keycloak_config.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )

        if token_response.status_code != 200:
            pytest.skip(f"Could not refresh JWT token: {token_response.status_code}")

        fresh_token = token_response.json().get("access_token")

        response = http_session.get(
            f"{gateway_url}/cost-management/v1/recommendations/openshift",
            headers={"Authorization": f"Bearer {fresh_token}"},
            timeout=30,
        )
        
        if response.status_code == 401:
            pytest.skip("Recommendations API returned 401 - may require different auth configuration")
        
        assert response.status_code == 200, (
            f"Recommendations API failed: {response.status_code}"
        )
        
        data = response.json()
        
        if "data" in data:
            assert isinstance(data["data"], list), "Invalid response format"
