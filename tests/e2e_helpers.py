"""
Shared E2E test helpers and utilities.

This module centralizes common E2E test functionality to avoid duplication across:
- tests/suites/e2e/test_complete_flow.py
- tests/suites/cost_management/conftest.py
- Any other test modules that need E2E setup

Key components:
- NISE data generation
- Source registration in Sources API
- Data upload to ingress
- Processing wait utilities
- Cleanup utilities
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests

from utils import (
    create_upload_package_from_files,
    execute_db_query,
    exec_in_pod,
    get_pod_by_label,
    wait_for_condition,
)


# =============================================================================
# Constants and Configuration
# =============================================================================

# Cluster ID prefix for E2E tests (used for cleanup and identification)
E2E_CLUSTER_PREFIX = "e2e-pytest-"

# Default expected values for NISE-generated test data
DEFAULT_NISE_CONFIG = {
    "node_name": "test-node-1",
    "namespace": "test-namespace",
    "pod_name": "test-pod-1",
    "resource_id": "test-resource-1",
    "cpu_cores": 2,
    "memory_gig": 8,
    "cpu_request": 0.5,
    "mem_request_gig": 1,
    "cpu_limit": 1,
    "mem_limit_gig": 2,
    "pod_seconds": 3600,
    "cpu_usage": 0.25,
    "mem_usage_gig": 0.5,
    "labels": "environment:test|app:e2e-test",
}

# S3 bucket name
DEFAULT_S3_BUCKET = "koku-bucket"

# Upload content type
UPLOAD_CONTENT_TYPE = "application/vnd.redhat.hccm.filename+tgz"


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class NISEConfig:
    """Configuration for NISE data generation."""
    node_name: str = DEFAULT_NISE_CONFIG["node_name"]
    namespace: str = DEFAULT_NISE_CONFIG["namespace"]
    pod_name: str = DEFAULT_NISE_CONFIG["pod_name"]
    resource_id: str = DEFAULT_NISE_CONFIG["resource_id"]
    cpu_cores: int = DEFAULT_NISE_CONFIG["cpu_cores"]
    memory_gig: int = DEFAULT_NISE_CONFIG["memory_gig"]
    cpu_request: float = DEFAULT_NISE_CONFIG["cpu_request"]
    mem_request_gig: float = DEFAULT_NISE_CONFIG["mem_request_gig"]
    cpu_limit: float = DEFAULT_NISE_CONFIG["cpu_limit"]
    mem_limit_gig: float = DEFAULT_NISE_CONFIG["mem_limit_gig"]
    pod_seconds: int = DEFAULT_NISE_CONFIG["pod_seconds"]
    cpu_usage: float = DEFAULT_NISE_CONFIG["cpu_usage"]
    mem_usage_gig: float = DEFAULT_NISE_CONFIG["mem_usage_gig"]
    labels: str = DEFAULT_NISE_CONFIG["labels"]
    
    def get_expected_values(self, hours: int = 24) -> Dict:
        """Calculate expected values for validation tests."""
        return {
            "node_name": self.node_name,
            "namespace": self.namespace,
            "pod_name": self.pod_name,
            "resource_id": self.resource_id,
            "cpu_request": self.cpu_request,
            "mem_request_gig": self.mem_request_gig,
            "hours": hours,
            "expected_cpu_hours": self.cpu_request * hours,
            "expected_memory_gb_hours": self.mem_request_gig * hours,
            "expected_node_count": 1,
            "expected_namespace_count": 1,
            "expected_pod_count": 1,
        }
    
    def to_yaml(self, cluster_id: str, start_date: datetime, end_date: datetime) -> str:
        """Generate NISE static report YAML."""
        return f"""---
generators:
  - OCPGenerator:
      start_date: {start_date.strftime('%Y-%m-%d')}
      end_date: {end_date.strftime('%Y-%m-%d')}
      nodes:
        - node:
          node_name: {self.node_name}
          cpu_cores: {self.cpu_cores}
          memory_gig: {self.memory_gig}
          resource_id: {self.resource_id}
          labels: node-role.kubernetes.io/worker:true|kubernetes.io/os:linux
          namespaces:
            {self.namespace}:
              labels: openshift.io/cluster-monitoring:true
              pods:
                - pod:
                  pod_name: {self.pod_name}
                  cpu_request: {self.cpu_request}
                  mem_request_gig: {self.mem_request_gig}
                  cpu_limit: {self.cpu_limit}
                  mem_limit_gig: {self.mem_limit_gig}
                  pod_seconds: {self.pod_seconds}
                  cpu_usage:
                    full_period: {self.cpu_usage}
                  mem_usage_gig:
                    full_period: {self.mem_usage_gig}
                  labels: {self.labels}
