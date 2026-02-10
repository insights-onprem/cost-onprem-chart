"""
ROS recommendations API tests.

Tests for ROS API health and accessibility.
Note: Recommendation generation and validation is tested in suites/e2e/ as part of the complete pipeline.

Covers Jira Test Cases:
- FLPATH-3094: ROS API recommendations endpoint accessible via UI
- FLPATH-3155: API: Recommendations accept filter parameters
- FLPATH-3156: API: Recommendations support pagination
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
        
        Covers FLPATH-3094: ROS API recommendations endpoint accessible via UI
        """
        auth_header = get_fresh_token(keycloak_config, http_session)
        assert auth_header is not None, "Failed to obtain JWT token - auth system may be down"

        # Gateway route always includes /api prefix
        endpoint = f"{ros_api_url.rstrip('/')}/cost-management/v1/recommendations/openshift"

        response = http_session.get(endpoint, headers=auth_header, timeout=30)

        # ROS API returns 200 with empty data array when no recommendations exist,
        # not 404. The endpoint should always be accessible with valid auth.
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text[:200]}"
        )
        
        # Verify response structure - must have both meta and data
        data = response.json()
        assert "meta" in data, f"Response missing 'meta' field: {list(data.keys())}"
        
        has_data = "recommendations" in data or "data" in data
        assert has_data, f"Response missing data array: {list(data.keys())}"

    def test_recommendations_accept_filter_parameters(
        self, ros_api_url: str, keycloak_config, http_session: requests.Session, ros_test_data: dict
    ):
        """Verify recommendations endpoint accepts filter query parameters.
        
        Covers FLPATH-3155: API: Recommendations accept filter parameters
        
        This test is SELF-CONTAINED - ros_test_data fixture sets up its own data.
        
        This test verifies that the API:
        1. Accepts filter parameters without returning 400/422
        2. Returns proper response structure with meta and data fields
        3. Filtering by the test cluster_id returns matching results
        4. Filtering by non-existent values returns empty results
        """
        auth_header = get_fresh_token(keycloak_config, http_session)
        assert auth_header is not None, "Failed to obtain JWT token - auth system may be down"

        endpoint = f"{ros_api_url.rstrip('/')}/cost-management/v1/recommendations/openshift"
        cluster_id = ros_test_data["cluster_id"]

        # First, get unfiltered results - should include our test data
        baseline_response = http_session.get(endpoint, headers=auth_header, timeout=30)
        assert baseline_response.status_code == 200, f"Baseline request failed: {baseline_response.status_code}"
        baseline_data = baseline_response.json()
        baseline_count = baseline_data.get("meta", {}).get("count", 0)
        
        # We should have data from ros_test_data fixture
        assert baseline_count > 0, (
            f"No recommendations found - ros_test_data fixture should have created data for cluster {cluster_id}"
        )
        
        # Test filtering by our known cluster_id - should return results
        response = http_session.get(
            endpoint, headers=auth_header, params={"cluster": cluster_id}, timeout=30
        )
        assert response.status_code == 200
        data = response.json()
        assert "meta" in data
        cluster_filtered_count = data["meta"].get("count", 0)
        assert cluster_filtered_count > 0, (
            f"Filtering by cluster_id '{cluster_id}' should return results, got 0"
        )
        
        # Test filter parameters that should NOT match any data (non-existent values)
        non_matching_filters = [
            {"cluster": "nonexistent-cluster-xyz123"},
            {"project": "nonexistent-project-xyz123"},
            {"workload": "nonexistent-workload-xyz123"},
        ]

        for params in non_matching_filters:
            response = http_session.get(
                endpoint, headers=auth_header, params=params, timeout=30
            )
            
            assert response.status_code == 200, (
                f"Expected 200 with filter {params}, got {response.status_code}: {response.text[:200]}"
            )
            
            data = response.json()
            assert "meta" in data, f"Response missing 'meta' field with filter {params}"
            
            # Non-matching filter should return 0 results
            filtered_count = data["meta"].get("count", 0)
            assert filtered_count == 0, (
                f"Filter {params} should return 0 results for non-existent value, got {filtered_count}"
            )
        
        # Test valid filter parameter names (API should accept them)
        valid_filter_params = [
            {"workload_type": "deployment"},
            {"workload_type": "daemonset"},
            {"workload_type": "statefulset"},
        ]

        for params in valid_filter_params:
            response = http_session.get(
                endpoint, headers=auth_header, params=params, timeout=30
            )
            
            # API should accept these filter params (200, not 400)
            assert response.status_code == 200, (
                f"Expected 200 with filter {params}, got {response.status_code}: {response.text[:200]}"
            )
            
            data = response.json()
            assert "meta" in data, f"Response missing 'meta' field with filter {params}"
            
            # Filtered count should be <= baseline (filtering reduces results)
            filtered_count = data["meta"].get("count", 0)
            assert filtered_count <= baseline_count, (
                f"Filtered count ({filtered_count}) should be <= baseline ({baseline_count})"
            )

    def test_recommendations_support_pagination(
        self, ros_api_url: str, keycloak_config, http_session: requests.Session, ros_test_data: dict
    ):
        """Verify recommendations endpoint supports pagination parameters.
        
        Covers FLPATH-3156: API: Recommendations support pagination
        
        This test is SELF-CONTAINED - ros_test_data fixture sets up its own data.
        
        This test verifies that the API:
        1. Accepts limit and offset parameters without returning 400/422
        2. Returns meta field with pagination info
        3. Respects the requested limit value
        4. Data array length respects limit
        """
        auth_header = get_fresh_token(keycloak_config, http_session)
        assert auth_header is not None, "Failed to obtain JWT token - auth system may be down"

        endpoint = f"{ros_api_url.rstrip('/')}/cost-management/v1/recommendations/openshift"

        # First, get total count without pagination
        baseline_response = http_session.get(endpoint, headers=auth_header, timeout=30)
        assert baseline_response.status_code == 200
        baseline_data = baseline_response.json()
        total_count = baseline_data.get("meta", {}).get("count", 0)
        
        # We should have data from ros_test_data fixture
        assert total_count > 0, (
            f"No recommendations found - ros_test_data fixture should have created data"
        )

        # Test pagination parameters
        pagination_params = [
            {"limit": 10},
            {"limit": 10, "offset": 0},
            {"limit": 1},  # Test with limit=1 to verify pagination works
            {"limit": 100, "offset": 0},
        ]

        for params in pagination_params:
            response = http_session.get(
                endpoint, headers=auth_header, params=params, timeout=30
            )
            
            # API should accept pagination params and return 200
            assert response.status_code == 200, (
                f"Expected 200 with pagination {params}, got {response.status_code}: {response.text[:200]}"
            )
            
            data = response.json()
            
            # Must have meta field for pagination
            assert "meta" in data, (
                f"Response missing 'meta' field with pagination {params}: {list(data.keys())}"
            )
            
            meta = data["meta"]
            
            # Verify limit is respected in response meta
            assert "limit" in meta, f"Meta missing 'limit' field. Available: {list(meta.keys())}"
            assert meta["limit"] == params["limit"], (
                f"Limit mismatch: requested {params['limit']}, got {meta['limit']}"
            )
            
            # Verify data array length respects limit
            data_array = data.get("recommendations") or data.get("data") or []
            assert len(data_array) <= params["limit"], (
                f"Data array length ({len(data_array)}) exceeds limit ({params['limit']})"
            )
            
            # If we have more data than limit, array should be exactly limit size
            if total_count > params["limit"]:
                assert len(data_array) == params["limit"], (
                    f"With {total_count} total items and limit={params['limit']}, "
                    f"expected {params['limit']} items, got {len(data_array)}"
                )

    def test_recommendations_pagination_meta_structure(
        self, ros_api_url: str, keycloak_config, http_session: requests.Session, ros_test_data: dict
    ):
        """Verify recommendations response includes proper pagination metadata.
        
        Covers FLPATH-3156: API: Recommendations support pagination
        
        This test is SELF-CONTAINED - ros_test_data fixture sets up its own data.
        
        This test verifies the response structure includes:
        1. meta field with count, limit, and offset
        2. data/recommendations array with actual data
        3. Proper types for pagination fields
        4. count >= data length (count is total, data is paginated subset)
        """
        auth_header = get_fresh_token(keycloak_config, http_session)
        assert auth_header is not None, "Failed to obtain JWT token - auth system may be down"

        endpoint = f"{ros_api_url.rstrip('/')}/cost-management/v1/recommendations/openshift"

        response = http_session.get(
            endpoint, headers=auth_header, params={"limit": 10}, timeout=30
        )
        
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text[:200]}"
        )
        
        data = response.json()
        
        # Must have meta field
        assert "meta" in data, f"Response missing 'meta' field: {list(data.keys())}"
        
        meta = data["meta"]
        
        # Required pagination fields
        required_fields = ["count", "limit", "offset"]
        for field in required_fields:
            assert field in meta, (
                f"Meta missing required pagination field '{field}'. Available: {list(meta.keys())}"
            )
        
        # Verify types
        assert isinstance(meta["count"], int), f"count should be int, got {type(meta['count'])}"
        assert isinstance(meta["limit"], int), f"limit should be int, got {type(meta['limit'])}"
        assert isinstance(meta["offset"], int), f"offset should be int, got {type(meta['offset'])}"
        
        # Verify requested limit is respected
        assert meta["limit"] == 10, f"Expected limit=10, got {meta['limit']}"
        
        # Must have data array
        has_data = "recommendations" in data or "data" in data
        assert has_data, f"Response missing data array: {list(data.keys())}"
        
        # Data must be a list
        data_array = data.get("recommendations") or data.get("data")
        assert isinstance(data_array, list), f"Data should be list, got {type(data_array)}"
        
        # We should have data from ros_test_data fixture
        assert meta["count"] > 0, (
            f"No recommendations found - ros_test_data fixture should have created data"
        )
        
        # Count should be >= data length (count is total, data is paginated)
        assert meta["count"] >= len(data_array), (
            f"Count ({meta['count']}) should be >= data length ({len(data_array)})"
        )
        
        # Data array should have items (up to limit)
        assert len(data_array) > 0, "Data array should not be empty when count > 0"


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
