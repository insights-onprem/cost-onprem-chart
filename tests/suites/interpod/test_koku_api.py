"""
Interpod Koku API tests via test-runner pod.

These tests execute commands inside the cluster to test Koku API directly,
bypassing the external gateway. This validates pod-to-pod service communication
and X-Rh-Identity header handling.

Uses the pod_session fixture which provides a standard requests.Session API
that routes through kubectl exec curl inside the test-runner pod.

Jira Test Cases:
- FLPATH-3173: Verify Koku API status endpoint returns healthy
- FLPATH-3174: Verify source types endpoint works with X-Rh-Identity header
"""

import pytest
import requests


@pytest.mark.interpod
@pytest.mark.component
class TestKokuAPIInternal:
    """Test Koku API directly via internal service URL."""

    def test_status_endpoint(
        self,
        pod_session: requests.Session,
        internal_api_url: str,
    ):
        """Verify Koku /api/cost-management/v1/status/ returns healthy.
        
        FLPATH-3173: Verify Koku API status endpoint returns healthy
        
        Tests:
        - Status endpoint is accessible internally
        - Response contains API version info
        - Service is healthy
        """
        response = pod_session.get(f"{internal_api_url}/api/cost-management/v1/status/")
        
        assert response.ok, f"Request failed: {response.status_code} - {response.text}"
        
        data = response.json()
        assert "api_version" in data or "server_address" in data, (
            f"Unexpected status response: {data}"
        )

    def test_reports_endpoint_with_identity(
        self,
        pod_session: requests.Session,
        internal_api_url: str,
    ):
        """Verify reports endpoint works with X-Rh-Identity header.
        
        FLPATH-3174: Verify internal API accepts X-Rh-Identity header
        
        Tests:
        - Internal service accepts X-Rh-Identity header
        - Reports endpoint returns valid response
        - Response structure is valid
        """
        response = pod_session.get(
            f"{internal_api_url}/api/cost-management/v1/reports/openshift/costs/"
        )
        
        assert response.ok, f"Request failed: {response.status_code} - {response.text}"
        
        data = response.json()
        assert "data" in data, f"Response missing 'data' field: {data}"
        assert "meta" in data, f"Response missing 'meta' field: {data}"

    def test_sources_list_with_identity(
        self,
        pod_session: requests.Session,
        internal_api_url: str,
    ):
        """Verify sources list endpoint works with X-Rh-Identity header.
        
        Tests:
        - Sources endpoint is accessible internally
        - Response structure is valid (may be empty)
        """
        response = pod_session.get(
            f"{internal_api_url}/api/cost-management/v1/sources/"
        )
        
        assert response.ok, f"Request failed: {response.status_code} - {response.text}"
        
        data = response.json()
        assert "data" in data, f"Response missing 'data' field: {data}"
        assert "meta" in data, f"Response missing 'meta' field: {data}"


@pytest.mark.interpod
@pytest.mark.component
class TestKokuAPIInternalRouting:
    """Test internal routing to different Koku API services."""

    def test_reads_service_accessible(
        self,
        pod_session: requests.Session,
        internal_api_url: str,
    ):
        """Verify koku-api-reads service is accessible internally.
        
        Tests:
        - Reads service responds to health check
        """
        response = pod_session.get(f"{internal_api_url}/api/cost-management/v1/status/")
        
        assert response.ok, f"Request failed: {response.status_code} - {response.text}"
        # Any valid JSON response indicates the service is up
        data = response.json()
        assert data is not None

    def test_writes_service_accessible(
        self,
        pod_session: requests.Session,
        cluster_config,
    ):
        """Verify koku-api-writes service is accessible internally.
        
        Tests:
        - Writes service responds to health check
        """
        writes_url = f"http://{cluster_config.helm_release_name}-koku-api-writes.{cluster_config.namespace}.svc:8000"
        
        response = pod_session.get(f"{writes_url}/api/cost-management/v1/status/")
        
        assert response.ok, f"Request failed: {response.status_code} - {response.text}"
        # Any valid JSON response indicates the service is up
        data = response.json()
        assert data is not None
