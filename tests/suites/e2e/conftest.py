"""
E2E suite fixtures.

Most fixtures are inherited from the root conftest.py.
This file contains E2E-specific fixtures for internal API access.
"""

import pytest
import requests

from conftest import ClusterConfig
from e2e_helpers import get_koku_api_url
from utils import create_identity_header_custom, create_pod_session, create_rh_identity_header


@pytest.fixture(scope="module")
def koku_api_url(cluster_config: ClusterConfig) -> str:
    """Get Koku API URL for E2E tests (unified deployment)."""
    return get_koku_api_url(cluster_config.helm_release_name, cluster_config.namespace)


@pytest.fixture(scope="module")
def rh_identity_header(org_id: str) -> str:
    """Admin X-Rh-Identity header for existing E2E tests.

    Uses is_org_admin=True which grants cost-management:*:* through the
    admin_default RBAC group. This keeps existing tests working without
    modification.
    """
    return create_rh_identity_header(org_id)


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
        timeout=120,
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


def make_user_identity(org_id: str, username: str) -> str:
    """Create a per-user X-Rh-Identity header with is_org_admin=False.

    Used for RBAC access control tests where each user gets permissions
    solely from their RBAC group membership.
    """
    return create_identity_header_custom(
        org_id=org_id,
        is_org_admin=False,
        username=username,
    )


def make_user_pod_session(
    namespace: str,
    pod: str,
    org_id: str,
    username: str,
) -> requests.Session:
    """Create a pod session authenticated as a specific user.

    The session routes requests through kubectl exec and includes the
    user's X-Rh-Identity header with is_org_admin=False so RBAC roles
    determine access.
    """
    identity = make_user_identity(org_id, username)
    return create_pod_session(
        namespace=namespace,
        pod=pod,
        container="runner",
        headers={
            "X-Rh-Identity": identity,
            "Content-Type": "application/json",
        },
        timeout=120,
    )
