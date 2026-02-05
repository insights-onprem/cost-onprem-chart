"""
Sources API suite fixtures.

Fixtures for testing the Sources API endpoints now served by Koku.
All sources endpoints are available via /api/cost-management/v1/
using X-Rh-Identity header for authentication.
"""

import base64
import json
import time
import uuid
from typing import Any, Dict, Generator, Optional

import pytest

from e2e_helpers import get_source_type_id
from utils import (
    create_identity_header_custom,
    create_rh_identity_header,
    exec_in_pod,
    get_pod_by_label,
)


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
    cluster_config: Any,
    ingress_pod: str,
    koku_api_reads_url: str,
    koku_api_writes_url: str,
    rh_identity_header: str,
    org_id: str,
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
        source_type_id = get_source_type_id(
            cluster_config.namespace,
            ingress_pod,
            koku_api_reads_url,
            rh_identity_header,
            container="ingress",
        )
        if source_type_id:
            break
        time.sleep(2)

    if not source_type_id:
        pytest.fail("Could not get OpenShift source type ID - this indicates a deployment issue")

    # Create source with retry logic for transient CI failures
    result: Optional[str] = None
    last_error: Optional[str] = None
    max_attempts: int = 5
    status_code: Optional[str] = None

    test_cluster_id = f"test-source-{uuid.uuid4().hex[:8]}"
    source_name = f"test-source-{uuid.uuid4().hex[:8]}"

    for attempt in range(max_attempts):
        source_payload = json.dumps({
            "name": source_name,
            "source_type_id": source_type_id,
            "source_ref": test_cluster_id,
        })

        raw_result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-w", "\n%{http_code}", "-X", "POST",
                f"{koku_api_writes_url}/sources",
                "-H", "Content-Type: application/json",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
                "-d", source_payload,
            ],
            container="ingress",
        )

        if not raw_result:
            last_error = f"Attempt {attempt + 1}: No response from source creation"
            time.sleep(3)
            continue

        # Parse response and status code
        lines = raw_result.strip().split('\n')
        status_code = lines[-1] if lines else ""
        body = '\n'.join(lines[:-1]) if len(lines) > 1 else ""

        # Retry on 5xx server errors only
        if status_code.startswith('5'):
            last_error = f"Attempt {attempt + 1}: Server error {status_code}"
            time.sleep(3)
            continue

        # Success or client error - exit loop
        result = body
        break

    if not result:
        pytest.fail(f"Source creation failed after {max_attempts} attempts. Last error: {last_error}")

    try:
        source_data = json.loads(result)
    except json.JSONDecodeError:
        pytest.fail(f"Source creation returned invalid JSON (status {status_code}): {result}")

    source_id = source_data.get("id")
    if not source_id:
        pytest.fail(f"Source creation failed (status {status_code}): {result}")

    yield {
        "source_id": source_id,
        "source_name": source_name,
        "cluster_id": test_cluster_id,
        "source_type_id": source_type_id,
    }

    # Cleanup: Delete the source
    delete_result = exec_in_pod(
        cluster_config.namespace,
        ingress_pod,
        [
            "curl", "-s", "-w", "\n%{http_code}", "-X", "DELETE",
            f"{koku_api_writes_url}/sources/{source_id}",
            "-H", f"X-Rh-Identity: {rh_identity_header}",
        ],
        container="ingress",
    )

    # Verify deletion succeeded
    if delete_result:
        lines = delete_result.strip().split('\n')
        status = lines[-1] if lines else ""
        if status not in ["204", "404"]:
            print(f"Warning: Failed to delete test source {source_id}, status: {status}")
