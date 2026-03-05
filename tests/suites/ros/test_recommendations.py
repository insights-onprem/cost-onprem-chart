"""
ROS recommendations API tests.

Tests for ROS API health and accessibility.
Note: Recommendation generation and validation is tested in suites/e2e/ as part of the complete pipeline.

Coverage:
- FLPATH-3094: ROS API recommendations endpoint accessible via UI
- FLPATH-3155: Recommendations accept filter parameters
- FLPATH-3156: Recommendations support pagination
"""

import pytest
import requests

from utils import check_pod_ready, run_oc_command


def get_fresh_token(keycloak_config, http_session: requests.Session) -> dict:
    """Get a fresh JWT token (avoids session-scoped token expiry issues)."""
    response = http_session.post(
        keycloak_config.token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": keycloak_config.client_id,
            "client_secret": keycloak_config.client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    
    if response.status_code != 200:
        return None
    
    token = response.json().get("access_token")
    return {"Authorization": f"Bearer {token}"} if token else None


def get_recommendations_endpoint(ros_api_url: str) -> str:
    """Build the recommendations endpoint URL."""
    return f"{ros_api_url.rstrip('/')}/cost-management/v1/recommendations/openshift"


@pytest.mark.ros
@pytest.mark.integration
class TestRecommendationsAPI:
    """Tests for ROS recommendations API accessibility."""

    @pytest.mark.smoke
    def test_ros_api_pod_ready(self, cluster_config):
        """Verify ROS API pod is ready."""
        assert check_pod_ready(
            cluster_config.namespace,
            "app.kubernetes.io/component=ros-api"
        ), "ROS API pod is not ready"

    def test_recommendations_endpoint_accessible(
        self, ros_api_url: str, keycloak_config, http_session: requests.Session
    ):
        """Verify recommendations endpoint is accessible with JWT.
        
        Covers: FLPATH-3094
        """
        auth_header = get_fresh_token(keycloak_config, http_session)
        if not auth_header:
            pytest.skip("Could not obtain fresh JWT token")

        endpoint = get_recommendations_endpoint(ros_api_url)
        response = http_session.get(endpoint, headers=auth_header, timeout=30)

        # Should not get auth errors
        assert response.status_code not in [401, 403], (
            f"Authentication failed: {response.status_code}"
        )
        # Accept 200 (has data) or 404 (no data yet)
        assert response.status_code in [200, 404], (
            f"Unexpected status: {response.status_code}"
        )

    @pytest.mark.parametrize("filter_param,filter_value", [
        ("cluster", "test-cluster"),
        ("project", "test-project"),
        ("workload", "test-workload"),
        ("workload_type", "deployment"),
        ("container", "test-container"),
    ])
    def test_recommendations_accept_filter_parameters(
        self, ros_api_url: str, keycloak_config, http_session: requests.Session,
        filter_param: str, filter_value: str
    ):
        """Verify recommendations endpoint accepts filter query parameters.
        
        Covers: FLPATH-3155
        
        Expected: 200 OK with valid JSON response (data may be empty if no matches).
        """
        auth_header = get_fresh_token(keycloak_config, http_session)
        if not auth_header:
            pytest.skip("Could not obtain fresh JWT token")

        endpoint = get_recommendations_endpoint(ros_api_url)
        params = {filter_param: filter_value}
        
        response = http_session.get(endpoint, headers=auth_header, params=params, timeout=30)

        # Positive assertion: expect 200 OK
        assert response.status_code == 200, (
            f"Expected 200 OK for filter {filter_param}={filter_value}, "
            f"got {response.status_code}: {response.text}"
        )
        
        # Verify valid JSON response
        data = response.json()
        assert isinstance(data, dict), "Response should be a JSON object"
        
        # Verify response has expected structure
        assert "data" in data or "recommendations" in data or "meta" in data, (
            f"Response should contain 'data', 'recommendations', or 'meta' field"
        )

    def test_recommendations_accept_multiple_filters(
        self, ros_api_url: str, keycloak_config, http_session: requests.Session
    ):
        """Verify recommendations endpoint accepts multiple filter parameters.
        
        Covers: FLPATH-3155
        
        Expected: 200 OK with valid JSON response.
        """
        auth_header = get_fresh_token(keycloak_config, http_session)
        if not auth_header:
            pytest.skip("Could not obtain fresh JWT token")

        endpoint = get_recommendations_endpoint(ros_api_url)
        params = {
            "cluster": "test-cluster",
            "project": "test-project",
            "workload_type": "deployment",
        }
        
        response = http_session.get(endpoint, headers=auth_header, params=params, timeout=30)

        assert response.status_code == 200, (
            f"Expected 200 OK with multiple filters, got {response.status_code}: {response.text}"
        )
        
        # Verify valid JSON response
        data = response.json()
        assert isinstance(data, dict), "Response should be a JSON object"

    @pytest.mark.parametrize("limit", [1, 5, 10, 50])
    def test_recommendations_pagination_limit(
        self, ros_api_url: str, keycloak_config, http_session: requests.Session,
        limit: int
    ):
        """Verify recommendations endpoint accepts limit parameter for pagination.
        
        Covers: FLPATH-3156
        
        Expected: 200 with data respecting limit, or 200 with empty data if no recommendations.
        """
        auth_header = get_fresh_token(keycloak_config, http_session)
        if not auth_header:
            pytest.skip("Could not obtain fresh JWT token")

        endpoint = get_recommendations_endpoint(ros_api_url)
        params = {"limit": limit}
        
        response = http_session.get(endpoint, headers=auth_header, params=params, timeout=30)

        # Positive assertion: expect 200 OK
        assert response.status_code == 200, (
            f"Expected 200 OK, got {response.status_code}: {response.text}"
        )
        
        data = response.json()
        
        # Verify response structure
        assert "data" in data or "recommendations" in data, (
            "Response should contain 'data' or 'recommendations' field"
        )
        
        # Verify limit is respected (use 'in' check to handle empty lists correctly)
        items = data["data"] if "data" in data else data.get("recommendations", [])
        if isinstance(items, list):
            assert len(items) <= limit, (
                f"Response returned {len(items)} items, expected <= {limit}"
            )

    @pytest.mark.parametrize("offset", [0, 5, 10])
    def test_recommendations_pagination_offset(
        self, ros_api_url: str, keycloak_config, http_session: requests.Session,
        offset: int
    ):
        """Verify recommendations endpoint accepts offset parameter for pagination.
        
        Covers: FLPATH-3156
        
        Expected: 200 with data (possibly empty if offset exceeds total count).
        """
        auth_header = get_fresh_token(keycloak_config, http_session)
        if not auth_header:
            pytest.skip("Could not obtain fresh JWT token")

        endpoint = get_recommendations_endpoint(ros_api_url)
        params = {"offset": offset}
        
        response = http_session.get(endpoint, headers=auth_header, params=params, timeout=30)

        assert response.status_code == 200, (
            f"Expected 200 OK, got {response.status_code}: {response.text}"
        )
        
        data = response.json()
        
        # Verify response structure
        assert "data" in data or "recommendations" in data, (
            "Response should contain 'data' or 'recommendations' field"
        )

    def test_recommendations_pagination_limit_and_offset(
        self, ros_api_url: str, keycloak_config, http_session: requests.Session
    ):
        """Verify recommendations endpoint accepts both limit and offset for pagination.
        
        Covers: FLPATH-3156
        
        Expected: 200 with proper pagination metadata.
        """
        auth_header = get_fresh_token(keycloak_config, http_session)
        if not auth_header:
            pytest.skip("Could not obtain fresh JWT token")

        endpoint = get_recommendations_endpoint(ros_api_url)
        params = {"limit": 10, "offset": 5}
        
        response = http_session.get(endpoint, headers=auth_header, params=params, timeout=30)

        assert response.status_code == 200, (
            f"Expected 200 OK, got {response.status_code}: {response.text}"
        )
        
        data = response.json()
        
        # Verify response structure
        assert "data" in data or "recommendations" in data, (
            "Response should contain 'data' or 'recommendations' field"
        )
        
        # Verify pagination metadata exists
        assert "meta" in data or "links" in data, (
            "Response should include pagination metadata ('meta' or 'links')"
        )
        
        # Verify limit is respected (use 'in' check to handle empty lists correctly)
        items = data["data"] if "data" in data else data.get("recommendations", [])
        if isinstance(items, list):
            assert len(items) <= 10, (
                f"Response returned {len(items)} items, expected <= 10"
            )

    def test_recommendations_response_structure(
        self, ros_api_url: str, keycloak_config, http_session: requests.Session
    ):
        """Verify recommendations response has expected structure.
        
        Covers: FLPATH-3156 (pagination metadata in response)
        
        Expected: 200 OK with JSON containing data array and pagination metadata.
        """
        auth_header = get_fresh_token(keycloak_config, http_session)
        if not auth_header:
            pytest.skip("Could not obtain fresh JWT token")

        endpoint = get_recommendations_endpoint(ros_api_url)
        response = http_session.get(endpoint, headers=auth_header, timeout=30)

        assert response.status_code == 200, (
            f"Expected 200 OK, got {response.status_code}: {response.text}"
        )
        
        data = response.json()
        assert isinstance(data, dict), "Response should be a JSON object"
        
        # Verify expected top-level structure
        assert "data" in data or "recommendations" in data, (
            "Response should contain 'data' or 'recommendations' field"
        )
        
        # Verify data field is a list (use 'in' check to handle empty lists correctly)
        if "data" in data:
            items = data["data"]
        else:
            items = data.get("recommendations")
        
        assert isinstance(items, list), (
            f"Data field should be a list, got {type(items).__name__}: {items}"
        )
        
        # Verify pagination metadata exists
        assert "meta" in data or "links" in data, (
            "Response should contain pagination metadata ('meta' or 'links')"
        )
        
        # If meta exists, verify it has pagination info
        if "meta" in data:
            meta = data["meta"]
            assert isinstance(meta, dict), "Meta should be a JSON object"
            # At least one pagination field should exist
            pagination_fields = ["count", "total", "limit", "offset"]
            has_pagination = any(field in meta for field in pagination_fields)
            assert has_pagination, (
                f"Meta should contain pagination info (one of: {pagination_fields})"
            )


@pytest.mark.ros
@pytest.mark.component
class TestROSProcessor:
    """Tests for ROS Processor service health."""

    @pytest.mark.smoke
    def test_ros_processor_pod_ready(self, cluster_config):
        """Verify ROS Processor pod is ready."""
        assert check_pod_ready(
            cluster_config.namespace,
            "app.kubernetes.io/component=ros-processor"
        ), "ROS Processor pod is not ready"

    def test_ros_processor_no_critical_errors(self, cluster_config):
        """Verify ROS Processor logs don't show critical errors."""
        result = run_oc_command([
            "logs", "-n", cluster_config.namespace,
            "-l", "app.kubernetes.io/component=ros-processor",
            "--tail=50"
        ], check=False)
        
        if result.returncode != 0:
            pytest.skip("Could not get ROS Processor logs")
        
        logs = result.stdout.lower()
        
        # Check for critical errors only
        critical_errors = ["fatal", "panic", "cannot connect"]
        for error in critical_errors:
            if error in logs:
                pytest.fail(f"Critical error '{error}' found in ROS Processor logs")
