"""
Utility functions for cost-onprem-chart tests.

These are helper functions that can be imported by test modules across all suites.
"""

import base64
import json
import subprocess
import tarfile
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


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


def run_kubectl_command(
    args: list[str],
    check: bool = True,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """Run a kubectl command and return the result."""
    cmd = ["kubectl"] + args
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
        "cluster_alias": f"e2e-source-{cluster_id[:12]}",
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

    # Write manifest with separate file lists
    manifest = {
        "uuid": str(uuid.uuid4()),
        "cluster_id": cluster_id,
        "cluster_alias": f"e2e-source-{cluster_id[:12]}",
        "date": now.isoformat(),
        "files": pod_filenames,  # Pod-level data for Koku cost management
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
