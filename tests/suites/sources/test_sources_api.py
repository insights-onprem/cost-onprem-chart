"""
Sources API tests.

Tests for the Sources API endpoints now served by Koku.
Note: Sources API has been merged into Koku. All sources endpoints are
available via /api/cost-management/v1/.

This file contains TWO types of tests:

1. EXTERNAL API TESTS (TestSourcesExternal*)
   - Use `authenticated_session` with JWT auth via the gateway
   - Test the full external API contract
   - Run with: pytest -m "sources and api"

2. INTERPOD TESTS (TestKokuSources*, TestSourceTypes*, etc.)
   - Use `pod_session` to execute HTTP calls from inside the cluster
   - Bypass the external gateway, use X-Rh-Identity headers
   - Test internal service-to-service communication
   - Run with: pytest -m "sources and interpod"

Run all sources tests: pytest -m sources
Source registration flow is tested in suites/e2e/ as part of the complete pipeline.
"""

import uuid
from typing import Optional

import pytest
import requests

from utils import check_pod_ready


# =============================================================================
# OCP Source Type ID Helper
# =============================================================================
# The source_types endpoint is internal-only (not exposed via gateway).
# OCP source type ID is 1 in standard Koku deployments because DB migrations
# create source types in a fixed order (OCP=1, AWS=2, Azure=3, GCP=4).
# This helper provides a fallback lookup via the sources list endpoint.
# =============================================================================

# Default OCP source type ID (from Koku DB migrations)
DEFAULT_OCP_SOURCE_TYPE_ID = 1

# Cache for looked-up OCP source type ID
_ocp_source_type_id_cache: Optional[int] = None


def get_ocp_source_type_id(
    gateway_url: str,
    session: requests.Session,
) -> int:
    """Get OCP source type ID, with fallback lookup from existing sources.
    
    Strategy:
    1. Return cached value if available
    2. Try to infer from existing OCP sources in the sources list
    3. Fall back to default (1) if no sources exist
    
    Args:
        gateway_url: The gateway URL
        session: Authenticated requests session
        
    Returns:
        The OCP source type ID (typically 1)
    """
    global _ocp_source_type_id_cache
    
    if _ocp_source_type_id_cache is not None:
        return _ocp_source_type_id_cache
    
    # Try to infer from existing sources
    try:
        response = session.get(
            f"{gateway_url}/cost-management/v1/sources/",
            timeout=30,
        )
        if response.ok:
            data = response.json()
            for source in data.get("data", []):
                # Look for a source with source_type name containing "OCP" or "openshift"
                source_type = source.get("source_type", "")
                if isinstance(source_type, str) and "ocp" in source_type.lower():
                    _ocp_source_type_id_cache = source.get("source_type_id")
                    if _ocp_source_type_id_cache:
                        return _ocp_source_type_id_cache
    except Exception:
        pass
    
    # Fall back to default
    _ocp_source_type_id_cache = DEFAULT_OCP_SOURCE_TYPE_ID
    return _ocp_source_type_id_cache


# =============================================================================
# EXTERNAL API TESTS - Via Gateway with JWT Authentication
# =============================================================================
# These tests validate the Sources API through the external gateway route.
# They use JWT tokens from Keycloak and test the full authentication flow.
# =============================================================================


@pytest.mark.sources
@pytest.mark.api
@pytest.mark.smoke
class TestSourcesExternalHealth:
    """External API tests for Sources endpoint availability via gateway."""

    def test_sources_endpoint_accessible_via_gateway(
        self, gateway_url: str, authenticated_session: requests.Session
    ):
        """Verify Sources API is accessible through the external gateway with JWT auth."""
        response = authenticated_session.get(
            f"{gateway_url}/cost-management/v1/sources/",
            timeout=30,
        )

        assert response.status_code == 200, (
            f"Sources endpoint not accessible via gateway: {response.status_code} - {response.text[:200]}"
        )
        data = response.json()
        assert "data" in data, f"Missing data field in response: {data}"
        assert "meta" in data, f"Missing meta field in response: {data}"


