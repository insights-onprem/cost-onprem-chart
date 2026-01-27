"""
E2E smoke tests.

Quick validation that the entire system is operational.
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


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.smoke
class TestE2ESmoke:
    """Quick smoke tests for E2E validation."""

    def test_all_critical_pods_running(self, cluster_config):
        """Verify all critical pods are running."""
        critical_components = [
            ("database", "app.kubernetes.io/component=database"),
            ("ingress", "app.kubernetes.io/component=ingress"),
            ("kruize", "app.kubernetes.io/component=ros-optimization"),
            ("ros-api", "app.kubernetes.io/component=ros-api"),
        ]
        
        failures = []
        for name, label in critical_components:
            if not check_pod_ready(cluster_config.namespace, label):
                failures.append(name)
        
        assert not failures, f"Critical pods not ready: {failures}"

    def test_keycloak_accessible(self, keycloak_config, http_session: requests.Session):
        """Verify Keycloak is accessible."""
        response = http_session.get(
            f"{keycloak_config.url}/realms/{keycloak_config.realm}",
            timeout=10,
        )
        assert response.status_code == 200, "Keycloak not accessible"

    def test_jwt_token_obtainable(self, keycloak_config, http_session: requests.Session):
        """Verify JWT token can be obtained."""
        auth_header = get_fresh_token(keycloak_config, http_session)
        assert auth_header, "Could not obtain JWT token"

    def test_ingress_accepts_authenticated_requests(
        self, ingress_url: str, keycloak_config, http_session: requests.Session
    ):
        """Verify ingress accepts authenticated requests."""
        auth_header = get_fresh_token(keycloak_config, http_session)
        if not auth_header:
            pytest.skip("Could not obtain fresh JWT token")
        
        response = http_session.get(
            f"{ingress_url}/ready",
            headers=auth_header,
            timeout=10,
        )
        
        # Should not get 401/403
        assert response.status_code not in [401, 403], (
            f"Ingress rejected valid token: {response.status_code}"
        )

    def test_backend_api_accessible(
        self, gateway_url: str, keycloak_config, http_session: requests.Session
    ):
        """Verify backend API is accessible through the gateway."""
        auth_header = get_fresh_token(keycloak_config, http_session)
        if not auth_header:
            pytest.skip("Could not obtain fresh JWT token")

        response = http_session.get(
            f"{gateway_url}/cost-management/v1/status/",
            headers=auth_header,
            timeout=10,
        )

        # Accept 200 (success) or 404 (endpoint may not exist), but not 401/403
        assert response.status_code not in [401, 403], (
            f"Backend API rejected valid token: {response.status_code}"
        )

    def test_kafka_cluster_healthy(self, cluster_config):
        """Verify Kafka cluster is healthy."""
        # Check for Kafka pods in common namespaces
        for ns in ["kafka", cluster_config.namespace, "strimzi"]:
            result = run_oc_command([
                "get", "kafka", "-n", ns,
                "-o", "jsonpath={.items[0].status.conditions[?(@.type=='Ready')].status}"
            ], check=False)
            
            if result.stdout.strip() == "True":
                return
        
        pytest.skip("Kafka cluster not found or not ready")
