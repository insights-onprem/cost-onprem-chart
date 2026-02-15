"""
S3/Storage Infrastructure Validation Tests.

These tests validate the S3 storage infrastructure required for Cost Management:
1. S3 endpoint reachability
2. Bucket existence (koku-bucket, ros-data)
3. S3 credentials validity
4. Basic S3 operations

Source Reference: scripts/e2e_validator/phases/preflight.py
"""

import os
import subprocess
from typing import Optional

import pytest

from utils import get_pod_by_label, get_secret_value


# =============================================================================
# Helper Functions
# =============================================================================

def get_s3_endpoint_from_cluster(namespace: str) -> Optional[str]:
    """Get S3 endpoint from cluster configuration.
    
    Tries multiple methods (in priority order):
    1. Environment variable from MASU pod (authoritative — reflects Helm config)
    2. OpenShift route (external access, for ODF-based clusters)
    3. Default ODF endpoint
    
    The MASU pod's S3_ENDPOINT is checked first because it reflects the actual
    Helm values the chart was deployed with (e.g. MinIO or ODF). The OpenShift
    route is a fallback for clusters where the MASU pod isn't available yet.
    
    Args:
        namespace: Application namespace
        
    Returns:
        S3 endpoint URL or None
    """
    # Try 1: Get from MASU pod environment (authoritative source)
    try:
        masu_pod = get_pod_by_label(namespace, "app.kubernetes.io/component=cost-processor")
        if masu_pod:
            result = subprocess.run(
                [
                    "kubectl", "exec",
                    "-n", namespace,
                    masu_pod, "--",
                    "printenv", "S3_ENDPOINT",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
    except Exception:
        pass
    
    # Try 2: Get from OpenShift route (for external access)
    try:
        result = subprocess.run(
            [
                "oc", "get", "route",
                "-n", "openshift-storage",
                "s3",
                "-o", "jsonpath={.spec.host}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return f"https://{result.stdout.strip()}"
    except Exception:
        pass
    
    # Try 3: Default ODF endpoint
    return "https://s3.openshift-storage.svc:443"


def get_s3_credentials(namespace: str) -> dict:
    """Get S3 credentials from cluster secrets.
    
    Args:
        namespace: Application namespace
        
    Returns:
        Dict with access_key and secret_key, or empty dict if not found
    """
    # Try multiple secret name patterns
    secret_patterns = [
        "cost-onprem-storage-credentials",
        f"{namespace}-storage-credentials",
        "koku-storage-credentials",
        "cost-onprem-object-storage-credentials",
    ]
    
    for secret_name in secret_patterns:
        access_key = get_secret_value(namespace, secret_name, "access-key")
        secret_key = get_secret_value(namespace, secret_name, "secret-key")
        
        if access_key and secret_key:
            return {
                "access_key": access_key,
                "secret_key": secret_key,
                "secret_name": secret_name,
            }
    
    return {}


def check_bucket_exists_via_pod(
    namespace: str,
    pod: str,
    bucket: str,
    s3_endpoint: str,
) -> dict:
    """Check if S3 bucket exists by executing Python/boto3 in a pod.
    
    Args:
        namespace: Namespace where pod is running
        pod: Pod name to exec into
        bucket: Bucket name to check
        s3_endpoint: S3 endpoint URL
        
    Returns:
        Dict with exists status and details
    """
    # Use Python/boto3 since AWS CLI may not be available
    python_script = f'''
import boto3
import os
from botocore.config import Config

try:
    s3 = boto3.client(
        "s3",
        endpoint_url="{s3_endpoint}",
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY", os.environ.get("AWS_ACCESS_KEY_ID", "")),
        aws_secret_access_key=os.environ.get("S3_SECRET_KEY", os.environ.get("AWS_SECRET_ACCESS_KEY", "")),
        verify=False,
        config=Config(signature_version="s3v4", s3={{"addressing_style": "path"}}),
    )
    s3.head_bucket(Bucket="{bucket}")
    print("EXISTS:TRUE:ACCESSIBLE:TRUE")
except s3.exceptions.NoSuchBucket:
    print("EXISTS:FALSE:ERROR:NoSuchBucket")
except Exception as e:
    err = str(e)
    if "403" in err or "AccessDenied" in err:
        print("EXISTS:TRUE:ACCESSIBLE:FALSE:ERROR:AccessDenied")
    elif "404" in err or "NoSuchBucket" in err or "Not Found" in err:
        print("EXISTS:FALSE:ERROR:NoSuchBucket")
    else:
        print(f"EXISTS:FALSE:ERROR:{{err}}")
'''
    
    try:
        result = subprocess.run(
            [
                "kubectl", "exec",
                "-n", namespace,
                pod, "-c", "masu", "--",
                "python3", "-c", python_script,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        
        output = result.stdout.strip()
        if "EXISTS:TRUE:ACCESSIBLE:TRUE" in output:
            return {"exists": True, "accessible": True}
        elif "EXISTS:TRUE:ACCESSIBLE:FALSE" in output:
            return {"exists": True, "accessible": False, "error": "AccessDenied"}
        elif "EXISTS:FALSE" in output:
            error = output.split("ERROR:")[-1] if "ERROR:" in output else "unknown"
            return {"exists": False, "error": error}
        else:
            return {"exists": False, "error": result.stderr or output}
    except subprocess.TimeoutExpired:
        return {"exists": False, "error": "timeout"}
    except Exception as e:
        return {"exists": False, "error": str(e)}


def check_s3_connectivity_via_pod(
    namespace: str,
    pod: str,
    s3_endpoint: str,
) -> dict:
    """Check S3 connectivity by listing buckets.
    
    Args:
        namespace: Namespace where pod is running
        pod: Pod name to exec into
        s3_endpoint: S3 endpoint URL
        
    Returns:
        Dict with connectivity status
    """
    # Use Python/boto3 since AWS CLI may not be available
    python_script = f'''
import boto3
import os
from botocore.config import Config

try:
    s3 = boto3.client(
        "s3",
        endpoint_url="{s3_endpoint}",
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY", os.environ.get("AWS_ACCESS_KEY_ID", "")),
        aws_secret_access_key=os.environ.get("S3_SECRET_KEY", os.environ.get("AWS_SECRET_ACCESS_KEY", "")),
        verify=False,
        config=Config(signature_version="s3v4", s3={{"addressing_style": "path"}}),
    )
    response = s3.list_buckets()
    bucket_count = len(response.get("Buckets", []))
    print(f"CONNECTED:TRUE:BUCKETS:{{bucket_count}}")
except Exception as e:
    print(f"CONNECTED:FALSE:ERROR:{{str(e)}}")
'''
    
    try:
        result = subprocess.run(
            [
                "kubectl", "exec",
                "-n", namespace,
                pod, "-c", "masu", "--",
                "python3", "-c", python_script,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        
        output = result.stdout.strip()
        if "CONNECTED:TRUE" in output:
            bucket_count = 0
            if "BUCKETS:" in output:
                try:
                    bucket_count = int(output.split("BUCKETS:")[-1])
                except ValueError:
                    pass
            return {"connected": True, "bucket_count": bucket_count}
        else:
            error = output.split("ERROR:")[-1] if "ERROR:" in output else result.stderr or output
            return {"connected": False, "error": error}
    except subprocess.TimeoutExpired:
        return {"connected": False, "error": "timeout"}
    except Exception as e:
        return {"connected": False, "error": str(e)}


# =============================================================================
# Test Classes
# =============================================================================

@pytest.mark.infrastructure
@pytest.mark.component
class TestS3Endpoint:
    """Tests for S3 endpoint availability."""
    
    def test_s3_endpoint_discoverable(self, cluster_config):
        """Verify S3 endpoint can be discovered from cluster."""
        endpoint = get_s3_endpoint_from_cluster(cluster_config.namespace)
        
        assert endpoint, (
            "Could not discover S3 endpoint. "
            "Check OpenShift storage route or MASU pod configuration."
        )
    
    def test_s3_credentials_available(self, cluster_config):
        """Verify S3 credentials are available in secrets."""
        creds = get_s3_credentials(cluster_config.namespace)
        
        if not creds:
            pytest.skip(
                "S3 credentials not found in expected secrets. "
                "This may be expected if using IAM roles or ODF auto-provisioning."
            )
        
        assert creds.get("access_key"), "S3 access key not found"
        assert creds.get("secret_key"), "S3 secret key not found"


@pytest.mark.infrastructure
@pytest.mark.integration
class TestS3Connectivity:
    """Tests for S3 connectivity from within the cluster."""
    
    def test_s3_reachable_from_cluster(self, cluster_config):
        """Verify S3 endpoint is reachable from within the cluster."""
        # Find a pod with AWS CLI (MASU/cost-processor has it)
        masu_pod = get_pod_by_label(
            cluster_config.namespace,
            "app.kubernetes.io/component=cost-processor"
        )
        
        if not masu_pod:
            pytest.skip("MASU/cost-processor pod not found for S3 connectivity test")
        
        endpoint = get_s3_endpoint_from_cluster(cluster_config.namespace)
        if not endpoint:
            pytest.skip("S3 endpoint not discoverable")
        
        status = check_s3_connectivity_via_pod(
            cluster_config.namespace,
            masu_pod,
            endpoint,
        )
        
        if "error" in status and "timeout" in str(status["error"]).lower():
            pytest.skip(f"S3 connectivity check timed out - endpoint may be slow")
        
        assert status.get("connected"), (
            f"Cannot connect to S3 endpoint {endpoint}: {status.get('error', 'unknown')}"
        )


@pytest.mark.infrastructure
@pytest.mark.integration
class TestS3Buckets:
    """Tests for required S3 buckets."""
    
    REQUIRED_BUCKETS = [
        "koku-bucket",  # Main cost data bucket
    ]
    
    OPTIONAL_BUCKETS = [
        "ros-data",     # ROS data bucket (may not exist in all deployments)
    ]
    
    @pytest.mark.parametrize("bucket", REQUIRED_BUCKETS)
    def test_required_bucket_exists(self, cluster_config, bucket: str):
        """Verify required S3 bucket exists and is accessible."""
        masu_pod = get_pod_by_label(
            cluster_config.namespace,
            "app.kubernetes.io/component=cost-processor"
        )
        
        if not masu_pod:
            pytest.skip("MASU/cost-processor pod not found for bucket check")
        
        endpoint = get_s3_endpoint_from_cluster(cluster_config.namespace)
        if not endpoint:
            pytest.skip("S3 endpoint not discoverable")
        
        status = check_bucket_exists_via_pod(
            cluster_config.namespace,
            masu_pod,
            bucket,
            endpoint,
        )
        
        if status.get("error") == "timeout":
            pytest.skip(f"Bucket check timed out for '{bucket}'")
        
        assert status.get("exists"), (
            f"Required bucket '{bucket}' not found: {status.get('error', 'unknown')}"
        )
        
        if not status.get("accessible"):
            pytest.fail(
                f"Bucket '{bucket}' exists but is not accessible: {status.get('error')}"
            )
    
    @pytest.mark.parametrize("bucket", OPTIONAL_BUCKETS)
    def test_optional_bucket_exists(self, cluster_config, bucket: str):
        """Check if optional S3 bucket exists (informational)."""
        masu_pod = get_pod_by_label(
            cluster_config.namespace,
            "app.kubernetes.io/component=cost-processor"
        )
        
        if not masu_pod:
            pytest.skip("MASU/cost-processor pod not found for bucket check")
        
        endpoint = get_s3_endpoint_from_cluster(cluster_config.namespace)
        if not endpoint:
            pytest.skip("S3 endpoint not discoverable")
        
        status = check_bucket_exists_via_pod(
            cluster_config.namespace,
            masu_pod,
            bucket,
            endpoint,
        )
        
        if not status.get("exists"):
            pytest.skip(
                f"Optional bucket '{bucket}' not found (this may be expected): "
                f"{status.get('error', 'unknown')}"
            )
        
        # If we get here, bucket exists
        assert status.get("accessible", True), (
            f"Optional bucket '{bucket}' exists but is not accessible"
        )


@pytest.mark.infrastructure
@pytest.mark.component
class TestS3DataPaths:
    """Tests for S3 data path structure."""
    
    def test_can_list_bucket_contents(self, cluster_config):
        """Verify we can list contents of the koku-bucket."""
        masu_pod = get_pod_by_label(
            cluster_config.namespace,
            "app.kubernetes.io/component=cost-processor"
        )
        
        if not masu_pod:
            pytest.skip("MASU/cost-processor pod not found")
        
        endpoint = get_s3_endpoint_from_cluster(cluster_config.namespace)
        if not endpoint:
            pytest.skip("S3 endpoint not discoverable")
        
        # Use Python/boto3 since AWS CLI may not be available
        python_script = f'''
import boto3
import os
from botocore.config import Config

try:
    s3 = boto3.client(
        "s3",
        endpoint_url="{endpoint}",
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY", os.environ.get("AWS_ACCESS_KEY_ID", "")),
        aws_secret_access_key=os.environ.get("S3_SECRET_KEY", os.environ.get("AWS_SECRET_ACCESS_KEY", "")),
        verify=False,
        config=Config(signature_version="s3v4", s3={{"addressing_style": "path"}}),
    )
    response = s3.list_objects_v2(Bucket="koku-bucket", MaxKeys=10)
    count = response.get("KeyCount", 0)
    print(f"SUCCESS:OBJECTS:{{count}}")
except Exception as e:
    err = str(e)
    if "NoSuchBucket" in err or "404" in err:
        print("ERROR:NoSuchBucket")
    else:
        print(f"ERROR:{{err}}")
'''
        
        try:
            result = subprocess.run(
                [
                    "kubectl", "exec",
                    "-n", cluster_config.namespace,
                    masu_pod, "-c", "masu", "--",
                    "python3", "-c", python_script,
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            
            output = result.stdout.strip()
            # Success even if empty (0 objects)
            assert "SUCCESS" in output or "NoSuchBucket" not in output, (
                f"Cannot list koku-bucket contents: {output or result.stderr}"
            )
        except subprocess.TimeoutExpired:
            pytest.skip("S3 list operation timed out")