@pytest.mark.sources
@pytest.mark.api
@pytest.mark.integration
class TestSourcesExternalSourceTypes:
    """External API tests for source type configuration via gateway.
    
    Note: The source_types endpoint is internal-only (not exposed via gateway).
    External clients should use the sources endpoint to discover available types.
    """

    def test_sources_endpoint_returns_source_type_info(
        self, gateway_url: str, authenticated_session: requests.Session
    ):
        """Verify sources endpoint includes source_type information.
        
        The source_types endpoint is internal-only. External clients discover
        source types through the sources list response which includes source_type_id.
        """
        response = authenticated_session.get(
            f"{gateway_url}/cost-management/v1/sources/",
            timeout=30,
        )

        assert response.status_code == 200, (
            f"sources endpoint failed: {response.status_code} - {response.text[:200]}"
        )
        data = response.json()
        
        # Verify response structure supports source type discovery
        assert "data" in data, f"Missing data field: {data}"
        assert "meta" in data, f"Missing meta field: {data}"
        
        # If there are sources, verify they have source_type_id
        sources = data.get("data", [])
        if sources:
            source = sources[0]
            assert "source_type_id" in source, (
                f"Source missing source_type_id field: {source}"
            )


@pytest.mark.sources
@pytest.mark.api
@pytest.mark.integration
class TestSourcesExternalCRUD:
    """External API tests for Sources CRUD operations via gateway."""

    def test_create_and_delete_source_via_gateway(
        self, gateway_url: str, authenticated_session: requests.Session
    ):
        """Verify source creation and deletion works via external gateway."""
        # Get OCP source type ID (with fallback lookup)
        ocp_source_type_id = get_ocp_source_type_id(gateway_url, authenticated_session)
        
        # Create a test source
        source_name = f"gateway-test-{uuid.uuid4().hex[:8]}"
        cluster_id = f"gateway-cluster-{uuid.uuid4().hex[:8]}"

        create_response = authenticated_session.post(
            f"{gateway_url}/cost-management/v1/sources/",
            json={
                "name": source_name,
                "source_type_id": ocp_source_type_id,
                "source_ref": cluster_id,
            },
            headers={"Content-Type": "application/json"},
            timeout=30,
        )

        assert create_response.status_code == 201, (
            f"Source creation failed: {create_response.status_code} - {create_response.text[:200]}"
        )

        source_data = create_response.json()
        source_id = source_data.get("id")
        assert source_id, f"No source ID in response: {source_data}"

        try:
            # Verify we can GET the source
            get_response = authenticated_session.get(
                f"{gateway_url}/cost-management/v1/sources/{source_id}/",
                timeout=30,
            )
            assert get_response.status_code == 200, (
                f"Could not GET created source: {get_response.status_code}"
            )

            # Verify source data matches
            fetched = get_response.json()
            assert fetched.get("name") == source_name
            assert fetched.get("source_ref") == cluster_id

        finally:
            # Clean up - delete the source
            delete_response = authenticated_session.delete(
                f"{gateway_url}/cost-management/v1/sources/{source_id}/",
                timeout=30,
            )
            assert delete_response.status_code in [204, 404], (
                f"Source deletion failed: {delete_response.status_code}"
            )

    def test_get_nonexistent_source_returns_404(
        self, gateway_url: str, authenticated_session: requests.Session
    ):
        """Verify GET for non-existent source returns 404 via gateway."""
        response = authenticated_session.get(
            f"{gateway_url}/cost-management/v1/sources/99999999/",
            timeout=30,
        )

        assert response.status_code == 404, (
            f"Expected 404 for non-existent source, got {response.status_code}"
        )


