"""
Utility functions for cost-onprem-chart tests.

These are helper functions that can be imported by test modules across all suites.
"""

import base64
import http.client
import io
import json
import re
import socket
import subprocess
import tarfile
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from requests.structures import CaseInsensitiveDict
from urllib3.response import HTTPResponse


class _FakeSocket:
    """Minimal socket-like object for http.client.HTTPResponse.
    
    Used by PodAdapter to parse raw HTTP response text from curl output.
    http.client.HTTPResponse expects a socket with a makefile() method.
    """
    
    def __init__(self, data: bytes) -> None:
        self._file = io.BytesIO(data)
    
    def makefile(self, mode: str) -> io.BytesIO:
        return self._file


# =============================================================================
# Kubernetes/OpenShift Commands
# =============================================================================


def run_oc_command(
    args: list[str],
    check: bool = True,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """Run an oc command and return the result.
    
    Args:
        args: Command arguments (without 'oc' prefix)
        check: Raise exception on non-zero exit code
        timeout: Command timeout in seconds
    
    Returns:
        CompletedProcess with stdout, stderr, returncode
    """
    cmd = ["oc"] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def get_route_url(namespace: str, route_name: str) -> Optional[str]:
    """Get the URL for an OpenShift route."""
    try:
        result = run_oc_command(
            ["get", "route", route_name, "-n", namespace, "-o", "jsonpath={.spec.host}"]
        )
        host = result.stdout.strip()
        if not host:
            return None

        # Check if TLS is enabled
        tls_result = run_oc_command(
            [
                "get",
                "route",
                route_name,
                "-n",
                namespace,
                "-o",
                "jsonpath={.spec.tls.termination}",
            ],
            check=False,
        )
        tls = tls_result.stdout.strip()

        scheme = "https" if tls else "http"
        return f"{scheme}://{host}"
    except subprocess.CalledProcessError:
        return None


def get_secret_value(namespace: str, secret_name: str, key: str) -> Optional[str]:
    """Get a decoded value from a Kubernetes secret."""
    try:
        result = run_oc_command(
            [
                "get",
                "secret",
                secret_name,
                "-n",
                namespace,
                "-o",
                f"jsonpath={{.data.{key}}}",
            ]
        )
        encoded = result.stdout.strip()
        if not encoded:
            return None
        return base64.b64decode(encoded).decode("utf-8")
    except (subprocess.CalledProcessError, ValueError):
        return None


def get_pod_by_label(namespace: str, label: str) -> Optional[str]:
    """Get the first pod name matching a label selector."""
    try:
        result = run_oc_command([
            "get", "pods", "-n", namespace,
            "-l", label,
            "-o", "jsonpath={.items[0].metadata.name}"
        ], check=False)
        pod_name = result.stdout.strip()
        return pod_name if pod_name else None
    except subprocess.CalledProcessError:
        return None


def exec_in_pod(
    namespace: str,
    pod_name: str,
    command: list[str],
    container: Optional[str] = None,
    timeout: int = 60,
) -> Optional[str]:
    """Execute a command in a pod and return stdout."""
    try:
        args = ["exec", "-n", namespace, pod_name]
        if container:
            args.extend(["-c", container])
        args.append("--")
        args.extend(command)
        
        result = run_oc_command(args, check=False, timeout=timeout)
        return result.stdout if result.returncode == 0 else None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def exec_in_pod_raw(
    namespace: str,
    pod_name: str,
    command: list[str],
    container: Optional[str] = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """Execute a command in a pod and return the full CompletedProcess.
    
    Unlike exec_in_pod(), this returns the full result including stderr
    and returncode, useful for the PodAdapter.
    """
    args = ["exec", "-n", namespace, pod_name]
    if container:
        args.extend(["-c", container])
    args.append("--")
    args.extend(command)
    
    return run_oc_command(args, check=False, timeout=timeout)


# =============================================================================
# Pod HTTP Adapter - Route requests.Session through kubectl exec curl
# =============================================================================


class PodAdapter(HTTPAdapter):
    """HTTP adapter that routes requests through curl inside a Kubernetes pod.
    
    This adapter allows using the standard requests.Session API for making
    HTTP calls that execute inside a pod, useful for testing internal
    cluster services that aren't exposed externally.
    
    Usage:
        session = requests.Session()
        adapter = PodAdapter(namespace="cost-onprem", pod="test-runner", container="runner")
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        # Now use standard requests API
        response = session.get("http://koku-api:8000/api/v1/status/")
        assert response.status_code == 200
        data = response.json()
    """
    
    def __init__(
        self,
        namespace: str,
        pod: str,
        container: Optional[str] = None,
        timeout: int = 60,
        **kwargs,
    ):
        """Initialize the PodAdapter.
        
        Args:
            namespace: Kubernetes namespace where the pod is running
            pod: Name of the pod to execute curl in
            container: Container name (if pod has multiple containers)
            timeout: Timeout for curl commands in seconds
        """
        self.namespace = namespace
        self.pod = pod
        self.container = container
        self.timeout = timeout
        super().__init__(**kwargs)
    
    def send(
        self,
        request: requests.PreparedRequest,
        stream: bool = False,
        timeout: Any = None,
        verify: bool = True,
        cert: Any = None,
        proxies: Any = None,
    ) -> requests.Response:
        """Send a PreparedRequest by executing curl inside the pod.
        
        This method builds a curl command from the PreparedRequest,
        executes it inside the pod, and parses the raw HTTP response
        into a requests.Response object.
        """
        # Build curl command with -i to include headers in output
        cmd = ["curl", "-i", "-s", "-S"]
        
        # Add method
        cmd.extend(["-X", request.method])
        
        # Add headers
        if request.headers:
            for key, value in request.headers.items():
                # Skip host header as curl sets it automatically
                if key.lower() != "host":
                    cmd.extend(["-H", f"{key}: {value}"])
        
        # Add request body to curl command
        # The requests library may provide body as bytes or str. Since we're
        # passing this through kubectl exec -> curl, we need it as a string.
        # Writing to a temp file inside the pod would require additional exec
        # calls, so we pass the body inline via --data-raw instead.
        if request.body:
            if isinstance(request.body, bytes):
                body_str = request.body.decode("utf-8", errors="replace")
            else:
                body_str = request.body
            cmd.extend(["--data-raw", body_str])
        
        # Add URL
        cmd.append(request.url)
        
        # Add timeout
        effective_timeout = timeout if timeout else self.timeout
        if isinstance(effective_timeout, tuple):
            effective_timeout = effective_timeout[1]  # Use read timeout
        cmd.extend(["--max-time", str(effective_timeout)])
        
        # For self-signed certs, add -k flag
        if not verify:
            cmd.append("-k")
        
        # Execute curl in pod
        result = exec_in_pod_raw(
            self.namespace,
            self.pod,
            cmd,
            container=self.container,
            timeout=effective_timeout + 10,  # Add buffer for kubectl overhead
        )
        
        # Parse the raw HTTP response
        return self._parse_curl_response(result, request)
    
    def _parse_curl_response(
        self,
        result: subprocess.CompletedProcess,
        request: requests.PreparedRequest,
    ) -> requests.Response:
        """Parse curl -i output into a requests.Response object.
        
        Uses http.client to parse the raw HTTP response and HTTPAdapter.build_response()
        to construct the Response properly.
        """
        # Handle curl errors
        if result.returncode != 0:
            raise requests.exceptions.ConnectionError(
                f"curl failed (exit {result.returncode}): {result.stderr or 'Connection failed'}"
            )
        
        raw_output = result.stdout
        if not raw_output:
            raise requests.exceptions.ConnectionError("curl returned empty response")
        
        # Normalize line endings to \r\n for HTTP parsing
        # HTTP spec requires \r\n, but curl output may have \n depending on server/platform
        # Use regex to normalize only \n that aren't already part of \r\n
        raw_output = re.sub(r'(?<!\r)\n', '\r\n', raw_output)
        
        # Handle HTTP/1.1 100 Continue - skip to the real response
        while raw_output.startswith("HTTP/") and " 100 " in raw_output.split("\r\n")[0]:
            # Find the end of the 100 Continue response and skip it
            parts = raw_output.split("\r\n\r\n", 1)
            if len(parts) > 1:
                raw_output = parts[1]
            else:
                break
        
        try:
            # Parse using http.client.HTTPResponse with a fake socket
            raw_bytes = raw_output.encode("utf-8")
            sock = _FakeSocket(raw_bytes)
            http_response = http.client.HTTPResponse(sock)
            http_response.begin()
            
            # Read the body
            body = http_response.read()
            
            # Build urllib3 HTTPResponse for build_response()
            # Setting preload_content=True ensures body is read into response
            urllib3_response = HTTPResponse(
                body=io.BytesIO(body),
                headers=dict(http_response.getheaders()),
                status=http_response.status,
                reason=http_response.reason,
                preload_content=True,
            )
            
            # Use HTTPAdapter's build_response to construct Response properly
            response = self.build_response(request, urllib3_response)
            
            # build_response with preload_content=True should set _content,
            # but ensure it's set for consistency
            if not response._content_consumed:
                response._content = body
            
            return response
            
        except Exception as e:
            raise requests.exceptions.ConnectionError(
                f"Failed to parse HTTP response: {e}"
            ) from e


def create_pod_session(
    namespace: str,
    pod: str,
    container: Optional[str] = None,
    headers: Optional[dict] = None,
    timeout: int = 60,
) -> requests.Session:
    """Create a requests.Session that routes through a pod.
    
    This is a convenience function to create a pre-configured session
    with the PodAdapter mounted for both http:// and https:// URLs.
    
    Args:
        namespace: Kubernetes namespace where the pod is running
        pod: Name of the pod to execute curl in
        container: Container name (if pod has multiple containers)
        headers: Default headers to include in all requests
        timeout: Default timeout for requests
    
    Returns:
        A requests.Session configured to route through the pod
    
    Example:
        session = create_pod_session("cost-onprem", "test-runner", container="runner")
        session.headers["X-Rh-Identity"] = identity_header
        
        response = session.get("http://koku-api:8000/api/v1/status/")
        assert response.ok
        data = response.json()
    """
    session = requests.Session()
    adapter = PodAdapter(namespace, pod, container=container, timeout=timeout)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    if headers:
        session.headers.update(headers)
    
    return session


# =============================================================================
# Database Utilities
# =============================================================================


def execute_db_query(
    namespace: str,
    pod_name: str,
    database: str,
    user: str,
    query: str,
    password: Optional[str] = None,
) -> Optional[list[tuple]]:
    """Execute a SQL query via kubectl exec and return results."""
    try:
        env_prefix = []
        if password:
            env_prefix = ["env", f"PGPASSWORD={password}"]
        
        cmd = env_prefix + [
            "psql", "-U", user, "-d", database,
            "-t", "-A", "-F", "|",
            "-c", query
        ]
        
        result = exec_in_pod(namespace, pod_name, cmd, timeout=120)
        if not result:
            return None
        
        # Parse pipe-delimited output
        rows = []
        for line in result.strip().split("\n"):
            if line:
                rows.append(tuple(line.split("|")))
        return rows
    except Exception:
        return None


# =============================================================================
# Authentication Utilities
# =============================================================================


def create_rh_identity_header(org_id: str, account_number: str = None) -> str:
    """Create X-Rh-Identity header value for Koku authentication.

    The X-Rh-Identity header is a base64-encoded JSON structure required by
    Koku middleware for tenant identification and authorization.

    Args:
        org_id: Organization ID for the tenant
        account_number: Account number (defaults to org_id if not provided)

    Returns:
        Base64-encoded identity JSON string
    """
    if account_number is None:
        account_number = org_id

    identity_json = {
        "org_id": org_id,
        "identity": {
            "org_id": org_id,
            "account_number": account_number,
            "type": "User",
            "user": {
                "username": "test",
                "email": "test@example.com",
                "is_org_admin": True,
            },
        },
        "entitlements": {
            "cost_management": {
                "is_entitled": True,
            },
        },
    }

    return base64.b64encode(json.dumps(identity_json).encode()).decode()


def create_identity_header_custom(
    org_id: str,
    is_org_admin: bool = True,
    email: Optional[str] = "test@example.com",
    entitlements: Optional[dict] = None,
    account_number: Optional[str] = None,
) -> str:
    """Create X-Rh-Identity header with customizable fields for error testing.

    This function allows creating identity headers with various configurations
    to test authentication error scenarios.

    Args:
        org_id: Organization ID for the tenant
        is_org_admin: Whether the user is an org admin (default: True)
        email: User email address (set to None to omit the field)
        entitlements: Custom entitlements dict (default: cost_management is_entitled=True)
        account_number: Account number (defaults to org_id if not provided)

    Returns:
        Base64-encoded identity JSON string
    """
    if account_number is None:
        account_number = org_id

    if entitlements is None:
        entitlements = {
            "cost_management": {
                "is_entitled": True,
            },
        }

    user_dict: dict[str, Any] = {
        "username": "test",
        "is_org_admin": is_org_admin,
    }
    if email is not None:
        user_dict["email"] = email

    identity_json = {
        "org_id": org_id,
        "identity": {
            "org_id": org_id,
            "account_number": account_number,
            "type": "User",
            "user": user_dict,
        },
        "entitlements": entitlements,
    }

    return base64.b64encode(json.dumps(identity_json).encode()).decode()


def check_service_exists(namespace: str, service_name: str) -> bool:
    """Check if Kubernetes service exists using oc get.

    Args:
        namespace: Kubernetes namespace
        service_name: Name of the service

    Returns:
        True if service exists, False otherwise
    """
    try:
        result = run_oc_command([
            "get", "service", service_name, "-n", namespace,
            "-o", "jsonpath={.metadata.name}"
        ], check=False)
        return result.returncode == 0 and result.stdout.strip() == service_name
    except subprocess.CalledProcessError:
        return False


def check_deployment_exists(namespace: str, deployment_name: str) -> bool:
    """Check if Kubernetes deployment exists using oc get.

    Args:
        namespace: Kubernetes namespace
        deployment_name: Name of the deployment

    Returns:
        True if deployment exists, False otherwise
    """
    try:
        result = run_oc_command([
            "get", "deployment", deployment_name, "-n", namespace,
            "-o", "jsonpath={.metadata.name}"
        ], check=False)
        return result.returncode == 0 and result.stdout.strip() == deployment_name
    except subprocess.CalledProcessError:
        return False


# =============================================================================
# Upload Package Creation
# =============================================================================


def create_upload_package(
    csv_data: str,
    cluster_id: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> str:
    """Create a tar.gz upload package with CSV and manifest.
    
    Args:
        csv_data: CSV content as string
        cluster_id: Unique cluster identifier
        start_date: Start date for the report period (required for summary processing)
        end_date: End date for the report period (required for summary processing)
    
    Returns:
        Path to the created tar.gz file
        
    IMPORTANT: The manifest.json MUST include 'start' and 'end' fields for Koku
    to trigger summary processing. Without these fields, Koku will log:
    "missing start or end dates - cannot summarize ocp reports"
    """
    from datetime import timedelta
    
    temp_dir = tempfile.mkdtemp()
    csv_file = Path(temp_dir) / "openshift_usage_report.csv"
    manifest_file = Path(temp_dir) / "manifest.json"
    tar_file = Path(temp_dir) / "cost-mgmt.tar.gz"

    # Write CSV
    csv_file.write_text(csv_data)

    # Calculate date range if not provided
    now = datetime.now(timezone.utc)
    if start_date is None:
        # Default to yesterday
        start_date = now - timedelta(days=1)
    if end_date is None:
        # Default to today
        end_date = now
    
    # Ensure dates are timezone-aware
    if start_date.tzinfo is None:
        start_date = start_date.replace(tzinfo=timezone.utc)
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=timezone.utc)

    # Write manifest - MUST include start and end for summary processing
    manifest = {
        "uuid": str(uuid.uuid4()),
        "cluster_id": cluster_id,
        "cluster_alias": f"e2e-source-{cluster_id[-8:]}",
        "date": now.isoformat(),
        "files": ["openshift_usage_report.csv"],
        "resource_optimization_files": ["openshift_usage_report.csv"],
        "certified": True,
        "operator_version": "1.0.0",
        "daily_reports": False,
        # CRITICAL: These fields are required for Koku to trigger summary processing
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
    }
    manifest_file.write_text(json.dumps(manifest, indent=2))

    # Create tar.gz
    with tarfile.open(tar_file, "w:gz") as tar:
        tar.add(csv_file, arcname="openshift_usage_report.csv")
        tar.add(manifest_file, arcname="manifest.json")

    return str(tar_file)


def create_upload_package_from_files(
    pod_usage_files: list[str],
    ros_usage_files: list[str],
    cluster_id: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    node_label_files: Optional[list[str]] = None,
    namespace_label_files: Optional[list[str]] = None,
) -> str:
    """Create a tar.gz upload package from NISE-generated files.
    
    This function creates a proper upload package with separate file lists
    for cost management (pod_usage) and resource optimization (ros_usage).
    
    Args:
        pod_usage_files: List of paths to pod_usage CSV files (for Koku cost management)
        ros_usage_files: List of paths to ros_usage CSV files (for ROS processor)
        cluster_id: Unique cluster identifier
        start_date: Start date for the report period
        end_date: End date for the report period
        node_label_files: Optional list of paths to node label CSV files
        namespace_label_files: Optional list of paths to namespace label CSV files
    
    Returns:
        Path to the created tar.gz file
    """
    from datetime import timedelta
    import os
    
    temp_dir = tempfile.mkdtemp()
    manifest_file = Path(temp_dir) / "manifest.json"
    tar_file = Path(temp_dir) / "cost-mgmt.tar.gz"

    # Calculate date range if not provided
    now = datetime.now(timezone.utc)
    if start_date is None:
        start_date = now - timedelta(days=1)
    if end_date is None:
        end_date = now
    
    # Ensure dates are timezone-aware
    if start_date.tzinfo is None:
        start_date = start_date.replace(tzinfo=timezone.utc)
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=timezone.utc)

    # Get just the filenames for the manifest
    pod_filenames = [os.path.basename(f) for f in pod_usage_files]
    ros_filenames = [os.path.basename(f) for f in ros_usage_files]
    node_label_filenames = [os.path.basename(f) for f in (node_label_files or [])]
    namespace_label_filenames = [os.path.basename(f) for f in (namespace_label_files or [])]
    
    # Combine all files for the manifest's "files" array
    # Koku processes all files listed here, including label files
    all_data_files = pod_filenames + node_label_filenames + namespace_label_filenames

    # Write manifest with separate file lists
    manifest = {
        "uuid": str(uuid.uuid4()),
        "cluster_id": cluster_id,
        "cluster_alias": f"e2e-source-{cluster_id[-8:]}",
        "date": now.isoformat(),
        "files": all_data_files,  # All data files for Koku cost management (pod, node labels, namespace labels)
        "resource_optimization_files": ros_filenames,  # Container-level data for ROS
        "certified": True,
        "operator_version": "1.0.0",
        "daily_reports": False,
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
    }
    manifest_file.write_text(json.dumps(manifest, indent=2))

    # Create tar.gz with all files
    with tarfile.open(tar_file, "w:gz") as tar:
        # Add pod usage files
        for filepath in pod_usage_files:
            tar.add(filepath, arcname=os.path.basename(filepath))
        # Add ROS usage files
        for filepath in ros_usage_files:
            tar.add(filepath, arcname=os.path.basename(filepath))
        # Add node label files if provided
        if node_label_files:
            for filepath in node_label_files:
                tar.add(filepath, arcname=os.path.basename(filepath))
        # Add namespace label files if provided
        if namespace_label_files:
            for filepath in namespace_label_files:
                tar.add(filepath, arcname=os.path.basename(filepath))
        # Add manifest
        tar.add(manifest_file, arcname="manifest.json")

    return str(tar_file)


# =============================================================================
# Helm Utilities
# =============================================================================


def run_helm_command(
    args: list[str],
    check: bool = True,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    """Run a helm command and return the result."""
    cmd = ["helm"] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def helm_lint(chart_path: str) -> tuple[bool, str]:
    """Run helm lint on a chart.
    
    Returns:
        Tuple of (success, output)
    """
    try:
        result = run_helm_command(["lint", chart_path], check=False)
        success = result.returncode == 0
        output = result.stdout + result.stderr
        return success, output
    except subprocess.TimeoutExpired:
        return False, "Helm lint timed out"


def helm_template(
    chart_path: str,
    release_name: str = "test-release",
    values_file: Optional[str] = None,
    set_values: Optional[dict] = None,
) -> tuple[bool, str]:
    """Run helm template on a chart.
    
    Returns:
        Tuple of (success, rendered_yaml)
    """
    try:
        args = ["template", release_name, chart_path]
        if values_file:
            args.extend(["-f", values_file])
        if set_values:
            for key, value in set_values.items():
                args.extend(["--set", f"{key}={value}"])
        
        result = run_helm_command(args, check=False)
        success = result.returncode == 0
        return success, result.stdout if success else result.stderr
    except subprocess.TimeoutExpired:
        return False, "Helm template timed out"


# =============================================================================
# Validation Helpers
# =============================================================================


def check_pod_exists(namespace: str, label: str) -> bool:
    """Check if a pod with the given label exists."""
    return get_pod_by_label(namespace, label) is not None


def check_pod_ready(namespace: str, label: str) -> bool:
    """Check if a pod with the given label is ready."""
    try:
        result = run_oc_command([
            "get", "pods", "-n", namespace,
            "-l", label,
            "-o", "jsonpath={.items[0].status.conditions[?(@.type=='Ready')].status}"
        ], check=False)
        return result.stdout.strip() == "True"
    except subprocess.CalledProcessError:
        return False


def wait_for_condition(
    check_func,
    timeout: int = 300,
    interval: int = 10,
    description: str = "condition",
) -> bool:
    """Wait for a condition to become true.
    
    Args:
        check_func: Callable that returns True when condition is met
        timeout: Maximum wait time in seconds
        interval: Check interval in seconds
        description: Description for logging
    
    Returns:
        True if condition was met, False if timeout
    """
    import time
    
    start_time = time.time()
    while time.time() - start_time < timeout:
        if check_func():
            return True
        time.sleep(interval)
    return False
