"""
Gateway JWT authentication tests.

Tests for JWT authentication on the centralized API gateway.
The gateway handles all external API traffic with Keycloak JWT validation.
"""

import subprocess
import tempfile

import pytest
import requests


def _check_gateway_reachable(gateway_url: str, http_session: requests.Session) -> bool:
    """Check if gateway service is reachable."""
    try:
        # Try the ingress ready endpoint through the gateway
        response = http_session.get(f"{gateway_url}/ingress/ready", timeout=5)
        return response.status_code != 503
    except requests.exceptions.RequestException:
        return False


def _generate_fake_jwt() -> str | None:
    """Generate a fake JWT with valid structure but wrong signature."""
    try:
        import base64
        import json
        import os

        with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
            key_file = f.name

        subprocess.run(
            ["openssl", "genrsa", "-out", key_file, "2048"],
            capture_output=True,
            check=True,
        )

        header = {"alg": "RS256", "typ": "JWT", "kid": "fake-key"}
        payload = {
            "sub": "attacker",
            "iss": "https://fake-issuer.com",
            "aud": "cost-management-operator",
            "exp": 9999999999,
        }

        def b64url_encode(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

        header_b64 = b64url_encode(json.dumps(header).encode())
        payload_b64 = b64url_encode(json.dumps(payload).encode())

        message = f"{header_b64}.{payload_b64}"
        sign_result = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", key_file],
            input=message.encode(),
            capture_output=True,
            check=True,
        )
        signature_b64 = b64url_encode(sign_result.stdout)

        os.unlink(key_file)

        return f"{header_b64}.{payload_b64}.{signature_b64}"

    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


@pytest.mark.auth
@pytest.mark.integration
class TestGatewayJWTAuthentication:
    """Tests for JWT authentication on the centralized API gateway.

    The gateway is a centralized Envoy proxy that:
    - Validates JWT tokens from Keycloak
    - Injects X-Rh-Identity headers for backend services
    - Routes requests to appropriate backends based on path and method
    """

    @pytest.mark.smoke
    def test_gateway_reachable(self, gateway_url: str, http_session: requests.Session):
        """Verify the API gateway is reachable."""
        try:
            # Test ingress ready endpoint through gateway
            response = http_session.get(f"{gateway_url}/ingress/ready", timeout=10)
        except requests.exceptions.RequestException as e:
            pytest.skip(f"Cannot reach gateway service: {e}")

        if response.status_code == 503:
            pytest.skip("Gateway service returning 503 - pods may not be ready yet")

        assert response.status_code in [200, 401, 403], (
            f"Gateway not reachable: {response.status_code}"
        )

    def test_request_without_token_rejected(
        self, gateway_url: str, http_session: requests.Session
    ):
        """Verify requests without JWT token are rejected with 401."""
        if not _check_gateway_reachable(gateway_url, http_session):
            pytest.skip("Gateway service not available")

        response = http_session.post(f"{gateway_url}/ingress/v1/upload", timeout=10)

        assert response.status_code == 401, (
            f"Expected 401 for unauthenticated request, got {response.status_code}"
        )

    def test_malformed_token_rejected(
        self, gateway_url: str, http_session: requests.Session
    ):
        """Verify requests with malformed JWT token are rejected."""
        if not _check_gateway_reachable(gateway_url, http_session):
            pytest.skip("Gateway service not available")

        response = http_session.post(
            f"{gateway_url}/ingress/v1/upload",
            headers={"Authorization": "Bearer invalid.malformed.token"},
            timeout=10,
        )

        assert response.status_code == 401, (
            f"Expected 401 for malformed token, got {response.status_code}"
        )

    def test_fake_signature_token_rejected(
        self, gateway_url: str, http_session: requests.Session
    ):
        """Verify JWT tokens with invalid signatures are rejected."""
        if not _check_gateway_reachable(gateway_url, http_session):
            pytest.skip("Gateway service not available")

        fake_jwt = _generate_fake_jwt()
        if fake_jwt is None:
            pytest.skip("OpenSSL not available to generate fake JWT")

        response = http_session.post(
            f"{gateway_url}/ingress/v1/upload",
            headers={"Authorization": f"Bearer {fake_jwt}"},
            timeout=10,
        )

        assert response.status_code in [401, 403], (
            f"Expected 401/403 for fake signature, got {response.status_code}. "
            "CRITICAL: JWT with fake signature may have been accepted!"
        )

    def test_valid_token_accepted(
        self, gateway_url: str, jwt_token, http_session: requests.Session
    ):
        """Verify requests with valid JWT token are accepted (auth passes)."""
        response = http_session.get(
            f"{gateway_url}/ingress/ready",
            headers=jwt_token.authorization_header,
            timeout=10,
        )

        assert response.status_code not in [401, 403], (
            f"Valid JWT token was rejected: {response.status_code}"
        )

    def test_cost_management_api_accessible(
        self, gateway_url: str, jwt_token, http_session: requests.Session
    ):
        """Verify Cost Management API is accessible through gateway with valid JWT."""
        response = http_session.get(
            f"{gateway_url}/cost-management/v1/status/",
            headers=jwt_token.authorization_header,
            timeout=10,
        )

        # Accept 200 (success), 404 (endpoint may not exist), but not 401/403
        assert response.status_code not in [401, 403], (
            f"Valid JWT token was rejected for cost-management API: {response.status_code}"
        )

    def test_sources_api_accessible(
        self, gateway_url: str, jwt_token, http_session: requests.Session
    ):
        """Verify Sources API is accessible through gateway with valid JWT."""
        response = http_session.get(
            f"{gateway_url}/sources/v1.0/source_types",
            headers=jwt_token.authorization_header,
            timeout=10,
        )

        # Accept 200 (success), 404 (endpoint may not exist), but not 401/403
        assert response.status_code not in [401, 403], (
            f"Valid JWT token was rejected for sources API: {response.status_code}"
        )
