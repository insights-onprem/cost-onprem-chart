"""
Cleanup utilities for E2E tests.

This module provides functions to clean up test artifacts between runs,
ensuring a clean state for repeated test execution.

Cleanup includes:
  - S3 data files (uploaded cost data)
  - Database processing records (manifests, report status)
  - Valkey cache (optional, via pod restart)
  - Koku listener state (optional, via pod restart)
"""

import subprocess
import time
from typing import Optional

try:
    import boto3
    from botocore.config import Config as BotoConfig
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False


def cleanup_s3_data(
    endpoint: str,
    access_key: str,
    secret_key: str,
    bucket: str,
    org_id: str,
    cluster_id: Optional[str] = None,
    verify_ssl: bool = False,
) -> dict:
    """Clean up S3 data files from previous test runs.
    
    Args:
        endpoint: S3 endpoint URL
        access_key: S3 access key
        secret_key: S3 secret key
        bucket: S3 bucket name
        org_id: Organization ID to clean up
        cluster_id: Optional cluster ID to limit cleanup scope
        verify_ssl: Whether to verify SSL certificates
        
    Returns:
        Dict with cleanup statistics
    """
    if not BOTO3_AVAILABLE:
        return {"error": "boto3 not available", "files_deleted": 0}
    
    files_deleted = 0
    errors = []
    
    try:
        # Configure boto3 for S3-compatible storage
        boto_config = BotoConfig(
            signature_version='s3v4',
            s3={'addressing_style': 'path'},
        )
        
        s3 = boto3.client(
            's3',
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            verify=verify_ssl,
            config=boto_config,
        )
        
        # Prefixes to clean up
        prefixes = [
            f'data/{org_id}/OCP/',
            f'data/csv/{org_id}/OCP/',
        ]
        
        # If cluster_id specified, be more targeted
        if cluster_id:
            prefixes = [
                f'data/{org_id}/OCP/{cluster_id}/',
                f'data/csv/{org_id}/OCP/{cluster_id}/',
            ]
        
        for prefix in prefixes:
            try:
                paginator = s3.get_paginator('list_objects_v2')
                pages = paginator.paginate(Bucket=bucket, Prefix=prefix)
                
                for page in pages:
                    if 'Contents' in page:
                        for obj in page['Contents']:
                            try:
                                s3.delete_object(Bucket=bucket, Key=obj['Key'])
                                files_deleted += 1
                            except Exception as e:
                                errors.append(f"Failed to delete {obj['Key']}: {e}")
            except Exception as e:
                errors.append(f"Failed to list {prefix}: {e}")
        
        return {
            "files_deleted": files_deleted,
            "errors": errors if errors else None,
        }
        
    except Exception as e:
        return {
            "error": str(e),
            "files_deleted": files_deleted,
        }