"""


@dataclass
class SourceRegistration:
    """Result of source registration."""
    source_id: str
    source_name: str
    cluster_id: str
    org_id: str


# =============================================================================
# NISE Utilities
# =============================================================================

def is_nise_available() -> bool:
    """Check if NISE is available for data generation."""
    try:
        result = subprocess.run(
            ["nise", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def install_nise() -> bool:
    """Attempt to install NISE via pip."""
    try:
        print("  Installing koku-nise...")
        result = subprocess.run(
            ["pip", "install", "koku-nise"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.returncode == 0
    except Exception:
        return False


def ensure_nise_available() -> bool:
    """Ensure NISE is available, installing if necessary."""
    if is_nise_available():
        return True
    return install_nise()


def generate_nise_data(
    cluster_id: str,
    start_date: datetime,
    end_date: datetime,
    output_dir: str,
    config: Optional[NISEConfig] = None,
    include_ros: bool = True,
) -> Dict[str, List[str]]:
    """Generate NISE OCP data and return categorized file paths.
    
    Args:
        cluster_id: Cluster ID for the generated data
        start_date: Start date for the report period
        end_date: End date for the report period
        output_dir: Directory to write output files
        config: NISE configuration (uses defaults if not provided)
        include_ros: Whether to include ROS data (--ros-ocp-info flag)
    
    Returns:
        Dict with keys: pod_usage_files, ros_usage_files, node_label_files, namespace_label_files
    """
    if config is None:
        config = NISEConfig()
    
    yaml_content = config.to_yaml(cluster_id, start_date, end_date)
    yaml_path = os.path.join(output_dir, "static_report.yml")
    with open(yaml_path, "w") as f:
        f.write(yaml_content)
    
    nise_output = os.path.join(output_dir, "nise_output")
    os.makedirs(nise_output, exist_ok=True)
    
    # Build command
    cmd = [
        "nise", "report", "ocp",
        "--static-report-file", yaml_path,
        "--ocp-cluster-id", cluster_id,
        "-w",  # Write monthly files
    ]
    if include_ros:
        cmd.append("--ros-ocp-info")
    
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
        cwd=nise_output,
    )
    
    if result.returncode != 0:
        raise RuntimeError(f"NISE failed: {result.stderr}")
    
    # Categorize generated files
    files = {
        "pod_usage_files": [],
        "ros_usage_files": [],
        "node_label_files": [],
        "namespace_label_files": [],
        "all_files": [],
    }
    
    for root, _, filenames in os.walk(nise_output):
        for f in filenames:
            if f.endswith(".csv"):
                full_path = os.path.join(root, f)
                files["all_files"].append(full_path)
                
                if "pod_usage" in f:
                    files["pod_usage_files"].append(full_path)
                elif "ros_usage" in f:
                    files["ros_usage_files"].append(full_path)
                elif "node_label" in f:
                    files["node_label_files"].append(full_path)
                elif "namespace_label" in f:
                    files["namespace_label_files"].append(full_path)
    
    # Fall back: if no ros_usage files, use pod_usage
    if not files["ros_usage_files"]:
        files["ros_usage_files"] = files["pod_usage_files"]
    
    return files


# =============================================================================
# Cluster ID Generation
# =============================================================================

def generate_cluster_id(prefix: str = "") -> str:
    """Generate a unique cluster ID for E2E tests.
    
    Args:
        prefix: Optional prefix to add after the standard e2e-pytest- prefix
    
    Returns:
        Unique cluster ID like "e2e-pytest-cost-val-abc12345"
    """
    timestamp = int(time.time())
    unique = uuid.uuid4().hex[:8]
    
    if prefix:
        return f"{E2E_CLUSTER_PREFIX}{prefix}-{unique}"
    return f"{E2E_CLUSTER_PREFIX}{timestamp}-{unique}"


# =============================================================================
# Koku API Utilities
# =============================================================================

def get_koku_api_reads_url(helm_release_name: str, namespace: str) -> str:
    """Get the internal Koku API reads URL for GET operations."""
    return (
        f"http://{helm_release_name}-koku-api-reads."
        f"{namespace}.svc.cluster.local:8000/api/cost-management/v1"
    )


def get_koku_api_writes_url(helm_release_name: str, namespace: str) -> str:
    """Get the internal Koku API writes URL for POST/PUT/DELETE operations."""
    return (
        f"http://{helm_release_name}-koku-api-writes."
        f"{namespace}.svc.cluster.local:8000/api/cost-management/v1"
    )


def get_source_type_id(
    namespace: str,
    pod: str,
    api_url: str,
    rh_identity_header: str,
    source_type_name: str = "openshift",
    container: str = "ingress",
) -> Optional[str]:
    """Get the source type ID for a given source type name.
    
    Args:
        namespace: Kubernetes namespace
        pod: Pod name for executing curl commands (typically ingress pod)
        api_url: Koku API URL (reads or writes)
        rh_identity_header: Base64-encoded X-Rh-Identity header value
        source_type_name: Name of the source type (default: "openshift")
        container: Container name in the pod (default: "ingress")
    
    Returns:
        Source type ID as string, or None if not found
    """
    result = exec_in_pod(
        namespace,
        pod,
        [
            "curl", "-s",
            f"{api_url}/source_types",
            "-H", "Content-Type: application/json",
            "-H", f"X-Rh-Identity: {rh_identity_header}",
        ],
        container=container,
    )
    
    if not result:
        return None
    
    try:
        data = json.loads(result)
        for st in data.get("data", []):
            if st.get("name") == source_type_name:
                return st.get("id")
    except json.JSONDecodeError:
        pass
    
    return None


def get_application_type_id(
    namespace: str,
    pod: str,
    api_url: str,
    rh_identity_header: str,
    app_type_name: str = "/insights/platform/cost-management",
    container: str = "ingress",
) -> Optional[str]:
    """Get the application type ID for cost management.
    
    Args:
        namespace: Kubernetes namespace
        pod: Pod name for executing curl commands (typically ingress pod)
        api_url: Koku API URL (reads or writes)
        rh_identity_header: Base64-encoded X-Rh-Identity header value
        app_type_name: Name of the application type
        container: Container name in the pod (default: "ingress")
    
    Returns:
        Application type ID as string, or None if not found
    """
    result = exec_in_pod(
        namespace,
        pod,
        [
            "curl", "-s",
            f"{api_url}/application_types",
            "-H", "Content-Type: application/json",
            "-H", f"X-Rh-Identity: {rh_identity_header}",
        ],
        container=container,
    )
    
    if not result:
        return None
    
    try:
        data = json.loads(result)
        for at in data.get("data", []):
            if at.get("name") == app_type_name:
                return at.get("id")
    except json.JSONDecodeError:
        pass
    
    return None


def register_source(
    namespace: str,
    pod: str,
    api_reads_url: str,
    api_writes_url: str,
    rh_identity_header: str,
    cluster_id: str,
    org_id: str,
    source_name: Optional[str] = None,
    bucket: str = DEFAULT_S3_BUCKET,
    container: str = "ingress",
    max_retries: int = 5,
    initial_retry_delay: int = 5,
) -> SourceRegistration:
    """Register a source in Koku Sources API.
    
    This creates:
    1. A source with source_ref set to cluster_id (critical for matching incoming data)
    2. An application linked to cost-management with cluster_id in extra
    
    Note: On first run for a new org, tenant schema creation can be slow,
    so this function uses retry logic with exponential backoff.
    
    Args:
        namespace: Kubernetes namespace
        pod: Pod name for executing curl commands (typically ingress pod)
        api_reads_url: Koku API reads URL
        api_writes_url: Koku API writes URL
        rh_identity_header: Base64-encoded X-Rh-Identity header value
        cluster_id: Cluster ID for the source
        org_id: Organization ID
        source_name: Optional custom source name (defaults to e2e-source-{cluster_id[-8:]})
        bucket: S3 bucket name
        container: Container name in the pod (default: "ingress")
        max_retries: Maximum number of retry attempts (default: 5)
        initial_retry_delay: Initial delay between retries in seconds (default: 5)
    
    Returns:
        SourceRegistration with source details
    """
    # Get type IDs using reads endpoint
    source_type_id = get_source_type_id(
        namespace, pod, api_reads_url, rh_identity_header, container=container
    )
    if not source_type_id:
        raise RuntimeError("Could not get OpenShift source type ID")
    
    app_type_id = get_application_type_id(
        namespace, pod, api_reads_url, rh_identity_header, container=container
    )
    
    # Generate source name using the unique suffix of cluster_id
    if not source_name:
        source_name = f"e2e-source-{cluster_id[-8:]}"
    
    # Create source with source_ref (critical for matching incoming data)
    source_payload = json.dumps({
        "name": source_name,
        "source_type_id": source_type_id,
        "source_ref": cluster_id,
    })
    
    # Retry logic for source creation
    # First request may fail due to tenant schema creation (slow operation)
    retry_delay = initial_retry_delay
    source_id = None
    last_error = None
    
    for attempt in range(max_retries):
        if attempt > 0:
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30)  # Exponential backoff, max 30s
        
        result = exec_in_pod(
            namespace,
            pod,
            [
                "curl", "-s", "-w", "\n__HTTP_CODE__:%{http_code}", "-X", "POST",
                f"{api_writes_url}/sources",
                "-H", "Content-Type: application/json",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
                "-d", source_payload,
            ],
            container=container,
            timeout=120,  # Longer timeout for first request (schema creation)
        )
        
        if not result:
            last_error = "exec_in_pod returned None (curl failed or timed out)"
            continue
        
        # Parse response and status code
        http_code = None
        if "__HTTP_CODE__:" in result:
            body, http_code = result.rsplit("__HTTP_CODE__:", 1)
            result = body.strip()
            http_code = http_code.strip()
        
        if http_code and http_code not in ("200", "201"):
            last_error = f"HTTP {http_code}: {result[:200]}"
            # 5xx errors might be transient, retry
            if http_code.startswith("5"):
                continue
            # 4xx errors are not retryable - break and fail
            break
        
        try:
            source_data = json.loads(result)
            source_id = source_data.get("id")
            if source_id:
                break
            else:
                last_error = f"No 'id' in response: {result[:200]}"
        except json.JSONDecodeError as e:
            last_error = f"Invalid JSON: {result[:200]} - {e}"
    
    if not source_id:
        raise RuntimeError(
            f"Source creation failed after {max_retries} attempts. "
            f"Last error: {last_error}. "
            f"pod={pod}, url={api_writes_url}/sources"
        )
    
    # Create application with cluster_id in extra
    if app_type_id:
        app_payload = json.dumps({
            "source_id": source_id,
            "application_type_id": app_type_id,
            "extra": {"bucket": bucket, "cluster_id": cluster_id},
        })
        
        exec_in_pod(
            namespace,
            pod,
            [
                "curl", "-s", "-X", "POST",
                f"{api_writes_url}/applications",
                "-H", "Content-Type: application/json",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
                "-d", app_payload,
            ],
            container=container,
        )
    
    return SourceRegistration(
        source_id=source_id,
        source_name=source_name,
        cluster_id=cluster_id,
        org_id=org_id,
    )


def delete_source(
    namespace: str,
    pod: str,
    api_writes_url: str,
    rh_identity_header: str,
    source_id: str,
    container: str = "ingress",
) -> bool:
    """Delete a source from Koku Sources API.
    
    Args:
        namespace: Kubernetes namespace
        pod: Pod name for executing curl commands (typically ingress pod)
        api_writes_url: Koku API writes URL
        rh_identity_header: Base64-encoded X-Rh-Identity header value
        source_id: ID of the source to delete
        container: Container name in the pod (default: "ingress")
    
    Returns:
        True if successful, False otherwise
    """
    try:
        exec_in_pod(
            namespace,
            pod,
            [
                "curl", "-s", "-X", "DELETE",
                f"{api_writes_url}/sources/{source_id}",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container=container,
        )
        return True
    except Exception:
        return False


# =============================================================================
# Upload Utilities
# =============================================================================

def upload_with_retry(
    session: requests.Session,
    url: str,
    package_path: str,
    auth_header: Dict[str, str],
    max_retries: int = 3,
    retry_delay: int = 5,
) -> requests.Response:
    """Upload file with retry logic for transient errors.
    
    Args:
        session: Requests session (should have verify=False for self-signed certs)
        url: Upload URL
        package_path: Path to the tar.gz package
        auth_header: Authorization header dict
        max_retries: Maximum number of retry attempts
        retry_delay: Base delay between retries (exponential backoff)
    
    Returns:
        Response object
    
    Raises:
        RuntimeError: If all retries fail
    """
    last_error = None
    
    for attempt in range(max_retries):
        try:
            with open(package_path, "rb") as f:
                response = session.post(
                    url,
                    files={"file": ("cost-mgmt.tar.gz", f, UPLOAD_CONTENT_TYPE)},
                    headers=auth_header,
                    timeout=60,
                )
            
            if response.status_code in [200, 201, 202]:
                return response
            
            # Retry on 5xx errors
            if response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
                print(f"       Attempt {attempt + 1}/{max_retries} failed: {last_error}, retrying...")
                time.sleep(retry_delay * (attempt + 1))
                continue
            
            # Don't retry on 4xx errors
            return response
            
        except requests.exceptions.RequestException as e:
            last_error = str(e)
            print(f"       Attempt {attempt + 1}/{max_retries} failed: {last_error}, retrying...")
            time.sleep(retry_delay * (attempt + 1))
    
    raise RuntimeError(f"Upload failed after {max_retries} attempts: {last_error}")


# =============================================================================
# Processing Wait Utilities
# =============================================================================

def wait_for_provider(
    namespace: str,
    db_pod: str,
    cluster_id: str,
    timeout: int = 300,
    interval: int = 10,
) -> bool:
    """Wait for provider to be created in Koku database.
    
    Note: Timeout increased to 300s for CI environments where Kafka → Koku
    provider creation can be slower due to resource constraints.
    
    Returns True if provider was created, False on timeout.
    """
    def check_provider():
        result = execute_db_query(
            namespace, db_pod, "costonprem_koku", "koku_user",
            f"""
            SELECT p.uuid FROM api_provider p
            JOIN api_providerauthentication pa ON p.authentication_id = pa.id
            WHERE pa.credentials->>'cluster_id' = '{cluster_id}'
               OR p.additional_context->>'cluster_id' = '{cluster_id}'
            """
        )
        return result and result[0][0]
    
    return wait_for_condition(check_provider, timeout=timeout, interval=interval)


def wait_for_summary_tables(
    namespace: str,
    db_pod: str,
    cluster_id: str,
    timeout: int = 600,
    interval: int = 30,
) -> Optional[str]:
    """Wait for summary tables to be populated and return schema name.
    
    Returns schema name if successful, None on timeout.
    """
    found_schema = {"name": None}
    
    def check_summary():
        result = execute_db_query(
            namespace, db_pod, "costonprem_koku", "koku_user",
            f"""
            SELECT c.schema_name FROM reporting_common_costusagereportmanifest m
            JOIN api_provider p ON m.provider_id = p.uuid
            JOIN api_customer c ON p.customer_id = c.id
            WHERE m.cluster_id = '{cluster_id}' LIMIT 1
            """
        )
        if not result or not result[0][0]:
            return False
        
        schema = result[0][0].strip()
        result = execute_db_query(
            namespace, db_pod, "costonprem_koku", "koku_user",
            f"SELECT COUNT(*) FROM {schema}.reporting_ocpusagelineitem_daily_summary WHERE cluster_id = '{cluster_id}'"
        )
        
        if result and int(result[0][0]) > 0:
            found_schema["name"] = schema
            return True
        return False
    
    if wait_for_condition(check_summary, timeout=timeout, interval=interval):
        return found_schema["name"]
    return None


# =============================================================================
# Cleanup Utilities
# =============================================================================

def cleanup_database_records(
    namespace: str,
    db_pod: str,
    cluster_id: str,
) -> bool:
    """Clean up database records for a cluster."""
    try:
        # Delete file statuses first (foreign key constraint)
        execute_db_query(
            namespace, db_pod, "costonprem_koku", "koku_user",
            f"""
            DELETE FROM reporting_common_costusagereportstatus
            WHERE manifest_id IN (
                SELECT id FROM reporting_common_costusagereportmanifest
                WHERE cluster_id = '{cluster_id}'
            )
            """
        )
        
        # Delete manifests
        execute_db_query(
            namespace, db_pod, "costonprem_koku", "koku_user",
            f"DELETE FROM reporting_common_costusagereportmanifest WHERE cluster_id = '{cluster_id}'"
        )
        
        return True
    except Exception:
        return False


def cleanup_e2e_sources(
    namespace: str,
    listener_pod: str,
    sources_api_url: str,
    org_id: str,
    prefix: str = "e2e-source-",
) -> int:
    """Clean up E2E test sources matching a prefix.
    
    Returns number of sources deleted.
    """
    deleted = 0
    
    try:
        result = exec_in_pod(
            namespace,
            listener_pod,
            [
                "curl", "-s", f"{sources_api_url}/sources",
                "-H", "Content-Type: application/json",
                "-H", f"x-rh-sources-org-id: {org_id}",
            ],
            container="sources-listener",
        )
        
        if not result:
            return 0
        
        sources = json.loads(result)
        for source in sources.get("data", []):
            source_name = source.get("name", "")
            source_id = source.get("id")
            
            if source_id and source_name.startswith(prefix):
                if delete_source(namespace, listener_pod, sources_api_url, source_id, org_id):
                    deleted += 1
                    time.sleep(1)  # Brief pause between deletions
    except Exception:
        pass
    
    return deleted