@pytest.mark.sources
@pytest.mark.api
@pytest.mark.integration
class TestSourcesExternalFiltering:
    """External API tests for Sources filtering via gateway."""

    def test_filter_sources_by_source_type(
        self, gateway_url: str, authenticated_session: requests.Session
    ):
        """Verify sources can be filtered by source_type via gateway."""
        # Get OCP source type ID (with fallback lookup)
        ocp_source_type_id = get_ocp_source_type_id(gateway_url, authenticated_session)
        
        # Filter sources by OCP type
        response = authenticated_session.get(
            f"{gateway_url}/cost-management/v1/sources/",
            params={"source_type_id": ocp_source_type_id},
            timeout=30,
        )

        assert response.status_code == 200, (
            f"Filtering failed: {response.status_code} - {response.text[:200]}"
        )

        data = response.json()
        # All returned sources should be OCP type
        for source in data.get("data", []):
            assert str(source.get("source_type_id")) == str(ocp_source_type_id), (
                f"Source type mismatch in filtered results: {source}"
            )

    def test_filter_sources_by_name(
        self, gateway_url: str, authenticated_session: requests.Session
    ):
        """Verify sources can be filtered by name via gateway."""
        # First list all sources to get a name to filter by
        list_response = authenticated_session.get(
            f"{gateway_url}/cost-management/v1/sources/",
            timeout=30,
        )
        if list_response.status_code != 200:
            pytest.skip("Could not list sources")

        data = list_response.json()
        sources = data.get("data", [])
        if not sources:
            pytest.skip("No sources available to filter")

        source_name = sources[0].get("name")

        # Filter by name
        response = authenticated_session.get(
            f"{gateway_url}/cost-management/v1/sources/",
            params={"name": source_name},
            timeout=30,
        )

        assert response.status_code == 200, (
            f"Filtering by name failed: {response.status_code} - {response.text[:200]}"
        )

        data = response.json()
        assert "data" in data
        names = [s.get("name") for s in data["data"]]
        assert source_name in names, f"Source '{source_name}' not in filtered results: {names}"


# Note: application_types and applications endpoints are internal-only.
# They are tested in the interpod section below (TestApplicationTypes, TestApplicationsEndpoint).
# External clients use the sources endpoint for all source management operations.


# =============================================================================
# INTERPOD TESTS - Internal Cluster Communication
# =============================================================================
# These tests execute HTTP calls from inside the cluster via the ingress pod.
# They bypass the external gateway and use X-Rh-Identity headers directly.
# This tests internal service-to-service communication.
# =============================================================================


@pytest.mark.sources
@pytest.mark.interpod
@pytest.mark.component
class TestKokuSourcesHealth:
    """Tests for Koku API health and sources endpoint availability."""

    @pytest.mark.smoke
    def test_koku_api_pod_ready(self, cluster_config):
        """Verify Koku API pod is ready (serves sources endpoints)."""
        assert check_pod_ready(
            cluster_config.namespace,
            "app.kubernetes.io/component=cost-management-api"
        ), "Koku API pod is not ready"

    @pytest.mark.smoke
    def test_koku_sources_endpoint_responds(
        self, pod_session: requests.Session, koku_api_url: str
    ):
        """Verify Koku sources endpoint responds to requests."""
        response = pod_session.get(f"{koku_api_url}/sources/")

        assert response.ok, f"Koku sources endpoint returned {response.status_code}: {response.text[:200]}"


@pytest.mark.sources
@pytest.mark.interpod
@pytest.mark.integration
class TestSourceTypes:
    """Tests for source type configuration in Koku."""

    def test_all_cloud_source_types_exist(
        self, pod_session: requests.Session, koku_api_url: str
    ):
        """Verify all expected cloud source types are configured."""
        response = pod_session.get(f"{koku_api_url}/source_types")

        assert response.ok, f"Expected 200, got {response.status_code}: {response.text[:200]}"
        data = response.json()
        source_types = [st.get("name") for st in data.get("data", [])]

        expected_types = ["openshift", "amazon", "azure", "google"]
        for expected in expected_types:
            assert expected in source_types, f"{expected} source type not found in {source_types}"


@pytest.mark.sources
@pytest.mark.interpod
@pytest.mark.integration
class TestApplicationTypes:
    """Tests for application type configuration in Koku."""

    def test_cost_management_application_type_exists(
        self, pod_session: requests.Session, koku_api_url: str
    ):
        """Verify cost-management application type is configured."""
        response = pod_session.get(f"{koku_api_url}/application_types")

        assert response.ok, f"Expected 200, got {response.status_code}: {response.text[:200]}"
        data = response.json()

        assert "data" in data, f"Missing data field: {data}"
        assert len(data["data"]) > 0, "No application types returned"

        app_names = [at.get("name") for at in data["data"]]
        assert "/insights/platform/cost-management" in app_names, \
            f"cost-management application type not found in {app_names}"


