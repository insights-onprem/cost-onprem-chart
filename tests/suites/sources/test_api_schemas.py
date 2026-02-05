"""
API Schema validation tests.

Tests to verify the response schemas of Koku Sources API endpoints
match expected field structures.
"""

import json
from typing import Any

import pytest

from utils import exec_in_pod


@pytest.mark.sources
@pytest.mark.component
class TestResponseSchemas:
    """Tests for API response schema validation."""

    def test_sources_list_response_schema(
        self, cluster_config: Any, koku_api_reads_url: str, ingress_pod: str, rh_identity_header: str
    ) -> None:
        """Verify sources list response has proper pagination structure.

        List response should include:
        - data: Array of source objects
        - meta: Pagination metadata with count
        - links: Navigation links (optional)
        """
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s",
                f"{koku_api_reads_url}/sources/",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        assert result is not None, "No response from sources endpoint"
        data = json.loads(result)

        # Must have data array
        assert "data" in data, f"Missing 'data' field: {data}"
        assert isinstance(data["data"], list), f"'data' should be a list: {type(data['data'])}"

        # Should have meta with count
        if "meta" in data:
            assert "count" in data["meta"], f"Missing 'count' in meta: {data['meta']}"
            assert isinstance(data["meta"]["count"], int), \
                f"'count' should be int: {type(data['meta']['count'])}"

    def test_source_types_response_schema(
        self, cluster_config: Any, koku_api_reads_url: str, ingress_pod: str, rh_identity_header: str
    ) -> None:
        """Verify source_types response contains expected fields.

        A source type should include:
        - id: Unique identifier
        - name: Type name (e.g., "openshift")
        """
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s",
                f"{koku_api_reads_url}/source_types",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        assert result is not None, "No response from source_types endpoint"
        data = json.loads(result)

        assert "data" in data, f"Missing 'data' field in response: {data}"
        assert len(data["data"]) > 0, "No source types returned"

        # Check first source type has required fields
        source_type = data["data"][0]
        required_fields = ["id", "name"]
        for field in required_fields:
            assert field in source_type, f"Missing required field '{field}': {source_type}"

        # Validate OpenShift type exists
        openshift_types = [st for st in data["data"] if st.get("name") == "openshift"]
        assert len(openshift_types) > 0, "OpenShift source type not found"
        assert openshift_types[0].get("id") is not None, "OpenShift source type missing id"

    def test_application_types_response_schema(
        self, cluster_config: Any, koku_api_reads_url: str, ingress_pod: str, rh_identity_header: str
    ) -> None:
        """Verify application_types response contains expected fields.

        An application type should include:
        - id: Unique identifier
        - name: Application name (e.g., "/insights/platform/cost-management")
        """
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s",
                f"{koku_api_reads_url}/application_types",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        assert result is not None, "No response from application_types endpoint"
        data = json.loads(result)

        assert "data" in data, f"Missing 'data' field in response: {data}"
        assert "meta" in data, f"Missing 'meta' field in response: {data}"
        assert len(data["data"]) > 0, "No application types returned"

        # Check first application type has required fields
        app_type = data["data"][0]
        required_fields = ["id", "name"]
        for field in required_fields:
            assert field in app_type, f"Missing required field '{field}': {app_type}"

    def test_applications_response_schema(
        self, cluster_config: Any, koku_api_reads_url: str, ingress_pod: str, rh_identity_header: str
    ) -> None:
        """Verify applications list response has proper structure.

        Applications response should include:
        - data: Array of application objects
        - meta: Pagination metadata
        """
        result = exec_in_pod(
            cluster_config.namespace,
            ingress_pod,
            [
                "curl", "-s",
                f"{koku_api_reads_url}/applications",
                "-H", f"X-Rh-Identity: {rh_identity_header}",
            ],
            container="ingress",
        )

        assert result is not None, "No response from applications endpoint"
        data = json.loads(result)

        assert "data" in data, f"Missing 'data' field: {data}"
        assert "meta" in data, f"Missing 'meta' field: {data}"
        assert isinstance(data["data"], list), f"'data' should be a list: {type(data['data'])}"
