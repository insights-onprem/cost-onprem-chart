"""
Interpod Koku API tests via test-runner pod.

These tests execute commands inside the cluster to test Koku API directly,
bypassing the external gateway. This validates pod-to-pod service communication
and X-Rh-Identity header handling.

Jira Test Cases:
- FLPATH-3173: Verify Koku API status endpoint returns healthy
- FLPATH-3174: Verify source types endpoint works with X-Rh-Identity header
"""

import pytest


@pytest.mark.interpod
@pytest.mark.component
class TestKokuAPIInternal:
    """Test Koku API directly via internal service URL."""

    def test_status_endpoint(
        self,
        internal_curl,
        internal_api_url: str,
    ):
        """Verify Koku /api/cost-management/v1/status/ returns healthy.
        
        FLPATH-3173: Verify Koku API status endpoint returns healthy
        
        Tests:
        - Status endpoint is accessible internally
        - Response contains API version info
        - Service is healthy
        """
        result = internal_curl(f"{internal_api_url}/api/cost-management/v1/status/")
        
        assert result.ok, f"curl failed: {result.stderr}"
        
        data = result.json()
        assert "api_version" in data or "server_address" in data, (
            f"Unexpected status response: {data}"
        )

    def test_reports_endpoint_with_identity(
        self,
        internal_curl,
        internal_api_url: str,
        internal_identity_header: str,
    ):
        """Verify reports endpoint works with X-Rh-Identity header.
        
        FLPATH-3174: Verify internal API accepts X-Rh-Identity header
        
        Tests:
        - Internal service accepts X-Rh-Identity header
        - Reports endpoint returns valid response
        - Response structure is valid
        """
        result = internal_curl(
            f"{internal_api_url}/api/cost-management/v1/reports/openshift/costs/",
            headers={
                "Content-Type": "application/json",
                "X-Rh-Identity": internal_identity_header,
            },
        )
        
        assert result.ok, f"curl failed: {result.stderr}"
        
        data = result.json()
        assert "data" in data, f"Response missing 'data' field: {data}"
        assert "meta" in data, f"Response missing 'meta' field: {data}"

    def test_sources_list_with_identity(
        self,
        internal_curl,
        internal_api_url: str,
        internal_identity_header: str,
    ):
        """Verify sources list endpoint works with X-Rh-Identity header.
        
        Tests:
        - Sources endpoint is accessible internally
        - Response structure is valid (may be empty)
        """
        result = internal_curl(
            f"{internal_api_url}/api/cost-management/v1/sources/",
            headers={
                "Content-Type": "application/json",
                "X-Rh-Identity": internal_identity_header,
            },
        )
        
        assert result.ok, f"curl failed: {result.stderr}"
        
        data = result.json()
        assert "data" in data, f"Response missing 'data' field: {data}"
        assert "meta" in data, f"Response missing 'meta' field: {data}"


@pytest.mark.interpod
@pytest.mark.component
class TestKokuAPIInternalRouting:
    """Test internal routing to different Koku API services."""

    def test_reads_service_accessible(
        self,
        internal_curl,
        internal_api_url: str,
    ):
        """Verify koku-api-reads service is accessible internally.
        
        Tests:
        - Reads service responds to health check
        """
        result = internal_curl(f"{internal_api_url}/api/cost-management/v1/status/")
        
        assert result.ok, f"curl failed: {result.stderr}"
        # Any valid JSON response indicates the service is up
        data = result.json()
        assert data is not None

    def test_writes_service_accessible(
        self,
        internal_curl,
        cluster_config,
    ):
        """Verify koku-api-writes service is accessible internally.
        
        Tests:
        - Writes service responds to health check
        """
        writes_url = f"http://{cluster_config.helm_release_name}-koku-api-writes.{cluster_config.namespace}.svc:8000"
        
        result = internal_curl(f"{writes_url}/api/cost-management/v1/status/")
        
        assert result.ok, f"curl failed: {result.stderr}"
        # Any valid JSON response indicates the service is up
        data = result.json()
        assert data is not None