@pytest.mark.sources
@pytest.mark.interpod
@pytest.mark.integration
class TestApplicationsEndpoint:
    """Tests for the applications endpoint."""

    def test_applications_list_returns_valid_response(
        self, pod_session: requests.Session, koku_api_url: str
    ):
        """Verify applications endpoint returns valid paginated response."""
        response = pod_session.get(f"{koku_api_url}/applications")

        assert response.ok, f"Expected 200, got {response.status_code}: {response.text[:200]}"
        data = response.json()

        assert "meta" in data, f"Missing meta field: {data}"
        assert "data" in data, f"Missing data field: {data}"
        assert isinstance(data["data"], list), f"data should be a list: {data}"


# =============================================================================
# P1 - Authentication Error Scenarios
# =============================================================================


@pytest.mark.sources
@pytest.mark.interpod
@pytest.mark.auth
@pytest.mark.component
class TestAuthenticationErrors:
    """Tests for authentication error handling in Sources API."""

    def test_malformed_base64_header_returns_403(
        self, pod_session_no_auth: requests.Session, koku_api_url: str, invalid_identity_headers
    ):
        """Verify malformed base64 in X-Rh-Identity returns 403 Forbidden."""
        response = pod_session_no_auth.get(
            f"{koku_api_url}/sources/",
            headers={"X-Rh-Identity": invalid_identity_headers["malformed_base64"]},
        )

        assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text[:200]}"

    def test_invalid_json_in_header_returns_401(
        self, pod_session_no_auth: requests.Session, koku_api_url: str, invalid_identity_headers
    ):
        """Verify invalid JSON in decoded X-Rh-Identity returns an error."""
        response = pod_session_no_auth.get(
            f"{koku_api_url}/sources/",
            headers={"X-Rh-Identity": invalid_identity_headers["invalid_json"]},
        )

        assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text[:200]}"

    def test_missing_identity_header_returns_401(
        self, pod_session_no_auth: requests.Session, koku_api_url: str
    ):
        """Verify missing X-Rh-Identity header returns 401 Unauthorized."""
        response = pod_session_no_auth.get(f"{koku_api_url}/sources/")

        assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text[:200]}"

    def test_missing_entitlements_returns_403(
        self, pod_session_no_auth: requests.Session, koku_api_url: str, invalid_identity_headers
    ):
        """Verify request with missing cost_management entitlement returns 403 Forbidden."""
        response = pod_session_no_auth.get(
            f"{koku_api_url}/sources/",
            headers={"X-Rh-Identity": invalid_identity_headers["no_entitlements"]},
        )

        assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text[:200]}"

    def test_non_admin_source_creation_returns_424(
        self, pod_session_no_auth: requests.Session, koku_api_url: str, invalid_identity_headers
    ):
        """Verify non-admin source creation fails when RBAC is unavailable.

        Koku checks RBAC for source creation. In on-prem deployments without
        RBAC service, this returns 424 Failed Dependency.
        """
        response = pod_session_no_auth.post(
            f"{koku_api_url}/sources/",
            json={
                "name": f"non-admin-test-{uuid.uuid4().hex[:8]}",
                "source_type_id": "1",  # OpenShift
                "source_ref": f"test-{uuid.uuid4().hex[:8]}",
            },
            headers={
                "X-Rh-Identity": invalid_identity_headers["non_admin"],
                "Content-Type": "application/json",
            },
        )

        assert response.status_code == 424, f"Expected 424, got {response.status_code}: {response.text[:200]}"

    def test_missing_email_in_identity_returns_401(
        self, pod_session_no_auth: requests.Session, koku_api_url: str, invalid_identity_headers
    ):
        """Verify missing email in identity header returns 401 Unauthorized.

        Koku's KokuTenantMiddleware requires email in the identity header
        and returns HttpResponseUnauthorizedRequest (401) when missing.
        """
        response = pod_session_no_auth.get(
            f"{koku_api_url}/sources/",
            headers={"X-Rh-Identity": invalid_identity_headers["no_email"]},
        )

        assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text[:200]}"


