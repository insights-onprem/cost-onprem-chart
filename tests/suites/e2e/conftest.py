"""
E2E suite fixtures.

Most fixtures are inherited from the root conftest.py.
This file contains E2E-specific fixtures for internal API access.
"""

import pytest
import requests

from conftest import ClusterConfig
from utils import create_pod_session


@pytest.fixture(scope="module")
def e2e_pod_session(
    test_runner_pod: str,
    cluster_config: ClusterConfig,
    rh_identity_header: str,
) -> requests.Session:
    """Pre-configured requests.Session for E2E internal API calls.
    
    This fixture provides a standard requests.Session API for making HTTP
    calls that execute inside the cluster via kubectl exec curl. It includes
    the X-Rh-Identity header required for internal service authentication.
    
    Scoped to module level to be shared across E2E test classes.
    
    Usage:
        def test_something(e2e_pod_session, koku_api_url):
            response = e2e_pod_session.get(f"{koku_api_url}/sources")
            assert response.ok
            data = response.json()
    """
    session = create_pod_session(
        namespace=cluster_config.namespace,
        pod=test_runner_pod,
        container="runner",
        headers={
            "X-Rh-Identity": rh_identity_header,
            "Content-Type": "application/json",
        },
        timeout=120,  # Longer timeout for E2E (schema creation can be slow)
    )
    return session


@pytest.fixture(scope="module")
def e2e_pod_session_no_auth(
    test_runner_pod: str,
    cluster_config: ClusterConfig,
) -> requests.Session:
    """Pre-configured requests.Session without authentication headers.
    
    Use this fixture when testing endpoints that don't require authentication
    or when you want to explicitly test authentication failures.
    """
    session = create_pod_session(
        namespace=cluster_config.namespace,
        pod=test_runner_pod,
        container="runner",
        timeout=120,
    )
    return session
