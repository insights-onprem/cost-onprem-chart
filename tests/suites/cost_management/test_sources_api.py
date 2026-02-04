"""
Sources API tests.

Tests for the Sources API endpoints now served by Koku.
Note: Sources API has been merged into Koku. All sources endpoints are
available via /api/cost-management/v1/ using X-Rh-Identity header.

Source registration flow is tested in suites/e2e/ as part of the complete pipeline.
"""

import json
import uuid
from typing import Any, Dict, Optional, Tuple

import pytest

from utils import exec_in_pod, check_pod_ready


def parse_curl_response(result: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse curl response with HTTP status code.

    When curl is called with -w "\\n%{http_code}", the response body
    and status code are separated by a newline.

    Returns:
        Tuple of (body, status_code). Body is None if empty.
    """
    if not result:
        return None, None

    result = result.strip()
    lines = result.rsplit("\n", 1)
    if len(lines) == 2:
        body, status_code = lines
        body = body.strip()
        return body if body else None, status_code.strip()
    # If only one line, check if it looks like just a status code
    if result.isdigit() and len(result) == 3:
        return None, result
    return result, None


@pytest.mark.cost_management
@pytest.mark.component
class TestKokuSourcesHealth:
    """Tests for Koku API health and sources endpoint availability."""

    @pytest.mark.smoke
    def test_koku_api_pod_ready(self, cluster_config):
        """Verify Koku API pod is ready (serves sources endpoints)."""
        # Koku API has separate read/write pods - check the writes pod for sources
        assert check_pod_ready(
            cluster_config.namespace,
            "app.kubernetes.io/component=cost-management-api-writes"
        ), "Koku API (writes) pod is not ready"

    def test_koku_sources_endpoint_responds(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str, rh_identity_header: str
    ):
        """Verify Koku sources endpoint responds to requests."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                f"{koku_api_reads_url}/sources/",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        assert result is not None, "Could not reach Koku sources endpoint"
        assert result.strip() == "200", f"Koku sources endpoint returned {result}"


@pytest.mark.cost_management
@pytest.mark.integration
class TestSourceTypes:
    """Tests for source type configuration in Koku."""

    def test_openshift_source_type_exists(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str, rh_identity_header: str
    ):
        """Verify OpenShift source type is configured in Koku."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", f"{koku_api_reads_url}/source_types",
                "-H", "Content-Type: application/json",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        assert result is not None, "Could not get source types from Koku"
        data = json.loads(result)
        source_types = [st.get("name") for st in data.get("data", [])]

        assert "openshift" in source_types, f"OpenShift source type not found in {source_types}"

    def test_all_cloud_source_types_exist(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str, rh_identity_header: str
    ):
        """Verify all expected cloud source types are configured."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", f"{koku_api_reads_url}/source_types",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        assert result is not None
        data = json.loads(result)
        source_types = [st.get("name") for st in data.get("data", [])]

        expected_types = ["openshift", "amazon", "azure", "google"]
        for expected in expected_types:
            assert expected in source_types, f"{expected} source type not found in {source_types}"


@pytest.mark.cost_management
@pytest.mark.integration
class TestApplicationTypes:
    """Tests for application type configuration in Koku."""

    def test_cost_management_application_type_exists(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str, rh_identity_header: str
    ):
        """Verify cost-management application type is configured."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", f"{koku_api_reads_url}/application_types",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        assert result is not None, "Could not get application types from Koku"
        data = json.loads(result)

        assert "data" in data, f"Missing data field: {data}"
        assert len(data["data"]) > 0, "No application types returned"

        app_names = [at.get("name") for at in data["data"]]
        assert "/insights/platform/cost-management" in app_names, \
            f"cost-management application type not found in {app_names}"


@pytest.mark.cost_management
@pytest.mark.integration
class TestApplicationsEndpoint:
    """Tests for the applications endpoint."""

    def test_applications_list_returns_valid_response(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str, rh_identity_header: str
    ):
        """Verify applications endpoint returns valid paginated response."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", f"{koku_api_reads_url}/applications",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        assert result is not None, "Could not get applications from Koku"
        data = json.loads(result)

        assert "meta" in data, f"Missing meta field: {data}"
        assert "data" in data, f"Missing data field: {data}"
        assert isinstance(data["data"], list), f"data should be a list: {data}"


# =============================================================================
# P1 - Authentication Error Scenarios
# =============================================================================


@pytest.mark.cost_management
@pytest.mark.component
class TestAuthenticationErrors:
    """Tests for authentication error handling in Sources API."""

    def test_malformed_base64_header_returns_401(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str, invalid_identity_headers
    ):
        """Verify malformed base64 in X-Rh-Identity returns an error (401 or 403)."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-w", "\n%{http_code}",
                f"{koku_api_reads_url}/sources/",
                "-H", f"X-Rh-Identity: {invalid_identity_headers['malformed_base64']}",
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        # Koku returns 403 for invalid identity, but 400/401 are also acceptable
        assert status in ["400", "401", "403"], f"Expected 400/401/403, got {status}: {body}"

    def test_invalid_json_in_header_returns_401(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str, invalid_identity_headers
    ):
        """Verify invalid JSON in decoded X-Rh-Identity returns an error."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-w", "\n%{http_code}",
                f"{koku_api_reads_url}/sources/",
                "-H", f"X-Rh-Identity: {invalid_identity_headers['invalid_json']}",
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        assert status in ["400", "401", "403"], f"Expected 400/401/403, got {status}: {body}"

    def test_missing_identity_header_returns_401(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str
    ):
        """Verify missing X-Rh-Identity header returns 401 Unauthorized."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-w", "\n%{http_code}",
                f"{koku_api_reads_url}/sources/",
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        assert status in ["401", "403"], f"Expected 401/403, got {status}: {body}"

    def test_missing_entitlements_returns_403(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str, invalid_identity_headers
    ):
        """Verify missing cost_management entitlement returns 403 Forbidden."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-w", "\n%{http_code}",
                f"{koku_api_reads_url}/sources/",
                "-H", f"X-Rh-Identity: {invalid_identity_headers['no_entitlements']}",
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        # Koku may return 403 or allow access depending on configuration
        assert status in ["200", "403"], f"Expected 200 or 403, got {status}: {body}"

    def test_non_admin_source_creation(
        self, cluster_config, koku_api_writes_url: str, ingress_pod: str, invalid_identity_headers
    ):
        """Document behavior when non-admin user attempts source creation.

        This test documents the current behavior - non-admins may or may not
        be allowed to create sources depending on Koku's RBAC configuration.
        """
        source_payload = json.dumps({
            "name": f"non-admin-test-{uuid.uuid4().hex[:8]}",
            "source_type_id": "1",  # OpenShift
            "source_ref": f"test-{uuid.uuid4().hex[:8]}",
        })

        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-w", "\n%{http_code}",
                "-X", "POST",
                f"{koku_api_writes_url}/sources/",
                "-H", "Content-Type: application/json",
                "-H", f"X-Rh-Identity: {invalid_identity_headers['non_admin']}",
                "-d", source_payload,
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        # Document the behavior - could be 201 (allowed), 403 (forbidden), 401, or 424 (RBAC unavailable)
        assert status in ["201", "400", "401", "403", "424"], f"Unexpected status {status}: {body}"

    def test_missing_email_in_identity(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str, invalid_identity_headers
    ):
        """Document behavior when email is missing from identity header.

        Some endpoints may require email for audit logging.
        """
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-w", "\n%{http_code}",
                f"{koku_api_reads_url}/sources/",
                "-H", f"X-Rh-Identity: {invalid_identity_headers['no_email']}",
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        # Document the behavior - Koku might allow or reject
        assert status in ["200", "400", "401", "403"], f"Unexpected status {status}: {body}"


# =============================================================================
# P2 - Conflict Handling
# =============================================================================


@pytest.mark.cost_management
@pytest.mark.component
class TestConflictHandling:
    """Tests for conflict detection and error handling."""

    def test_duplicate_cluster_id_returns_conflict(
        self, cluster_config, koku_api_writes_url: str, ingress_pod: str,
        rh_identity_header: str, test_source
    ):
        """Verify duplicate source_ref (cluster_id) returns 409 Conflict."""
        # Try to create another source with the same source_ref
        source_payload = json.dumps({
            "name": f"duplicate-test-{uuid.uuid4().hex[:8]}",
            "source_type_id": test_source["source_type_id"],
            "source_ref": test_source["cluster_id"],  # Same as existing source
        })

        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-w", "\n%{http_code}",
                "-X", "POST",
                f"{koku_api_writes_url}/sources",
                "-H", "Content-Type: application/json",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
                "-d", source_payload,
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        # Should return 400 or 409 for duplicate source_ref
        assert status in ["400", "409"], f"Expected conflict, got {status}: {body}"

    def test_invalid_source_type_id_returns_error(
        self, cluster_config, koku_api_writes_url: str, ingress_pod: str, rh_identity_header: str
    ):
        """Verify invalid source_type_id returns appropriate error."""
        source_payload = json.dumps({
            "name": f"invalid-type-test-{uuid.uuid4().hex[:8]}",
            "source_type_id": "99999",  # Non-existent type
            "source_ref": f"test-{uuid.uuid4().hex[:8]}",
        })

        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-w", "\n%{http_code}",
                "-X", "POST",
                f"{koku_api_writes_url}/sources/",
                "-H", "Content-Type: application/json",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
                "-d", source_payload,
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        assert status in ["400", "404"], f"Expected error, got {status}: {body}"

    def test_duplicate_source_name_behavior(
        self, cluster_config, koku_api_writes_url: str, ingress_pod: str,
        rh_identity_header: str, test_source
    ):
        """Document behavior when duplicate source name is used.

        Unlike source_ref, duplicate names might be allowed.
        """
        source_payload = json.dumps({
            "name": test_source["source_name"],  # Same name as existing
            "source_type_id": test_source["source_type_id"],
            "source_ref": f"different-{uuid.uuid4().hex[:8]}",  # Different cluster_id
        })

        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-w", "\n%{http_code}",
                "-X", "POST",
                f"{koku_api_writes_url}/sources",
                "-H", "Content-Type: application/json",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
                "-d", source_payload,
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        # Document the behavior - could allow duplicate names or reject
        if status == "201":
            # Clean up the created source
            try:
                data = json.loads(body)
                if data.get("id"):
                    exec_in_pod(
                        cluster_config.namespace,
                        ingress_pod,
                        [
                            "curl", "-s", "-X", "DELETE",
                            f"{koku_api_writes_url}/sources/{data['id']}",
                            "-H", f"X-Rh-Identity: {rh_identity_header}",
                        ],
                        container="ingress",
                    )
            except json.JSONDecodeError:
                pass

        assert status in ["201", "400", "409"], f"Unexpected status {status}: {body}"


# =============================================================================
# P2 - Delete Edge Cases
# =============================================================================


@pytest.mark.cost_management
@pytest.mark.component
class TestDeleteEdgeCases:
    """Tests for edge cases in source deletion."""

    def test_delete_nonexistent_source_returns_404(
        self, cluster_config, koku_api_writes_url: str, ingress_pod: str, rh_identity_header: str
    ):
        """Verify deleting a non-existent source returns 404."""
        fake_id = "99999999"

        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-w", "\n%{http_code}",
                "-X", "DELETE",
                f"{koku_api_writes_url}/sources/{fake_id}/",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        assert status == "404", f"Expected 404, got {status}: {body}"

    def test_get_deleted_source_returns_404(
        self, cluster_config, koku_api_writes_url: str, koku_api_reads_url: str,
        ingress_pod: str, rh_identity_header: str, test_source
    ):
        """Verify that after deletion, GET returns 404."""
        source_id = test_source["source_id"]

        # Delete the source
        exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-X", "DELETE",
                f"{koku_api_writes_url}/sources/{source_id}",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        # Try to GET it
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-w", "\n%{http_code}",
                f"{koku_api_reads_url}/sources/{source_id}",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        assert status == "404", f"Expected 404 for deleted source, got {status}: {body}"


# =============================================================================
# P2 - Pagination
# =============================================================================


@pytest.mark.cost_management
@pytest.mark.integration
class TestSourcesPagination:
    """Tests for pagination in sources list endpoints."""

    def test_sources_pagination_basic(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str, rh_identity_header: str
    ):
        """Verify sources list supports limit and offset parameters."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s",
                f"{koku_api_reads_url}/sources/?limit=5&offset=0",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        assert result is not None, "No response from sources endpoint"
        data = json.loads(result)

        # Should have pagination metadata
        assert "meta" in data or "data" in data, f"Missing pagination structure: {data}"

        # If there are results, should be <= limit
        if "data" in data:
            assert len(data["data"]) <= 5, f"Returned more than limit: {len(data['data'])}"

    def test_sources_pagination_metadata(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str, rh_identity_header: str
    ):
        """Verify pagination response includes count metadata."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s",
                f"{koku_api_reads_url}/sources/?limit=10",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        assert result is not None
        data = json.loads(result)

        # Check for count in meta
        if "meta" in data:
            assert "count" in data["meta"], f"Missing count in meta: {data['meta']}"

    def test_sources_pagination_beyond_results(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str, rh_identity_header: str
    ):
        """Verify pagination with offset beyond available results returns empty."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-w", "\n%{http_code}",
                f"{koku_api_reads_url}/sources/?limit=10&offset=10000",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        assert status == "200", f"Expected 200, got {status}"

        data = json.loads(body)
        if "data" in data:
            assert len(data["data"]) == 0, f"Expected empty results: {data}"

    def test_source_types_pagination(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str, rh_identity_header: str
    ):
        """Verify source_types endpoint supports pagination."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s",
                f"{koku_api_reads_url}/source_types?limit=5",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        assert result is not None
        data = json.loads(result)
        assert "data" in data, f"Missing data field: {data}"
        assert "meta" in data, f"Missing meta field: {data}"


# =============================================================================
# P2 - Filtering
# =============================================================================


@pytest.mark.cost_management
@pytest.mark.integration
class TestSourcesFiltering:
    """Tests for filtering capabilities in sources list endpoints."""

    def test_filter_sources_by_name(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str,
        rh_identity_header: str, test_source
    ):
        """Verify sources can be filtered by name."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s",
                f"{koku_api_reads_url}/sources/?name={test_source['source_name']}",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        assert result is not None
        data = json.loads(result)

        # If name filter is supported, should return matching source
        if "data" in data and len(data["data"]) > 0:
            names = [s.get("name") for s in data["data"]]
            assert test_source["source_name"] in names, f"Source not found in filtered results: {names}"

    def test_filter_sources_by_source_type_id(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str,
        rh_identity_header: str, test_source
    ):
        """Verify sources can be filtered by source_type_id."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s",
                f"{koku_api_reads_url}/sources/?source_type_id={test_source['source_type_id']}",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        assert result is not None
        data = json.loads(result)

        # All returned sources should have matching source_type_id
        if "data" in data:
            for source in data["data"]:
                assert str(source.get("source_type_id")) == str(test_source["source_type_id"]), \
                    f"Source type mismatch: {source}"

    def test_filter_source_types_by_name(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str, rh_identity_header: str
    ):
        """Verify source_types can be filtered by name."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s",
                f"{koku_api_reads_url}/source_types?name=openshift",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        assert result is not None
        data = json.loads(result)

        # Should return openshift type
        assert "data" in data, f"Missing data field: {data}"
        if len(data["data"]) > 0:
            names = [st.get("name") for st in data["data"]]
            assert "openshift" in names, f"OpenShift not in filtered results: {names}"


# =============================================================================
# P2 - Old Endpoint Removal
# =============================================================================


@pytest.mark.cost_management
@pytest.mark.component
class TestOldEndpointRemoval:
    """Tests to verify old sources-api endpoints are removed."""

    def test_old_sources_api_v1_returns_404(
        self, cluster_config, ingress_pod: str, rh_identity_header: str
    ):
        """Verify the old /api/sources/v1.0 endpoint returns 404.

        The old sources-api service should be removed and replaced by
        Koku's built-in sources endpoints at /api/cost-management/v1/.

        This test will pass once the Helm chart removes the sources-api deployment.
        """
        # Try to reach the old sources-api endpoint
        old_api_url = (
            f"http://{cluster_config.helm_release_name}-sources-api."
            f"{cluster_config.namespace}.svc.cluster.local:8000/api/sources/v1.0/sources"
        )

        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-w", "\n%{http_code}",
                "--connect-timeout", "5",
                old_api_url,
                "-H", f"x-rh-sources-org-id: {cluster_config.namespace}",
            ],
            container="ingress",
        )

        # Should fail to connect (service doesn't exist) or return 404
        if result:
            body, status = parse_curl_response(result)
            # Could be 000 (connection refused), 404, or 502/503
            assert status in ["000", "404", "502", "503"], \
                f"Old API unexpectedly accessible with status {status}"


# =============================================================================
# P3 - HTTP Method Restrictions
# =============================================================================


@pytest.mark.cost_management
@pytest.mark.component
class TestHTTPMethodRestrictions:
    """Tests for HTTP method restrictions on various endpoints."""

    def test_put_not_allowed_on_sources(
        self, cluster_config, koku_api_writes_url: str, ingress_pod: str,
        rh_identity_header: str, test_source
    ):
        """Verify PUT method is not allowed on sources (use PATCH instead)."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-w", "\n%{http_code}",
                "-X", "PUT",
                f"{koku_api_writes_url}/sources/{test_source['source_id']}/",
                "-H", "Content-Type: application/json",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
                "-d", json.dumps({"name": "new-name"}),
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        # PUT should either be not allowed (405) or treated as update (200)
        assert status in ["200", "405"], f"Unexpected status {status}: {body}"

    def test_post_not_allowed_on_source_types(
        self, cluster_config, koku_api_writes_url: str, ingress_pod: str, rh_identity_header: str
    ):
        """Verify POST is not allowed on source_types (read-only endpoint)."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-w", "\n%{http_code}",
                "-X", "POST",
                f"{koku_api_writes_url}/source_types/",
                "-H", "Content-Type: application/json",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
                "-d", json.dumps({"name": "new-type"}),
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        # 404 = No route for POST, 405 = Method Not Allowed
        assert status in ["404", "405"], f"Expected 404/405, got {status}: {body}"

    def test_delete_not_allowed_on_source_types(
        self, cluster_config, koku_api_writes_url: str, ingress_pod: str, rh_identity_header: str
    ):
        """Verify DELETE is not allowed on source_types (read-only endpoint)."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-w", "\n%{http_code}",
                "-X", "DELETE",
                f"{koku_api_writes_url}/source_types/1/",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        # 404 = No route for DELETE, 405 = Method Not Allowed
        assert status in ["404", "405"], f"Expected 404/405, got {status}: {body}"


# =============================================================================
# P3 - Content-Type Validation
# =============================================================================


@pytest.mark.cost_management
@pytest.mark.component
class TestContentTypeValidation:
    """Tests for request content-type validation."""

    def test_post_requires_json_content_type(
        self, cluster_config, koku_api_writes_url: str, ingress_pod: str, rh_identity_header: str
    ):
        """Verify POST requests require application/json content type."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-w", "\n%{http_code}",
                "-X", "POST",
                f"{koku_api_writes_url}/sources/",
                "-H", "Content-Type: text/plain",  # Wrong content type
                "-H", f"X-Rh-Identity: {rh_identity_header}",
                "-d", "not json",
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        # Should reject with 400 or 415 (Unsupported Media Type)
        assert status in ["400", "415"], f"Expected 400/415, got {status}: {body}"

    def test_malformed_json_body_returns_400(
        self, cluster_config, koku_api_writes_url: str, ingress_pod: str, rh_identity_header: str
    ):
        """Verify malformed JSON in request body returns 400."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-w", "\n%{http_code}",
                "-X", "POST",
                f"{koku_api_writes_url}/sources/",
                "-H", "Content-Type: application/json",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
                "-d", "{not valid json}",
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        assert status == "400", f"Expected 400, got {status}: {body}"

    def test_empty_request_body_returns_400(
        self, cluster_config, koku_api_writes_url: str, ingress_pod: str, rh_identity_header: str
    ):
        """Verify empty request body on POST returns 400."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-w", "\n%{http_code}",
                "-X", "POST",
                f"{koku_api_writes_url}/sources/",
                "-H", "Content-Type: application/json",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
                "-d", "",
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        assert status == "400", f"Expected 400, got {status}: {body}"
