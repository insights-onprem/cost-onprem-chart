"""
Sources API tests.

Tests for the Sources API endpoints now served by Koku.
Note: Sources API has been merged into Koku. All sources endpoints are
available via /api/cost-management/v1/ using X-Rh-Identity header.

Source registration flow is tested in suites/e2e/ as part of the complete pipeline.
"""

import json
import uuid
from typing import Optional, Tuple

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


@pytest.mark.sources
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

    @pytest.mark.smoke
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


@pytest.mark.sources
@pytest.mark.integration
class TestSourceTypes:
    """Tests for source type configuration in Koku."""

    def test_all_cloud_source_types_exist(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str, rh_identity_header: str
    ):
        """Verify all expected cloud source types are configured."""
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s", "-w", "\n%{http_code}",
                f"{koku_api_reads_url}/source_types",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        assert status == "200", f"Expected 200, got {status}: {body}"
        assert body is not None
        data = json.loads(body)
        source_types = [st.get("name") for st in data.get("data", [])]

        expected_types = ["openshift", "amazon", "azure", "google"]
        for expected in expected_types:
            assert expected in source_types, f"{expected} source type not found in {source_types}"


@pytest.mark.sources
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
                "curl", "-s", "-w", "\n%{http_code}",
                f"{koku_api_reads_url}/application_types",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        assert status == "200", f"Expected 200, got {status}: {body}"
        assert body is not None, "Could not get application types from Koku"
        data = json.loads(body)

        assert "data" in data, f"Missing data field: {data}"
        assert len(data["data"]) > 0, "No application types returned"

        app_names = [at.get("name") for at in data["data"]]
        assert "/insights/platform/cost-management" in app_names, \
            f"cost-management application type not found in {app_names}"


@pytest.mark.sources
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
                "curl", "-s", "-w", "\n%{http_code}",
                f"{koku_api_reads_url}/applications",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        assert status == "200", f"Expected 200, got {status}: {body}"
        assert body is not None, "Could not get applications from Koku"
        data = json.loads(body)

        assert "meta" in data, f"Missing meta field: {data}"
        assert "data" in data, f"Missing data field: {data}"
        assert isinstance(data["data"], list), f"data should be a list: {data}"


# =============================================================================
# P1 - Authentication Error Scenarios
# =============================================================================


@pytest.mark.sources
@pytest.mark.auth
@pytest.mark.component
class TestAuthenticationErrors:
    """Tests for authentication error handling in Sources API."""

    def test_malformed_base64_header_returns_403(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str, invalid_identity_headers
    ):
        """Verify malformed base64 in X-Rh-Identity returns 403 Forbidden."""
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
        assert status == "403", f"Expected 403, got {status}: {body}"

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
        assert status == "401", f"Expected 401, got {status}: {body}"

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
        assert status == "401", f"Expected 401, got {status}: {body}"

    def test_missing_entitlements_returns_403(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str, invalid_identity_headers
    ):
        """Verify request with missing cost_management entitlement returns 403 Forbidden."""
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
        assert status == "403", f"Expected 403, got {status}: {body}"

    def test_non_admin_source_creation_returns_424(
        self, cluster_config, koku_api_writes_url: str, ingress_pod: str, invalid_identity_headers
    ):
        """Verify non-admin source creation fails when RBAC is unavailable.

        Koku checks RBAC for source creation. In on-prem deployments without
        RBAC service, this returns 424 Failed Dependency.
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
        assert status == "424", f"Expected 424, got {status}: {body}"

    def test_missing_email_in_identity_returns_401(
        self, cluster_config, koku_api_reads_url: str, ingress_pod: str, invalid_identity_headers
    ):
        """Verify missing email in identity header returns 401 Unauthorized.

        Koku's KokuTenantMiddleware requires email in the identity header
        and returns HttpResponseUnauthorizedRequest (401) when missing.
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
        assert status == "401", f"Expected 401, got {status}: {body}"


# =============================================================================
# P2 - Conflict Handling
# =============================================================================


@pytest.mark.sources
@pytest.mark.component
class TestConflictHandling:
    """Tests for conflict detection and error handling."""

    def test_duplicate_cluster_id_returns_400(
        self, cluster_config, koku_api_writes_url: str, ingress_pod: str,
        rh_identity_header: str, test_source
    ):
        """Verify duplicate source_ref (cluster_id) returns 400 Bad Request."""
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
        assert status == "400", f"Expected 400, got {status}: {body}"

    def test_invalid_source_type_id_returns_400(
        self, cluster_config, koku_api_writes_url: str, ingress_pod: str, rh_identity_header: str
    ):
        """Verify invalid source_type_id returns 400 Bad Request.

        Koku's AdminSourcesSerializer.validate_source_type() raises
        ValidationError when the source_type_id doesn't exist.
        """
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
        assert status == "400", f"Expected 400, got {status}: {body}"

    def test_duplicate_source_name(
        self, cluster_config, koku_api_writes_url: str, ingress_pod: str,
        rh_identity_header: str, test_source
    ):
        """Verify duplicate source names are allowed.

        Unlike source_ref, duplicate names are permitted.
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
        assert status == "201", f"Expected 201, got {status}: {body}"

        # Clean up the created source
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


# =============================================================================
# P2 - Delete Edge Cases
# =============================================================================


@pytest.mark.sources
@pytest.mark.component
class TestDeleteEdgeCases:
    """Tests for edge cases in source deletion."""

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
# P2 - Filtering
# =============================================================================


@pytest.mark.sources
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
                "curl", "-s", "-w", "\n%{http_code}",
                f"{koku_api_reads_url}/sources/?name={test_source['source_name']}",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        assert status == "200", f"Expected 200, got {status}: {body}"
        assert body is not None
        data = json.loads(body)

        assert "data" in data, f"Missing data field: {data}"
        assert len(data["data"]) > 0, f"Expected filtered results, got empty list"
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
                "curl", "-s", "-w", "\n%{http_code}",
                f"{koku_api_reads_url}/sources/?source_type_id={test_source['source_type_id']}",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        assert status == "200", f"Expected 200, got {status}: {body}"
        assert body is not None
        data = json.loads(body)

        assert "data" in data, f"Missing data field: {data}"
        assert len(data["data"]) > 0, f"Expected filtered results, got empty list"
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
                "curl", "-s", "-w", "\n%{http_code}",
                f"{koku_api_reads_url}/source_types?name=openshift",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        body, status = parse_curl_response(result)
        assert status == "200", f"Expected 200, got {status}: {body}"
        assert body is not None
        data = json.loads(body)

        assert "data" in data, f"Missing data field: {data}"
        assert len(data["data"]) > 0, f"Expected openshift in results, got empty list"
        names = [st.get("name") for st in data["data"]]
        assert "openshift" in names, f"OpenShift not in filtered results: {names}"


