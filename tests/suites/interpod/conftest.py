"""
Fixtures for interpod (pod-to-pod) cluster tests.

These fixtures provide helpers for executing commands inside the cluster
via the dedicated test-runner pod, testing internal service networking.
"""

import json
import pytest
from dataclasses import dataclass
from typing import Callable, Optional

from conftest import ClusterConfig
from utils import run_oc_command, create_rh_identity_header


@dataclass
class CurlResult:
    """Result from internal curl command."""
    stdout: str
    stderr: str
    returncode: int
    
    @property
    def ok(self) -> bool:
        """Check if the curl command succeeded."""
        return self.returncode == 0
    
    def json(self) -> dict:
        """Parse stdout as JSON."""
        return json.loads(self.stdout)


@pytest.fixture
def internal_curl(
    test_runner_pod: str,
    cluster_config: ClusterConfig,
) -> Callable:
    """Helper to execute curl commands from the test runner pod.
    
    This fixture provides a callable that executes curl commands inside
    the cluster, useful for testing internal service endpoints.
    
    Usage:
        def test_something(internal_curl, internal_api_url):
            result = internal_curl(f"{internal_api_url}/api/v1/status/")
            assert result.ok
            data = result.json()
            assert data["status"] == "ok"
    
    Args:
        url: The URL to request
        method: HTTP method (GET, POST, etc.)
        headers: Optional dict of headers to include
        data: Optional request body (string)
        
    Returns:
        CurlResult with stdout, stderr, and returncode
    """
    def _curl(
        url: str,
        method: str = "GET",
        headers: Optional[dict] = None,
        data: Optional[str] = None,
    ) -> CurlResult:
        cmd = ["curl", "-s", "-X", method]
        
        # Add headers
        for key, value in (headers or {}).items():
            cmd.extend(["-H", f"{key}: {value}"])
        
        # Add data
        if data:
            cmd.extend(["-d", data])
        
        cmd.append(url)
        
        # Build full exec command
        args = ["exec", "-n", cluster_config.namespace, test_runner_pod, "-c", "runner", "--"]
        args.extend(cmd)
        
        result = run_oc_command(args, check=False, timeout=60)
        
        return CurlResult(
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode,
        )
    
    return _curl


@pytest.fixture
def internal_identity_header(cluster_config: ClusterConfig, org_id: str) -> str:
    """Pre-built X-Rh-Identity header for internal API calls.
    
    Internal services expect the X-Rh-Identity header that would normally
    be injected by the gateway. This fixture provides a valid header for
    direct service-to-service testing.
    """
    return create_rh_identity_header(
        org_id=org_id,
        account_number="1234567",
    )