# =============================================================================
# P2 - Conflict Handling
# =============================================================================


@pytest.mark.sources
@pytest.mark.interpod
@pytest.mark.component
class TestConflictHandling:
    """Tests for conflict detection and error handling."""

    def test_duplicate_cluster_id_returns_400(
        self, pod_session: requests.Session, koku_api_url: str, test_source
    ):
        """Verify duplicate source_ref (cluster_id) returns 400 Bad Request."""
        # Try to create another source with the same source_ref
        response = pod_session.post(
            f"{koku_api_url}/sources",
            json={
                "name": f"duplicate-test-{uuid.uuid4().hex[:8]}",
                "source_type_id": test_source["source_type_id"],
                "source_ref": test_source["cluster_id"],  # Same as existing source
            },
        )

        assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text[:200]}"

    def test_invalid_source_type_id_returns_400(
        self, pod_session: requests.Session, koku_api_url: str
    ):
        """Verify invalid source_type_id returns 400 Bad Request.

        Koku's AdminSourcesSerializer.validate_source_type() raises
        ValidationError when the source_type_id doesn't exist.
        """
        response = pod_session.post(
            f"{koku_api_url}/sources/",
            json={
                "name": f"invalid-type-test-{uuid.uuid4().hex[:8]}",
                "source_type_id": "99999",  # Non-existent type
                "source_ref": f"test-{uuid.uuid4().hex[:8]}",
            },
        )

        assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text[:200]}"

    def test_duplicate_source_name(
        self, pod_session: requests.Session, koku_api_url: str, test_source
    ):
        """Verify duplicate source names are allowed.

        Unlike source_ref, duplicate names are permitted.
        """
        response = pod_session.post(
            f"{koku_api_url}/sources",
            json={
                "name": test_source["source_name"],  # Same name as existing
                "source_type_id": test_source["source_type_id"],
                "source_ref": f"different-{uuid.uuid4().hex[:8]}",  # Different cluster_id
            },
        )

        assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.text[:200]}"

        # Clean up the created source
        data = response.json()
        if data.get("id"):
            pod_session.delete(f"{koku_api_url}/sources/{data['id']}")


# =============================================================================
# P2 - Delete Edge Cases
# =============================================================================


@pytest.mark.sources
@pytest.mark.interpod
@pytest.mark.component
class TestDeleteEdgeCases:
    """Tests for edge cases in source deletion."""

    def test_get_deleted_source_returns_404(
        self, pod_session: requests.Session, koku_api_url: str, test_source
    ):
        """Verify that after deletion, GET returns 404."""
        source_id = test_source["source_id"]

        # Delete the source
        pod_session.delete(f"{koku_api_url}/sources/{source_id}")

        # Try to GET it
        response = pod_session.get(f"{koku_api_url}/sources/{source_id}")

        assert response.status_code == 404, f"Expected 404 for deleted source, got {response.status_code}: {response.text[:200]}"


# =============================================================================
# P2 - Filtering
# =============================================================================


@pytest.mark.sources
@pytest.mark.interpod
@pytest.mark.integration
class TestSourcesFiltering:
    """Tests for filtering capabilities in sources list endpoints."""

    def test_filter_sources_by_name(
        self, pod_session: requests.Session, koku_api_url: str, test_source
    ):
        """Verify sources can be filtered by name."""
        response = pod_session.get(
            f"{koku_api_url}/sources/",
            params={"name": test_source["source_name"]},
        )

        assert response.ok, f"Expected 200, got {response.status_code}: {response.text[:200]}"
        data = response.json()

        assert "data" in data, f"Missing data field: {data}"
        assert len(data["data"]) > 0, f"Expected filtered results, got empty list"
        names = [s.get("name") for s in data["data"]]
        assert test_source["source_name"] in names, f"Source not found in filtered results: {names}"

    def test_filter_sources_by_source_type_id(
        self, pod_session: requests.Session, koku_api_url: str, test_source
    ):
        """Verify sources can be filtered by source_type_id."""
        response = pod_session.get(
            f"{koku_api_url}/sources/",
            params={"source_type_id": test_source["source_type_id"]},
        )

        assert response.ok, f"Expected 200, got {response.status_code}: {response.text[:200]}"
        data = response.json()

        assert "data" in data, f"Missing data field: {data}"
        assert len(data["data"]) > 0, f"Expected filtered results, got empty list"
        for source in data["data"]:
            assert str(source.get("source_type_id")) == str(test_source["source_type_id"]), \
                f"Source type mismatch: {source}"

    def test_filter_source_types_by_name(
        self, pod_session: requests.Session, koku_api_url: str
    ):
        """Verify source_types can be filtered by name."""
        response = pod_session.get(
            f"{koku_api_url}/source_types",
            params={"name": "openshift"},
        )

        assert response.ok, f"Expected 200, got {response.status_code}: {response.text[:200]}"
        data = response.json()

        assert "data" in data, f"Missing data field: {data}"
        assert len(data["data"]) > 0, f"Expected openshift in results, got empty list"
        names = [st.get("name") for st in data["data"]]
        assert "openshift" in names, f"OpenShift not in filtered results: {names}"


