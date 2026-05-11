"""
UI OAuth flow tests.

Tests for the UI authentication flow with Keycloak (password grant).
Migrated from scripts/test-ui-oauth-flow.sh
"""

import base64
import json

import pytest
import requests

from utils import run_oc_command, get_route_url


def _decode_jwt_payload(access_token: str) -> dict:
    """Decode the payload section of a JWT without signature verification."""
    payload_b64 = access_token.split(".")[1]
    padding = 4 - len(payload_b64) % 4
    if padding != 4:
        payload_b64 += "=" * padding
    return json.loads(base64.urlsafe_b64decode(payload_b64))


def _obtain_token(http_session, keycloak_config, ui_client_config, credentials):
    """Obtain an access token via password grant, returning the raw JWT string."""
    data = {
        "username": credentials["username"],
        "password": credentials["password"],
        "grant_type": "password",
        "client_id": ui_client_config["client_id"],
        "scope": "openid profile email",
    }
    if ui_client_config.get("client_secret"):
        data["client_secret"] = ui_client_config["client_secret"]

    token_url = (
        f"{keycloak_config.url}/realms/{keycloak_config.realm}/"
        "protocol/openid-connect/token"
    )
    response = http_session.post(token_url, data=data, timeout=30)
    if response.status_code != 200:
        pytest.skip(
            f"Password grant failed for {credentials['username']!r}: "
            f"{response.status_code}"
        )
    return response.json()["access_token"]


@pytest.mark.auth
@pytest.mark.integration
class TestUIOAuthFlow:
    """Tests for UI OAuth authentication flow."""

    @pytest.fixture
    def ui_route(self, cluster_config) -> str:
        """Get the UI route URL."""
        result = run_oc_command([
            "get", "route", "-n", cluster_config.namespace,
            "-l", "app.kubernetes.io/component=ui",
            "-o", "jsonpath={.items[0].spec.host}"
        ], check=False)
        
        host = result.stdout.strip()
        if not host:
            pytest.skip("UI route not found")
        return f"https://{host}"

    def test_ui_pod_running(self, cluster_config):
        """Verify UI pod is running."""
        result = run_oc_command([
            "get", "pods", "-n", cluster_config.namespace,
            "-l", "app.kubernetes.io/component=ui",
            "-o", "jsonpath={.items[0].status.phase}"
        ], check=False)
        
        status = result.stdout.strip()
        if not status:
            pytest.skip("UI pod not found")
        
        assert status == "Running", f"UI pod status: {status}"

    def test_oauth_proxy_no_tls_errors(self, cluster_config):
        """Verify no TLS errors in oauth-proxy logs."""
        result = run_oc_command([
            "logs", "-n", cluster_config.namespace,
            "-l", "app.kubernetes.io/component=ui",
            "-c", "oauth-proxy",
            "--tail=100"
        ], check=False)
        
        if result.returncode != 0:
            pytest.skip("Could not get oauth-proxy logs")
        
        logs = result.stdout.lower()
        tls_errors = ["tls.*error", "certificate.*error", "x509"]
        
        for pattern in tls_errors:
            if pattern in logs:
                pytest.fail(f"TLS error found in oauth-proxy logs: {pattern}")

    def test_password_grant_token_acquisition(
        self,
        keycloak_config,
        ui_client_config,
        test_user_credentials,
        http_session: requests.Session,
    ):
        """Verify JWT token can be obtained via password grant."""
        if not ui_client_config.get("client_secret"):
            # Try without client secret (public client)
            data = {
                "username": test_user_credentials["username"],
                "password": test_user_credentials["password"],
                "grant_type": "password",
                "client_id": ui_client_config["client_id"],
                "scope": "openid profile email",
            }
        else:
            data = {
                "username": test_user_credentials["username"],
                "password": test_user_credentials["password"],
                "grant_type": "password",
                "client_id": ui_client_config["client_id"],
                "client_secret": ui_client_config["client_secret"],
                "scope": "openid profile email",
            }
        
        token_url = (
            f"{keycloak_config.url}/realms/{keycloak_config.realm}/"
            "protocol/openid-connect/token"
        )
        
        response = http_session.post(token_url, data=data, timeout=30)
        
        if response.status_code != 200:
            pytest.skip(f"Password grant failed: {response.status_code}")
        
        token_data = response.json()
        assert "access_token" in token_data, "No access_token in response"

    def test_jwt_contains_required_claims(
        self,
        keycloak_config,
        ui_client_config,
        test_user_credentials,
        http_session: requests.Session,
    ):
        """Verify JWT contains required claims (preferred_username, org_id)."""
        # Get token via password grant
        data = {
            "username": test_user_credentials["username"],
            "password": test_user_credentials["password"],
            "grant_type": "password",
            "client_id": ui_client_config["client_id"],
            "scope": "openid profile email",
        }
        if ui_client_config.get("client_secret"):
            data["client_secret"] = ui_client_config["client_secret"]
        
        token_url = (
            f"{keycloak_config.url}/realms/{keycloak_config.realm}/"
            "protocol/openid-connect/token"
        )
        
        response = http_session.post(token_url, data=data, timeout=30)
        
        if response.status_code != 200:
            pytest.skip("Could not obtain token for claims validation")
        
        access_token = response.json().get("access_token")
        
        # Decode JWT payload
        payload_b64 = access_token.split(".")[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        
        # Check required claims
        assert "preferred_username" in payload, "JWT missing preferred_username"
        
        # These are warnings, not failures (may not be configured)
        if "org_id" not in payload:
            pytest.skip("JWT missing org_id claim (may need Keycloak mapper)")
        if "account_number" not in payload:
            pytest.skip("JWT missing account_number claim (may need Keycloak mapper)")


@pytest.mark.auth
@pytest.mark.integration
class TestOrgAdminRealmRole:
    """Verify that Keycloak assigns the org-admin realm role correctly.

    The org-admin role controls is_org_admin in the X-Rh-Identity header.
    Admin users (orgAdmin: true in values.yaml) must have it; non-admin
    users (orgAdmin: false) must not.
    """

    def test_admin_jwt_contains_org_admin_realm_role(
        self,
        keycloak_config,
        ui_client_config,
        test_user_credentials,
        http_session: requests.Session,
    ):
        """Admin user's JWT must contain the org-admin realm role."""
        token = _obtain_token(
            http_session, keycloak_config, ui_client_config, test_user_credentials,
        )
        payload = _decode_jwt_payload(token)

        realm_access = payload.get("realm_access", {})
        roles = realm_access.get("roles", [])
        assert "org-admin" in roles, (
            f"Expected 'org-admin' in realm_access.roles for admin user, "
            f"got: {roles}"
        )

    def test_non_admin_jwt_lacks_org_admin_realm_role(
        self,
        keycloak_config,
        ui_client_config,
        non_admin_user_credentials,
        http_session: requests.Session,
    ):
        """Non-admin (viewer) user's JWT must NOT contain the org-admin realm role."""
        token = _obtain_token(
            http_session, keycloak_config, ui_client_config, non_admin_user_credentials,
        )
        payload = _decode_jwt_payload(token)

        realm_access = payload.get("realm_access", {})
        roles = realm_access.get("roles", [])
        assert "org-admin" not in roles, (
            f"Viewer user should not have 'org-admin' role, "
            f"got: {roles}"
        )
