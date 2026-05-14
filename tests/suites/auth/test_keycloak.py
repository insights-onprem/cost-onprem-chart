"""
Keycloak connectivity and configuration tests.
"""

import pytest
import requests


@pytest.mark.auth
@pytest.mark.component
@pytest.mark.smoke
class TestKeycloakConnectivity:
    """Tests for Keycloak connectivity."""

    def test_keycloak_reachable(self, keycloak_config, http_session: requests.Session):
        """Verify Keycloak is reachable."""
        well_known_url = (
            f"{keycloak_config.url}/realms/{keycloak_config.realm}/"
            ".well-known/openid-configuration"
        )
        response = http_session.get(well_known_url, timeout=10)

        assert response.status_code == 200, (
            f"Keycloak not reachable at {well_known_url}: {response.status_code}"
        )

        data = response.json()
        assert "token_endpoint" in data, "Invalid OpenID configuration response"

    def test_oidc_discovery_endpoint(self, keycloak_config, http_session: requests.Session):
        """Verify OIDC discovery endpoint returns expected fields."""
        well_known_url = (
            f"{keycloak_config.url}/realms/{keycloak_config.realm}/"
            ".well-known/openid-configuration"
        )
        response = http_session.get(well_known_url, timeout=10)
        
        assert response.status_code == 200
        data = response.json()
        
        required_fields = [
            "issuer",
            "authorization_endpoint",
            "token_endpoint",
            "jwks_uri",
        ]
        
        for field in required_fields:
            assert field in data, f"OIDC config missing '{field}'"


@pytest.mark.auth
@pytest.mark.component
class TestJWTTokenAcquisition:
    """Tests for JWT token acquisition."""

    @pytest.mark.smoke
    def test_jwt_token_obtained(self, jwt_token):
        """Verify we can obtain a JWT token from Keycloak."""
        assert jwt_token.access_token, "JWT token is empty"
        assert len(jwt_token.access_token) > 100, "JWT token seems too short"
        assert not jwt_token.is_expired, "JWT token is already expired"

    def test_token_has_valid_structure(self, jwt_token):
        """Verify JWT token has valid structure (3 parts)."""
        parts = jwt_token.access_token.split(".")
        assert len(parts) == 3, "JWT should have 3 parts (header.payload.signature)"

    def test_token_payload_decodable(self, jwt_token):
        """Verify JWT payload can be decoded."""
        from conftest import decode_jwt_payload

        payload = decode_jwt_payload(jwt_token.access_token)

        assert "exp" in payload, "Token missing 'exp' claim"
        assert "iss" in payload, "Token missing 'iss' claim"