def cleanup_database_records(
    namespace: str,
    db_pod: str,
    org_id: str,
    cluster_id: Optional[str] = None,
    database: str = "koku",
    db_user: str = "koku",
) -> dict:
    """Clean up database processing records from previous test runs.
    
    This clears manifest and report status records so files aren't seen
    as "already processed" on subsequent runs.
    
    Args:
        namespace: Kubernetes namespace
        db_pod: Database pod name
        org_id: Organization ID to clean up
        cluster_id: Optional cluster ID to limit cleanup scope
        database: Database name (detected from deployment, defaults to 'koku')
        db_user: Database user (detected from deployment, defaults to 'koku')
        
    Returns:
        Dict with cleanup statistics
    """
    records_deleted = 0
    errors = []
    
    # Build WHERE clause for cluster_id (stored in manifest as text)
    cluster_filter = ""
    if cluster_id:
        cluster_filter = f"AND cluster_id = '{cluster_id}'"
    
    # Queries to clean up processing records
    # Note: Koku uses UUIDs for provider_id and customer_id, so we need proper joins
    cleanup_queries = [
        # Clean up report status records for this org
        f"""
        DELETE FROM reporting_common_costusagereportstatus
        WHERE manifest_id IN (
            SELECT m.id FROM reporting_common_costusagereportmanifest m
            JOIN api_provider p ON m.provider_id = p.uuid
            JOIN api_customer c ON p.customer_id = c.id
            WHERE c.org_id = '{org_id}'
            {cluster_filter}
        )
        """,
        # Clean up manifest records for this org
        f"""
        DELETE FROM reporting_common_costusagereportmanifest
        WHERE provider_id IN (
            SELECT p.uuid FROM api_provider p
            JOIN api_customer c ON p.customer_id = c.id
            WHERE c.org_id = '{org_id}'
        )
        {cluster_filter}
        """,
    ]
    
    for query in cleanup_queries:
        try:
            # Execute via psql in the database pod
            result = subprocess.run(
                [
                    "oc", "exec", "-n", namespace, db_pod, "--",
                    "psql", "-U", db_user, "-d", database, "-t", "-c", query
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            
            if result.returncode == 0:
                # Try to parse deleted count from output
                output = result.stdout.strip()
                if output.startswith("DELETE"):
                    try:
                        count = int(output.split()[1])
                        records_deleted += count
                    except (IndexError, ValueError):
                        pass
            else:
                errors.append(f"Query failed: {result.stderr}")
                
        except subprocess.TimeoutExpired:
            errors.append("Query timed out")
        except Exception as e:
            errors.append(str(e))
    
    return {
        "records_deleted": records_deleted,
        "errors": errors if errors else None,
    }


def restart_valkey(namespace: str, timeout: int = 120) -> dict:
    """Restart Valkey to clear cached processing state.
    
    Args:
        namespace: Kubernetes namespace
        timeout: Timeout in seconds to wait for pod to be ready
        
    Returns:
        Dict with restart status
    """
    try:
        # Delete Valkey pod (will be recreated by deployment)
        result = subprocess.run(
            [
                "oc", "delete", "pod", "-n", namespace,
                "-l", "app.kubernetes.io/component=cache",
                "--wait=false"
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        if result.returncode != 0:
            # Try alternative label
            result = subprocess.run(
                [
                    "oc", "delete", "pod", "-n", namespace,
                    "-l", "app.kubernetes.io/component=cache",
                    "--wait=false"
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        
        # Wait for pod to be ready
        time.sleep(5)  # Brief pause before checking
        
        result = subprocess.run(
            [
                "oc", "wait", "--for=condition=ready", "pod",
                "-l", "app.kubernetes.io/component=cache",
                "-n", namespace,
                f"--timeout={timeout}s"
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 10,
        )
        
        if result.returncode != 0:
            # Try alternative label
            result = subprocess.run(
                [
                    "oc", "wait", "--for=condition=ready", "pod",
                    "-l", "app.kubernetes.io/component=cache",
                    "-n", namespace,
                    f"--timeout={timeout}s"
                ],
                capture_output=True,
                text=True,
                timeout=timeout + 10,
            )
        
        return {"success": result.returncode == 0}
        
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Timeout waiting for Valkey"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def restart_koku_listener(namespace: str, timeout: int = 180) -> dict:
    """Restart Koku listener to clear in-memory state.
    
    Args:
        namespace: Kubernetes namespace
        timeout: Timeout in seconds to wait for pod to be ready
        
    Returns:
        Dict with restart status
    """
    try:
        # Delete listener pod
        result = subprocess.run(
            [
                "oc", "delete", "pod", "-n", namespace,
                "-l", "app.kubernetes.io/component=listener",
                "--wait=false"
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        # Wait for pod to be ready
        time.sleep(10)  # Brief pause before checking
        
        result = subprocess.run(
            [
                "oc", "wait", "--for=condition=ready", "pod",
                "-l", "app.kubernetes.io/component=listener",
                "-n", namespace,
                f"--timeout={timeout}s"
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 10,
        )
        
        return {"success": result.returncode == 0}
        
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Timeout waiting for listener"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def full_cleanup(
    namespace: str,
    db_pod: str,
    org_id: str,
    s3_config: Optional[dict] = None,
    cluster_id: Optional[str] = None,
    restart_services: bool = False,
    verbose: bool = True,
    database: str = "koku",
    db_user: str = "koku",
) -> dict:
    """Perform full cleanup of test artifacts.
    
    Args:
        namespace: Kubernetes namespace
        db_pod: Database pod name
        org_id: Organization ID to clean up
        s3_config: Optional S3 configuration dict with endpoint, access_key, secret_key, bucket
        cluster_id: Optional cluster ID to limit cleanup scope
        restart_services: Whether to restart Valkey and listener (slower but more thorough)
        verbose: Whether to print progress
        database: Database name (detected from deployment, defaults to 'koku')
        db_user: Database user (detected from deployment, defaults to 'koku')
        
    Returns:
        Dict with cleanup statistics
    """
    results = {}
    
    if verbose:
        print("\n🧹 Cleaning up test artifacts...")
    
    # Clean up S3 data
    if s3_config:
        if verbose:
            print("  📦 Cleaning S3 data files...")
        s3_result = cleanup_s3_data(
            endpoint=s3_config["endpoint"],
            access_key=s3_config["access_key"],
            secret_key=s3_config["secret_key"],
            bucket=s3_config.get("bucket", "koku-bucket"),
            org_id=org_id,
            cluster_id=cluster_id,
            verify_ssl=s3_config.get("verify_ssl", False),
        )
        results["s3"] = s3_result
        if verbose:
            if s3_result.get("files_deleted", 0) > 0:
                print(f"     ✅ Deleted {s3_result['files_deleted']} S3 files")
            elif s3_result.get("error"):
                print(f"     ⚠️  S3 cleanup error: {s3_result['error']}")
    
    # Clean up database records
    if verbose:
        print("  🗄️  Cleaning database records...")
    db_result = cleanup_database_records(
        namespace=namespace,
        db_pod=db_pod,
        org_id=org_id,
        cluster_id=cluster_id,
        database=database,
        db_user=db_user,
    )
    results["database"] = db_result
    if verbose:
        if db_result.get("records_deleted", 0) > 0:
            print(f"     ✅ Deleted {db_result['records_deleted']} database records")
        elif db_result.get("errors"):
            print(f"     ⚠️  Database cleanup errors: {db_result['errors']}")
    
    # Optionally restart services
    if restart_services:
        if verbose:
            print("  🔄 Restarting Valkey...")
        valkey_result = restart_valkey(namespace)
        results["valkey"] = valkey_result
        if verbose:
            if valkey_result.get("success"):
                print("     ✅ Valkey restarted")
            else:
                print(f"     ⚠️  Valkey restart failed: {valkey_result.get('error')}")
        
        if verbose:
            print("  🔄 Restarting Koku listener...")
        listener_result = restart_koku_listener(namespace)
        results["listener"] = listener_result
        if verbose:
            if listener_result.get("success"):
                print("     ✅ Listener restarted")
            else:
                print(f"     ⚠️  Listener restart failed: {listener_result.get('error')}")
    
    if verbose:
        print("  ✅ Cleanup complete\n")
    
    return results
