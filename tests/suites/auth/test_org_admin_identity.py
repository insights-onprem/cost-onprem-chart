"""
Org-admin identity propagation tests.

Validates the full chain: Keycloak org-admin realm role -> Envoy Lua filter
reads realm_access.roles from the JWT -> sets is_org_admin in X-Rh-Identity ->
RBAC grants admin_default permissions (cost-management:*:*).

The observable proxy for is_org_admin is the RBAC /api/rbac/v1/access/ endpoint:
admin users receive cost-management:*:* permissions via the admin_default group,
while non-admin users with no RBAC group membership receive empty permissions.
"""

import pytest
import requests

from conftest import KeycloakConfig, ClusterConfig, decode_jwt_payload, obtain_user_jwt_token_for


@pytest.mark.auth
@pytest.mark.integration
class TestOrgAdminIdentityPropagation:
    """Verify that Envoy correctly propagates is_org_admin through the gateway."""

    @pytest.fixture
    def admin_jwt(self, keycloak_config: KeycloakConfig, cluster_config: ClusterConfig):
        """JWT for the admin user (has org-admin realm role)."""
        return obtain_user_jwt_token_for(keycloak_config, cluster_config)

    @pytest.fixture
    def viewer_jwt(self, keycloak_config: KeycloakConfig, cluster_config: ClusterConfig):
        """JWT for the viewer user (no org-admin realm role)."""
        return obtain_user_jwt_token_for(
            keycloak_config, cluster_config, username="viewer", password="viewer",
        )

    @pytest.fixture
    def rbac_access_url(self, gateway_url: str) -> str:
        """RBAC access endpoint through the gateway."""
        return f"{gateway_url}/rbac/v1/access/?application=cost-management&limit=50"

    def _get_permissions(self, url: str, token) -> list[str]:
        """Call RBAC access endpoint and return the list of permission strings."""
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {token.access_token}"},
            verify=False,
            timeout=30,
        )
        assert response.status_code == 200, (
            f"RBAC /access/ returned {response.status_code}: {response.text[:200]}"
        )
        data = response.json()
        return [entry["permission"] for entry in data.get("data", [])]

    def test_admin_gets_full_cost_management_access(
        self, rbac_access_url, admin_jwt,
    ):
        """Admin user (is_org_admin=true) receives cost-management:*:* from RBAC.

        The admin_default group in RBAC grants Cost Administrator
        (cost-management:*:*) to any user whose X-Rh-Identity has
        is_org_admin=true.
        """
        permissions = self._get_permissions(rbac_access_url, admin_jwt)
        assert "cost-management:*:*" in permissions, (
            f"Admin should have cost-management:*:* via admin_default, got: {permissions}"
        )

    def test_non_admin_lacks_cost_management_access(
        self, rbac_access_url, viewer_jwt,
    ):
        """Non-admin user (is_org_admin=false, no group) gets no cost-management perms.

        The viewer user has no RBAC group membership and is_org_admin=false,
        so RBAC should return no cost-management permissions.
        """
        permissions = self._get_permissions(rbac_access_url, viewer_jwt)
        wildcard_perms = [p for p in permissions if p == "cost-management:*:*"]
        assert not wildcard_perms, (
            f"Viewer should not have cost-management:*:* but got: {permissions}"
        )

    @pytest.mark.parametrize(
        "creds,expect_admin",
        [
            ({"username": "admin", "password": "admin"}, True),
            ({"username": "viewer", "password": "viewer"}, False),
        ],
        ids=["admin-is-org-admin", "viewer-is-not-org-admin"],
    )
    def test_org_admin_role_determines_access(
        self,
        keycloak_config: KeycloakConfig,
        cluster_config: ClusterConfig,
        rbac_access_url: str,
        creds: dict,
        expect_admin: bool,
    ):
        """Architectural invariant: is_org_admin in the JWT controls RBAC access.

        Any user with the org-admin realm role must receive
        cost-management:*:*, and any user without it must not. This
        parametrized test validates the invariant for every provisioned
        user, ensuring the architecture works for N admins (not just the
        bootstrapped one).
        """
        token = obtain_user_jwt_token_for(
            keycloak_config, cluster_config,
            username=creds["username"], password=creds["password"],
        )
        payload = decode_jwt_payload(token.access_token)
        roles = payload.get("realm_access", {}).get("roles", [])
        has_role = "org-admin" in roles

        assert has_role == expect_admin, (
            f"User {creds['username']}: expected org-admin={expect_admin}, "
            f"JWT has roles={roles}"
        )

        permissions = self._get_permissions(rbac_access_url, token)
        has_wildcard = "cost-management:*:*" in permissions

        assert has_wildcard == expect_admin, (
            f"User {creds['username']}: expected wildcard={expect_admin}, "
            f"got permissions={permissions}"
        )