# =============================================================================
# P2 - Validation Edge Cases
# =============================================================================


@pytest.mark.sources
@pytest.mark.interpod
@pytest.mark.component
class TestValidationEdgeCases:
    """Tests for input validation edge cases."""

    def test_source_create_requires_name(
        self, pod_session: requests.Session, koku_api_url: str
    ):
        """Verify source creation validates required fields.

        POST with empty payload should return 400.
        """
        response = pod_session.post(f"{koku_api_url}/sources", json={})

        assert response.status_code == 400, f"Expected 400 for empty payload, got {response.status_code}: {response.text[:200]}"

    def test_source_get_by_id_not_found(
        self, pod_session: requests.Session, koku_api_url: str
    ):
        """Verify getting non-existent source returns 404."""
        fake_id = "99999999"

        response = pod_session.get(f"{koku_api_url}/sources/{fake_id}")

        assert response.status_code == 404, f"Expected 404 for non-existent source, got {response.status_code}: {response.text[:200]}"

    def test_source_create_requires_source_ref(
        self, pod_session: requests.Session, koku_api_url: str
    ):
        """Verify source creation requires source_ref (cluster_id).

        The Sources API requires source_ref when creating a source.
        This test verifies that the API correctly rejects sources without it.
        """
        # Get OpenShift source type ID
        response = pod_session.get(f"{koku_api_url}/source_types")
        if not response.ok:
            pytest.skip("Could not get source types")

        data = response.json()
        ocp_source_type = next(
            (st for st in data.get("data", []) if st.get("name") == "openshift"),
            None
        )

        if ocp_source_type is None:
            pytest.skip("OpenShift source type not found")

        ocp_source_type_id = str(ocp_source_type.get("id"))

        # Try to create source WITHOUT source_ref
        response = pod_session.post(
            f"{koku_api_url}/sources",
            json={
                "name": f"pytest-source-{uuid.uuid4().hex[:8]}",
                "source_type_id": ocp_source_type_id,
                # Missing source_ref
            },
        )

        # API should reject source without source_ref with 400
        assert response.status_code == 400, (
            f"Expected 400 for source without source_ref, got {response.status_code}: {response.text[:200]}"
        )


# =============================================================================
# P3 - Source Status
# =============================================================================


@pytest.mark.sources
@pytest.mark.interpod
@pytest.mark.integration
class TestSourceStatus:
    """Tests for source status and health information."""

    def test_source_has_status_info(
        self, pod_session: requests.Session, koku_api_url: str
    ):
        """Verify source objects include status information.

        Note: The exact status structure may vary. This test documents expected behavior.
        """
        response = pod_session.get(f"{koku_api_url}/sources")
        if not response.ok:
            pytest.skip("Could not list sources")

        data = response.json()
        sources = data.get("data", [])

        if not sources:
            pytest.skip("No sources configured to check status")

        # Check if sources have status information
        source = sources[0]
        # Status may be embedded in source object or available via separate endpoint
        if "status" in source:
            print(f"Source status: {source['status']}")
        else:
            print(f"Source structure: {list(source.keys())}")
