"""
RBAC behavior through the public API gateway (Envoy + Keycloak JWT).

Complements suites/e2e/test_rbac_access.py (which uses in-cluster X-Rh-Identity)
by exercising real JWT → Envoy → Koku/ROS → insights-rbac flows.
"""

from __future__ import annotations

import json
import os

import pytest
import requests

from conftest import obtain_password_grant_token
from rbac_keycloak_users import (
    ensure_realm_user_with_password,
    fetch_keycloak_master_admin_token,
)
from suites.auth.test_gateway_auth import _check_gateway_reachable
from utils import get_route_url, run_oc_command


@pytest.fixture(scope="session")
def rbac_gateway_test_user_password() -> str:
    """Password for synthetic RBAC gateway test users (Keycloak + password grant)."""
    return os.environ.get("RBAC_GATEWAY_TEST_USER_PASSWORD", "RbacGwTest1!")


@pytest.fixture(scope="module")
def _provision_gateway_nobody_user(
    cluster_config,
    keycloak_config,
    org_id: str,
    rbac_gateway_test_user_password: str,
):
    """Ensure ``nobody-unassigned`` exists in Keycloak (no RBAC group membership)."""
    admin_token = fetch_keycloak_master_admin_token(
        keycloak_config.url,
        cluster_config.keycloak_namespace,
    )
    if not admin_token:
        pytest.skip("Could not obtain Keycloak master admin token (secret or API)")

    try:
        ensure_realm_user_with_password(
            keycloak_base_url=keycloak_config.url,
            realm=keycloak_config.realm,
            admin_token=admin_token,
            username="nobody-unassigned",
            password=rbac_gateway_test_user_password,
            org_id=org_id,
            account_number="7890123",
            email="nobody-unassigned@rbac-gateway.test",
        )
    except RuntimeError as exc:
        pytest.skip(f"Keycloak user provisioning failed: {exc}")

    yield


@pytest.fixture
def gateway_nobody_user_jwt(
    cluster_config,
    keycloak_config,
    rbac_gateway_test_user_password: str,
    _provision_gateway_nobody_user,
):
    """Fresh password-grant JWT for ``nobody-unassigned``."""
    return obtain_password_grant_token(
        "nobody-unassigned",
        rbac_gateway_test_user_password,
        keycloak_config,
        cluster_config,
    )


@pytest.mark.auth
@pytest.mark.integration
class TestRBACGateway:
    """RBAC enforcement on routes reached through the Envoy gateway."""

    def test_gateway_openshift_costs_unauthenticated_returns_401(
        self, gateway_url: str, http_session: requests.Session
    ):
        """Protected Koku route rejects missing JWT."""
        if not _check_gateway_reachable(gateway_url, http_session):
            pytest.skip("Gateway service not available")

        url = f"{gateway_url.rstrip('/')}/cost-management/v1/reports/openshift/costs/"
        response = http_session.get(url, timeout=20)
        assert response.status_code == 401, (
            f"Expected 401 without Authorization, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    def test_gateway_rbac_status_unauthenticated_returns_401(
        self, gateway_url: str, http_session: requests.Session
    ):
        """RBAC admin API is JWT-gated at the gateway."""
        if not _check_gateway_reachable(gateway_url, http_session):
            pytest.skip("Gateway service not available")

        url = f"{gateway_url.rstrip('/')}/rbac/v1/status/"
        response = http_session.get(url, timeout=20)
        assert response.status_code == 401, (
            f"Expected 401 without Authorization on RBAC status, got "
            f"{response.status_code}: {response.text[:200]}"
        )

    def test_gateway_openshift_costs_user_without_rbac_returns_403(
        self,
        gateway_url: str,
        http_session: requests.Session,
        gateway_nobody_user_jwt,
    ):
        """Authenticated user with no cost-management permissions receives 403."""
        if not _check_gateway_reachable(gateway_url, http_session):
            pytest.skip("Gateway service not available")

        url = f"{gateway_url.rstrip('/')}/cost-management/v1/reports/openshift/costs/"
        response = http_session.get(
            url,
            headers=gateway_nobody_user_jwt.authorization_header,
            timeout=90,
        )
        assert response.status_code in (403, 424), (
            f"Expected 403 (RBAC deny) or 424 (RBAC dependency failure), "
            f"got {response.status_code}: {response.text[:300]}"
        )

    def test_gateway_ros_recommendations_user_without_rbac_returns_403(
        self,
        gateway_url: str,
        http_session: requests.Session,
        gateway_nobody_user_jwt,
    ):
        """ROS recommendations require the same cost-management permissions as Koku."""
        if not _check_gateway_reachable(gateway_url, http_session):
            pytest.skip("Gateway service not available")

        url = f"{gateway_url.rstrip('/')}/cost-management/v1/recommendations/openshift"
        response = http_session.get(
            url,
            headers=gateway_nobody_user_jwt.authorization_header,
            timeout=90,
        )
        assert response.status_code in (403, 424), (
            f"Expected 403 or 424 from ROS without RBAC permissions, "
            f"got {response.status_code}: {response.text[:300]}"
        )


@pytest.mark.auth
@pytest.mark.integration
def test_rbac_migration_job_completed(cluster_config):
    """RBAC Helm hook migration job finished successfully (cluster sanity)."""
    gateway_route = f"{cluster_config.helm_release_name}-api"
    if not get_route_url(cluster_config.namespace, gateway_route):
        pytest.skip("API route not found — cluster not deployed for this suite")

    result = run_oc_command(
        [
            "get",
            "jobs",
            "-n",
            cluster_config.namespace,
            "-l",
            "app.kubernetes.io/component=rbac-migration",
            "-o",
            "json",
        ],
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"oc get jobs failed: {result.stderr}")

    data = json.loads(result.stdout or "{}")
    items = data.get("items") or []
    if not items:
        pytest.skip("No rbac-migration job found (chart without RBAC or hook removed)")

    status = items[0].get("status") or {}
    if status.get("active"):
        pytest.skip("rbac-migration job still running")
    if status.get("failed"):
        pytest.fail(
            f"rbac-migration job failed: {json.dumps(status, indent=2)[:800]}"
        )
    assert status.get("succeeded") == 1, (
        f"Expected rbac-migration job succeeded=1, got: {json.dumps(status)[:800]}"
    )
