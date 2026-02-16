"""
Sources API suite fixtures.

Fixtures for testing the Sources API endpoints now served by Koku.
All sources endpoints are available via /api/cost-management/v1/
using X-Rh-Identity header for authentication.

Uses PodAdapter/pod_session for all HTTP calls to internal services.
The test_runner_pod fixture from conftest.py provides the dedicated
test-runner pod for executing internal API calls.
"""

import base64
import json
import time
import uuid
from typing import Any, Dict, Generator, Optional

import pytest
import requests

from e2e_helpers import get_koku_api_url
from utils import (
    create_identity_header_custom,
    create_pod_session,
    create_rh_identity_header,
)


@pytest.fixture(scope="module")
def koku_api_url(cluster_config) -> str:
    """Get Koku API URL for all operations (unified deployment)."""
    return get_koku_api_url(cluster_config.helm_release_name, cluster_config.namespace)


@pytest.fixture(scope="module")
def rh_identity_header(org_id) -> str:
    """Get X-Rh-Identity header value for the test org."""
    return create_rh_identity_header(org_id)


@pytest.fixture(scope="module")
def pod_session(
    test_runner_pod: str,
    cluster_config,
    rh_identity_header: str,
) -> requests.Session:
    """Pre-configured requests.Session that routes through the test-runner pod.
    
    This fixture provides a standard requests.Session API for making HTTP
    calls that execute inside the cluster via kubectl exec curl. It includes
    the X-Rh-Identity header required for internal service authentication.
    """
    session = create_pod_session(
        namespace=cluster_config.namespace,
        pod=test_runner_pod,
        container="runner",
        headers={
            "X-Rh-Identity": rh_identity_header,
            "Content-Type": "application/json",
        },
        timeout=60,
    )
    return session


@pytest.fixture(scope="module")
def pod_session_no_auth(
    test_runner_pod: str,
    cluster_config,
) -> requests.Session:
    """Pre-configured requests.Session without authentication headers.
    
    Use this fixture when testing endpoints that don't require authentication
    or when you want to explicitly test authentication failures with custom headers.
    """
    session = create_pod_session(
        namespace=cluster_config.namespace,
        pod=test_runner_pod,
        container="runner",
        timeout=60,
    )
    return session


@pytest.fixture(scope="module")
def invalid_identity_headers(org_id: str) -> Dict[str, str]:
    """Dict of invalid headers for authentication error testing.

    Returns a dictionary with various invalid header configurations:
    - malformed_base64: Invalid base64 string
    - invalid_json: Valid base64 but invalid JSON content
    - no_entitlements: Missing cost_management entitlement
    - not_entitled: cost_management is_entitled=False
    - non_admin: is_org_admin=False
    - no_email: Missing email field
    """
    return {
        "malformed_base64": "not-valid-base64!!!",
        "invalid_json": base64.b64encode(b"not valid json").decode(),
        "no_entitlements": create_identity_header_custom(
            org_id=org_id,
            entitlements={},  # Empty entitlements
        ),
        "not_entitled": create_identity_header_custom(
            org_id=org_id,
            entitlements={
                "cost_management": {
                    "is_entitled": False,
                },
            },
        ),
        "non_admin": create_identity_header_custom(
            org_id=org_id,
            is_org_admin=False,
        ),
        "no_email": create_identity_header_custom(
            org_id=org_id,
            email=None,  # Omit email field
        ),
    }


@pytest.fixture(scope="function")
def test_source(
    pod_session: requests.Session,
    koku_api_url: str,
) -> Generator[Dict[str, Any], None, None]:
    """Create a test source with automatic cleanup.

    This fixture creates a source for tests that need an existing source,
    and automatically deletes it after the test completes.

    Yields:
        dict with keys: source_id, source_name, cluster_id, source_type_id
    """
    # Get source type ID with retry
    source_type_id = None
    for attempt in range(3):
        try:
            response = pod_session.get(f"{koku_api_url}/source_types")
            if response.ok:
                data = response.json()
                for st in data.get("data", []):
                    if st.get("name") == "openshift":
                        source_type_id = str(st.get("id"))
                        break
        except Exception:
            pass
        if source_type_id:
            break
        time.sleep(2)

    if not source_type_id:
        pytest.fail("Could not get OpenShift source type ID - this indicates a deployment issue")

    # Create source with retry logic for transient CI failures
    last_error: Optional[str] = None
    max_attempts: int = 5
    source_data: Optional[Dict] = None

    test_cluster_id = f"test-source-{uuid.uuid4().hex[:8]}"
    source_name = f"test-source-{uuid.uuid4().hex[:8]}"

    for attempt in range(max_attempts):
        try:
            response = pod_session.post(
                f"{koku_api_url}/sources",
                json={
                    "name": source_name,
                    "source_type_id": source_type_id,
                    "source_ref": test_cluster_id,
                },
            )

            # Retry on 5xx server errors only
            if response.status_code >= 500:
                last_error = f"Attempt {attempt + 1}: Server error {response.status_code}"
                time.sleep(3)
                continue

            # Success or client error - exit loop
            if response.status_code == 201:
                source_data = response.json()
                break
            else:
                last_error = f"Attempt {attempt + 1}: {response.status_code} - {response.text[:200]}"
                break

        except Exception as e:
            last_error = f"Attempt {attempt + 1}: {e}"
            time.sleep(3)
            continue

    if not source_data:
        pytest.fail(f"Source creation failed after {max_attempts} attempts. Last error: {last_error}")

    source_id = source_data.get("id")
    if not source_id:
        pytest.fail(f"Source creation failed - no ID in response: {source_data}")

    yield {
        "source_id": source_id,
        "source_name": source_name,
        "cluster_id": test_cluster_id,
        "source_type_id": source_type_id,
    }

    # Cleanup: Delete the source
    try:
        response = pod_session.delete(f"{koku_api_url}/sources/{source_id}")
        if response.status_code not in [204, 404]:
            print(f"Warning: Failed to delete test source {source_id}, status: {response.status_code}")
    except Exception as e:
        print(f"Warning: Failed to delete test source {source_id}: {e}")
