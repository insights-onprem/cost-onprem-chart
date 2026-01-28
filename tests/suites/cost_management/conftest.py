"""
Cost Management (Koku) suite fixtures.

Note: Sources API has been merged into Koku. All sources endpoints are now
available via the Koku API at /api/cost-management/v1/ using X-Rh-Identity
header for authentication instead of x-rh-sources-org-id.
"""

import pytest

from utils import create_rh_identity_header, get_pod_by_label


@pytest.fixture(scope="module")
def koku_api_writes_url(cluster_config) -> str:
    """Get Koku API writes URL for operations that modify state (POST, PUT, DELETE).
    
    The Koku deployment separates reads/writes for scalability.
    """
    return (
        f"http://{cluster_config.helm_release_name}-koku-api-writes."
        f"{cluster_config.namespace}.svc.cluster.local:8000/api/cost-management/v1"
    )


@pytest.fixture(scope="module")
def koku_api_reads_url(cluster_config) -> str:
    """Get Koku API reads URL for read-only operations (GET).
    
    The Koku deployment separates reads/writes for scalability.
    """
    return (
        f"http://{cluster_config.helm_release_name}-koku-api-reads."
        f"{cluster_config.namespace}.svc.cluster.local:8000/api/cost-management/v1"
    )


@pytest.fixture(scope="module")
def ingress_pod(cluster_config) -> str:
    """Get ingress pod name for executing API calls.
    
    The ingress pod has NetworkPolicy access to koku-api, so we use it
    to make internal API calls.
    """
    pod = get_pod_by_label(
        cluster_config.namespace,
        "app.kubernetes.io/component=ingress"
    )
    if not pod:
        pytest.skip("Ingress pod not found for API calls")
    return pod


@pytest.fixture(scope="module")
def rh_identity_header(org_id) -> str:
    """Get X-Rh-Identity header value for the test org."""
    return create_rh_identity_header(org_id)
