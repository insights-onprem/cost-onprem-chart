"""
ROS recommendations API tests.

Tests for ROS API health and accessibility.
Note: Recommendation generation and validation is tested in suites/e2e/ as part of the complete pipeline.
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
        """Verify recommendations endpoint is accessible with JWT."""
        auth_header = get_fresh_token(keycloak_config, http_session)
        if not auth_header:
            pytest.skip("Could not obtain fresh JWT token")

        # Gateway route already includes /api prefix
        base = ros_api_url.rstrip("/")
        if base.endswith("/api"):
            endpoint = f"{base}/cost-management/v1/recommendations/openshift"
        else:
            endpoint = f"{base}/api/cost-management/v1/recommendations/openshift"

        response = http_session.get(endpoint, headers=auth_header, timeout=30)

        # Should not get auth errors
        assert response.status_code not in [401, 403], (
            f"Authentication failed: {response.status_code}"
        )
        # Accept 200 (has data) or 404 (no data yet)
        assert response.status_code in [200, 404], (
            f"Unexpected status: {response.status_code}"
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
